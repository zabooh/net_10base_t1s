# tfuture — Coordinated Firing at an Absolute PTP_CLOCK Time

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. How it Works](#2-how-it-works)
  - [2.1 The idea in one paragraph](#21-the-idea-in-one-paragraph)
  - [2.2 Timing mechanism — hybrid precision](#22-timing-mechanism--hybrid-precision)
  - [2.3 wc_ns → TC0 tick conversion](#23-wc_ns--tc0-tick-conversion)
- [3. Module Structure](#3-module-structure)
- [4. CLI Commands](#4-cli-commands)
- [5. Firmware Flow](#5-firmware-flow)
  - [5.1 Arm](#51-arm)
  - [5.2 Service](#52-service)
  - [5.3 Ring buffer](#53-ring-buffer)
- [6. Test Script](#6-test-script)
- [7. Measured Results](#7-measured-results)
- [8. Bias Investigation (history of the two critical fixes)](#8-bias-investigation-history-of-the-two-critical-fixes)
- [9. Crystal-Deviation By-product](#9-crystal-deviation-by-product)
- [10. Limitations and Next Steps](#10-limitations-and-next-steps)

---

## 1. Purpose

`tfuture` (time-future) schedules a **single-shot firing event** at a specified
absolute point on the PTP_CLOCK timeline. When two HW-PTP-synchronised boards
arm the **same** target value, each fires when its own PTP_CLOCK reaches that
value — which, because HW-PTP keeps the two clocks aligned to ~50 ns, produces
two physical firings that occur within hundreds of nanoseconds of each other.

The module is the capstone of the time-sync chain documented in
[README_PTP.md](README_PTP.md) and [README_NTP.md](README_NTP.md):

```
 IEEE 1588 protocol           → ptp_trace_debug_test.py
        │
        ▼
 LAN865x HW timestamps        → ptp_offset_capture.py  (~50 ns at SFD)
        │
        ▼
 PTP_CLOCK anchor + TC0 interp → PTP_CLOCK_GetTime_ns()
        │
        ▼
 Application reading clock    → sw_ntp_vs_ptp_test.py  (~25 µs SW floor)
        │
        ▼
 Application ACTING on clock  → tfuture_sync_test.py   (this module)
```

Where SW-NTP only *observes* the clock, `tfuture` *acts* on it. It closes the
loop from "we have a synchronised clock" to "we can perform a coordinated
action at a specified moment." This is the end-user-visible payoff of the whole
PTP stack.

---

## 2. How it Works

### 2.1 The idea in one paragraph

Each board, independently, is told an absolute PTP_CLOCK value `T_ns`. The
module records the current `(wc_ns, tc0_tick)` pair, projects `T_ns` onto the
raw TC0 tick counter (with drift correction), then every main-loop iteration
checks "has the TC0 counter reached my target tick yet?" — fires once it does,
and logs the actual PTP_CLOCK-read at the firing moment. Since both boards use
the same `T_ns` and their PTP_CLOCKs are aligned, both fire nearly simultaneously.

### 2.2 Timing mechanism — hybrid precision

Two regimes in a single service routine:

1. **Coarse phase** (target is more than 1 ms away):
   `tfuture_service()` is called from the main loop every iteration, compares
   `SYS_TIME_Counter64Get()` against the stored `target_tick`, and returns
   immediately if there is still more than 60 000 ticks (1 ms at 60 MHz) to go.
2. **Tight spin** (target within 1 ms):
   Service enters a busy-wait on `SYS_TIME_Counter64Get()` until the exact
   target tick is reached, captures `PTP_CLOCK_GetTime_ns()` one instruction
   later, and transitions back to IDLE.

This gives near-tick-level precision (~17 ns resolution, typically <1 µs
jitter) without requiring direct TC-compare interrupt programming (registers,
NVIC, IRQ handler). The trade-off is that a single firing can block the main
loop for up to 1 ms; other services (PTP, SW-NTP, TCP/IP) are briefly paused.
Acceptable for a diagnostic module armed every few seconds; not suitable for
high-frequency continuous operation.

### 2.3 wc_ns → TC0 tick conversion

The PTP_CLOCK output is computed as:

```
wc_ns = anchor_wc_ns + ticks_to_ns_corrected(current_tick − anchor_tick, drift_ppb)
```

where `ticks_to_ns_corrected(t, d) = t × (50/3) × (1 − d/1e9)` — TC0 ticks at
60 MHz, scaled by the filtered drift estimate.

To arm at absolute `target_wc_ns`, we need the inverse:

```
delta_wc_ns  = target_wc_ns − current_wc_ns
base_ticks   = delta_wc_ns × 3 / 50                     (nominal)
adj_ticks    = base_ticks × drift_ppb / 1e9              (first-order correction)
target_tick  = current_tick + base_ticks + adj_ticks
```

The module reads `(current_wc_ns, current_tick)` atomically at arm time — close
enough in time that the difference between the two reads is negligible (~ns).
The projection is exact only at arm time; if the PTP servo later adjusts
`drift_ppb` or re-anchors `(anchor_wc_ns, anchor_tick)`, the fixed `target_tick`
drifts slightly from the original `target_wc_ns` (worst case ~5 µs over 5 s of
lead time at moderate drift change). This is below the module's own
firing-jitter floor, so ignoring it is acceptable for the MVP.

---

## 3. Module Structure

```
apps/tcpip_iperf_lan865x/firmware/src/
├── tfuture.h          # Public API + state enum + ring-buffer size +
│                      #   post-fire callback hook + spin-threshold setter
└── tfuture.c          # Arm/cancel, service, ring buffer, dump
```

Callback hook API (added in the `cyclic-fire` branch, general-purpose):

```c
typedef void (*tfuture_fire_cb_t)(uint64_t target_ns, uint64_t actual_ns);
void     tfuture_set_fire_callback(tfuture_fire_cb_t cb);
void     tfuture_set_spin_threshold_us(uint32_t us);
uint32_t tfuture_get_spin_threshold_us(void);
```

The callback runs inside `tfuture_service()` right after the spin-wait
exits, with state set to IDLE beforehand — so the callback may re-arm
via `tfuture_arm_at_ns()` to produce periodic firing.  The
`cyclic_fire` module is the first consumer and drops the spin threshold
from the 1 ms default to 100 µs on entry so other main-loop services
still get CPU between fires at sub-millisecond periods.

Integration in `app.c`:

| Location                 | Call                     |
|--------------------------|--------------------------|
| `APP_Initialize()`       | `tfuture_init()`         |
| `APP_Tasks()` STATE_IDLE | `tfuture_service()`      |

`tfuture_service()` is called on every main-loop iteration, not once per ms.
This is essential so the tight-spin phase starts as soon as the target is
within the 1-ms window.

Build integration (add to `user.cmake`):

```cmake
target_sources(... PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/tfuture.c"
)
```

---

## 4. CLI Commands

| Command                | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| `tfuture_at <abs_ns>`  | Arm firing at absolute PTP_CLOCK nanosecond value.                          |
| `tfuture_in <ms>`      | Convenience: arm at current PTP_CLOCK + `<ms>` milliseconds.                |
| `tfuture_cancel`       | Cancel a pending firing. No effect if IDLE/FIRED.                           |
| `tfuture_status`       | Show state, total fires, last target_ns, last actual_ns, last delta.        |
| `tfuture_reset`        | Clear the ring buffer (does not affect any pending arm).                    |
| `tfuture_dump`         | Dump all recorded fires, one line per record: `<target_ns> <actual_ns> <delta_ns>`. |

Arming fails (and prints `tfuture_at FAIL`) when PTP_CLOCK is not yet valid,
the target is in the past, or another firing is already pending.

Dump format:

```
tfuture_dump: start count=20 overwrites=0 capacity=256
1234567890123456 1234567890123488 +32
1234567892123456 1234567892123620 +164
...
tfuture_dump: end
```

The third column (`delta = actual − target`) is redundant with the first two
but is included so Python parsers can read it directly without computation.

---

## 5. Firmware Flow

### 5.1 Arm

```c
bool tfuture_arm_at_ns(uint64_t target_wc_ns)
{
    if (state == PENDING)        return false;  // must cancel first
    if (!PTP_CLOCK_IsValid())    return false;  // need live clock

    uint64_t now_wc   = PTP_CLOCK_GetTime_ns();
    uint64_t now_tick = SYS_TIME_Counter64Get();
    if (target_wc_ns <= now_wc)  return false;  // must be future

    delta_wc    = target_wc_ns − now_wc;
    base_ticks  = delta_wc × 3 / 50;                 // exact integer 50/3
    adj         = base_ticks × drift_ppb / 1e9;      // first-order drift
    target_tick = now_tick + base_ticks + adj;

    state       = PENDING;
    return true;
}
```

### 5.2 Service

```c
void tfuture_service(void)
{
    if (state != PENDING)  return;

    int64_t ticks_left = (int64_t)(target_tick − SYS_TIME_Counter64Get());
    if (ticks_left > 60000)  return;                // >1 ms, come back later

    // Within 1 ms — tight spin
    while ((int64_t)(target_tick − SYS_TIME_Counter64Get()) > 0) { /* spin */ }

    // Fire!
    actual_ns = PTP_CLOCK_GetTime_ns();
    trace_record(target_ns, actual_ns);
    fires++;
    state = IDLE;
}
```

### 5.3 Ring buffer

- **256 entries** × 16 bytes (uint64 target + uint64 actual) = 4 KB.
- Overflow wraps; the `overwrites` counter in the dump header flags wrap-around.
- Dump is rate-limited (4 lines per 20 ms pause) to avoid `SYS_CONSOLE_PRINT`
  overruns; the full 256-entry dump completes in ~1.3 s.
- **No UART activity during measurement.** The ring buffer accumulates silently
  in RAM; all UART I/O happens via `tfuture_reset` (before) or `tfuture_dump`
  (after). Firing-jitter measurement is not distorted by UART serialization.

---

## 6. Test Scripts

The module ships with five Python drivers, each for a different purpose.
All share the common helpers (`Logger`, `open_port`, `send_command`,
`wait_for_pattern`) imported from `ptp_drift_compensate_test.py`, so they
must live in the same directory.

| Script | Purpose | Typical duration |
|---|---|---:|
| `tfuture_sync_test.py` | Baseline regression (20 rounds × 2 s lead), full setup | ~2 min |
| `tfuture_quick_check.py` | **Fast iteration tool** (10 rounds, `--no-reset` available); also reports the 4-crystal deviation analysis | ~40 s / ~25 s |
| `tfuture_diagnose_test.py` | 4-phase lead-ms scan (1 / 2 / 4 s) + drift-toggle.  Used to separate proportional from fixed bias contributions. | ~4 min |
| `tfuture_anchor_delay_test.py` | Sweeps the GM anchor_wc offset through 0..12 ms. Historical tool used to reject Hypothesis 1 during the bias investigation. | ~3 min |
| `tfuture_drift_forced_test.py` | Sweeps **GM** `drift_ppb` manually via `clk_set_drift` CLI. Used to confirm Hypothesis 2. | ~4 min |
| `tfuture_drift_forced_fol_test.py` | Same, but targeting **FOL** `drift_ppb`. Documents that manual drift-forcing does not stick on FOL because PTP continuously re-converges. | ~5 min |

### 6.1 Primary test — `tfuture_sync_test.py`

Drives a dual-board coordinated-firing experiment:

```
python tfuture_sync_test.py --gm-port COM8 --fol-port COM10
python tfuture_sync_test.py --rounds 50 --lead-ms 2000 --csv fires.csv
```

Flow:

1. Reset both boards, configure IPs.
2. Enable HW-PTP, wait for FOL `PTP FINE`.
3. Settle for `--settle-s` seconds (default 5 s).
4. For `--rounds` iterations (default 20, max 256):
   - Query GM's current PTP_CLOCK via `clk_get`
   - Compute `target_ns = gm_now + --lead-ms × 1 000 000` (default 2 s)
   - Send `tfuture_at <target_ns>` to **both** boards
   - Wait `lead_ms + 200 ms` for both to fire
5. Dump both ring buffers via `tfuture_dump`.
6. Join records by `target_ns` (identical across boards by construction).
7. Compute three metric series per round:
   - `self_jitter_GM  = actual_GM  − target` — module precision on GM
   - `self_jitter_FOL = actual_FOL − target` — module precision on FOL
   - `inter_board     = actual_GM  − actual_FOL` — physical firing coincidence
8. Print robust (median/MAD/p05..p95) and classical (mean/stdev) statistics.

The `inter_board` series is the headline number: it measures how closely two
boards physically fire when given the same future target, and is bounded from
below by HW-PTP inter-board clock alignment (~50 ns) plus the sum of both
boards' self-jitter.

### 6.2 Fast-iteration tool — `tfuture_quick_check.py`

Single-phase, fewer rounds, with a one-line PASS/FAIL verdict and the
crystal-deviation side-analysis (see §9).

```
python tfuture_quick_check.py                        # full run, ~40 s
python tfuture_quick_check.py --no-reset             # skip setup, ~25 s
python tfuture_quick_check.py --rounds 5 --no-reset  # even faster (~15 s)
```

Use this for rapid iteration after any change to `PTP_CLOCK_Update`,
`PTP_FOL_task`, `ptp_gm_task`, or the `tfuture` module itself.

### Logic of target pairing

The CLI round-trip from Python to each board takes ~10–50 ms, and the two arms
are sequential. As long as `--lead-ms` exceeds that round-trip with margin
(default 2000 ms is generous), both arms complete well before the target, and
the test is insensitive to CLI timing jitter. Reducing lead-ms below ~500 ms
risks arming one board after the target has already passed.

Optional CSV output columns:

```
round, target_ns, actual_gm_ns, actual_fol_ns,
       self_jitter_gm, self_jitter_fol, inter_board
```

---

## 7. Measured Results

Measured on the current firmware (after both bias fixes, see §8)
on ATSAME54P20A + LAN865x at HW-PTP FINE.  Representative of the
expected behaviour on similar hardware.

### 7.1 Typical single-run

Single `tfuture_quick_check.py --no-reset` run, 10 rounds, lead=2 s:

| Metric                    | Median   | Robust stdev |
|---------------------------|---------:|-------------:|
| `self_jitter_GM`          | +1.8 µs  | 54 µs        |
| `self_jitter_FOL`         | +3.1 µs  | 45 µs        |
| `inter_board`             | +28 µs   | 100 µs       |

### 7.2 Lead-scan behaviour (from `tfuture_diagnose_test.py`)

| Phase | lead_ms | self_GM | self_FOL | inter |
|-------|--------:|--------:|---------:|------:|
| B     | 2 000   | +8 µs   | +2 µs    | +8 µs |
| C     | 4 000   | -6 µs   | +15 µs   | +4 µs |

No scaling with lead_ms; both boards remain within ±20 µs of their
declared target.  `drift_ppb` values converge to ~+1 000 000 to +1 200 000
ppb on both sides after a few seconds of PTP FINE — reflecting the real
TC0-vs-LAN865x crystal mismatch on this hardware.

### 7.3 Interpretation

- `self_jitter` floor (tens of µs): dominated by PI-servo disturbances
  that happen mid-lead.  The TC0 tick quantisation itself is ~17 ns; the
  remaining tens-of-µs noise comes from the servo re-anchoring PTP_CLOCK
  every 125 ms while tfuture's `target_tick` is fixed at arm time.
- `inter_board` floor (~30 µs typical, ~100 µs stdev): how closely two
  boards physically fire when given the same target.  Set by the sum of
  each board's self-jitter plus any remaining HW-PTP sync imperfection.
- No systematic bias: mean and median are within 2x of each other for
  all three metrics, meaning the distributions are symmetric and
  outlier-free in typical runs.

Occasional ~1 ms outliers can occur when a tfuture fire coincides with
a PTP Sync frame burst or SPI bus contention.  Use the robust statistics
(MAD-based) to characterise typical behaviour; classical mean/stdev are
reported alongside for cross-validation.

---

## 8. Bias Investigation (history of the two critical fixes)

First hardware runs of `tfuture_sync_test.py` revealed a **~1.3 ms
systematic self-jitter bias** on both boards at lead=2 s, scaling with
lead_ms.  Hypothesis: tfuture's `compute_target_tick()` was using a
`drift_ppb` value that did not match the real TC0 rate.

Investigation through four diagnostic scripts (retained as regression
probes, see §6 table) narrowed the root cause to the PTP_CLOCK drift
filter.  Two independent bugs had to be fixed:

### 8.1 Primary fix — `DRIFT_SANITY_PPB_ABS` widened (commit d18a102)

The sanity clamp on the per-sample `inst_ppb` estimator in
`src/ptp_clock.c` was set to ±200 000 ppb (±200 ppm).  Every single
sample on GM exceeded this because the measured rate ratio between
LAN8651 TSU and SAME54 TC0 on this board pair is ~**1 200 ppm** —
well above the clamp.  Consequence: filter silently rejected every
sample, `drift_ppb` stayed at 0, tfuture's target_tick uncompensated
for the ~1 200 ppm rate mismatch, firing happened ~0.6 ms early per
1 s of lead time on GM.

Fix: widened clamp to ±5 000 000 ppb (±5 000 ppm), which comfortably
covers any realistic pair of cheap commodity crystals while still
rejecting obviously-bad outliers from anchor-update glitches.

Verification: GM self_jitter dropped from −1.6 ms to −34 µs median (47×
improvement).

### 8.2 Secondary fix — remove `rateRatio` override on FOL (commit 47c7ca5)

After the primary fix GM was clean but FOL still showed a lead-scaling
bias: −633 µs at lead=2 s, −2.8 ms at lead=4 s.  Follow-up diagnosis
located a second distinct bug in `processFollowUp()`:

```c
/* REMOVED in 47c7ca5 */
PTP_CLOCK_SetDriftPPB((int32_t)((rateRatioFIR - 1.0) * 1e9));
```

This line was called every Sync in FINE state and **overwrote** the
IIR filter's `drift_ppb` with a value derived from `rateRatio`
(= `t1 / t2` ratio = `GM_LAN_rate / FOL_LAN_rate`).  After PI-servo
convergence, FOL_LAN tracks GM_LAN exactly → rateRatio → 1.0 → forced
drift_ppb → 0.  The IIR filter's correct estimate of the anchor_wc vs.
anchor_tick rate was continuously clobbered.

Fix: removed that line (kept as a commented-out block with rationale
for future readers).  The `rateRatio` value is still correctly used at
line ~1076 for CLOCK_INCREMENT calibration — that usage is the right
one.

Verification: FOL self_jitter at lead=2 s dropped from −633 µs to +1.8 µs
(350× improvement); at lead=4 s from −2801 µs to +15 µs (186× improvement).

### 8.3 Diagnostic tooling kept

Three CLI knobs were added during this investigation and are retained
as regression aids:

- `tfuture_drift [on|off]` — disable drift correction in
  `compute_target_tick` to localise the blame between the formula and
  the filter value.
- `ptp_gm_delay [<ns>]` — add a signed ns offset to GM's `anchor_wc`
  at `PTP_CLOCK_Update` call.  Used to reject the "6-ms anchor gap"
  hypothesis.
- `clk_set_drift [<ppb>]` — manually force `PTP_CLOCK drift_ppb`.
  Sticks on GM when the filter isn't converging; does NOT stick on
  FOL because PTP continuously re-estimates.  The FOL behaviour was
  itself a diagnostic hint that led to the secondary fix.

None of these are used in the default path; they are available via
the serial CLI for future debugging.

---

## 9. Crystal-Deviation By-product

As a side-effect of the two drift_ppb readings + the live CLOCK_INCREMENT
register contents, `tfuture_quick_check.py` derives the ppm deviation of
all **four** crystals in the system relative to GM_LAN8651 as the
nominated reference.  This comes for free — no extra measurements are
taken.

### 9.1 Hardware setup

```
GM:                              FOL:
  SAME54 XTAL → PLL → TC0          SAME54 XTAL → PLL → TC0 (60 MHz)
  LAN8651 XTAL → internal          LAN8651 XTAL → internal
                ↓ CLOCK_INCREMENT                ↓ CLOCK_INCREMENT (PI-regulated)
              TSU_GM                           TSU_FOL ≈ TSU_GM
```

Four independent crystals, two different technologies.  After PTP-PI
convergence, FOL's LAN8651 TSU rate is regulated (via CLOCK_INCREMENT)
to match GM's LAN8651 TSU rate.  This rate-matching gives us the
essential information needed to derive all four deviations from two
ratio measurements.

### 9.2 Derivation

With GM_LAN8651 chosen as reference (δ = 0 ppm):

```
δ_GM_SAME54  = −drift_ppb_GM    (anchor_wc rate vs anchor_tick rate)
δ_FOL_SAME54 = −drift_ppb_FOL   (same formula, PI makes FOL_TSU = GM_TSU)
δ_FOL_LAN8651 ≈ CLOCK_INC_GM / CLOCK_INC_FOL − 1
             (because PI sets CLOCK_INC_FOL to cancel FOL_LAN's rate error)
```

The last line reads the `MAC_TI + MAC_TISUBN` registers on both boards
via the existing `lan_read` CLI, decodes the byte-swapped fractional
format, and takes the ratio.  No firmware changes, no new CLI.

### 9.3 Example output on this board pair

```
Crystal deviations  (reference: GM_LAN8651 = 0 ppm)
  GM  LAN8651 :        0 ppm   (reference)
  GM  SAME54  :  −1028 ppm     (from GM drift_ppb)
  FOL SAME54  :  −1012 ppm     (PI makes FOL_TSU = GM_TSU)
  FOL LAN8651 :     −5 ppm     (from live CLOCK_INCREMENT ratio)

CLOCK_INCREMENT raw registers:
  GM : TI=40  TISUBN=0x00000000
  FOL: TI=40  TISUBN=0x3800000D
```

Interpretation for this specific board pair:
- GM's SAME54 crystal is ~1 000 ppm slower than GM's LAN8651 crystal.
  That's the primary "large" mismatch and the reason the original
  ±200 ppm clamp rejected every sample.
- FOL's SAME54 crystal is ~16 ppm apart from GM's SAME54 — plausible
  board-to-board manufacturing variance.
- FOL's LAN8651 crystal is only 5 ppm apart from GM's LAN8651 — two
  crystals of the same type and (likely) same lot.

### 9.4 Precision

- `δ_FOL_LAN8651` from the register ratio is **stable to <0.1 ppm**
  across runs (pure register read, no filter).
- `δ_*_SAME54` from `drift_ppb` readings has run-to-run variance of
  ~100–200 ppm because the IIR filter is α = 1/32 and samples have
  non-negligible noise from the ~6 ms anchor-tick capture jitter.
  Good enough for "which board has which mismatch" but not for
  calibration-grade numbers.

---

## 10. Limitations and Next Steps

### Current constraints

- **Single pending arm** at a time. No queue; `tfuture_at` fails if state is
  `PENDING`. Sufficient for a diagnostic; trivially extensible if needed.
- **Main-loop blocking during spin** up to 1 ms. Other services (PTP, SW-NTP,
  TCP/IP) pause briefly at the firing moment. Harmless for a one-shot demo;
  problematic if you arm at a high rate.
- **No GPIO output.** The firing is purely a software event plus a ring-buffer
  record. You cannot hang an oscilloscope on an output pin without adding code.
- **No cross-board capture.** The "physical coincidence" number comes from each
  board self-reporting its firing time — it assumes the PTP_CLOCKs are accurate.
  This is a reasonable assumption after the PTP tests (§5.9 of top-level
  readme) validated the clock, but independent verification would require an
  EIC-based capture pin.
- **Arm-time projection only.** `target_tick` is frozen at arm time; later PTP
  anchor adjustments are not re-applied.  Measured tens-of-µs of the self_jitter
  in §7.1 comes from this; it is bounded and does not scale with lead_ms once
  both bias fixes (§8) are in place.
- **SAME54-vs-LAN8651 `drift_ppb` filter precision** is bounded by the IIR
  time constant (α = 1/32).  Run-to-run variance of ~100–200 ppm is normal
  and propagates into the §9 crystal-deviation readings.  Good enough for
  "which board has which mismatch" but not for calibration-grade numbers.
- **Scale-invariant ~1.7× rate factor in the callback-driven cyclic path.**
  When the post-fire callback re-arms `tfuture` for a periodic schedule
  (see `cyclic_fire`), the observed firing rate is ~1.7× higher than the
  configured period predicts (6800 cycles observed vs 4000 nominal at
  500 µs / 2 s, same ratio at 2000 µs).  Ruled out: main-loop starvation
  (misses == 0), TC0 frequency (verified 60 MHz from GCLK config),
  hardcoded tick conversion (now uses `SYS_TIME_FrequencyGet()` at
  runtime).  The single-shot tests in §7 are unaffected because they arm
  once with large leads — the factor only surfaces at sub-millisecond
  periodic arming.  Filed as Ticket 7 in
  [prompts/codebase_cleanup_followups.md](../../../../prompts/codebase_cleanup_followups.md).

### Natural extensions

1. **GPIO output** — **implemented**: the `cyclic_fire` module uses the
   new post-fire callback hook to toggle `PB22` on every callback.  See
   [README_modules.md → cyclic_fire](../../src/README_modules.md#cyclic_fire).
2. **EIC capture pin** on the other board — independent verification of firing
   coincidence without relying on PTP_CLOCK self-reports. ~100 lines + MCC
   config change.
3. **Periodic firing** — **implemented**: `cyclic_fire` produces a
   periodic GPIO rectangle synchronously across GM and FOL at a
   configurable `period_us`.  Subject to the 1.7× rate caveat above.
4. **TC compare interrupt** instead of spin — eliminates the 1 ms main-loop
   blocking. Gains ~zero precision but scales to continuous operation.
5. **Dedicated `ptp_clock_inc` CLI** returning `TI + TISUBN` in a single
   reply.  Current §9 analysis uses two sequential `lan_read` calls, which
   works fine; a single-call CLI would simplify the parsing and tighten
   the atomicity window.
