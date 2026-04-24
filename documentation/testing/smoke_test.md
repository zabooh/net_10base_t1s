# Smoke Test — Broad Functional Regression Guard

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. When to Run It](#2-when-to-run-it)
- [3. What It Covers](#3-what-it-covers)
  - [3.1 Phase 1 — Boot + PTP FINE](#31-phase-1--boot--ptp-fine)
  - [3.2 Phase 2 — CLI coverage](#32-phase-2--cli-coverage)
  - [3.3 Phase 3 — End-to-end](#33-phase-3--end-to-end)
- [3.4 Post-test teardown](#34-post-test-teardown)
- [4. What It Deliberately Omits](#4-what-it-deliberately-omits)
- [5. Usage](#5-usage)
- [6. Exit Codes and Log File](#6-exit-codes-and-log-file)
- [7. Sanity Gates](#7-sanity-gates)
- [8. Refactoring Workflow](#8-refactoring-workflow)
- [9. Reading a Failure](#9-reading-a-failure)
- [10. Limitations](#10-limitations)

---

## 1. Purpose

`smoke_test.py` is a broad, fast regression guard. It exercises every CLI
command in `app.c` plus the critical end-to-end path (PTP boot → FINE lock →
`tfuture` arm/fire) in one run, reports a single **PASS/FAIL** per check, and
returns a non-zero exit code on any failure.

It is intentionally **not** a performance test. The dedicated tests
(`tfuture_quick_check.py`, `ptp_drift_compensate_test.py`, etc.) measure
timing quality with tight bounds. The smoke test only asks:
_"Does every command still respond, and does the full chain still work at all?"_

Runtime: **~3 minutes** with a full reset, **~45 s** with `--no-reset`.

---

## 2. When to Run It

- After every build that might have changed firmware behaviour (refactors,
  new features, bug fixes).
- After every commit during a multi-commit refactor — specifically intended
  as the per-commit gate for the `refactor/app-split` branch (see
  `prompts/refactor_app_c_into_modules.md`).
- Before opening a pull request, as the last pre-flight check.

It is **not** meant to replace:

- Dedicated timing tests (`tfuture_quick_check.py`, `tfuture_sync_test.py`)
- Long-running soak tests (`ptp_drift_compensate_test.py` with defaults)
- Interactive debugging sessions

---

## 3. What It Covers

### 3.1 Phase 1 — Boot + PTP FINE

Checks:

- GM and FOL respond to a serial command after power-up.
- `reset` command accepted on both boards.
- `setip eth0 ...` succeeds on both.
- `ptp_mode master` / `ptp_mode follower` start the protocol.
- Follower reaches **`PTP FINE`** within `--conv-timeout` (default 60 s).
- Settle for `--settle-s` seconds so the drift filter can converge.

Skipped with `--no-reset` — useful when iterating on a firmware change
without re-booting between runs.

### 3.2 Phase 2 — CLI coverage

One call per command, each matched against a regex for the expected response:

| Subsystem        | Commands tested                                                                 |
| ---------------- | ------------------------------------------------------------------------------- |
| `lan_regs`       | `lan_read`                                                                      |
| `ptp`            | `ptp_mode`, `ptp_status`, `ptp_time`, `ptp_interval`, `ptp_offset`, `ptp_trace` (on/off), `ptp_dst` |
| `clk`            | `clk_get` (both boards)                                                         |
| `loop_stats`     | `loop_stats`, `loop_stats reset`                                                |
| `ptp_offset_trace` | `ptp_offset_reset`, `ptp_offset_dump`                                         |
| `sw_ntp`         | `sw_ntp_mode`, `sw_ntp_status`, `sw_ntp_poll` (query + set), `sw_ntp_trace` (on/off), `sw_ntp_offset_reset`, `sw_ntp_offset_dump` |
| `tfuture`        | `tfuture_status`, `tfuture_reset`, `tfuture_dump`, `tfuture_cancel`             |

Each check prints the first line of the matched response, so the log doubles
as a sanity snapshot of the system state.

### 3.3 Phase 3 — End-to-end

- **PTP offset < 50 µs** on the follower (sanity gate — typical is ~1 µs).
- **5 rounds of `tfuture_at`** armed on both boards at identical 2-second
  future targets.
- **FOL self-jitter median < 200 µs** across the 5 fires (typical is ~70 µs
  on the reference hardware).
- **SW-NTP end-to-end exchange**: GM enters `sw_ntp_mode master`, FOL
  enters `sw_ntp_mode follower <gm_ip>`, and after an 8 s dwell the
  follower's `sw_ntp_status` is checked for:
  - **samples ≥ 5** (at 1 Hz poll)
  - **success rate ≥ 70 %** (tolerates 1-2 initial ARP-settling timeouts
    after a fresh boot; typical steady-state is 100 %)
  - **|last_offset| < 1 ms** (PTP is locked, so application-layer offset
    is dominated by UDP jitter, typically 100-300 µs)
  Both boards revert to `sw_ntp_mode off` afterwards.

This is the real litmus test: the whole chain — serial CLI, PTP lock, TC0
tick conversion, spin-wait firing, ring buffer, dump, UDP socket layer —
has to work for this phase to pass.

**cyclic_fire end-to-end** — at the very end of Phase 3 the test starts
`cyclic_start 500` on **both** boards with a shared PTP-wallclock anchor,
dwells 2 s, then checks on each side:

- **running** flag == yes during the dwell
- **cycles** within a **loose** sanity range (500..15000) — see note below
- **misses** < 20 (catches main-loop starvation)
- **cyclic_stop** succeeds, **running** == no afterwards

The cycle-count gate is intentionally wide because a scale-invariant
~1.7× rate factor is currently observed on the reference hardware (4000
nominal cycles, ~6800 observed at 500 µs period / 2 s dwell).  See
Ticket 7 in `prompts/codebase_cleanup_followups.md`.  The smoke test's
job here is to catch **"callback never fires"** and **"runaway"**, not
to measure rate precision.  An oscilloscope on PB22 of both boards is
the right tool for phase-alignment verification.

**By-product: Crystal deviations** — at the end of Phase 3 the test
prints per-crystal ppm deviations (no PASS/FAIL, informational only):

```
Crystal deviations (by-product, informational):
  GM  LAN8651 :     0.000 ppm   (reference)
  GM  SAME54  :  -965.835 ppm   (from drift_ppb)
  FOL SAME54  : -1124.758 ppm   (from drift_ppb)
  FOL LAN8651 :    +5.020 ppm   (from MAC_TI + MAC_TISUBN)
```

Derived as a side-effect of the PTP lock:

- **SAME54 crystals** → `−drift_ppb / 1000` (the PI servo drives the
  LAN865x TSU to the real wallclock, so the reported drift is the
  complement of the SAME54's deviation).
- **FOL LAN8651** → decode of the PI-calibrated `CLOCK_INCREMENT`
  register pair (`MAC_TI` + `MAC_TISUBN`, read live via `lan_read`).
- **GM LAN8651** → 0 ppm by definition (reference).

The same analysis is implemented in `tfuture_quick_check.py`. If you
need numerical cross-validation across many runs use that tool; the
smoke test just prints the current values once per run.

### 3.4 Post-test teardown

After the PASS/FAIL summary prints, the test runs an **automatic
teardown phase** that does NOT contribute to the summary but leaves
the hardware in a clean, immediately-usable state:

1. **Reset both boards** and wait for the `[APP] Build:` boot banner
   (same flow as Phase 1).
2. **Re-configure IPs** via `setip eth0 …` on both boards.
3. **Start PTP**: `ptp_mode follower` on FOL, `ptp_mode master` on GM.
4. **Wait for FOL `PTP FINE`** (usually 2-3 s).

Rationale: Phase 3 ends with the firmware in an unusual state —
`cyclic_start_free` has force-reset PTP_CLOCK, SW-NTP mode was toggled,
`demo_autopilot off` was sent, and various trace toggles happened.
Without the teardown, a user who wanted to immediately run a Saleae
capture or another CLI session would first need to power-cycle or run
a separate reset script.  The teardown makes that unnecessary: the
final log line reports either

```
  FOL reached PTP FINE in 2.1 s — GM (master) + FOL (follower) in sync,
  ready for next use.
```

or, on failure, a clear "NOT in sync" message prompting a power-cycle.
Teardown failures do **not** change the smoke-test exit code — the
PASS/FAIL summary remains the source of truth for CI pipelines.

---

## 4. What It Deliberately Omits

Some commands would disturb the test state itself if called during Phase 2:

| Command       | Reason for omission                                               |
| ------------- | ----------------------------------------------------------------- |
| `lan_write`   | Would mutate LAN865x register state, potentially unpredictably.   |
| `ptp_reset`   | Would tear down the FINE lock needed for Phase 3.                 |
| `clk_set`     | Would make the software clock jump; breaks subsequent checks.     |
| `ptp_mode off` / `master` | Would interrupt the PTP session mid-run.             |
| `tfuture_at` / `tfuture_in` (isolated) | Exercised via Phase 3 instead.       |

If you need to validate those commands, they each have a dedicated test
(e.g. `tfuture_drift_forced_test.py` forces `clk_set_drift`).

---

## 5. Usage

Run from [tools/test-harness/](../../tools/test-harness/):

```bash
cd tools/test-harness

# Full run (~60 s) — reset boards, wait for FINE, all phases + teardown
python smoke_test.py

# Fast iteration (~45 s) — skip Phase 1 reset+FINE, use boards as-is
python smoke_test.py --no-reset

# Stop at first failure (for debugging)
python smoke_test.py --abort-on-fail --verbose

# Custom ports and IPs
python smoke_test.py --gm-port COM8 --fol-port COM10 \
                     --gm-ip 192.168.0.30 --fol-ip 192.168.0.20

# Custom log file
python smoke_test.py --log-file smoke_pre_refactor.log
```

All defaults come from `ptp_drift_compensate_test.py` (COM8 / COM10,
192.168.0.30 / 192.168.0.20, netmask 255.255.255.0).

---

## 6. Exit Codes and Log File

- **Exit 0** — every check passed. Safe to proceed.
- **Exit 1** — at least one failure, or fatal I/O error (serial port busy etc.).

A log file is always written: `smoke_test_<YYYYMMDD_HHMMSS>.log` by default,
or whatever you pass to `--log-file`. The file mirrors stdout, so it can be
committed or attached to a bug report verbatim.

The log ends with a summary block:

```
======================================================================
  Summary: 42 PASS / 0 FAIL  (total 42)
======================================================================
```

On failure, each failing check is re-listed with the reason text below
the summary.

---

## 7. Sanity Gates

Four numeric gates in Phase 3 guard the end-to-end chain:

| Gate                            | Default      | Typical observed | Purpose                                  |
| ------------------------------- | ------------ | ---------------- | ---------------------------------------- |
| `GATE_PTP_OFFSET_ABS_NS`        | 50 000 ns    | ~1 000 ns        | PTP lock quality after settle            |
| `GATE_FOL_SELF_JITTER_NS`       | 200 000 ns   | ~70 000 ns       | `tfuture` tick-conversion end-to-end     |
| `GATE_SW_NTP_OFFSET_NS`         | 1 000 000 ns | ~200 000 ns      | SW-NTP app-layer round-trip + UDP jitter |
| `GATE_SW_NTP_MIN_SAMPLES`       | 5            | ~9 (at 1 Hz/8 s) | SW-NTP request/response actually works   |
| `GATE_SW_NTP_MIN_SUCCESS`       | 70 %         | 100 % steady     | Tolerates ARP-settling timeouts on boot  |
| `GATE_CYCLIC_MIN_CYCLES`        | 500          | ~6800 at 500 µs  | Catch "callback never fires"             |
| `GATE_CYCLIC_MAX_CYCLES`        | 15 000       | ~6800 at 500 µs  | Catch runaway firing                     |
| `GATE_CYCLIC_MAX_MISSES`        | 20           | 0                | Catch main-loop starvation               |

Both are intentionally loose — roughly 3× the typical value on reference
hardware. The smoke test is meant to flag _gross_ breakage (a factor-of-ten
regression), not subtle drift. Performance bounds live in the dedicated
tests. If you want tighter bounds for a specific investigation, edit the
constants at the top of `smoke_test.py`.

---

## 8. Refactoring Workflow

The test is designed as the per-commit gate for multi-step refactors. The
recommended flow:

1. **Before starting the refactor**: run the full smoke test on the current
   code. Commit the resulting log as a baseline (`smoke_baseline.log`).

2. **Per refactor commit** (e.g. extracting one module at a time):

   ```bash
   # edit sources, build firmware, flash both boards, then:
   python smoke_test.py --no-reset
   ```

   If red: revert the commit or fix the bug before proceeding. Never stack
   refactor commits on top of a failing smoke test.

3. **Before pushing the branch**: one full run without `--no-reset` to also
   validate the boot path.

This workflow is specifically prescribed for the `refactor/app-split`
branch described in `prompts/refactor_app_c_into_modules.md`, where `app.c`
is split into six modules — each extraction is one commit, and each commit
has to keep the smoke test green.

---

## 9. Reading a Failure

A failing check prints the actual response snippet so you can see _what_
the firmware said instead of the expected pattern:

```
  [FAIL] ptp_offset  — no match for /Offset:\s+([+-]?\d+)\s+ns\s+\(abs:\s+(\d+)\s+ns\)/;
                       got: 'ptp_offset\r\n\r\n>'
```

Common causes:

- **Command not registered** — the refactor extracted a handler but forgot
  to wire it into `Command_Init`. Check the corresponding `*_CLI_Register()`
  call.
- **Response format changed** — someone edited a `SYS_CONSOLE_PRINT`
  format string. Fix either the firmware or the regex in `smoke_test.py`.
- **Firmware crashed** — the response is empty or truncated. Check USB
  console output for an assert / fault.
- **PTP didn't lock** — Phase 1 fails at `FOL reaches PTP FINE`. Usually a
  cabling, IP, or driver-init issue rather than a CLI regression.

---

## 10. Limitations

- **Needs two boards wired up**: GM + FOL on two COM ports with a 10BASE-T1S
  link between them. No partial mode.
- **Does not cover `lan_write` / `ptp_reset` / `clk_set` / `ptp_mode`
  transitions** — see section 4 for why. If you are refactoring one of
  those code paths, run the dedicated test for it as well.
- **No hardware-probe validation** — the test trusts what the firmware
  reports over serial. A board that lies about its own state (e.g. prints
  `clk_get: 0 ns` but the clock is really running) would not be caught
  here. Use a logic analyzer or `tfuture_sync_test.py` with GPIO capture
  for ground-truth validation.
- **Gates are loose**. A 100× timing regression would still pass. For
  timing-sensitive changes, pair the smoke test with
  `tfuture_quick_check.py`.
