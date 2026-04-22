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

- **Steady-state Median Absolute Deviation (MAD): ~25 µs** on the cross-board
  rising-edge delta after detrending the linear drift component
  (see [README_drift_filter.md](README_drift_filter.md) §5 for the full
  measurement methodology and `pd10_filter_freeze_test.py` for the analysis).

The natural follow-up question: **if a software callback on each board
triggered an ADC conversion at the same nominal PTP wallclock time instead of
toggling a GPIO, what is the highest signal frequency that can be reliably
sampled across both boards for correlation, FFT, beamforming, phase analysis,
or sensor fusion?**

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
jitter**. With Δt = 25 µs (the measured cross-board MAD), this gives the
practical "bandwidth ceiling" for any given amplitude-error tolerance.

---

## 3. Bandwidth vs Acceptable Amplitude Error

Solving `2π·f·Δt < ε` for `f` with Δt = 25 µs:

| Acceptable ΔV/A | Max signal frequency `f` | Use case implication |
|---|---|---|
| 0.01 % | ~0.6 Hz | Reference comparison (rare) |
| 0.1 % | ~6 Hz | Sub-Hz drift / DC monitoring |
| 1 % | **~64 Hz** | Power-quality fundamental (50/60 Hz at 1 % error) |
| 5 % | ~320 Hz | Vibration low-frequency band |
| 10 % | **~640 Hz** | Audio bass, mechanical resonance |
| 25 % | ~1.6 kHz | Coarse correlation, trigger-only sampling |
| 50 % | **~3.2 kHz** | Practical absolute ceiling |
| 100 % | ~6.4 kHz | Aliasing in cross-board correlation — unusable |

> **Reading the table:** Pick the row matching how much amplitude error you
> can tolerate in the cross-board comparison; that row's `f` is your highest
> usable signal frequency. Beyond ~3 kHz the same physical signal sampled on
> the two boards differs by more than 50 % of full scale on every cycle —
> meaningful correlation is lost.

For comparison, the formula `f < 0.5 / (2π · Δt)` gives the −4 dB point
(50 % amplitude error); a stricter "well-correlated" engineering rule of
thumb `Δt < period/10` translates to `f < 1/(10·Δt) ≈ 4 kHz` — same order.

---

## 4. Bandwidth by Application Class

Mapping the table above onto common distributed-sensing applications:

| Application | Signal of interest | Tolerance | Achievable today? |
|---|---|---|---|
| **Power monitoring (50/60 Hz)** | Fundamental + harmonics to ~3 kHz | 1 % on fundamental, ≤ 50 % on harmonics | **Fundamental yes**, mid-harmonics marginal |
| **Slow vibration / building monitoring** | < 100 Hz | 1 % | **Yes** comfortably |
| **Industrial vibration / bearing analysis** | 100 Hz – 1 kHz | 5–10 % | **Yes**, with caveats above ~600 Hz |
| **Audio correlation / acoustic localisation** | 20 Hz – 20 kHz | 1° phase ≈ 0.017 amplitude | **No** — need < 1 µs sync |
| **Phase-precise voltage/current measurement** | 50/60 Hz, 1° phase | 0.017 | **Borderline** — Δt = 25 µs ≈ 0.45° phase at 50 Hz |
| **Beamforming / array processing** | Application-dependent | typically < 10 % | **Sub-kHz only** |
| **Sub-µs distributed time-of-flight** | Pulses / event timing | absolute timing | **No** — not the right tool, see §6 |

Rule of thumb: this synchronisation quality is comfortable for **anything
below ~640 Hz** and acceptable for power-line + low harmonics. It is **not
suitable for audio or precision phase measurement above ~100 Hz**.

---

## 5. Caveats

### 5.1 Nyquist Is a Separate Constraint

The bandwidth limit derived above is purely about **time-alignment** between
the two boards' sample instants. Each board independently must still satisfy
the Nyquist criterion `f_sample > 2 · f_signal` for its own ADC. The SAME54
ADC handles up to ~1 MS/s, so on-board Nyquist is rarely the binding limit
in this discussion — the cross-board jitter dominates by 3–4 orders of
magnitude.

### 5.2 Constant Offset Is Calibratable

The measured cross-board delta has two components:

- A **constant median offset** (typically +143 µs in 10 s captures, −32 µs in
  60 s captures — varies with capture window, but stable within a session).
- A **jitter** (MAD ~25 µs / 39 µs respectively).

The constant offset can be measured once and **subtracted** in post-processing
(or compensated by skewing one board's `target_ns` by the measured median).
**Only the jitter (MAD) limits the achievable bandwidth.** This is why the
table above uses 25 µs and not the much larger raw spread.

### 5.3 Capture-Window Length Affects MAD

Measured MAD grows with capture-window length because longer windows expose
slow filter wander and Sync-anchor breathing on top of the per-edge ISR jitter:

| Capture window | Cross-board MAD | Ceiling at 1 % | Ceiling at 50 % |
|---|---|---|---|
| 10 s | 7.2 µs | ~220 Hz | ~11 kHz |
| 60 s | 39 µs | ~41 Hz | ~2 kHz |

For a long-running distributed acquisition, design for the **60 s figure
(~2 kHz at 50 % tolerance)** rather than the optimistic 10 s number. The
extra noise over long windows comes from the IIR filter and Sync-anchor
breathing, not from the cyclic_fire backend itself
(see [README_drift_filter.md](README_drift_filter.md) §5).

### 5.4 Where the Trigger-Jitter Comes From

The 25 µs MAD floor is **not** caused by the drift IIR filter — that was
explicitly tested with `pd10_filter_freeze_test.py`, which froze the filter
and saw the jitter get **worse**, not better. The dominant contributors are:

- ~5 µs ISR latency on the LAN8651 RX-nIRQ anchor capture (per board).
- ~3 µs cyclic_fire decimator phase computation.
- Sync-anchor breathing between Sync intervals (~125 ms gap × residual ppb).
- Cross-board crystal-rate residual (~0.0–10 ppm, depends on filter convergence).

A full architecture analysis is in [README_PTP.md](README_PTP.md) §12 and the
filter-vs-architecture investigation is in
[README_drift_filter.md](README_drift_filter.md) §5.3. **Tightening the IIR
filter alone will not push the bandwidth higher** — the floor is set by the
software-trigger architecture.

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
- **Achievable bandwidth at 1 %: ~8 kHz; at 50 %: ~400 kHz**

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

- Initial arming jitter: 25 µs (same SW limit)
- Per-fire jitter once running: ~50 ns (TC compare-match HW)
- **Effective continuous-stream jitter: ~50 ns + slow drift**

This is the standard "PTP-disciplined timer" recipe used in industrial
PTP-based sampling systems and is described conceptually in
[README_PTP.md](README_PTP.md) §12.3.

Either approach pushes the achievable distributed-ADC bandwidth from
**~3 kHz → tens to hundreds of kHz**.

---

## 7. Practical Recipe

To use the *current* SW trigger chain for distributed ADC sampling without
hardware modifications:

```c
/* Both boards: schedule ADC at next round 100 ms boundary */
#include "ptp_clock.h"

uint64_t now_ns    = PTP_CLOCK_GetTime_ns();
uint64_t period_ns = 100ULL * 1000ULL * 1000ULL;     // 100 ms => 10 Hz
uint64_t target_ns = ((now_ns / period_ns) + 1ULL) * period_ns;

/* Spin-wait or use tfuture to fire at target_ns */
while (PTP_CLOCK_GetTime_ns() < target_ns) { /* spin */ }
ADC_ConversionStart();
```

Both boards execute this at the same nominal `target_ns`. The actual ADC
trigger times differ by ~25 µs (1σ) per the measured floor.

For periodic acquisition use the existing `cyclic_fire` module with a
user-callback (`cyclic_fire_set_user_callback`) that calls
`ADC_ConversionStart()` instead of toggling PD10:

```c
static void on_fire(uint64_t target_ns, uint32_t cycles, void *ctx)
{
    (void)target_ns; (void)cycles; (void)ctx;
    ADC_ConversionStart();   // fires within ~25 µs of the same instant on both boards
}

cyclic_fire_set_user_callback(on_fire, NULL);
cyclic_fire_set_pattern(CYCLIC_FIRE_PATTERN_SILENT);   // skip native PD10 toggle
cyclic_fire_start(period_us, anchor_ns);
```

For event-by-event correlation, log `PTP_CLOCK_GetTime_ns()` *immediately
after* `ADC_ConversionStart()` and ship that timestamp alongside the sample
value. Post-processing can then correlate samples by timestamp without
relying on the trigger being perfectly simultaneous.

---

## 8. References

- [README_PTP.md](README_PTP.md) §11 — measured PTP performance at the wire.
- [README_PTP.md](README_PTP.md) §12 — accuracy when used from app code,
  including the hardware-trigger path for sub-µs.
- [README_drift_filter.md](README_drift_filter.md) §5 — measurement
  methodology + fixed-vs-adaptive filter comparison + capture-window MAD
  characterisation.
- [README_standalone_demo.md](README_standalone_demo.md) — end-to-end demo
  showing the cross-board cyclic_fire alignment in action.
- [pd10_filter_freeze_test.py](pd10_filter_freeze_test.py) — A/B test that
  isolated the IIR filter's contribution to jitter (rejected hypothesis;
  filter is not dominant).
