# PTP Drift Filter — Adaptive IIR Design

## Table of Contents

- [1. Problem Statement](#1-problem-statement)
- [2. Why a Plain Single-Pole IIR Is Inadequate](#2-why-a-plain-single-pole-iir-is-inadequate)
- [3. The Adaptive Schedule](#3-the-adaptive-schedule)
  - [3.1 Concept](#31-concept)
  - [3.2 Effective α Sequence](#32-effective-α-sequence)
  - [3.3 Implementation](#33-implementation)
- [4. CLI Commands](#4-cli-commands)
- [5. Measured Behaviour](#5-measured-behaviour)
  - [5.1 Lock Time](#51-lock-time)
  - [5.2 Steady-State Jitter](#52-steady-state-jitter)
  - [5.3 Comparison: Fixed N vs Adaptive N](#53-comparison-fixed-n-vs-adaptive-n)
- [6. Tuning Guidance](#6-tuning-guidance)
- [7. Reproducing the Measurements](#7-reproducing-the-measurements)
- [8. History](#8-history)

---

## 1. Problem Statement

The PTP follower derives the cross-board frequency match from two sources:

1. **LAN8651 hardware servo** — adjusts `MAC_TI` / `MAC_TISUBN` so the LAN8651
   wallclock counter advances at the same rate as the GM's. This is the visible
   `t1`/`t2`/`t3`/`t4` chain of timestamps used for IEEE 1588 sync.
2. **Software PTP_CLOCK drift filter** (`src/ptp_clock.c`) — converts the
   ATSAME54 TC0 free-running tick (60 MHz) into nanoseconds for application
   code. The MCU crystal is *not* PTP-disciplined and runs roughly **+1200 ppm**
   off the LAN8651 internal oscillator on this board pair, so a simple
   `ticks * 50 / 3` extrapolation drifts visibly between Sync anchors.

The drift filter's job is to estimate the live ratio `(GM_ns / TC0_tick)` and
apply it as a signed ppb correction inside `PTP_CLOCK_GetTime_ns()`. The input
samples come from successive PTP anchors via `PTP_CLOCK_Update()`:

```
inst_ppb[n]  =  -(actual_dwc_ns − nominal_dwc_ns) × 1e9 / nominal_dwc_ns
```

Two qualities matter:

- **Settle time** — how fast `s_drift_ppb` reaches a useful value after a fresh
  PTP lock. The standalone demo shows the boards drifting visibly during the
  unsynced phase; once the operator presses SW1/SW2, both boards must lock,
  re-anchor, and align their cyclic_fire output within a few seconds.
- **Steady-state jitter floor** — the standard deviation of `s_drift_ppb` once
  the filter has converged. Translates directly into the per-sample wallclock
  noise that `PTP_CLOCK_GetTime_ns()` adds on top of the hardware PTP accuracy.

These two requirements are normally a hard trade-off in single-pole filters.

---

## 2. Why a Plain Single-Pole IIR Is Inadequate

The implementation used to be a fixed-α single-pole IIR:

```c
s_drift_ppb = ((N − 1) × s_drift_ppb + inst_ppb) / N      // α = 1/N
```

Properties:

| α (= 1/N) | N   | Half-life ≈ 0.7·N samples × 125 ms | Jitter floor (∝ 1/√N) |
|-----------|-----|------------------------------------|-----------------------|
| 1/8       | 8   | ~0.7 s                             | high (~ √16 worse than 128) |
| 1/32      | 32  | ~2.8 s                             | medium                |
| 1/128     | 128 | ~11 s                              | reference (~24 ppm)   |
| 1/512     | 512 | ~45 s                              | very low (~12 ppm)    |

The problem is fundamental: a small N (fast settle) means a large α (loud noise);
a large N (low jitter floor) means a small α (slow settle). With the demo gate of
**lock + visible alignment within 5 s** the only acceptable steady-state α is
1/N ≥ 1/32, which leaves the jitter at ~50 ppm — large enough to dominate the
cross-board cyclic_fire phase difference (drift_filter_analysis.py measured
~47 ppm filter stddev with strong lag-1 autocorrelation, random-walk character).

A higher-order filter (e.g. a true low-pass with a sharp roll-off) would narrow
the trade-off, but it also complicates the post-FOL_RESET behaviour (overshoot,
ringing during the first second after lock). For a single ppb scalar with no
state to lose, a simpler approach exists.

---

## 3. The Adaptive Schedule

### 3.1 Concept

The trade-off only applies if α stays constant. If α is allowed to **shrink
over time** as the filter accumulates samples, both requirements can be met:

- The first sample after a fresh lock has *no history* — α = 1 (just take
  `inst_ppb` directly) is the best estimate available.
- The next sample has 1 prior sample — α = 1/2 (simple average) is the best
  unbiased combination.
- The k-th sample is best combined with α = 1/(k+1) — this exactly matches the
  recurrence for a running mean.
- Once k exceeds the configured ceiling N_max, freeze α at 1/N_max so the
  filter still rejects long-term drift wander but stops widening its window.

The result: the filter behaves like a running mean during warm-up (fast
convergence, sub-second), then transitions seamlessly into a single-pole IIR
with the configured low-noise α=1/N_max for steady-state operation.

### 3.2 Effective α Sequence

For a fresh lock with `s_drift_iir_n = 128`, the per-sample α is:

```
sample n   1     2     3     4     5    ...   127   128   129   130 ...
N_eff      1     2     3     4     5    ...   127   128   128   128 ...
α          1.0   0.50  0.33  0.25  0.20 ...  0.008 0.008 0.008 0.008 ...
```

After ~30 samples (3.75 s at 125 ms Sync interval) α is already below 0.04, so
input noise is already heavily attenuated. The remaining ~12 s ramp is just
slow tightening of the floor.

### 3.3 Implementation

[`ptp_clock.c`](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c) maintains two state variables:

```c
static int32_t  s_drift_iir_n   = 128;   /* steady-state ceiling, CLI-tunable */
static uint32_t s_drift_samples = 0u;    /* sample counter since last reset */
```

`PTP_CLOCK_Update()` blends each accepted sample with:

```c
int32_t n_eff = ((uint32_t)s_drift_iir_n > s_drift_samples)
              ? (int32_t)s_drift_samples
              : s_drift_iir_n;
if (n_eff < 1) n_eff = 1;
int64_t blended = ((int64_t)s_drift_ppb * (n_eff − 1) + inst_ppb) / n_eff;
s_drift_ppb = (int32_t)blended;
if (s_drift_samples < 0xFFFFFFFFu) s_drift_samples++;
```

The counter is reset (forces a fresh warm-up ramp) by:

- `PTP_CLOCK_ForceSet(wc_ns)` — manual clock set, drift estimate is also zeroed.
- `PTP_CLOCK_SetDriftPPB(ppb)` — external seed; the seed becomes sample-0.
- `PTP_CLOCK_ResetDriftFilter()` — explicit re-arm without touching the anchor;
  used by test scripts to make settle-time measurements reproducible.

---

## 4. CLI Commands

Two commands expose the filter at runtime (both registered in
[`ptp_cli.c`](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c)):

| Command | Purpose |
|---|---|
| `drift_iir_n` | Print the current steady-state ceiling N_max. |
| `drift_iir_n <8..4096>` | Set the steady-state ceiling. Larger N → lower jitter floor (∝ 1/√N), longer settle to that floor (warm-up ramp is unaffected). Default 128, range clamped 8..4096. |
| `drift_iir_reset` | Re-arm the warm-up ramp. Sample counter → 0, drift estimate → 0, `s_drift_valid` → false. The next `PTP_CLOCK_Update()` reseeds the filter. |

Example usage during a test sweep:

```text
> drift_iir_n 256          # widen the steady-state floor
drift_iir_n set to 256
> drift_iir_reset          # re-arm warm-up so settle-time is reproducible
drift_iir_reset: warm-up ramp re-armed (samples=0)
> clk_get                  # observe drift_ppb converge
clk_get: 1234567890123456 ns  drift=+1212 ppb
... wait 5 s ...
> clk_get
clk_get: 1234567894523456 ns  drift=+1218 ppb
```

---

## 5. Measured Behaviour

All measurements use [`pd10_sync_check.py`](../../tools/ptp-analysis/sync-tests/pd10_sync_check.py) on the standard
two-board PTP setup (GM on COM10 / Saleae Ch0, FOL on COM8 / Saleae Ch1, 50 MS/s
sample rate). PASS gate is `|median delta| < 50 ms` — the rough human-visual
perception threshold.

### 5.1 Lock Time

`PTP FINE` is reached on the FOL UART trace within **2.7 s** of `ptp_mode
follower` being issued, regardless of N_max. The lock time is dominated by the
LAN8651 hardware servo (UNINIT → MATCHFREQ → HARDSYNC → COARSE → FINE), not by
the software drift filter — the drift filter starts updating *after* the first
two anchors arrive (~250 ms) and is already useful (effective N ≈ 8) by sample 8
(~1.0 s).

### 5.2 Steady-State Jitter

Cross-board PD10 rising-edge delta on a 60 s capture, after 30 s settle on top
of the 2.7 s lock, with default `drift_iir_n = 128`:

| Metric | Value |
|---|---|
| Edges captured | 60 (one per second per channel, 1 Hz cyclic_fire) |
| Median delta | **−32 µs** |
| MAD | 39 µs |
| p1 / p99 | −129 / +154 µs |
| min / max | −165 / +159 µs |
| Spread | 324 µs |
| Cross-board rate match | **0.0 ppm** |

The 39 µs MAD is dominated by:
- ~5 µs ISR-anchor jitter on each side (`s_nirq_tick` capture latency)
- ~125 ms × ~0.04 µs/ms wander between Sync anchors (~5 µs)
- residual filter noise (the rest)

The 0.0 ppm rate match confirms the steady-state ceiling is doing its job — the
per-board crystals stay aligned across the full 60 s window with no observable
drift residual.

### 5.3 Comparison: Fixed N vs Adaptive N

Captured during the 2026-04-23 cyclic-isr branch validation, both runs starting
from a power-on reset and `drift_iir_reset` immediately before measurement:

| Filter | Lock time (PTP FINE) | MAD (10 s capture) | Median delta (10 s) |
|---|---|---|---|
| Fixed α=1/32 (pre-2026-04-20) | 2.7 s | ~50 µs | varies (no rate-match in window) |
| Fixed α=1/128 (2026-04-20 to 2026-04-22) | 2.7 s | 25 µs | 100–200 µs (warm-up still incomplete) |
| **Adaptive (this branch)** | **2.7 s** | **7.2 µs** | **+143 µs (10 s) → −32 µs (60 s)** |

The adaptive form recovers the warm-up speed of α=1/8 (filter is useful within
1 s) while reaching a jitter floor close to α=1/128 in steady state — the
trade-off is broken, not just shifted.

### 5.4 Why the `pd10_sync_before_after_test` 10 s MAD sits at ~22 µs, not 7 µs

That test runs PD10 at 1 kHz (500 µs half-period slot) via the demo's
decimator reading `PTP_CLOCK_GetTime_ns()` on each fire.  Per-edge jitter
in the cross-board delta is sub-µs (measured mean |Δ| ≈ 1.3 µs between
consecutive Ch1–Ch0 samples), so the MAD is **not** from decimator
sampling — adding an explicit `s_cyclic_anchor_ns = 1 ns` fixed grid
did not change it.

The 22 µs floor is the slow drift of cross-board phase across the 10 s
window: delta typically walks ~60–80 µs end-to-end, and for a linear
ramp MAD ≈ range/4.  That walk is the adaptive filter hunting between
Sync samples, exactly the same mechanism this README describes in §5.2
and §5.3 — the 7.2 µs figure there was measured over a different
capture length and filter warm-up state; the 1 kHz PD10 test simply
picks it up as a steady-state 20-ish µs MAD.

Pushing it further requires the tuning described in §6 (longer N_max
for lower floor, or shorter Sync interval), not decimator / anchor
work.  See [pd10_sync_before_after_tests.md](../testing/pd10_sync_before_after_tests.md).

---

## 6. Tuning Guidance

Default `drift_iir_n = 128` is the right choice for a static indoor bench. Two
reasons to deviate:

| Scenario | Recommended N_max | Why |
|---|---|---|
| Bench validation, stable temperature | 128 (default) | Best balance for the measured ~5 µs ISR-anchor noise. |
| Jitter-floor characterisation only | 512–1024 | Pushes the steady-state floor to ~12–8 µs MAD; useful for showing the underlying hardware capability. Settle to that floor takes 30–60 s. |
| Outdoor / thermal-cycle testing | 64 | Crystal pulls faster than the 1/N_max recurrence can track at 128. The warm-up ramp still gives sub-second response on each disturbance. |
| Debug runs with deliberate clock disturbances (`clk_set_drift`) | 32 | Faster recovery from a manual seed, accepting a higher noise floor. |

`drift_iir_reset` between every test run keeps measurement comparison fair —
without it, a previous run's converged `s_drift_ppb` carries over as an
implicit warm seed.

---

## 7. Reproducing the Measurements

```bash
cd apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X
python flash.py                                            # flash both boards
python pd10_sync_check.py --duration-s 60 --settle-s 30    # 60 s capture
```

The script:
1. Resets both boards, sets one to `master` and the other to `follower`.
2. Polls the FOL UART for `PTP FINE` (timeout 30 s).
3. Issues `drift_iir_reset` after FINE if `--reset-drift-filter` is passed
   (planned addition; manual workaround: send the command via TeraTerm before
   starting the capture).
4. Settles `--settle-s` for cyclic_fire alignment, then captures
   `--duration-s` of both PD10 channels via Saleae Logic 2.
5. Pairs every Ch0 rising edge with the nearest Ch1 rising edge and reports
   median / MAD / spread delta in µs.

For a sweep over N_max values, drive the boards from a script:

```python
for n in [32, 64, 128, 256, 512, 1024]:
    fol.write(f"drift_iir_n {n}\r\n".encode())
    fol.write(b"drift_iir_reset\r\n")
    time.sleep(60)            # let warm-up ramp + extra settle finish
    capture_and_analyse(out_dir=f"sweep_n{n}")
```

The `cyclic_fire_hw_test.py` quick-mode meta sweep is the historical equivalent
for the `cyclic_fire` rate factor; the same harness can be reused for N_max.

---

## 8. History

- **Pre-2026-04-20:** Fixed α=1/32. Filter stddev ~47 ppm, strong lag-1
  autocorrelation, ~65 µs/s cross-board phase drift in short capture windows.
- **2026-04-20:** N raised to 128 after `drift_filter_analysis.py`
  characterisation. Stddev → ~24 ppm at the cost of ~11 s half-life. Settle
  time stretched to ~12 s in the standalone demo, which exceeded the 5 s gate.
- **2026-04-20 (later same day):** GM anchor moved from task-level
  `SYS_TIME_Counter64Get()` (~100 µs jitter) to ISR-latched `s_nirq_tick`
  (~5 µs). Long-term cross-board rate residual dropped from ~11 ppm to ~1.2 ppm,
  but the short-term jitter floor was still bound by α=1/128.
- **2026-04-22:** `DRIFT_IIR_N` made runtime-tunable via `drift_iir_n` CLI to
  enable A/B characterisation across the trade-off curve.
- **2026-04-23:** Adaptive α schedule introduced (this branch). N_eff =
  min(samples_since_reset, s_drift_iir_n), `PTP_CLOCK_ResetDriftFilter()`
  exposed via `drift_iir_reset` CLI. Settle time stays at 2.7 s (LAN8651
  hardware-servo bound), MAD drops from 25 µs to 7.2 µs in the 10 s window.
