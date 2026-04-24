#!/usr/bin/env python3
"""standalone_demo_test.py — guided demo + Saleae sync verification
====================================================================

Walks the operator through the standalone PTP-sync demo on two boards
and uses Saleae Logic 2 to check whether the LED1 visible blink (mirrored
onto PD10 by standalone_demo.c) is "visually synchronous" across both
boards after the PTP lock.

The point of the demo:
  - Both boards boot in PTP-OFF mode and start a 500 ms decimated
    rectangle on LED1 (PC21) and PD10.  Because the two SAME54 crystals
    differ by ~100 ppm, the two boards' rectangles drift apart by
    ~100 µs/s — visible to the eye after a few seconds.
  - Press SW1 on one board → that board becomes PTP follower (LED2 blinks
    at 250 ms during sync, then stays solid on FINE).
  - Press SW2 on the other board → that board becomes PTP master (LED2
    blinks for ~2 s, then stays solid).
  - Once both LED2 are solid, PTP_CLOCK is locked and LED1 / PD10
    re-aligns to GM's wallclock — the rectangles snap back into lock-step
    and stop drifting.

The Saleae step here measures the after-lock state: cross-board edge
delta on the PD10 mirror should be << 50 ms (the human-visual threshold)
and ideally well under 1 ms (PTP sub-µs sync × 250 µs decimator floor).

Usage:
    python standalone_demo_test.py                     # default 6 s capture
    python standalone_demo_test.py --duration-s 12     # longer for more samples
    python standalone_demo_test.py --threshold-ms 20   # tighter PASS gate
"""

import argparse
import csv
import datetime
import re
import statistics
import sys
import time
from pathlib import Path

import serial

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ptp-analysis", "ptp-drift-tests"))

# Re-use the helpers that are already battle-tested in cyclic_fire_hw_test
from cyclic_fire_hw_test import (                                    # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
# Re-use the Tee-style logger + serial helpers used across the other tests
from ptp_drift_compensate_test import (                              # noqa: E402
    Logger, open_port, send_command,
)

DEFAULT_A_PORT = "COM10"   # "Board A" on Saleae Ch0 — set by --a-port
DEFAULT_B_PORT = "COM8"    # "Board B" on Saleae Ch1 — set by --b-port


# ---------------------------------------------------------------------------
# CLI debug dump — runs a fixed set of diagnostic commands on BOTH boards
# at critical points in the demo flow so we can see later from the log
# whether PTP_CLOCK jumped, whether cyclic_fire is actually running, etc.
# ---------------------------------------------------------------------------

DEBUG_COMMANDS = [
    "clk_get",
    "ptp_status",
    "ptp_mode",
    "cyclic_status",
    "tfuture_status",
]

def dump_board_state(ser_a, ser_b, label: str, log) -> None:
    log.info("")
    log.info(f"  --- CLI dump [{label}] ---")
    if ser_a is None or ser_b is None:
        log.info("      (serial ports not opened — pass --a-port and --b-port "
                 "to enable CLI dumps)")
        return
    for cmd in DEBUG_COMMANDS:
        for tag, ser in (("A", ser_a), ("B", ser_b)):
            try:
                resp = send_command(ser, cmd, 1.5, None)
            except Exception as exc:
                resp = f"[send_command failed: {exc}]"
            # Keep only the reply body — strip the echoed command and the
            # trailing prompt; log each non-empty response line prefixed
            # with "A"/"B" for side-by-side comparison in the log.
            for line in resp.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s == cmd or s.endswith(cmd):
                    continue
                if s.startswith(">"):
                    continue
                log.info(f"      [{tag}] {cmd:16s} | {s}")


# ---------------------------------------------------------------------------
# User-interaction helpers — all prose and results go through the shared
# Logger so the run is captured to disk for later post-hoc analysis.  Only
# the blocking input() read itself is kept on stdout (it's an interactive
# prompt — the log records what was asked and when the user acknowledged).
# ---------------------------------------------------------------------------

def banner(title: str, log) -> None:
    log.info("")
    log.info("=" * 72)
    log.info(f"  {title}")
    log.info("=" * 72)


def prompt(text: str, log) -> None:
    """Block until the user presses Enter, with the given instruction shown."""
    log.info("")
    log.info(f">>> {text}")
    t0 = time.monotonic()
    input("    [press Enter to continue]")
    log.info(f"    (acknowledged after {time.monotonic()-t0:.1f} s)")


# ---------------------------------------------------------------------------
# Edge-delta analysis
# ---------------------------------------------------------------------------

def median_or_nan(values):
    return statistics.median(values) if values else float("nan")


def cross_board_delta_ms(rising_a, rising_b):
    """For each rising edge on channel A, find the closest rising edge on
    channel B (within ±half a period) and return the signed delta in ms.
    Drops edges with no neighbour inside the bracket."""
    if not rising_a or not rising_b:
        return []
    # Use ±300 ms bracket: anything outside that means the boards have
    # drifted by more than half a 1 Hz cycle and pairing isn't meaningful.
    bracket = 0.300
    deltas_ms = []
    j = 0
    for ta in rising_a:
        # Advance j while channel B's edge is well below ta - bracket
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        candidates = []
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = rising_b[k] - ta
                if abs(d) <= bracket:
                    candidates.append(d)
        if candidates:
            deltas_ms.append(min(candidates, key=abs) * 1000.0)
    return deltas_ms


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration-s",     type=float, default=6.0,
                   help="Saleae capture duration AFTER sync (default 6 s)")
    p.add_argument("--free-duration-s", type=float, default=4.0,
                   help="Saleae capture duration BEFORE sync (default 4 s)")
    p.add_argument("--sample-rate",    type=int,   default=1_000_000,
                   help="Saleae sample rate, samples/s (default 1 MS/s)")
    p.add_argument("--threshold-ms",   type=float, default=50.0,
                   help="median |delta| gate for 'visually synchronous' (default 50 ms)")
    p.add_argument("--out-dir",        type=str,   default=None)
    p.add_argument("--skip-free",      action="store_true",
                   help="Skip the pre-sync free-run capture (only measure post-sync)")
    p.add_argument("--a-port",  default=DEFAULT_A_PORT,
                   help="serial port of the board wired to Saleae Ch0 "
                        "(default %(default)s)")
    p.add_argument("--b-port",  default=DEFAULT_B_PORT,
                   help="serial port of the board wired to Saleae Ch1 "
                        "(default %(default)s)")
    p.add_argument("--no-cli",  action="store_true",
                   help="skip the serial CLI debug dumps (Saleae only)")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"standalone_demo_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tee everything through a Logger that writes to both stdout and a
    # run log inside the output directory — matches the file layout of
    # cyclic_fire_hw_test and smoke_test (run_<ts>.log alongside the
    # captured Saleae data) so the operator can attach the log file for
    # post-hoc analysis.
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("standalone-demo cross-board sync test (Saleae-verified)", log)
    log.info(f"  Capture:        {args.duration_s:.1f} s post-sync, "
             f"{args.free_duration_s:.1f} s free-run")
    log.info(f"  Sample rate:    {args.sample_rate/1_000_000:.1f} MS/s "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  Threshold:      median |delta| < {args.threshold_ms:.1f} ms")
    log.info(f"  Saleae channels: Ch0 = Board A PD10, Ch1 = Board B PD10")
    log.info(f"  Output dir:     {out_dir.resolve()}")
    log.info(f"  Log file:       {log_path.resolve()}")

    # -----------------------------------------------------------------------
    # Open serial ports for CLI debug dumps (optional but strongly
    # recommended — these are what let us see AFTER THE FACT why Board B
    # had 0 edges in the failing runs: cyclic_status running=no, big jump
    # in clk_get, etc.).
    # -----------------------------------------------------------------------
    ser_a = ser_b = None
    if not args.no_cli:
        try:
            ser_a = open_port(args.a_port)
            ser_b = open_port(args.b_port)
            log.info(f"  Serial A:       {args.a_port} (Saleae Ch0)")
            log.info(f"  Serial B:       {args.b_port} (Saleae Ch1)")
        except Exception as exc:
            log.info(f"  WARN: could not open serial ports ({exc}) — continuing "
                     f"without CLI debug dumps.  Pass --no-cli to suppress this.")
            ser_a = ser_b = None

    # -----------------------------------------------------------------------
    # Step 0 — physical setup
    # -----------------------------------------------------------------------
    banner("Step 0 — Setup", log)
    for ln in [
        "  Make sure of the following BEFORE proceeding:",
        "    1. Both boards are flashed with the standalone-demo firmware",
        "       (this branch's firmware, currently active).",
        "    2. Saleae Logic 2 is running on this PC and connected to the Logic",
        "       device with at least 2 digital channels enabled (Ch0 + Ch1).",
        "    3. Saleae Ch0 → Board A PD10 pin, GND to GND.",
        "       Saleae Ch1 → Board B PD10 pin, GND to GND.",
        "       PD10 is \"GPIO1\" on the EXT1 Xplained-Pro header (pin 5 from top).",
        "    4. Both boards have just been power-cycled (or reset) so they are in",
        "       the FREE state with LED1 visibly drifting between boards.",
    ]:
        log.info(ln)
    prompt("Confirm setup is complete and both LED1 are blinking (drifting apart)", log)
    dump_board_state(ser_a, ser_b, "after setup — FREE state",  log)

    # -----------------------------------------------------------------------
    # Step 1 — capture FREE-RUN baseline (LED1 on both boards drifts)
    # -----------------------------------------------------------------------
    free_deltas_ms = []
    if not args.skip_free:
        banner("Step 1 — Capture FREE-RUN baseline (no PTP)", log)
        log.info("  In this capture both boards run on independent crystals.")
        log.info("  Their LED1 edges drift apart at ~100 µs/s crystal mismatch.")
        log.info(f"  Capturing {args.free_duration_s:.0f} s — cross-board deltas "
                 f"SHOULD vary.")
        prompt("Ready to start the free-run capture (do not press SW1/SW2 yet!)", log)
        mgr_f, cap_f = start_saleae_capture(args.sample_rate,
                                            args.free_duration_s, log)
        cap_f.wait()
        csv_f = export_capture_csv(cap_f, out_dir / "free", log)
        cap_f.close()

        rising_f, _ = parse_edges(csv_f)
        ra, rb = rising_f.get(0, []), rising_f.get(1, [])
        log.info(f"  Free-run edges: Board A = {len(ra)} rising,  "
                 f"Board B = {len(rb)} rising  "
                 f"(expect ~{int(args.free_duration_s)})")
        free_deltas_ms = cross_board_delta_ms(ra, rb)
        if free_deltas_ms:
            free_deltas_ms.sort()
            spread_ms = free_deltas_ms[-1] - free_deltas_ms[0]
            log.info(f"  Free-run delta (ms): n={len(free_deltas_ms)}  "
                     f"min={free_deltas_ms[0]:+.1f}  "
                     f"median={statistics.median(free_deltas_ms):+.1f}  "
                     f"max={free_deltas_ms[-1]:+.1f}  "
                     f"spread={spread_ms:.1f}")

    # -----------------------------------------------------------------------
    # Step 2 — operator triggers PTP role selection
    # -----------------------------------------------------------------------
    banner("Step 2 — Activate PTP", log)
    for ln in [
        "  Now press the buttons IN THIS ORDER:",
        "    a) Press SW1 on Board A  →  LED2 starts blinking (250 ms)",
        "    b) Press SW2 on Board B  →  LED2 starts blinking (250 ms)",
        "    c) Wait until LED2 is SOLID ON on BOTH boards.",
        "",
        "  When LED2 is solid on both boards, PTP is locked and the cyclic_fire",
        "  decimator fires at synchronised PTP-wallclock instants — LED1 edges",
        "  snap into alignment and stop drifting.",
    ]:
        log.info(ln)
    prompt("Confirm BOTH LED2 are SOLID ON (PTP lock achieved)", log)
    dump_board_state(ser_a, ser_b, "immediately after SOLID confirmation",  log)

    # Give the system another 2 s of settle time so the PTP_CLOCK jump
    # propagates through the cyclic_fire catch-up logic and the next
    # rectangle edges land on synchronised tick targets.
    log.info("  (settling 2 s for cyclic_fire to re-align edges to PTP_CLOCK ...)")
    time.sleep(2.0)
    dump_board_state(ser_a, ser_b, "after 2 s post-lock settle",  log)

    # -----------------------------------------------------------------------
    # Step 3 — capture POST-SYNC and verify alignment
    # -----------------------------------------------------------------------
    banner("Step 3 — Capture POST-SYNC and measure", log)
    prompt("Ready to start the post-sync capture", log)
    mgr_s, cap_s = start_saleae_capture(args.sample_rate, args.duration_s, log)
    cap_s.wait()
    csv_s = export_capture_csv(cap_s, out_dir / "synced", log)
    cap_s.close()

    rising_s, _ = parse_edges(csv_s)
    ra, rb = rising_s.get(0, []), rising_s.get(1, [])
    log.info(f"  Post-sync edges: Board A = {len(ra)} rising,  "
             f"Board B = {len(rb)} rising  "
             f"(expect ~{int(args.duration_s)})")
    dump_board_state(ser_a, ser_b, "after post-sync capture",  log)

    sync_deltas_ms = cross_board_delta_ms(ra, rb)
    if not sync_deltas_ms:
        log.info("  ERROR: no paired edges found in post-sync capture.")
        log.info("         Check Saleae channel mapping and that LED1 actually toggles.")
        log.info("         (See the CLI dumps above: a board with 0 edges but "
                 "cyclic_status: running=yes AND a stale cycles counter means "
                 "cyclic_fire's tfuture was armed far in the future by a "
                 "PTP_CLOCK backward jump at role change.)")
        return 2

    sync_deltas_ms.sort()
    median_ms = statistics.median(sync_deltas_ms)
    abs_max_ms = max(abs(d) for d in sync_deltas_ms)
    log.info(f"  Post-sync delta (ms): n={len(sync_deltas_ms)}  "
             f"min={sync_deltas_ms[0]:+.3f}  "
             f"median={median_ms:+.3f}  "
             f"max={sync_deltas_ms[-1]:+.3f}  "
             f"|median|={abs(median_ms):.3f}  "
             f"max|delta|={abs_max_ms:.3f}")

    # CSV summary (machine-readable companion to run_<ts>.log)
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "n", "median_ms", "max_abs_ms", "min_ms", "max_ms"])
        for label, ds in (("free", free_deltas_ms), ("synced", sync_deltas_ms)):
            if ds:
                w.writerow([label, len(ds),
                            f"{statistics.median(ds):+.3f}",
                            f"{max(abs(x) for x in ds):.3f}",
                            f"{min(ds):+.3f}", f"{max(ds):+.3f}"])

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    banner("Verdict", log)
    pass_median = abs(median_ms) < args.threshold_ms
    log.info(f"  median |delta| < {args.threshold_ms:.1f} ms : "
             f"{'PASS' if pass_median else 'FAIL'}  "
             f"(={abs(median_ms):.2f} ms)")
    if free_deltas_ms:
        free_max_abs = max(abs(d) for d in free_deltas_ms)
        improvement = free_max_abs / max(abs_max_ms, 0.001)
        log.info(f"  Improvement vs free-run max |delta|: {improvement:.1f}×  "
                 f"(free={free_max_abs:.1f} ms → synced={abs_max_ms:.2f} ms)")
    log.info("")
    log.info(f"  Output files in: {out_dir.resolve()}")
    log.info(f"  Log file       : {log_path.resolve()}")

    for ser in (ser_a, ser_b):
        try:
            if ser is not None:
                ser.close()
        except Exception:
            pass
    return 0 if pass_median else 1


if __name__ == "__main__":
    sys.exit(main())
