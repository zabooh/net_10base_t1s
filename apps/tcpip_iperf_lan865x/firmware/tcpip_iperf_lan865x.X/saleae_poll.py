#!/usr/bin/env python3
"""saleae_poll.py — continuous logic-level monitor for Ch0+Ch1
================================================================

Repeats short captures in a loop and prints the last-sampled logic
level on Ch0 and Ch1, plus the number of transitions seen during the
capture window.  A simple poor-man's logic-probe / activity monitor.

Because Logic 2's automation API has no streaming mode, each update
is one full capture cycle (~200 ms overhead on a Logic 8).  Practical
update rate: roughly 3 Hz.

Usage:
    python saleae_poll.py                       # 100 ms captures, 1 MS/s
    python saleae_poll.py --window-ms 50        # faster display
    python saleae_poll.py --window-ms 500       # see more transitions per update
    python saleae_poll.py --channels 0 1 2 3    # watch four channels
    python saleae_poll.py --once                # single reading, then exit

Ctrl+C to stop.
"""

import argparse
import csv
import shutil
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


def run_single(mgr, channels: List[int], window_s: float,
               sample_rate_hz: int, tmp_dir: Path
               ) -> Dict[int, Tuple[int, int]]:
    """Run one capture and return {channel: (last_state, transitions)}."""
    dev = automation.LogicDeviceConfiguration(
        enabled_digital_channels=channels,
        digital_sample_rate=sample_rate_hz,
    )
    cfg = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=window_s)
    )
    cap = mgr.start_capture(device_configuration=dev, capture_configuration=cfg)
    cap.wait()

    # Export into a fresh, empty sub-directory so we can pick the single
    # CSV without ambiguity.
    sub = tmp_dir / f"run_{time.perf_counter_ns()}"
    sub.mkdir(parents=True, exist_ok=True)
    cap.export_raw_data_csv(directory=str(sub), digital_channels=channels)
    cap.close()

    csv_path = next(iter(sub.glob("*.csv")), None)
    if csv_path is None:
        raise FileNotFoundError(f"No CSV exported into {sub}")

    # Walk the CSV and per channel: track transitions + remember last value.
    last:   Dict[int, int] = {c: 0 for c in channels}
    trans:  Dict[int, int] = {c: 0 for c in channels}
    prev:   Dict[int, int] = {c: -1 for c in channels}

    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        # Map channel index → column index via header names.
        ch_col: Dict[int, int] = {}
        for col_i, name in enumerate(header):
            low = name.strip().lower()
            for ch in channels:
                if (low == f"channel {ch}" or low.endswith(f" {ch}")
                        or low.endswith(f"d{ch}")):
                    ch_col[ch] = col_i
        # Fallback: channels in order start at column 1.
        for i, ch in enumerate(channels, start=1):
            ch_col.setdefault(ch, i)

        for row in reader:
            if not row:
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
                    last[ch] = v
                    continue
                if v != prev[ch]:
                    trans[ch] += 1
                    prev[ch] = v
                last[ch] = v

    # Clean up this run's temp directory so they don't pile up.
    shutil.rmtree(sub, ignore_errors=True)

    return {ch: (last[ch], trans[ch]) for ch in channels}


def render_line(ch: int, state: int, transitions: int) -> str:
    bar   = "▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇" if state == 1 else "················"
    arrow = "HI" if state == 1 else "LO"
    return f"  Ch{ch}  [{bar}]  {arrow}   transitions: {transitions:>5}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channels",   default=[0, 1], type=int, nargs="+",
                   help="digital channel indices to watch (default: 0 1)")
    p.add_argument("--window-ms",  default=100,   type=int,
                   help="capture duration per update (ms)")
    p.add_argument("--sample-rate", default=1_000_000, type=int,
                   help="digital sample rate Hz (default 1 MS/s)")
    p.add_argument("--idle-ms",    default=0,     type=int,
                   help="pause between captures (ms)")
    p.add_argument("--port",       default=10430, type=int,
                   help="Logic 2 gRPC port (default 10430)")
    p.add_argument("--once",       action="store_true",
                   help="single reading then exit")
    args = p.parse_args()

    channels   = sorted(set(args.channels))
    window_s   = args.window_ms / 1000.0
    idle_s     = args.idle_ms   / 1000.0

    print(f"Saleae poll — channels {channels}  "
          f"window {args.window_ms} ms  "
          f"@ {args.sample_rate/1e6:.1f} MS/s")
    print("Ctrl+C to stop.\n")

    tmp_root = Path(tempfile.mkdtemp(prefix="saleae_poll_"))
    try:
        mgr = automation.Manager.connect(address="127.0.0.1", port=args.port)
    except Exception as exc:
        print(f"ERROR: cannot connect to Logic 2 on port {args.port}: {exc}")
        print("Is the Logic 2 app running with the automation server enabled?")
        shutil.rmtree(tmp_root, ignore_errors=True)
        return 1

    # Reserve screen lines for the per-channel display, then rewind to
    # overwrite them each update.  One line per channel + one status line.
    num_lines = len(channels) + 1
    print("\n" * num_lines, end="")                                  # allocate
    CURSOR_UP = f"\033[{num_lines}A"                                 # move up

    iteration = 0
    t0 = time.perf_counter()
    try:
        while True:
            iteration += 1
            try:
                result = run_single(mgr, channels, window_s,
                                    args.sample_rate, tmp_root)
            except Exception as exc:
                print(f"\n[capture error] {type(exc).__name__}: {exc}")
                time.sleep(0.5)
                continue

            now = time.perf_counter() - t0
            sys.stdout.write(CURSOR_UP)
            sys.stdout.write(f"  t={now:7.2f}s  iter={iteration:>5}\033[K\n")
            for ch in channels:
                state, trans = result[ch]
                sys.stdout.write(render_line(ch, state, trans) + "\033[K\n")
            sys.stdout.flush()

            if args.once:
                break
            if idle_s > 0:
                time.sleep(idle_s)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            mgr.close()
        except Exception:
            pass
        shutil.rmtree(tmp_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
