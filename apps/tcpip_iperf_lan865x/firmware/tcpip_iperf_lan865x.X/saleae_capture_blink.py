#!/usr/bin/env python3
"""saleae_capture_blink.py — measure the PD10 auto-blink on 2 boards
====================================================================

Captures a few seconds on Saleae Ch0 + Ch1 and reports, per channel,
whether the signal is toggling and what the measured period / duty
cycle look like.  No serial-port / PTP / firmware-CLI interaction —
pure passive observation, ideal for verifying the wiring after a
fresh flash of the auto-blink firmware.

Expected on a correctly wired board with the current auto-blink
firmware (toggles every 500 ms in APP_Tasks):

    Ch0  n_rising=5  n_falling=5  period=1.000 s  duty=50.0 %  -> TOGGLING
    Ch1  n_rising=5  n_falling=5  period=1.000 s  duty=50.0 %  -> TOGGLING

A `STATIC` verdict means the probe sees no transitions in the capture
window — check wiring, ground, board power.

Usage:
    python saleae_capture_blink.py                        # 5 s, 1 MS/s, Ch0+Ch1
    python saleae_capture_blink.py --duration 10          # longer capture
    python saleae_capture_blink.py --channels 0 1 2 3     # more channels
    python saleae_capture_blink.py --sample-rate 10000000 # 10 MS/s
    python saleae_capture_blink.py --keep-csv             # don't delete the CSV

Prerequisites:  Logic 2 running with the automation server enabled.
"""

import argparse
import csv
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


def parse_edges(csv_path: Path, channels: List[int]
                ) -> Tuple[Dict[int, List[float]], Dict[int, List[float]]]:
    """Return ({ch: rising_times_s}, {ch: falling_times_s})."""
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


def robust_median(xs: List[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def channel_stats(rising: List[float], falling: List[float]) -> Dict[str, float]:
    """Return period_s, duty, etc. for one channel."""
    n_r = len(rising)
    n_f = len(falling)
    period = 0.0
    if n_r >= 2:
        period = robust_median([rising[i + 1] - rising[i]
                                for i in range(n_r - 1)])
    # Duty cycle: average (falling_time - preceding_rising_time) / period.
    duty = 0.0
    if n_r >= 1 and n_f >= 1 and period > 0:
        highs: List[float] = []
        ri = 0
        for tf in falling:
            while ri + 1 < n_r and rising[ri + 1] < tf:
                ri += 1
            if rising[ri] < tf:
                highs.append(tf - rising[ri])
        if highs:
            duty = robust_median(highs) / period
    return {
        "n_rising":  n_r,
        "n_falling": n_f,
        "period_s":  period,
        "duty":      duty,
    }


def fmt_period(s: float) -> str:
    if s == 0:    return "—"
    if s < 1e-6:  return f"{s*1e9:.1f} ns"
    if s < 1e-3:  return f"{s*1e6:.3f} µs"
    if s < 1.0:   return f"{s*1e3:.3f} ms"
    return f"{s:.3f} s"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channels",    default=[0, 1], type=int, nargs="+",
                   help="digital channels to watch (default: 0 1)")
    p.add_argument("--duration",    default=5.0,   type=float,
                   help="capture duration in seconds (default 5.0)")
    p.add_argument("--sample-rate", default=1_000_000, type=int,
                   help="digital sample rate in Hz (default 1 MS/s)")
    p.add_argument("--port",        default=10430, type=int,
                   help="Logic 2 gRPC port (default 10430)")
    p.add_argument("--min-edges",   default=2,     type=int,
                   help="edges required before reporting TOGGLING")
    p.add_argument("--out-dir",     default=None,
                   help="where to keep the CSV (default: temp, deleted on exit)")
    p.add_argument("--keep-csv",    action="store_true",
                   help="don't delete the exported CSV after analysis")
    args = p.parse_args()

    channels = sorted(set(args.channels))

    print("=" * 62)
    print(" Saleae blink capture")
    print("=" * 62)
    print(f"  channels       : {channels}")
    print(f"  duration       : {args.duration:.1f} s")
    print(f"  sample rate    : {args.sample_rate/1e6:.1f} MS/s  "
          f"(resolution {1e9/args.sample_rate:.0f} ns)")

    try:
        mgr = automation.Manager.connect(address="127.0.0.1", port=args.port)
    except Exception as exc:
        print(f"\nERROR: cannot connect to Logic 2 on port {args.port}: {exc}")
        print("Is the Logic 2 app running with the automation server enabled?")
        return 1

    using_tmp = args.out_dir is None
    out_dir = Path(args.out_dir or tempfile.mkdtemp(prefix="saleae_blink_"))
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

        t0 = time.perf_counter()
        print(f"\n  capturing ...", end="", flush=True)
        cap = mgr.start_capture(device_configuration=dev_cfg,
                                capture_configuration=cap_cfg)
        cap.wait()
        cap.export_raw_data_csv(directory=str(out_dir),
                                digital_channels=channels)
        cap.close()
        print(f" done ({time.perf_counter()-t0:.1f} s).")

        csv_path = next(iter(out_dir.glob("*.csv")), None)
        if csv_path is None:
            print(f"ERROR: no CSV exported to {out_dir}")
            return 1
        print(f"  CSV            : {csv_path} "
              f"({csv_path.stat().st_size/1024:.1f} KiB)")

        rising, falling = parse_edges(csv_path, channels)

        print("")
        print("  Per-channel result:")
        print("  " + "-" * 60)
        any_toggling = False
        for ch in channels:
            st = channel_stats(rising[ch], falling[ch])
            verdict = "TOGGLING" if (st["n_rising"] + st["n_falling"]
                                     >= args.min_edges) else "STATIC"
            if verdict == "TOGGLING":
                any_toggling = True
            period = fmt_period(st["period_s"])
            duty   = f"{st['duty']*100:5.1f} %" if st["duty"] > 0 else "  —  "
            print(f"  Ch{ch}  n_rising={st['n_rising']:>4}  "
                  f"n_falling={st['n_falling']:>4}  "
                  f"period={period:>10}  duty={duty}  -> {verdict}")
        print("  " + "-" * 60)

        if not any_toggling:
            print("\n  No channel showed activity — check probe wiring, "
                  "ground, board power.")
            rc = 2

    finally:
        try:
            mgr.close()
        except Exception:
            pass
        if using_tmp and not args.keep_csv:
            shutil.rmtree(out_dir, ignore_errors=True)
        elif args.keep_csv:
            print(f"\n  (kept CSV directory: {out_dir})")

    return rc


if __name__ == "__main__":
    sys.exit(main())
