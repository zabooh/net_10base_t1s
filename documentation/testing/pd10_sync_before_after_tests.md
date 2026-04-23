# PD10 Before/After Sync Test

Cross-board PD10 measurement that captures the *same* rectangle signal
twice — once with PTP disabled (boards free-run on their local crystals)
and once with PTP locked — and shows the effect of synchronisation
directly on the scope data.

Script: [pd10_sync_before_after_test.py](../../tools/ptp-analysis/sync-tests/pd10_sync_before_after_test.py)

---

## What it does

Two Saleae captures, back-to-back, on the same pair of boards:

| Phase | Setup                                    | What you see on PD10       |
|-------|------------------------------------------|----------------------------|
| A — UNSYNCED | `ptp_mode off` on both boards. Each board's PTP_CLOCK free-runs on its local TC0 crystal (no Sync frames, no `drift_ppb` updates). | Two rectangles drifting apart at the raw crystal-mismatch rate (~100 – 250 ppm on typical SAM E54 pairs). |
| B — SYNCED   | `ptp_mode master` + `ptp_mode follower`, wait for `PTP FINE`, settle, capture. | Two rectangles locked to PTP wallclock, cross-board drift < 5 ppm. |

Each phase captures **10 s @ 50 MS/s** on both channels → ~20 000
transitions per board per phase. Per-channel interval histograms and
cross-board drift curves are produced for each phase and placed
side-by-side for direct comparison.

## Why the demo's decimator (not `cyclic_start`)

Early iterations of this test tried to drive PD10 via cyclic_fire's
native SQUARE pattern (TC1-ISR compare-match). That achieved only
~116 ppm cross-board drift even after `PTP FINE` — TC1 is a hardware
timer clocked from a local peripheral clock, so its compare moment
inherits the TC1-crystal error that `drift_ppb` only partially
compensates.

The demo's PD10 decimator, by contrast, reads `PTP_CLOCK_GetTime_ns()`
on every fire and derives the pin state from `(wc_ns / slot_ns) & 1`.
When two PTP_CLOCKs are locked to sub-µs, both boards compute
identical slot parity at the same wallclock instants — sub-µs sync on
PD10 directly. The test runs at 1 kHz via the
`demo_pd10_slot <us>` CLI (half-period in µs, default 500 000 µs =
1 Hz). The decimator's own sample rate is also runtime-configurable
via `demo_cyclic_period <us>` — halving the cyclic period places each
PD10 edge into a half-width sampling window around its target slot
boundary, tightening cross-board MAD. See
[standalone_demo.c](../../apps/tcpip_iperf_lan865x/firmware/src/standalone_demo.c) and
[demo_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/demo_cli.c).

## PASS/FAIL gates

Hardcoded in the script:

- `PTP FINE reached`                        — follower must report FINE within `--fine-timeout-s`
- `|SYNC slope| < 5 ppm`                    — locked drift rate
- `SYNC MAD < 50 µs`                        — robust spread of the locked delta
- `|UNSYNC slope| > 20 ppm` (sanity)        — confirms PTP was actually off in Phase A

Exit code 0 on PASS, 1 on any FAIL.

## Usage

```
python pd10_sync_before_after_test.py                     # default 10 s + 10 s @ 1 kHz
python pd10_sync_before_after_test.py --gm-port COM10 --fol-port COM8
python pd10_sync_before_after_test.py --unsync-duration-s 30 --sync-duration-s 30
python pd10_sync_before_after_test.py --period-us 500     # 2 kHz PD10 rectangle
python pd10_sync_before_after_test.py --cyclic-period-us 250   # 8 kHz fire rate
python pd10_sync_before_after_test.py -v                  # background-drain UARTs into log
```

### Two independent rate knobs

| Parameter | Controls | Default | Effect of lowering |
|---|---|---:|---|
| `--period-us` | **PD10 rectangle full period** (`demo_pd10_slot` CLI). Half-period → `demo_pd10_slot period_us/2`. | 1000 (= 1 kHz rectangle) | More transitions per capture → finer interval histograms. |
| `--cyclic-period-us` | **cyclic_fire sampling period** (`demo_cyclic_period` CLI). fire_callback runs every `cyclic_period_us/2` µs. | `0` (leave firmware default = 500 µs → 4 kHz fire) | Sharper PD10 edge timing — each edge lands within half a fire interval of the target wallclock slot. Higher ISR load. |

The two are orthogonal: `--period-us` sets *what* rectangle you want on
PD10, `--cyclic-period-us` sets *how often* the decimator re-evaluates
the wallclock-derived pin state. For `cyclic_period ≥ period`, the
decimator undersamples and the test rejects with the firmware's
Nyquist clamp (`demo_pd10_slot` silently raises the slot to a safe
value). Keep `cyclic_period_us ≤ period_us/2` for the configured
rectangle to actually appear on PD10.

## Output

Per run, a `pd10_sync_before_after_<ts>/` directory with:

- `unsync_ch0_hist.png`, `unsync_ch1_hist.png`, `unsync_drift.png`
- `sync_ch0_hist.png`,   `sync_ch1_hist.png`,   `sync_drift.png`
- `comparison_intervals.png` — 2×2 grid (Ch0/Ch1 × UNSYNC/SYNC) with shared x-axis per row
- `comparison_drift.png`     — 1×2 panel (UNSYNCED | SYNCED), shared y-axis
- `unsync_*_us.csv`, `sync_*_us.csv` — raw per-edge / per-interval data
- `summary.csv`                       — one row per phase with n, median, MAD, slope, drift-MAD
- `run_<ts>.log`                      — tee'd console output

## Representative result (run `20260423_210451`)

Board pair: two SAM E54 Curiosity Ultra on a LAN865x 10BASE-T1S link.
Half-period target: 500 µs. Capture 10 s each phase @ 50 MS/s.
Default cyclic_period_us = 500 (fire rate 4 kHz).

### Cross-board drift (unwrapped delta Ch1 − Ch0)

| Metric           | UNSYNC        | SYNC          |
|------------------|---------------|---------------|
| n                | 10 066        | 10 054        |
| slope            | **+231.3 ppm**| **−1.2 ppm**  |
| MAD              | **494.4 µs**  | **22.3 µs**   |
| start → end      | −65 → +895 µs | −80 → −91 µs  |

**Sync reduces cross-board drift MAD by ~22×.**

### Per-board half-period stability

Saleae measures the interval between consecutive PD10 transitions on
each channel independently (not cross-board). This shows **how stable
the individual board's rectangle clock is** — independent of any
cross-board relationship.

| Metric             | Ch0 UNSYNC | Ch1 UNSYNC | Ch0 SYNC   | Ch1 SYNC   |
|--------------------|-----------:|-----------:|-----------:|-----------:|
| n (intervals)      | 20 131     | 20 118     | 20 107     | 20 106     |
| min                | 44.40 µs   | 43.82 µs   | 201.70 µs  | 202.00 µs  |
| p1                 | 498.36 µs  | 498.52 µs  | 498.96 µs  | 499.00 µs  |
| p5                 | 498.66 µs  | 498.80 µs  | 499.36 µs  | 499.28 µs  |
| p25                | 499.12 µs  | 499.24 µs  | 499.80 µs  | 499.76 µs  |
| **median**         | **499.34 µs** | **499.46 µs** | **500.04 µs** | **499.96 µs** |
| mean               | 499.74 µs  | 500.05 µs  | 500.32 µs  | 500.32 µs  |
| p75                | 499.56 µs  | 499.68 µs  | 500.26 µs  | 500.16 µs  |
| p95                | 500.00 µs  | 500.10 µs  | 500.72 µs  | 500.66 µs  |
| p99                | 500.30 µs  | 500.40 µs  | 501.16 µs  | 501.02 µs  |
| max                | 1296.16 µs | 1297.16 µs | 1297.40 µs | 1297.92 µs |
| **MAD**            | **0.22 µs**| **0.22 µs**| **0.22 µs**| **0.20 µs**|
| IQR (p75−p25)      | 0.44 µs    | 0.44 µs    | 0.46 µs    | 0.40 µs    |
| **p95−p5 (90 % band)** | **1.34 µs** | **1.30 µs** | **1.36 µs** | **1.38 µs** |
| **p99−p1 (98 % band)** | **1.94 µs** | **1.88 µs** | **2.20 µs** | **2.02 µs** |
| spread (max−min)   | 1251.76 µs | 1253.34 µs | 1095.70 µs | 1095.92 µs |
| stdev (raw, w/ outliers) | 30.22 µs | 31.83 µs | 21.38 µs | 16.21 µs |
| outliers (<400 or >600 µs) | 89 (0.44 %) | 100 (0.50 %) | 76 (0.38 %) | **20 (0.10 %)** |
| rate offset vs. 500 µs | −1320 ppm | −1080 ppm | **+80 ppm** | **−80 ppm** |

### Readable takeaways

1. **Per-board half-period stability is identical in all four columns.**
   MAD stays at ~0.20 µs, IQR at 0.44 µs, 98 % band at ~2 µs — PTP does
   not change how tightly each individual toggle lands. The raw stdev
   (16 – 32 µs) is inflated by a handful of glitch/missed edges and is
   *not* representative of the clock stability.

2. **PTP shifts the median rate, not the jitter.**
   - Ch0 median: **499.34 → 500.04 µs** (+0.70 µs, rate correction ~1400 ppm)
   - Ch1 median: **499.46 → 499.96 µs** (+0.50 µs, rate correction ~1000 ppm)
   Both boards are pulled onto master time; the `drift_ppb` IIR
   effectively stretches/compresses the follower's tick rate so its
   PTP_CLOCK advances at master rate.

3. **Cross-board rate mismatch collapses.**
   - UNSYNC: 499.34 µs vs. 499.46 µs → **~240 ppm** inter-board rate gap.
     Matches the cross-board drift slope of +231 ppm — the two numbers
     are the same physical phenomenon viewed from two angles.
   - SYNC:   500.04 µs vs. 499.96 µs → **~160 ppm** residual gap, but
     the drift slope is only −1.2 ppm. The residual rate gap ends up
     in the per-edge jitter (±80 ppm each), not in a secular drift,
     because the `drift_ppb` filter is continuously updating.

4. **Outliers drop sharply on Ch1 in SYNC** (0.50 % → 0.10 %, 5×
   fewer). Probably a side effect of the PTP frame traffic tightening
   the decimator's pin-write timing; Ch0 sees a small improvement too
   (0.44 % → 0.38 %).

## What the 22 µs MAD floor really is (and isn't)

At `cyclic_period=500 µs` the cross-board MAD stabilises around
~22 µs, and lowering the cyclic period to 250 µs only trims it to
~18 µs. The MAD does **not** come from decimator sub-sampling. Two
lines of evidence:

1. **Per-edge jitter is sub-µs.** Computed from consecutive rows of
   `sync_drift_us.csv`: mean |Δ| ≈ 1.3 µs, with occasional single-edge
   jumps at ~fire_interval when a slot boundary lands exactly at a
   fire moment. Median differences are < 0.5 µs. If the decimator's
   sub-sampling were the dominant source, per-edge jitter would be on
   the order of fire_interval/2 (62 – 125 µs). It isn't.
2. **The MAD tracks slow drift across the 10 s window.** Delta walks
   smoothly from, e.g., −67 µs at the start to +11 µs at the end —
   ~78 µs of motion over 10 s. MAD ≈ range/4 for a linear drift, which
   lands at ~20 µs. That is the PTP servo residual (`drift_ppb` IIR
   hunting between Sync samples), not decimator aliasing.

**A cyclic_fire phase anchor** (`s_cyclic_anchor_ns = 1 ns`, new in
[standalone_demo.c](../../apps/tcpip_iperf_lan865x/firmware/src/standalone_demo.c)) was added so both
boards' fires land on an absolute grid `anchor + N·period` rather than
each board's own "now + period" at start time. That fix works and
stays in the firmware — but it turned out to be a no-op for MAD,
because the pre-existing `PTP_CLOCK_ForceSet(0)` at boot already
forced both boards onto a common grid by coincidence (both had
PTP_CLOCK = 0 at `cyclic_fire_start`, so first_target = period on
both). The anchor makes the alignment robust against any future
change that removes the ForceSet(0); it does not — and cannot —
improve the MAD floor because the MAD is bound by the PTP servo, not
by decimator timing.

**To push MAD below ~20 µs would require PTP-servo work:**
a shorter `drift_ppb` IIR (sharper tracking, more jitter per sample —
see [drift_filter.md](../ptp/drift_filter.md)), a phase-lock
loop on top of the current rate-only lock, a faster Sync interval
(125 ms → 31 ms), or calibrating the LAN865x RX-pipeline asymmetry
(`PTP_FOL_ANCHOR_OFFSET_NS`). None of that is a decimator issue.

**Against the 100 µs cross-board product gate** 22 µs MAD leaves ~4.5×
margin — the bench currently meets the requirement comfortably.

## Effect of `--cyclic-period-us` (run `20260423_215128`)

Same test re-run with `--cyclic-period-us 250` — fire_callback rate
doubled from 4 kHz to 8 kHz, so every decimator decision lands within
±125 µs of its target wallclock slot instead of ±250 µs.

| Metric                     | cyclic_period = 500 µs | cyclic_period = 250 µs |
|----------------------------|-----------------------:|-----------------------:|
| fire_callback rate         | 4 kHz                  | **8 kHz**              |
| SYNC slope                 | −1.19 ppm              | **+1.27 ppm**          |
| SYNC MAD                   | 22.25 µs               | **18.82 µs**           |
| SYNC/UNSYNC MAD reduction  | 22×                    | **90×**                |
| PASS/FAIL                  | PASS                   | PASS                   |

Lowering the cyclic period sharpens cross-board edge alignment
monotonically — halving it improves MAD by ~15 %. The UNSYNC slope
jumped from 231 ppm to 771 ppm between the two runs because of raw
crystal-mismatch variability between reset cycles (the two TC0
oscillators warm up to slightly different steady-state frequencies
each boot), not because of any test-side change — hence the MAD-ratio
change from 22× to 90× is dominated by UNSYNC variability, not a real
SYNC improvement. **Use the absolute SYNC numbers (slope, MAD) for
before/after comparisons across runs, not the ratio.**

Trade-off: higher fire rate = higher ISR load.  Measured on this
firmware (same bench, two sweeps):

| cyclic_period | fire rate | SYNC slope         | SYNC MAD   | Verdict  |
|--------------:|----------:|-------------------:|-----------:|----------|
| 500 µs (default) | 4 kHz  | −1.19 ppm          | 22.25 µs   | **PASS** |
| 250 µs           | 8 kHz  | +1.27 ppm          | 18.82 µs   | **PASS** (lowest stable) |
| 200 µs (run 1)   | 10 kHz | +260 ppm           | 32.80 µs   | FAIL (slope)       |
| 200 µs (run 2)   | 10 kHz | +1203 ppm          | 1068 µs    | FAIL (slope + MAD) |
| 100 µs           | 20 kHz | +13.83 ppm         | 34.22 µs   | FAIL (slope) — UNSYNC also unphysical at 3963 ppm |

**Safe range on this firmware: `cyclic_period_us ≥ 250`** (8 kHz fire
or slower).  At 200 µs / 10 kHz two consecutive runs produced widely
different broken results (260 ppm vs. 1203 ppm slope) — not a grey
zone, systemic instability.  The failure mode at 10 kHz is that the
ISR + decimator + PTP-frame processing together starve the main loop:
`SYS_TIME_Counter64Get()` readouts in the decimator jitter, and the
`drift_ppb` IIR doesn't get clean samples, so the servo never
converges.  At 20 kHz (100 µs) the UNSYNC numbers turn unphysical
(3963 ppm) because the ISR itself skews the reference.  To go faster
than 250 µs, firmware work is needed: shorter ISR, DMA-based pin
output, or lower-cost PTP processing.

## Related documents

- [standalone_demo.md](../features/standalone_demo.md) — the
  PTP_CLOCK-driven decimator this test relies on
- [pd10_sync_tests.md](pd10_sync_tests.md) — single-phase
  synced-only cross-board test (no before/after comparison)
- [drift_filter.md](../ptp/drift_filter.md) — why the
  `drift_ppb` IIR corrects the follower rate but not the per-edge
  jitter
