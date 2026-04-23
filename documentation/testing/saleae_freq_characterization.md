# `saleae_freq_check.py` — Rectangle-Signal Frequency & Phase Characterisation

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. What it measures](#2-what-it-measures)
- [3. Hardware + software prerequisites](#3-hardware--software-prerequisites)
- [4. Usage](#4-usage)
- [5. Command-line options](#5-command-line-options)
- [6. Output format](#6-output-format)
- [7. Measurement settings block](#7-measurement-settings-block)
- [8. Per-channel analysis block](#8-per-channel-analysis-block)
- [9. Auto-detected nominal frequency](#9-auto-detected-nominal-frequency)
- [10. Accuracy, limits, and what improves with duration](#10-accuracy-limits-and-what-improves-with-duration)
- [11. Digital vs. analog mode](#11-digital-vs-analog-mode)
- [12. Logging](#12-logging)
- [13. Common use cases](#13-common-use-cases)
- [14. Troubleshooting](#14-troubleshooting)

---

## 1. Purpose

`saleae_freq_check.py` measures a rectangular digital signal on one or
more Saleae channels and reports its **frequency**, **period jitter**,
**high- and low-phase durations**, and **duty cycle** — with
nanosecond-level precision, limited only by the Saleae sample rate.

It is the hardware-level complement to what the firmware reports via
`clk_get` / `ptp_offset` / `tfuture_status`: an independent,
instrument-based ground truth that does not rely on the board's own
self-reporting.

Typical targets for this tool in the 10BASE-T1S PTP project:

- The `blink` CLI rectangle on `PD10` at any integer Hz — verify the
  MCU quartz drift directly at the GPIO level (bypasses the firmware's
  own `drift_ppb` reporting).  See the `blink` command in the top-level
  readme §5.13.
- The `cyclic_fire` rectangle on `PD10` at configurable period —
  directly measure whether the configured firmware period matches the
  GPIO edges (key to investigating the 1.7× rate factor, see
  Ticket 7 in `prompts/codebase_cleanup_followups.md`).
- Any general-purpose GPIO toggle in experiments to come.

## 2. What it measures

On each enabled channel the script extracts every rising and falling
edge from the capture and derives:

| Quantity | Definition | Why it matters |
| -------- | ---------- | -------------- |
| **Period** | rising-to-next-rising gap | one full rectangle cycle; used for frequency |
| **Frequency** | 1 / median(Period) | primary result |
| **Deviation** | (freq − nominal) / nominal, in ppm | how far the clock is from the design target |
| **Period jitter** | robust stdev of period samples (1.4826 × MAD) | shows how stable the generator is cycle-to-cycle |
| **High phase** | rising-to-next-falling gap | sanity check on generator symmetry |
| **Low phase** | falling-to-next-rising gap | sanity check on generator symmetry |
| **Duty cycle** | median(High) / median(Period) | reveals bias in the toggle mechanism |

All statistics use the **median + MAD (median absolute deviation)**
combination because it is robust against single-cycle outliers — a
single glitched edge does not corrupt the reported values.

## 3. Hardware + software prerequisites

- **Saleae Logic 8** (or compatible) connected via USB.
- **Logic 2 desktop app** running, with the automation server enabled
  (Options → Preferences → Developer → "Enable scripting socket
  server").  See `saleae_smoke.py` for a connectivity pre-flight.
- **`logic2-automation` Python library** installed:
  `pip install logic2-automation`.
- **A probe** connected to the signal under test, plus Saleae GND to
  the board's GND.
- The script also imports the shared `Logger` helper from
  `ptp_drift_compensate_test.py`, so run it from the same directory.

## 4. Usage

```bash
# Default: Ch0, 3 s, 100 MS/s — ideal for a quick frequency reading
python saleae_freq_check.py

# Explicit nominal: for known targets, show direct ppm deviation
python saleae_freq_check.py --nominal-hz 1          # `blink 1`   on the board
python saleae_freq_check.py --nominal-hz 1000       # `blink`     (default 1 kHz), or cyclic_fire @ 500 µs

# Long run for tighter frequency statistics (~ ±5 ppm at 60 s)
python saleae_freq_check.py --duration 60

# Two boards in parallel: Ch0 = GM_PD10, Ch1 = FOL_PD10
python saleae_freq_check.py --channels 0 1

# Include a high-phase histogram to visualise jitter distribution
python saleae_freq_check.py --duration 30 --histogram

# Slower sample rate to free channel bandwidth on Logic 8
python saleae_freq_check.py --sample-rate 25000000 --channels 0 1 2 3

# Keep the exported CSV for offline analysis
python saleae_freq_check.py --keep-csv --out-dir ./capture_out
```

## 5. Command-line options

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--channels N [N ...]` | `0` | Digital channels to watch (space-separated). |
| `--duration FLOAT` | `3.0` | Capture duration in seconds — continuous, not polled. |
| `--sample-rate INT` | `100000000` | Digital sample rate in Hz (max 100 MS/s on Logic 8 single channel). |
| `--nominal-hz FLOAT` | *auto* | Nominal rectangle frequency; if omitted, the script snaps to the nearest 1 / 2 / 2.5 / 5 × 10^k value. |
| `--histogram` | off | Print a 20-bin histogram of high-phase durations. |
| `--port INT` | `10430` | Logic 2 gRPC port. |
| `--out-dir PATH` | temp | Where the CSV is exported.  Deleted on exit unless `--keep-csv` is set. |
| `--keep-csv` | off | Do not delete the exported CSV directory. |
| `--log-file PATH` | `saleae_freq_check_<ts>.log` | Where the full session log is written. |
| `--verbose` | off | Also log debug-level messages. |

## 6. Output format

Every run produces a single structured output (to stdout **and** to the
log file) in three parts:

1. **Measurement settings block** (§7)
2. **Capture progress + CSV path**
3. **Per-channel analysis block** (§8)

All time values are printed with SI suffixes auto-selected so the
sample-rate floor is visible:

```
edge resolution   :         10.0 ns
median Period     :    998.835470 ms
robust stdev      :      67.1618 µs
```

## 7. Measurement settings block

Printed first so every log starts with the exact configuration used:

```
  Measurement settings:
    channels          : [0, 1]  (2 enabled)
    sample rate       :     100,000,000 Hz  (= 100.000 MS/s)
    duration          :          60.000 s
    samples / channel :   6,000,000,000  (= sample_rate × duration)
    total samples     :  12,000,000,000  (= samples/ch × 2 channels)
    edge resolution   :         10.0 ns  (= 1 / sample_rate)
    width quantisation:         20.0 ns  (= 2 × edge resolution, rising + falling)
    nominal frequency :     1.000000 Hz  (for ppm deviation report)
```

- **samples / channel** = sample_rate × duration
- **total samples** = samples/ch × number of enabled channels
- **edge resolution** = smallest time step resolvable on a single edge
- **width quantisation** = measurement uncertainty of a pulse width
  (rising edge + falling edge, each with ±1 sample)

## 8. Per-channel analysis block

For every enabled channel:

```
Channel 1  —  60 rising / 60 falling edges
  Period (rising-to-rising):
    n             = 59
    min           = 998.686160 ms
    max           = 999.030940 ms
    range         = 344.7800 µs
    mean          = 998.844961 ms
    stdev         = 74.3473 µs
    median        = 998.835470 ms
    MAD           = 45.3000 µs
    robust stdev  = 67.1618 µs  (= 1.4826 × MAD)
    frequency     = 1.001166 Hz
    vs nominal    = 1.000000 Hz   deviation = +1165.888 ppm  (auto-detected)
    period jitter = 67.1618 µs (= +67.240 ppm of period)

  High phase (rising-to-next-falling):
    n             = 59
    min           = 499.340990 ms
    max           = 499.550630 ms
    ...

  Low phase (falling-to-next-rising):
    n             = 60
    ...

  Duty cycle    = 50.0006 %
    deviation   = +0.0006 pp from 50 %  (= +6.3 ppm)
```

Quick reading guide:

- **frequency** — the primary answer.
- **deviation** — how far from the configured / expected nominal.
- **period jitter** — short-term stability (typical main-loop latency
  in firmware-generated signals).
- **Duty cycle** — healthiness of the generator; should be 50 % for an
  even toggle.  Drift from 50 % suggests asymmetric toggle timing or
  an edge-detection bias.

## 9. Auto-detected nominal frequency

When `--nominal-hz` is not given, the script *always* reports a
deviation by snapping the measured frequency to the closest
value of the form `N × 10^k`, N ∈ {1, 2, 2.5, 5, 10}, using log-space
proximity (so errors above/below are weighted equally).

Examples:

| Measured | Auto-snapped nominal | Report |
| -------- | -------------------- | ------ |
| 1.001047 Hz | 1 Hz | +1 047 ppm |
| 1000.523 Hz | 1000 Hz | +523 ppm |
| 4998.3 Hz | 5000 Hz | −340 ppm |
| 243.1 Hz | 250 Hz | −2 756 ppm |

The output always labels the nominal as `(auto-detected)` or
`(user-supplied)` so it is clear which one was used.

## 10. Accuracy, limits, and what improves with duration

Three regimes:

**1. Short runs (< ~10 s)** — **jitter dominates**.
Uncertainty of the median period shrinks with `1 / √N`.  Doubling the
duration improves frequency precision by `√2 ≈ 1.41×`.

**2. Medium runs (10 s – 2 min)** — **diminishing returns**.
The Saleae Logic 8's own quartz (typically ±20–50 ppm absolute) becomes
the limiting factor.  Once the median-of-N is tighter than the Saleae
reference, longer capture yields no further precision gain.

**3. Very long runs (>> minutes)** — **can get worse**.
Thermal drift of the Saleae quartz can slowly shift the reference.
Without a GPS / atomic-clock discipline on the Saleae, this ultimately
caps the attainable accuracy.

Practical guidance:

| Goal | Recommended duration |
| ---- | -------------------- |
| Quartz drift (~1000 ppm magnitudes) | **30 s** → ±5 ppm |
| Board-to-board offset (~10 ppm) | **5 min** |
| Sub-ppm effects | Not possible without an external time reference |

Worth noting: the width **quantisation** floor is fixed by the sample
rate (2 / sample_rate).  At 100 MS/s that is 20 ns — any jitter below
that cannot be resolved regardless of duration.

## 11. Digital vs. analog mode

`saleae_freq_check.py` runs the Saleae in **digital mode only**:

```python
automation.LogicDeviceConfiguration(
    enabled_digital_channels=[0, 1],   # digital comparator pathway
    digital_sample_rate=100_000_000,   # up to 100 MS/s on Logic 8
)
```

That is intentional:

- Digital has **10× higher sample rate** than the ADC path on Logic 8
- The threshold is a fixed 1.65 V comparator — appropriate for 3.3 V
  CMOS signals (far above threshold)
- The resulting CSV is transition-based and small (one line per edge)

Use analog mode only if you need signal integrity (overshoot, ringing,
slope) or voltage levels — neither is needed for edge-timing analysis.

## 12. Logging

Every run writes a logfile named `saleae_freq_check_<timestamp>.log`
in the current working directory (or the path given via `--log-file`).
The format matches `smoke_test.py` — both use the same `Logger` helper
from `ptp_drift_compensate_test.py`.

The log contains the full measurement-settings block + capture info +
per-channel analysis, so it can be committed or attached to bug
reports without needing the original stdout.

## 13. Common use cases

| Scenario | Command |
| -------- | ------- |
| Wiring / probe verification (any rectangle) | `python saleae_freq_check.py`  *(auto-detects nominal)* |
| Verify `blink 1` on GM only | `python saleae_freq_check.py --nominal-hz 1` |
| Characterise FOL quartz drift (precise) | `python saleae_freq_check.py --channels 1 --duration 60 --nominal-hz 1` *(after `blink 1` on FOL)* |
| Compare two boards side-by-side at 1 Hz | `python saleae_freq_check.py --channels 0 1 --duration 60 --nominal-hz 1` |
| Compare two boards at 1 kHz (`blink` default) | `python saleae_freq_check.py --channels 0 1 --duration 5 --nominal-hz 1000` |
| Measure `cyclic_fire` GPIO-level period | `python saleae_freq_check.py --duration 5 --nominal-hz 1000` *(after `cyclic_start 500` on the board)* |

## 14. Troubleshooting

**"Connection refused" on connect** — Logic 2 is not running, or the
scripting socket server is off.  Start the app and enable it in
Preferences → Developer.

**"Digital threshold … requested, but the requested device does not
support a configurable threshold voltage"** — Only an issue on the
non-Pro Logic 8.  The script does not set a threshold; if you see this
message it means you modified the device-configuration block.

**No edges detected on a channel** — Verify wiring, probe grounding,
and board power.  Use `saleae_capture_blink.py` for a simple
activity check first.

**Sample-rate rejected** — Logic 8 cannot hit 100 MS/s on many
channels at once.  Either reduce `--channels` to a single channel, or
lower `--sample-rate` (25 or 50 MS/s is still plenty for sub-kHz
signals).

**Very few edges in the log** — Short capture duration on a low
frequency: a 3 s window on a 1 Hz signal only has ~3 cycles.  Use a
longer `--duration` (60 s for 1 Hz signals is a good starting point).

**Frequency deviation much larger than expected** — Usually the
auto-detected nominal snapped to the wrong target.  Pass `--nominal-hz`
explicitly to anchor the report to the intended value.
