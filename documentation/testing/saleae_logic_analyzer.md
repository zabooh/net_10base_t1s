# Saleae Logic 2 — Setup and Test Scripts

This document covers the Saleae Logic 8 hardware-verification tooling in
the firmware directory: how to wire the probes, how to enable the
scripting socket, which Python package to install, and what each of the
six Saleae-related Python scripts actually does.

## Table of Contents

- [1. Hardware setup](#1-hardware-setup)
- [2. Software setup (one-time)](#2-software-setup-one-time)
- [3. Sanity check — is the toolchain working?](#3-sanity-check--is-the-toolchain-working)
- [4. Script overview](#4-script-overview)
  - [4.1 saleae_smoke.py](#41-saleae_smokepy--connectivity-check)
  - [4.2 saleae_poll.py](#42-saleae_pollpy--live-logic-level-monitor)
  - [4.3 saleae_capture_blink.py](#43-saleae_capture_blinkpy--wiring-verification-for-pd10)
  - [4.4 saleae_freq_check.py](#44-saleae_freq_checkpy--frequency--phase-characterisation)
  - [4.5 cyclic_fire_hw_test.py](#45-cyclic_fire_hw_testpy--ptp-sync-end-to-end-hardware-test)
  - [4.6 drift_filter_analysis.py](#46-drift_filter_analysispy--ptp-drift-iir-filter-diagnostic)
- [5. Typical workflows](#5-typical-workflows)
- [6. Output files and directory layout](#6-output-files-and-directory-layout)
- [7. Troubleshooting](#7-troubleshooting)

---

## 1. Hardware setup

All scripts target the **Saleae Logic 8** (8-channel, 100 MS/s digital
sampling).  The higher-end Logic Pro 16 also works but the defaults are
tuned for Logic 8.

### Standard wiring for cyclic_fire / PTP-sync tests

```
┌───────────────────────┐
│  Board 1 (e.g. GM)    │
│  SAME54 Curiosity     │
│  Ultra                │          ┌─────────────┐
│                       │          │             │
│  EXT1 pin 5 (PD10) ◄──┼──── Ch0 ─┤             │
│  EXT1 GND          ◄──┼──── GND ─┤  Saleae     │
│                       │          │  Logic 8    │
└───────────────────────┘          │             │
┌───────────────────────┐          │             │
│  Board 2 (e.g. FOL)   │          │             │
│                       │          │             │
│  EXT1 pin 5 (PD10) ◄──┼──── Ch1 ─┤             │
│  EXT1 GND          ◄──┼──── GND ─┤             │
│                       │          │             │
└───────────────────────┘          └─────────────┘
                                   USB → Host PC
```

- **PD10** = "GPIO1" position on the EXT1 Xplained-Pro header
  (physical pin 5), which is a 2.54 mm through-hole pin → directly
  scope-clippable.  The firmware's `cyclic_fire` and `blink`
  modules both drive this pin.
- **GND**: any EXT1 GND pin is fine; a single GND return per board is
  enough because all boards share the same Saleae ground via USB.
- Logic 8 uses a fixed 1.65 V threshold — 3.3 V logic from the SAME54
  is well above, no level-shift needed.

### COM-port convention

Throughout the scripts the default is:

| Role | COM | Saleae Channel |
|---|---|---|
| Grandmaster (GM) | `COM10` | Ch0 |
| Follower (FOL)   | `COM8`  | Ch1 |

All scripts accept `--gm-port` / `--fol-port` overrides.  If your
physical setup differs (e.g. different COM numbers after reboot), pass
the flags or edit the defaults at the top of each script.

---

## 2. Software setup (one-time)

### Logic 2 desktop application

1. Install Logic 2 from <https://www.saleae.com/downloads/> (Windows,
   Mac, Linux all supported).
2. Launch Logic 2 once manually — it must be running for the scripts
   to connect.
3. **Enable the scripting socket**:
   Options → Preferences → Developer →
   **"Enable scripting socket server"** → restart Logic 2.

The scripts all connect to `localhost:10430` (Logic 2's default gRPC
port).  If you changed the port, pass `--port` to the scripts that
support it.

### Python packages

```bash
pip install logic2-automation pyserial
```

- `logic2-automation`: Saleae's official Python client for the
  scripting socket.
- `pyserial`: needed by the scripts that also talk to the boards
  over UART (cyclic_fire_hw_test, drift_filter_analysis).

No virtual environment is required but recommended.  Python 3.8+.

---

## 3. Sanity check — is the toolchain working?

Before any real test, run:

```
python saleae_smoke.py
```

This is a 5-second dry-run: import the package, connect to Logic 2,
list devices, run a 200 ms dummy capture on Ch0+Ch1.  Exits non-zero
on any step's failure.  If this passes, the rest of the scripts will
work; if it fails, fix the environment first (see §7).

---

## 4. Script overview

All scripts live in `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/`.

### 4.1 `saleae_smoke.py` — connectivity check

**Purpose**: the 5-second pre-flight check described in §3.  Nothing
measured, just verifies the full `import → connect → enumerate →
capture → close` path works.

**Usage**:
```
python saleae_smoke.py
python saleae_smoke.py --duration 1.0 --sample-rate 25000000
python saleae_smoke.py --port 10430           # non-default gRPC port
```

**When to use**: after any Logic 2 install, update, or when another
script starts failing with unclear errors.

---

### 4.2 `saleae_poll.py` — live logic-level monitor

**Purpose**: poor-man's continuous logic probe.  Repeats short
captures in a loop and prints the last-sampled level on each watched
channel plus the number of transitions seen.

**Typical output** (one line per capture window):
```
Ch0=HIGH (0 tr)  Ch1=LOW (12 tr)  Ch2=HIGH (0 tr)
Ch0=HIGH (0 tr)  Ch1=HIGH (14 tr) Ch2=HIGH (0 tr)
...
```

**Usage**:
```
python saleae_poll.py                       # 100 ms windows, 1 MS/s, Ch0+Ch1
python saleae_poll.py --window-ms 50        # faster display
python saleae_poll.py --window-ms 500       # more transitions per update
python saleae_poll.py --channels 0 1 2 3    # watch four channels
python saleae_poll.py --once                # single reading, then exit
```

Ctrl+C to stop.  Effective update rate ~3 Hz because each iteration is
a full Logic-2 capture cycle (~200 ms overhead).

**When to use**: quick "is the signal alive?" check without committing
to a full capture.

---

### 4.3 `saleae_capture_blink.py` — wiring verification for PD10

**Purpose**: passive observation (no serial / no firmware CLI
interaction) of the PD10 pin to confirm wiring + firmware after a
fresh flash.  Expects the `pd10_blink` CLI module to be running on
both boards (see `readme.md` §5.13 and `blink` command).

**Typical output**:
```
Ch0  n_rising=5  n_falling=5  period=1.000 s  duty=50.0 %  -> TOGGLING
Ch1  n_rising=5  n_falling=5  period=1.000 s  duty=50.0 %  -> TOGGLING
```

A `STATIC` verdict means the probe sees no transitions in the capture
window — check wiring, ground, and that `blink` is enabled on the
board.

**Usage**:
```
python saleae_capture_blink.py              # 5 s capture, Ch0+Ch1
python saleae_capture_blink.py --duration 2.0 --sample-rate 1000000
```

**When to use**: immediately after flashing new firmware, before running
any PTP-dependent test, to be sure the probes and pins are OK.

---

### 4.4 `saleae_freq_check.py` — frequency + phase characterisation

**Purpose**: high-resolution measurement of a rectangular signal — its
actual frequency (ppm deviation from nominal), period jitter, high/low
phase distributions, duty-cycle deviation.  Single-channel by default.

Typically paired with `pd10_blink` or `cyclic_fire` running on a single
board to check its rate stability against the Saleae crystal.

**Headline numbers in the output**:
```
Period (rising-to-rising):
    mean          = 1.997647 ms
    median        = 1.997660 ms
    robust stdev  = 518.9 ns  (= 1.4826 × MAD)
    frequency     = 500.585685 Hz
    vs nominal    = 500.000000 Hz   deviation = +1171.371 ppm

High phase:  mean = 998.82 µs, robust stdev = 355.8 ns
Low phase:   mean = 998.83 µs, robust stdev = 355.8 ns
Duty cycle = 50.0005 %, deviation = +0.0005 pp
```

**Usage**:
```
python saleae_freq_check.py                        # 3 s @ 100 MS/s, Ch0
python saleae_freq_check.py --nominal-hz 1000      # explicit target
python saleae_freq_check.py --duration 10.0 --channels 0 1
```

**When to use**: measuring the per-board crystal drift, or verifying a
newly added rectangle pattern's frequency.  For **cross-board sync
quality** use `cyclic_fire_hw_test.py` instead — it understands pair
semantics.

Full details: [saleae_freq_characterization.md](saleae_freq_characterization.md).

---

### 4.5 `cyclic_fire_hw_test.py` — PTP-sync end-to-end hardware test

**Purpose**: the main automated verification of the cyclic_fire module
and by extension the PTP-sync quality.  Drives both boards over serial
through the complete sequence and analyses the resulting 2-channel
capture.

**Sequence** (all automated):

1. Reset both boards, set IPs, enable `ptp_mode follower` / `ptp_mode master`.
2. Wait for `PTP FINE` (typical: 2 s after boot).
3. Settle 30 s so the drift-IIR filter converges.
4. Start a 3 s Saleae capture on Ch0+Ch1 at 50 MS/s (20 ns edge resolution).
5. Read GM's `clk_get`, compute a shared anchor 2 s in the future.
6. Send `cyclic_start <period_us> <anchor_ns>` to both boards (or
   `cyclic_start_marker` with `--marker`).
7. Let the capture run; boards fire their first edge at the shared
   anchor moment.
8. Export capture CSV + `.sal` file.
9. Parse edges, compute per-channel period stats and cross-board rising-
   edge delta median, MAD, distribution, and linear drift rate across
   the capture window.
10. Emit five **verification-sample timestamps** the user can feed into
    Logic 2's cursor tool to cross-check the analysis by hand.

**`cyclic_fire` keeps toggling after the test ends** — no auto-`cyclic_stop`
is sent.  You can zoom in on the `.sal` capture or watch the live
signal in Logic 2.  Send `cyclic_stop` or `reset` on each console to
halt.

**Usage**:
```
python cyclic_fire_hw_test.py                     # full run, SQUARE pattern
python cyclic_fire_hw_test.py --marker            # 1-high + 4-low pulse pattern
python cyclic_fire_hw_test.py --no-reset          # skip boot/FINE
python cyclic_fire_hw_test.py --period-us 2000    # 500 Hz rectangle
python cyclic_fire_hw_test.py --sample-rate 25000000 --duration-s 5.0
python cyclic_fire_hw_test.py --compensate-offset # EXPERIMENTAL, currently broken
```

**Why `--no-compensate` is the default**: the `--compensate-offset` mode
tries to measure the FOL-vs-GM wallclock offset via back-to-back
`clk_get` bracketing and use it to correct the FOL anchor.  In practice
the USB-CDC serial round-trip jitter is ~ms-level while the real offset
is ~µs-level, so the compensation is dominated by noise and makes the
cross-board phase worse, not better.  Documented for completeness but
not recommended.

**Output files** (one per run): see §6.

**When to use**: whenever the PTP firmware, cyclic_fire or ptp_clock
are touched.  Also as the headline plot for a "PTP sync works" demo.

---

### 4.6 `drift_filter_analysis.py` — PTP drift-IIR filter diagnostic

**Purpose**: characterise the per-board PTP `drift_ppb` IIR-filter
output over 60 s of rapid `clk_get` polling, and compute the cross-board
rate residual from linear regression on `(FOL_wc − GM_wc_interp)` vs
PC time.  **No Saleae** — this script is serial-only, listed here
because it lives alongside the Saleae tools and answers the "is the
PTP-clock rate-sync tight enough for cyclic_fire to look good?"
question.

**Output per board**:
```
GM :
    n samples           : 44
    mean drift_ppb      :    +923357.0  (+923.357 ppm)
    stddev              :      36697.5 ppb (36.697 ppm)
    spread              : 138 ppm
    lag-1 autocorrelation: +0.976  (strongly correlated — filter state drifts slowly)
    first-half / last-half median : +942408 / +880946 ppb  (trend: -1045 ppb/s)
```

**Cross-board**:
```
Cross-board offset (FOL_wc - GM_wc_interp) vs PC time:
    drift rate (slope)  : +1221.4 ns/s  (+1.22 µs/s = +1.22 ppm)
    residual stddev     : 6231348 ns (6231.35 µs)
```

**Usage**:
```
python drift_filter_analysis.py                         # 60 s settle + 60 s sample
python drift_filter_analysis.py --settle-s 5 --sample-s 20   # quick version
python drift_filter_analysis.py --no-reset              # skip boot (needs PTP running)
```

**When to use**:
- After changing `DRIFT_IIR_N` in `ptp_clock.c` — verify stddev impact.
- After changing the anchor-tick capture path in `ptp_gm_task.c` or
  `drv_lan865x_api.c` — verify GM filter noise floor.
- When `cyclic_fire_hw_test.py` shows large delta drift in the 0.7 s
  capture window — this script tells you whether the problem is
  long-term rate residual (slope > 10 ppm) or short-term filter wander.

**Note on sampling rate**: effective rate is ~1.4 Hz combined (60 s
window → ~88 samples) because `send_command` waits 500 ms after the
last byte to detect response end.  That's plenty to detect drift
behaviour since the IIR filter only updates once per Sync (every 125 ms),
but it's noticeably less than the "20 Hz per board" mentioned in the
docstring.

---

## 5. Typical workflows

### First-time bring-up of a new board pair

1. Flash both boards with current firmware.
2. `python saleae_smoke.py` → Logic 2 socket OK?
3. `blink 1` on both boards via their consoles.
4. `python saleae_capture_blink.py` → wiring + Ch0/Ch1 mapping OK?
5. `blink 0` (stop blink) on both boards.
6. `python cyclic_fire_hw_test.py` → full PTP-sync end-to-end test.

### Regression after a firmware change

1. Flash both boards with new firmware.
2. `python cyclic_fire_hw_test.py` — compare `median`, `MAD`,
   `drift rate` against the previous run's numbers.
3. If drift rate regressed: `python drift_filter_analysis.py` →
   locate the cause (per-board filter stddev? cross-board slope?).

### Quick demo ("Boards are PTP-synced — look!")

1. Boards powered, PTP running (or `python cyclic_fire_hw_test.py
   --marker` does all of this).
2. Open Logic 2 → Start capture manually → zoom to the isolated rising
   edges → the two channels' edges are visually on top of each other.
3. For contrast, run `cyclic_start_free 1000` on both boards (no PTP)
   and watch the rectangles drift apart at ~1000 ppm crystal mismatch
   = ~1 ms/s relative phase drift.

---

## 6. Output files and directory layout

`cyclic_fire_hw_test.py` and `drift_filter_analysis.py` both create a
timestamped output directory under the current working directory:

```
cyclic_fire_hw_20260420_154857/
├── run_20260420_154857.log      — full text log of the run
├── digital.csv                  — raw Saleae edge data (Ch0 + Ch1)
├── capture_20260420_154857.sal  — Logic-2 session file (double-click to re-open in Logic 2)
└── deltas_rising_20260420_154857.csv  — per-edge-pair deltas (idx, t_gm_s, t_fol_s, delta_s)
```

```
drift_filter_20260420_151006/
├── run_20260420_151006.log       — full text log + per-board stats
└── drift_samples_20260420_151006.csv  — all clk_get samples
                                        (idx, board, pc_time_s, wc_ns, drift_ppb)
```

These outputs are intentionally not committed — they are ephemeral
measurement artefacts.  Keep the interesting ones by copying the
directory somewhere outside the repo.

`saleae_smoke.py`, `saleae_poll.py`, `saleae_capture_blink.py`, and
`saleae_freq_check.py` write their output to stdout only; the
`freq_check` run can be redirected via `--log-file` to a file.

---

## 7. Troubleshooting

### `ConnectionError: Failed to connect to localhost:10430`

- Is Logic 2 running?
- Did you enable the scripting socket (Options → Preferences →
  Developer)?  A Logic-2 restart is required after enabling.
- Is another process (older script, previous instance) holding the
  port?  Close all other Logic-2-automation connections first.

### `ImportError: No module named 'saleae'`

```
pip install logic2-automation
```

Beware: the older `saleae` package (without the `.automation`
submodule) is a different, unmaintained library.  We need
`logic2-automation` specifically.

### `InternalServerError: create_directories: Access is denied`

Happens in `cyclic_fire_hw_test.py` if Logic 2 gets a relative path for
the CSV export and can't write to its own CWD.  The script already
resolves to an absolute path; if you see this error, check that the
output directory is writable (usually just the current working
directory).

### PTP doesn't reach FINE (timeout after 60 s)

If you see `PTP FINE not reached in 60.1 s — aborting`, the boards may
be stuck in a state that a software `reset` can't clear.  Most common
after running `cyclic_fire` for many minutes.

**Fix**: hard power-cycle both boards (USB unplug → wait 3 s → USB
plug).  Then re-run the script without `--no-reset`.

### `cyclic_start failed  GM=False FOL=True` (or similar)

Usually means `PTP_CLOCK_IsValid()` is false on one side — PTP Sync
messages aren't flowing.  Cable disconnected, link down, or the MAC is
in a bad state.  Power-cycle fixes it.

### Empty capture / 0 edges in `cyclic_fire_hw_test.py`

The `cyclic_start` was issued after the capture already ended.  This
happened during the development of the `--compensate-offset` flag when
the offset measurement's serial round-trips ate more than the 3 s
capture window.  Current code does the offset measurement before
starting the capture, so this should not recur — but if you extend the
script, keep `capture_start → cyclic_start` latency well below
`duration_s`.

### Saleae automation calls seem very slow

Each `start_capture / wait / close` cycle has ~200-500 ms of overhead
beyond the nominal `duration_s`.  For timed single-shot captures (our
use case) this is fine.  For rapid-fire polling (like `saleae_poll.py`)
the effective update rate tops out at ~3 Hz on Logic 8.

---

## See also

- [saleae_freq_characterization.md](saleae_freq_characterization.md) — deep-dive into
  just the `saleae_freq_check.py` output format and measurement accuracy.
- [readme.md](../../readme.md) §5.12 — `cyclic_fire` module
  overview + bench measurement results.
- [implementation.md](../ptp/implementation.md) §§4.2, 4.6, 12 — PTP anchor-tick
  capture path, IIR drift filter, cross-board SW accuracy ceiling.
- `cyclic_fire_branch_state.md` (memory) — current state of the
  cyclic-fire branch and what the last measurement run looked like.
