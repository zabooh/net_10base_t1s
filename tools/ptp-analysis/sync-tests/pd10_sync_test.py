#!/usr/bin/env python3
"""pd10_sync_test.py — pure Saleae cross-board PD10 synchronicity test
=======================================================================

Focused measurement of the TC1-ISR cyclic_fire backend's precision.
Both boards must already be PTP-synced (LED2 solid).  No serial
involvement — only Saleae captures the two PD10 mirror signals and the
script reports per-edge deltas plus distribution statistics.

Default capture: 30 s at 50 MS/s (20 ns resolution) so the achievable
precision floor (~200 ns NVIC entry + ~50 ns ISR prologue + drift
filter residual) is actually visible above the sampling resolution.

Usage:
    python pd10_sync_test.py                       # default 30 s @ 50 MS/s
    python pd10_sync_test.py --duration-s 10       # quicker run
    python pd10_sync_test.py --label tc1_isr       # tag the output dir
"""

import argparse
import csv
import datetime
import statistics
import sys
import time
from pathlib import Path

from cyclic_fire_hw_test import (                       # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from ptp_drift_compensate_test import Logger             # noqa: E402


def banner(title, log):
    log.info("")
    log.info("=" * 72)
    log.info(f"  {title}")
    log.info("=" * 72)


def prompt(text, log):
    log.info("")
    log.info(f">>> {text}")
    t0 = time.monotonic()
    input("    [press Enter to continue]")
    log.info(f"    (acknowledged after {time.monotonic()-t0:.1f} s)")


def cross_board_delta_us(rising_a, rising_b):
    """For each rising edge on Ch0 find the closest rising edge on Ch1
    (within ±300 ms bracket = half a 1 Hz cycle), return signed delta in µs."""
    if not rising_a or not rising_b:
        return []
    bracket = 0.300
    deltas = []
    j = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        cand = []
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = rising_b[k] - ta
                if abs(d) <= bracket:
                    cand.append(d)
        if cand:
            deltas.append(min(cand, key=abs) * 1e6)
    return deltas


def percentile(sorted_values, pct):
    if not sorted_values:
        return float("nan")
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def mad(values):
    if not values:
        return float("nan")
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration-s", type=float, default=30.0)
    p.add_argument("--sample-rate", type=int, default=50_000_000,
                   help="Saleae sample rate (default 50 MS/s = 20 ns res)")
    p.add_argument("--label", default="",
                   help="optional label appended to the output dir name")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--no-prompt", action="store_true",
                   help="skip the operator-readiness prompt")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.label}" if args.label else ""
    out_dir = Path(args.out_dir or f"pd10_sync_{ts}{suffix}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 cross-board synchronicity test", log)
    log.info(f"  Capture     : {args.duration_s:.1f} s")
    log.info(f"  Sample rate : {args.sample_rate/1_000_000:.0f} MS/s "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  Saleae chs  : Ch0 = Board A PD10, Ch1 = Board B PD10")
    log.info(f"  Output      : {out_dir.resolve()}")
    log.info(f"  Log         : {log_path.resolve()}")

    if not args.no_prompt:
        log.info("")
        log.info("  Prereqs:")
        log.info("    - Both boards flashed with iperf-payload-test firmware")
        log.info("    - Saleae Logic 2 running, two digital channels enabled")
        log.info("    - Both boards in DEMO_SYNCED state (LED2 solid on both)")
        prompt("Confirm boards are SYNCED and Saleae is wired", log)

    banner("Capture", log)
    mgr, cap = start_saleae_capture(args.sample_rate, args.duration_s, log)
    cap.wait()
    csv_path = export_capture_csv(cap, out_dir, log)
    cap.close()

    banner("Edge analysis", log)
    rising, falling = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  Board A rising edges: {len(ra)}")
    log.info(f"  Board B rising edges: {len(rb)}")
    log.info(f"  expected ≈ {int(args.duration_s)} per channel  "
             f"(LED1 / PD10 toggles every 500 ms → 1 rising/sec)")

    deltas = cross_board_delta_us(ra, rb)
    if not deltas:
        log.info("  ERROR: no paired rising edges found.")
        return 2

    deltas_sorted = sorted(deltas)
    abs_deltas = [abs(d) for d in deltas_sorted]

    log.info("")
    log.info(f"  n           : {len(deltas)}")
    log.info(f"  min         : {deltas_sorted[0]:+10.3f} µs")
    log.info(f"  p10         : {percentile(deltas_sorted, 10):+10.3f} µs")
    log.info(f"  p25         : {percentile(deltas_sorted, 25):+10.3f} µs")
    log.info(f"  median      : {statistics.median(deltas):+10.3f} µs")
    log.info(f"  p75         : {percentile(deltas_sorted, 75):+10.3f} µs")
    log.info(f"  p90         : {percentile(deltas_sorted, 90):+10.3f} µs")
    log.info(f"  p99         : {percentile(deltas_sorted, 99):+10.3f} µs")
    log.info(f"  max         : {deltas_sorted[-1]:+10.3f} µs")
    log.info(f"  spread      : {deltas_sorted[-1] - deltas_sorted[0]:10.3f} µs")
    log.info(f"  MAD (robust): {mad(deltas):10.3f} µs")
    log.info(f"  |median|+MAD: {abs(statistics.median(deltas)) + mad(deltas):10.3f} µs")
    log.info(f"  |max|       : {max(abs_deltas):10.3f} µs")

    # Per-edge CSV for offline plotting / regression history
    csv_out = out_dir / "deltas_us.csv"
    with open(csv_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_index", "t_a_s", "delta_us"])
        for i, (ta, d) in enumerate(zip(ra, deltas)):
            w.writerow([i, f"{ta:.9f}", f"{d:+.4f}"])
    log.info("")
    log.info(f"  Per-edge CSV: {csv_out}")

    # Summary CSV (one-line) for cross-run comparison (e.g. tfuture vs ISR)
    summary_csv = out_dir / "summary.csv"
    with open(summary_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "n", "median_us", "mad_us", "p99_us",
                    "max_abs_us", "spread_us"])
        w.writerow([args.label or "default", len(deltas),
                    f"{statistics.median(deltas):+.3f}",
                    f"{mad(deltas):.3f}",
                    f"{percentile(deltas_sorted, 99):+.3f}",
                    f"{max(abs_deltas):.3f}",
                    f"{deltas_sorted[-1] - deltas_sorted[0]:.3f}"])
    log.info(f"  Summary CSV : {summary_csv}")
    log.info("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
