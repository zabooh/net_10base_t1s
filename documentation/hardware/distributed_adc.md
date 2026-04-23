# Distributed ADC Sampling Bandwidth — What Can the Sync Quality Capture?

## Table of Contents

- [1. Problem Statement](#1-problem-statement)
- [2. The Underlying Math](#2-the-underlying-math)
- [3. Bandwidth vs Acceptable Amplitude Error](#3-bandwidth-vs-acceptable-amplitude-error)
- [4. Bandwidth by Application Class](#4-bandwidth-by-application-class)
- [5. Caveats](#5-caveats)
  - [5.1 Nyquist Is a Separate Constraint](#51-nyquist-is-a-separate-constraint)
  - [5.2 Constant Offset Is Calibratable](#52-constant-offset-is-calibratable)
  - [5.3 Capture-Window Length Affects MAD](#53-capture-window-length-affects-mad)
  - [5.4 Where the Trigger-Jitter Comes From](#54-where-the-trigger-jitter-comes-from)
- [6. How to Push the Bandwidth Higher](#6-how-to-push-the-bandwidth-higher)
- [7. Practical Recipe](#7-practical-recipe)
- [8. References](#8-references)

---

## 1. Problem Statement

The PTP setup on this project synchronises two SAME54 + LAN8651 boards over
10BASE-T1S. The cross-board synchronisation quality, measured at the GPIO
output level (`PD10` toggles via the `cyclic_fire` module), reaches:

- **Sample rate per board: 8 kHz** — `cyclic_fire` configured with
  `cyclic_period_us = 250` (= `demo_cyclic_period 250` via CLI, or
  `--cyclic-period-us 250` in the bench test).  `fire_callback` runs
  every `period/2 = 125 µs` → 8 000 samples/s per board.  This is the
  highest sustained fire rate that still keeps the PTP servo stable;
  see [README_pd10_sync_before_after.md](README_pd10_sync_before_after.md)
  for the sweep across 100 / 200 / 250 / 500 / 1000 µs periods.
- **Steady-state Median Absolute Deviation (MAD): ~18 µs** on the
  cross-board rising-edge delta after detrending the linear drift
  component, measured over a 10 s capture at 1 kHz PD10 rate with the
  8 kHz fire rate.  Reproducible across runs (18.42 µs and 18.82 µs in
  two independent bench measurements — see run
  `pd10_sync_before_after_20260423_221820`).

The natural follow-up question: **if a software callback on each board
triggered an ADC conversion at each `fire_callback` (8 kHz on each side)
instead of toggling a GPIO, what is the highest signal frequency that can
be reliably sampled across both boards for correlation, FFT, beamforming,
phase analysis, or sensor fusion?**

This document derives the answer from the timing-jitter floor and gives a
practical mapping from timing accuracy to signal bandwidth.

---

## 2. The Underlying Math

When two boards both attempt to sample a signal `V(t)` at the same nominal
time `t_target`, but their actual sample instants differ by `Δt`, the captured
voltage values differ by an amount proportional to the signal's slew rate at
that moment:

```
ΔV  =  V(t_target + Δt)  −  V(t_target)
    ≈  (dV/dt) × Δt          (first-order Taylor expansion, valid for small Δt)
```

For a single-tone sine wave `V(t) = A · sin(2πf·t)`:

```
dV/dt              =  2π·f·A · cos(2πf·t)
max |dV/dt|        =  2π·f·A
relative error     =  ΔV / A   =  2π·f · Δt
```

So **the maximum amplitude error scales linearly with frequency × timing
jitter**. With Δt = 18 µs (the measured cross-board MAD at 8 kHz sample
rate), this gives the practical "bandwidth ceiling" for any given
amplitude-error tolerance.

---

## 3. Bandwidth vs Acceptable Amplitude Error

Two independent limits apply simultaneously:

1. **Cross-board-alignment limit** — from `2π·f·Δt < ε`, with Δt = 18 µs.
   This says *"how closely can the two boards' samples agree on the
   captured amplitude at each instant?"*
2. **Per-board Nyquist limit** — from `f < f_sample/2 = 8 kHz / 2 = 4 kHz`.
   Each board's own ADC train must still obey Nyquist, independent of
   cross-board quality.

The practical ceiling is whichever one bites first:

| Acceptable ΔV/A | f from alignment (Δt = 18 µs) | Binding limit (min with 4 kHz Nyquist) | Use case implication |
|---|---|---|---|
| 0.01 % | ~0.9 Hz       | **0.9 Hz** (alignment) | Reference comparison (rare) |
| 0.1 %  | ~8.8 Hz       | **8.8 Hz** (alignment) | Sub-Hz drift / DC monitoring |
| 1 %    | **~88 Hz**    | **88 Hz**  (alignment) | Power-quality fundamental (50/60 Hz at 0.6 % error) |
| 5 %    | ~440 Hz       | **440 Hz** (alignment) | Vibration low-frequency band |
| 10 %   | **~885 Hz**   | **885 Hz** (alignment) | Audio bass, mechanical resonance |
| 25 %   | ~2.2 kHz      | **2.2 kHz** (alignment) | Coarse correlation, trigger-only sampling |
| 45 %   | **~4 kHz**    | **4 kHz**  (Nyquist)   | Both limits meet — practical ceiling |
| 50 %+  | ~4.4 kHz      | **4 kHz**  (Nyquist)   | Nyquist binds — can't sample higher |

> **Reading the table:** Pick the row matching how much amplitude error you
> can tolerate in the cross-board comparison; that row's `f` is your highest
> usable signal frequency.  At 8 kHz sample rate the per-board Nyquist
> kicks in at 4 kHz — beyond that, **you can't sample anyway** regardless
> of cross-board quality.  Inside the [0, 4 kHz] window, alignment
> dominates: from ~0 – 4 kHz the cross-board amplitude error ramps
> linearly from 0 % to ~45 % as the signal frequency approaches Nyquist.

For comparison, the formula `f < 0.5 / (2π · Δt)` gives the −4 dB point
(50 % amplitude error) at ~4.4 kHz — coincidentally just above Nyquist.
A stricter "well-correlated" engineering rule of thumb `Δt < period/10`
translates to `f < 1/(10·Δt) ≈ 5.5 kHz`, also above Nyquist, so at the
8 kHz sample rate **Nyquist is the true ceiling** for any tolerance worse
than ~45 %.

---

## 4. Bandwidth by Application Class

Mapping the table above onto common distributed-sensing applications:

| Application | Signal of interest | Tolerance | Achievable at 8 kHz sample rate, 18 µs MAD? |
|---|---|---|---|
| **Power monitoring (50/60 Hz)** | Fundamental + harmonics to 4 kHz (Nyquist) | 1 % on fundamental, ≤ 45 % on harmonics | **Fundamental: yes** (0.6 % error at 50 Hz); harmonics up to ~900 Hz at 10 %; higher harmonics only as "present/absent" markers |
| **Slow vibration / building monitoring** | < 100 Hz | 1 % | **Yes** comfortably (88 Hz ceiling at 1 %) |
| **Industrial vibration / bearing analysis** | 100 Hz – 4 kHz | 5–10 % | **Yes up to ~1 kHz** at 10 % tolerance; 1 – 4 kHz only at ≥25 % tolerance (coarse correlation) |
| **Audio correlation / acoustic localisation** | 20 Hz – 20 kHz | 1° phase ≈ 0.017 amplitude | **No** — need < 1 µs sync AND 44+ kHz sample rate; this setup is two orders of magnitude short |
| **Phase-precise voltage/current measurement** | 50/60 Hz, 1° phase | 0.017 | **Borderline** — Δt = 18 µs ≈ 0.32° phase at 50 Hz, 0.39° at 60 Hz |
| **Beamforming / array processing** | < 4 kHz, ≤ 10 % per-channel error | typically < 10 % | **~900 Hz ceiling** (alignment-limited) |
| **Sub-µs distributed time-of-flight** | Pulses / event timing | absolute timing | **No** — not the right tool, see §6 |

Rule of thumb: this synchronisation quality is comfortable for **anything
below ~885 Hz** at 10 % tolerance, or **~88 Hz at 1 % tolerance**.  Above
~2 kHz the error exceeds 25 %; the 4 kHz Nyquist ceiling puts a hard cap
regardless.  It is **not suitable for audio or precision phase measurement
above ~100 Hz** — both the alignment and the sample rate are insufficient.

---

## 5. Caveats

### 5.1 Nyquist Is a Hard Ceiling at This Sample Rate

The bandwidth limit derived above is primarily about **time-alignment**
between the two boards' sample instants.  However, because the
cyclic_fire-driven sample rate is 8 kHz, the **Nyquist ceiling of
4 kHz** is already comparable to the alignment ceiling (~4.4 kHz at
50 % amplitude error) — so Nyquist binds first for any tolerance looser
than ~45 %.

The SAME54 ADC hardware itself handles up to ~1 MS/s, so raising the
fire rate and the sample rate in lock-step would relax Nyquist —
but the fire rate itself tops out at 8 kHz on this firmware due to PTP
processing load (see [README_pd10_sync_before_after.md](README_pd10_sync_before_after.md)
§ "Effect of `--cyclic-period-us`").  The only way above 8 kHz sample
rate is to decouple the ADC trigger from the fire_callback — e.g. use
the fire_callback to arm a free-running TC that does hardware triggers
at a much higher rate between PTP re-syncs.  See §6.

### 5.2 Constant Offset Is Calibratable

The measured cross-board delta has two components:

- A **constant median offset** (typically tens to hundreds of µs — varies
  with capture window and boot state, but stable within a session).  For
  the 8 kHz / 1 kHz PD10 reference run `20260423_221820` the median was
  about −28 µs across a 10 s capture.
- A **jitter** (MAD ~18 µs at 8 kHz fire rate / cyclic_period = 250 µs;
  ~22 µs at the 4 kHz default / cyclic_period = 500 µs; both measured
  over 10 s).

The constant offset can be measured once and **subtracted** in post-processing
(or compensated by skewing one board's `target_ns` by the measured median).
**Only the jitter (MAD) limits the achievable bandwidth.** This is why the
table above uses 18 µs and not the much larger raw spread.

### 5.3 Capture-Window Length Affects MAD

Measured MAD grows with capture-window length because longer windows expose
slow filter wander and Sync-anchor breathing on top of the per-edge ISR jitter:

| Capture window | Cross-board MAD | Ceiling at 1 % (alignment) | Binding ceiling (with Nyquist = 4 kHz) |
|---|---|---|---|
| 10 s (cyclic_period = 250 µs, 8 kHz fire) | 18 µs | ~88 Hz | 4 kHz Nyquist above ~45 % tol. |
| 10 s (cyclic_period = 500 µs, 4 kHz fire — default) | 22 µs | ~72 Hz | 2 kHz Nyquist above ~45 % tol. |
| 60 s (adaptive filter, 1 Hz cyclic_fire, drift_filter §5) | 39 µs | ~41 Hz | *not measured at 8 kHz fire rate; capture-window effect likely similar* |

For a long-running distributed acquisition, design for the **longer-window
figure** rather than the optimistic 10 s number.  The extra noise over
long windows comes from the IIR filter and Sync-anchor breathing, not
from the cyclic_fire backend itself (see
[README_drift_filter.md](README_drift_filter.md) §5).  If your
measurement duration exceeds ~30 s, budget for an MAD closer to 40 µs
than 18 µs.

### 5.4 Where the Trigger-Jitter Comes From

At the current 8 kHz fire rate the MAD floor is ~18 µs.  Per-edge
cross-board jitter (consecutive Ch1–Ch0 delta changes) is **sub-µs**
(measured mean |Δ| ≈ 1.3 µs) — so the 18 µs is **not** decimator
sampling but **slow drift** across the 10 s window.  Dominant
contributors:

- PTP servo residual: the `drift_ppb` IIR filter hunts between Sync
  samples (125 ms apart), producing ~1 µs/s phase wander.
  Over 10 s this accumulates to the observed ~40–80 µs range, and
  MAD ≈ range/4 → ~18 µs.
- ~5 µs ISR latency on the LAN8651 RX-nIRQ anchor capture (per board).
- ~3 µs cyclic_fire decimator phase computation.
- Cross-board crystal-rate residual (~0.0–10 ppm, depends on filter
  convergence).

The earlier `pd10_filter_freeze_test.py` work (freezing the IIR)
found that the jitter got **worse**, not better — the servo *is* doing
useful tightening, and turning it off doesn't help.  To push lower,
the filter needs tuning (see
[README_drift_filter.md](README_drift_filter.md) §6 for N_max recipes)
or the PTP Sync interval has to be raised (125 ms → 31 ms).

**Adding a phase anchor to cyclic_fire** (`s_cyclic_anchor_ns = 1 ns`,
shipped with the current firmware) does not reduce MAD — the
pre-existing `PTP_CLOCK_ForceSet(0)` at boot already aligned both
boards' fire grids by coincidence.  The anchor is now part of
standard_demo to make that alignment explicit and robust against
future changes.

A full architecture analysis is in [README_PTP.md](README_PTP.md) §12.
**Tightening the IIR filter or shortening the Sync interval are the
two effective knobs** — the decimator/trigger architecture itself is
not the dominant source of the 18 µs MAD at 8 kHz fire rate.

---

## 6. How to Push the Bandwidth Higher

To reach kHz / MHz cross-board acquisition the trigger has to leave the
software path and ride a hardware timer that is anchored directly to the
LAN8651 wallclock. Two practical approaches on this hardware:

### Option A — LAN8651 1PPS pin → SAME54 EIC → ADC HW trigger

The LAN8651 already exposes a 1PPS output (currently routed to PD10 via
`PADCTRL` and used for the `cyclic_fire` GPIO). Re-route or fan-out the same
signal to a SAME54 EIC line whose interrupt vector starts an ADC conversion
through the Event System (no software in the path). Cross-board jitter would
then be:

- LAN8651 internal 1PPS generation jitter: < 100 ns (PHY-internal)
- SAME54 EIC → Event System → ADC START: < 50 ns (deterministic in HW)
- **Total cross-board jitter: < 200 ns**
- **Achievable bandwidth at 1 %: ~8 kHz signal;** sample rate limited
  only by ADC throughput (up to ~1 MS/s on SAME54)

The 1PPS edge happens once per second, so for a continuous sample stream
either use the 1PPS as a periodic re-sync that gates a free-running TC, or
set the LAN8651 to produce a faster periodic event (subject to the LAN8651
TSU output capabilities).

### Option B — TC compare anchored to PTP_CLOCK

The same idea inside the SAME54: schedule an `ADC_START` event from a TC
compare register written from `PTP_CLOCK_GetTime_ns()`. Once the TC is
running, every subsequent fire is hardware-deterministic; only the *initial*
arming and any periodic re-syncs ride the SW jitter (which can be amortised
over many fires).

- Initial arming jitter: 18 µs (same SW limit as cyclic_fire)
- Per-fire jitter once running: ~50 ns (TC compare-match HW)
- **Effective continuous-stream jitter: ~50 ns + slow drift**

This is the standard "PTP-disciplined timer" recipe used in industrial
PTP-based sampling systems and is described conceptually in
[README_PTP.md](README_PTP.md) §12.3.

Either approach pushes the achievable distributed-ADC bandwidth from
**~4 kHz Nyquist at 18 µs MAD → tens to hundreds of kHz**, and lifts
the sample rate well above the current 8 kHz software fire ceiling.

---

## 7. Practical Recipe

To use the *current* SW trigger chain for distributed ADC sampling at the
**8 kHz sample rate** without hardware modifications, piggyback on the
existing cyclic_fire infrastructure that already runs the PD10 decimator:

```c
/* One-time setup (both boards) */
standalone_demo_set_enabled(false);          // unhook the demo's LED/PD10 decimator
cyclic_fire_set_user_callback(adc_on_fire);  // install our own

/* Option 1: re-use the demo's already-running cyclic_fire.
 * Assumes `demo_cyclic_period 250` was sent (or the default 500 is fine).
 *
 * Option 2: re-configure explicitly for 8 kHz:
 *   standalone_demo_set_cyclic_period_us(250);  // 8 kHz fire rate
 *                                                // fire_callback every 125 µs
 */

static void adc_on_fire(uint64_t target_ns, uint64_t actual_ns)
{
    (void)target_ns; (void)actual_ns;
    ADC_ConversionStart();
    /* Log the PTP wallclock alongside the conversion so post-processing
     * can re-align cross-board samples by timestamp rather than relying
     * on fire_callback simultaneity. */
    sample_timestamp_queue_push(PTP_CLOCK_GetTime_ns());
}
```

Both boards execute `adc_on_fire` every 125 µs at the same nominal PTP
wallclock instant.  The actual ADC trigger times on the two boards differ
by ~18 µs MAD per the measured floor — well inside the 100 µs product
gate for a 10 s capture.

For event-by-event correlation, the logged `PTP_CLOCK_GetTime_ns()`
timestamps above let post-processing correlate samples by absolute PTP
time instead of by fire ordinal.  This removes the need for the two
boards' fire_callbacks to fire at *exactly* the same instant — they only
need to produce a shared time reference for each sample.

For a one-shot (single-sample) capture at a precise absolute wallclock:

```c
/* Both boards: one ADC conversion at the next round 100 ms boundary */
uint64_t now_ns    = PTP_CLOCK_GetTime_ns();
uint64_t period_ns = 100ULL * 1000ULL * 1000ULL;     // 100 ms boundary
uint64_t target_ns = ((now_ns / period_ns) + 1ULL) * period_ns;

while (PTP_CLOCK_GetTime_ns() < target_ns) { /* spin */ }
ADC_ConversionStart();
```

Cross-board trigger jitter: same ~18 µs MAD.

---

## 8. References

- [README_PTP.md](README_PTP.md) §11 — measured PTP performance at the wire.
- [README_PTP.md](README_PTP.md) §12 — accuracy when used from app code,
  including the hardware-trigger path for sub-µs.
- [README_pd10_sync_before_after.md](README_pd10_sync_before_after.md) —
  cyclic_period sweep (100/200/250/500/1000 µs) that established 8 kHz
  as the practical fire-rate ceiling on this firmware, and the 18/22 µs
  cross-board MAD numbers used above.
- [README_drift_filter.md](README_drift_filter.md) §5 — measurement
  methodology + fixed-vs-adaptive filter comparison + capture-window MAD
  characterisation.  §5.4 explicitly reconciles the 7.2 µs figure there
  with the 18 – 22 µs measured by the 1 kHz PD10 bench test.
- [README_standalone_demo.md](README_standalone_demo.md) — end-to-end demo
  showing the cross-board cyclic_fire alignment in action, plus the
  `demo_autopilot` / `demo_pd10_slot` / `demo_cyclic_period` bench CLIs.
- [pd10_filter_freeze_test.py](pd10_filter_freeze_test.py) — A/B test that
  isolated the IIR filter's contribution to jitter (rejected hypothesis;
  filter is not dominant).
- [pd10_sync_before_after_test.py](pd10_sync_before_after_test.py) — the
  unsynced-vs-synced cross-board PD10 test referenced throughout this
  document; `--cyclic-period-us 250` reproduces the 8 kHz / 18 µs MAD
  figures.
