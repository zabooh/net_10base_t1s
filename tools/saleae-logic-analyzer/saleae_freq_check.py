#!/usr/bin/env python3
"""saleae_freq_check.py — rectangle-signal frequency + phase characterisation
==============================================================================

Captures with the highest practical sample rate on the Saleae Logic 8
and reports per-channel statistics on a rectangular signal:

  - measured frequency + ppm deviation from a nominal target
  - period jitter (median + MAD + robust stdev)
  - high-phase duration
  - low-phase duration
  - duty-cycle deviation from 50 %

Defaults: single channel (Ch0), 100 MS/s → 10 ns edge resolution.
The width-measurement uncertainty from quantisation alone is therefore
≤ 2 samples ≈ 20 ns; anything beyond that is real jitter (firmware
main-loop latency, clock jitter, etc.).

If --nominal-hz is not supplied, the script auto-snaps the measured
frequency to the nearest "nice" value of the form N × 10^k with
N ∈ {1, 2, 2.5, 5, 10} so a ppm deviation is always reported.

Usage:
    python saleae_freq_check.py                         # Ch0, 3 s, 100 MS/s
    python saleae_freq_check.py --channels 0 1          # both channels
    python saleae_freq_check.py --duration 60           # longer sample
    python saleae_freq_check.py --sample-rate 50000000  # 50 MS/s
    python saleae_freq_check.py --nominal-hz 1000       # explicit nominal
    python saleae_freq_check.py --histogram             # add histogram
    python saleae_freq_check.py --keep-csv              # retain the CSV

Logic 8 can't do 100 MS/s on many channels at once.  If the requested
configuration is rejected, drop --sample-rate or reduce --channels.
"""

import argparse
import csv
import datetime
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from saleae import automation
except ImportError:
    print("ERROR: logic2-automation not installed.  Run: pip install logic2-automation")
    sys.exit(1)

# Reuse the Logger class already used by smoke_test.py / the PTP tests so
# log format is consistent across the whole test suite.
from ptp_drift_compensate_test import Logger  # noqa: E402


# ---------------------------------------------------------------------------
# CSV parsing — transition-based export from Logic 2
# ---------------------------------------------------------------------------

def parse_edges(csv_path: Path, channels: List[int]
                ) -> Tuple[Dict[int, List[float]], Dict[int, List[float]]]:
    rising:  Dict[int, List[float]] = {c: [] for c in channels}
    falling: Dict[int, List[float]] = {c: [] for c in channels}
    prev:    Dict[int, int]         = {c: -1 for c in channels}
    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        ch_col: Dict[int, int] = {}
        for col_i, name in enumerate(header):
            low = name.strip().lower()
            for ch in channels:
                if (low == f"channel {ch}" or low.endswith(f" {ch}")
                        or low.endswith(f"d{ch}")):
                    ch_col[ch] = col_i
        for i, ch in enumerate(channels, start=1):
            ch_col.setdefault(ch, i)
        for row in reader:
            if not row:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue
            for ch, col in ch_col.items():
                if col >= len(row):
                    continue
                try:
                    v = int(float(row[col]))
                except ValueError:
                    continue
                if prev[ch] == -1:
                    prev[ch] = v
                    continue
                if v != prev[ch]:
                    (rising if v == 1 else falling)[ch].append(t)
                    prev[ch] = v
    return rising, falling


# ---------------------------------------------------------------------------
# High-phase extraction + stats
# ---------------------------------------------------------------------------

def high_phase_durations(rising: List[float], falling: List[float]) -> List[float]:
    """For each rising edge, find the next falling edge on the same channel
    and return the delta.  Drops any trailing rising without a following
    falling (likely cut off at capture end)."""
    out: List[float] = []
    fi = 0
    for tr in rising:
        # advance `fi` to the first falling edge AFTER this rising
        while fi < len(falling) and falling[fi] <= tr:
            fi += 1
        if fi >= len(falling):
            break
        out.append(falling[fi] - tr)
        fi += 1    # this falling is consumed by this high phase
    return out


def low_phase_durations(rising: List[float], falling: List[float]) -> List[float]:
    """For each falling edge, find the next rising edge on the same channel
    and return the delta (low-phase width)."""
    out: List[float] = []
    ri = 0
    for tf in falling:
        while ri < len(rising) and rising[ri] <= tf:
            ri += 1
        if ri >= len(rising):
            break
        out.append(rising[ri] - tf)
        ri += 1
    return out


def period_durations(rising: List[float]) -> List[float]:
    """Rising-to-rising gaps = full rectangle period, one sample per cycle.
    Uses same-polarity edges so the quantisation bias cancels symmetrically."""
    return [rising[i + 1] - rising[i] for i in range(len(rising) - 1)]


def stats(xs: List[float]) -> Dict[str, float]:
    if not xs:
        return {"n": 0}
    m    = statistics.median(xs)
    mad  = statistics.median(abs(x - m) for x in xs)
    mean = statistics.mean(xs)
    stdev = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return {
        "n":      len(xs),
        "min":    min(xs),
        "max":    max(xs),
        "mean":   mean,
        "stdev":  stdev,
        "median": m,
        "mad":    mad,
        "robust_stdev": 1.4826 * mad,
        "range":  max(xs) - min(xs),
    }


def fmt_t(s: float) -> str:
    """Smart SI formatting with enough precision to show the sample-rate limit."""
    if s == 0:    return "0"
    a = abs(s)
    if a < 1e-6:  return f"{s*1e9:+.1f} ns"     if s < 0 else f"{s*1e9:.1f} ns"
    if a < 1e-3:  return f"{s*1e6:+.4f} µs"     if s < 0 else f"{s*1e6:.4f} µs"
    if a < 1.0:   return f"{s*1e3:+.6f} ms"     if s < 0 else f"{s*1e3:.6f} ms"
    return f"{s:+.9f} s" if s < 0 else f"{s:.9f} s"


def fmt_hz(hz: float) -> str:
    if hz == 0:     return "0 Hz"
    a = abs(hz)
    if a >= 1e6:    return f"{hz/1e6:.6f} MHz"
    if a >= 1e3:    return f"{hz/1e3:.6f} kHz"
    return f"{hz:.6f} Hz"


def fmt_ppm(ppm: float) -> str:
    return f"{ppm:+.3f} ppm"


def auto_nominal_hz(measured_hz: float) -> float:
    """Snap the measured frequency to the nearest "nice" nominal value of
    the form N × 10^k with N ∈ {1, 2, 2.5, 5, 10}.  Used when the user
    didn't provide --nominal-hz, so we can still report a ppm deviation
    against a plausible design target.

    Examples:
       1.001047 Hz → 1 Hz
       1000.523  Hz → 1000 Hz
       4998.3    Hz → 5000 Hz
       243.1     Hz → 250 Hz
    """
    import math
    if measured_hz <= 0:
        return 0.0
    magnitude = 10.0 ** math.floor(math.log10(measured_hz))
    mantissa  = measured_hz / magnitude
    # In log space, 1/2/2.5/5/10 is more natural; pick the closest in ratio.
    candidates = [1.0, 2.0, 2.5, 5.0, 10.0]
    best = min(candidates, key=lambda c: abs(math.log(c / mantissa)))
    return best * magnitude


def log_stats(log: Logger, st: Dict[str, float], label: str):
    if st["n"] == 0:
        log.info(f"  {label}: no high phases detected")
        return
    log.info(f"  {label}:")
    log.info(f"    n             = {st['n']}")
    log.info(f"    min           = {fmt_t(st['min'])}")
    log.info(f"    max           = {fmt_t(st['max'])}")
    log.info(f"    range         = {fmt_t(st['range'])}")
    log.info(f"    mean          = {fmt_t(st['mean'])}")
    log.info(f"    stdev         = {fmt_t(st['stdev'])}")
    log.info(f"    median        = {fmt_t(st['median'])}")
    log.info(f"    MAD           = {fmt_t(st['mad'])}")
    log.info(f"    robust stdev  = {fmt_t(st['robust_stdev'])}  (= 1.4826 × MAD)")


def log_histogram(log: Logger, xs: List[float], bins: int = 20):
    if len(xs) < 2:
        return
    lo, hi = min(xs), max(xs)
    if hi == lo:
        log.info(f"    (all samples identical at {fmt_t(lo)})")
        return
    w = (hi - lo) / bins
    counts = [0] * bins
    for x in xs:
        i = min(int((x - lo) / w), bins - 1)
        counts[i] += 1
    mx = max(counts) or 1
    log.info(f"    histogram [{bins} bins from {fmt_t(lo)} to {fmt_t(hi)}]:")
    for i, c in enumerate(counts):
        bin_lo = lo + i * w
        bar = "#" * int(40 * c / mx)
        log.info(f"    {fmt_t(bin_lo):>14}  {c:>5}  {bar}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channels",    default=[0],   type=int, nargs="+",
                   help="digital channels to watch (default: 0)")
    p.add_argument("--duration",    default=3.0,   type=float,
                   help="capture duration in seconds (default 3.0)")
    p.add_argument("--sample-rate", default=100_000_000, type=int,
                   help="digital sample rate in Hz (default 100 MS/s)")
    p.add_argument("--port",        default=10430, type=int,
                   help="Logic 2 gRPC port (default 10430)")
    p.add_argument("--nominal-hz",  default=None, type=float,
                   help="nominal rectangle frequency — enables ppm deviation "
                        "reporting (e.g. 1 for the 1 Hz auto-blink, 1000 for "
                        "cyclic_fire at period_us=500)")
    p.add_argument("--histogram",   action="store_true",
                   help="print a per-channel histogram of high-phase durations")
    p.add_argument("--out-dir",     default=None)
    p.add_argument("--keep-csv",    action="store_true")
    p.add_argument("--log-file",    default=None,
                   help="log file path (default: saleae_freq_check_<ts>.log)")
    p.add_argument("--verbose",     action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Logger(log_file=args.log_file or f"saleae_freq_check_{ts}.log",
                 verbose=args.verbose)

    channels          = sorted(set(args.channels))
    sample_period_s   = 1.0 / args.sample_rate
    samples_per_ch    = int(args.sample_rate * args.duration)
    total_samples     = samples_per_ch * len(channels)

    log.info("=" * 62)
    log.info(" Saleae high-phase measurement")
    log.info("=" * 62)
    log.info("  Measurement settings:")
    log.info(f"    channels          : {channels}  ({len(channels)} enabled)")
    log.info(f"    sample rate       : {args.sample_rate:>15,} Hz  "
             f"(= {args.sample_rate/1e6:.3f} MS/s)")
    log.info(f"    duration          : {args.duration:>15.3f} s")
    log.info(f"    samples / channel : {samples_per_ch:>15,}  "
             f"(= sample_rate × duration)")
    log.info(f"    total samples     : {total_samples:>15,}  "
             f"(= samples/ch × {len(channels)} channels)")
    log.info(f"    edge resolution   : {fmt_t(sample_period_s):>15}  "
             f"(= 1 / sample_rate)")
    log.info(f"    width quantisation: {fmt_t(2 * sample_period_s):>15}  "
             f"(= 2 × edge resolution, rising + falling)")
    if args.nominal_hz is not None:
        log.info(f"    nominal frequency : {fmt_hz(args.nominal_hz):>15}  "
                 f"(for ppm deviation report)")

    try:
        mgr = automation.Manager.connect(address="127.0.0.1", port=args.port)
    except Exception as exc:
        log.info(f"\nERROR: cannot connect to Logic 2 on port {args.port}: {exc}")
        log.info("Is Logic 2 running with the automation server enabled?")
        log.close()
        return 1

    using_tmp = args.out_dir is None
    out_dir = Path(args.out_dir or tempfile.mkdtemp(prefix="saleae_freq_check_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rc = 0
    try:
        dev_cfg = automation.LogicDeviceConfiguration(
            enabled_digital_channels=channels,
            digital_sample_rate=args.sample_rate,
        )
        cap_cfg = automation.CaptureConfiguration(
            capture_mode=automation.TimedCaptureMode(duration_seconds=args.duration)
        )
        log.info("")
        log.info(f"  capturing ...")
        t0 = time.perf_counter()
        cap = mgr.start_capture(device_configuration=dev_cfg,
                                capture_configuration=cap_cfg)
        cap.wait()
        cap.export_raw_data_csv(directory=str(out_dir),
                                digital_channels=channels)
        cap.close()
        log.info(f"  capture done ({time.perf_counter()-t0:.1f} s)")

        csv_path = next(iter(out_dir.glob("*.csv")), None)
        if csv_path is None:
            log.info(f"ERROR: no CSV exported to {out_dir}")
            return 1
        log.info(f"  CSV             : {csv_path} "
                 f"({csv_path.stat().st_size/1024:.1f} KiB)")

        rising, falling = parse_edges(csv_path, channels)

        log.info("")
        log.info("=" * 62)
        for ch in channels:
            highs   = high_phase_durations(rising[ch], falling[ch])
            lows    = low_phase_durations(rising[ch], falling[ch])
            periods = period_durations(rising[ch])

            log.info("")
            log.info(f"Channel {ch}  —  {len(rising[ch])} rising / "
                     f"{len(falling[ch])} falling edges")

            # Period + frequency (derived from rising-to-rising gaps).
            ps = stats(periods)
            log_stats(log, ps, "Period (rising-to-rising)")
            if ps.get("n", 0) >= 1 and ps["median"] > 0:
                freq_hz = 1.0 / ps["median"]
                log.info(f"    frequency     = {fmt_hz(freq_hz)}")

                # Always report deviation: use user-provided nominal if given,
                # otherwise auto-snap the measured frequency to the nearest
                # "nice" target (1, 2, 2.5, 5 × 10^k).
                if args.nominal_hz is not None and args.nominal_hz > 0:
                    nominal   = args.nominal_hz
                    nom_label = "user-supplied"
                else:
                    nominal   = auto_nominal_hz(freq_hz)
                    nom_label = "auto-detected"
                if nominal > 0:
                    dev_ppm = (freq_hz - nominal) / nominal * 1e6
                    log.info(f"    vs nominal    = "
                             f"{fmt_hz(nominal)}   "
                             f"deviation = {fmt_ppm(dev_ppm)}  ({nom_label})")

                # Jitter as a fraction of the period (also in ppm).
                jitter_ppm = ps["robust_stdev"] / ps["median"] * 1e6
                log.info(f"    period jitter = {fmt_t(ps['robust_stdev'])} "
                         f"(= {fmt_ppm(jitter_ppm)} of period)")

            # High phase.
            hs = stats(highs)
            log.info("")
            log_stats(log, hs, "High phase (rising-to-next-falling)")

            # Low phase.
            ls = stats(lows)
            log.info("")
            log_stats(log, ls, "Low phase (falling-to-next-rising)")

            # Duty cycle derived from medians (both robust against single-cycle outliers).
            if (hs.get("n", 0) >= 1 and ps.get("n", 0) >= 1 and ps["median"] > 0):
                duty = hs["median"] / ps["median"]
                log.info("")
                log.info(f"  Duty cycle    = {duty*100:.4f} %")
                log.info(f"    deviation   = {(duty-0.5)*100:+.4f} pp from 50 %  "
                         f"(= {(duty-0.5)*1e6:+.1f} ppm)")

            if args.histogram and hs.get("n", 0) >= 2:
                log.info("")
                log.info("  High-phase histogram:")
                log_histogram(log, highs)
        log.info("=" * 62)

    finally:
        try:
            mgr.close()
        except Exception:
            pass
        if using_tmp and not args.keep_csv:
            shutil.rmtree(out_dir, ignore_errors=True)
        elif args.keep_csv:
            log.info(f"\n  (kept CSV directory: {out_dir})")
        log.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
