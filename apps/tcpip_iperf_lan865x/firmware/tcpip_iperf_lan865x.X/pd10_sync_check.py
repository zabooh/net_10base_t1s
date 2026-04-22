#!/usr/bin/env python3
"""pd10_sync_check.py — automated PD10 synchronicity test

Workflow (no operator interaction needed once boards + Saleae are wired):

  1. Reset both boards via UART (serial reset command).
  2. Set one to PTP master and the other to follower via the existing
     `ptp_mode master / follower` CLI.
  3. Poll the follower's UART for "PTP FINE" — actual servo-locked
     state (not a fixed-time guess).  Times out after --fine-timeout-s.
  4. Settle a few seconds for the demo's cyclic_fire to align.
  5. Capture both PD10 channels on Saleae for --duration-s.
  6. Pair every Ch0 rising edge with the nearest Ch1 rising edge
     (within ±300 ms bracket) and report:
        - mean / median / spread of cross-board edge delta in µs
        - frequency match (each channel's median rising-edge period)
        - PASS/FAIL gate: |median| < THRESHOLD_VISIBLE_MS (50 ms = the
          rough human-visual perception threshold for offset blink)

All output is tee'd into pd10_sync_check_<ts>/run_<ts>.log together
with a per-edge CSV + a one-row summary.csv.

Usage:
    python pd10_sync_check.py                              # default 10 s
    python pd10_sync_check.py --gm-port COM10 --fol-port COM8
    python pd10_sync_check.py --duration-s 30 --threshold-ms 5
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

from cyclic_fire_hw_test import (                            # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from ptp_drift_compensate_test import Logger, open_port      # noqa: E402

DEFAULT_GM_PORT     = "COM10"   # Saleae Ch0 board
DEFAULT_FOL_PORT    = "COM8"    # Saleae Ch1 board
DEFAULT_DURATION_S  = 10.0
DEFAULT_SAMPLE_HZ   = 50_000_000
DEFAULT_FINE_TO_S   = 30.0   # lock typically 12-18 s on this hardware
DEFAULT_SETTLE_S    = 3.0
DEFAULT_THRESH_MS   = 50.0     # human-visual blink-asynchrony threshold

RE_FINE = re.compile(r"PTP\s+FINE", re.IGNORECASE)


def banner(title, log):
    log.info("")
    log.info("=" * 72)
    log.info(f"  {title}")
    log.info("=" * 72)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def verbose_send(ser, cmd, log, tag):
    """Log + send a command line to one of the boards."""
    log.info(f"  >>> [{tag}] {cmd}")
    ser.write((cmd + "\r\n").encode("ascii"))


def drain_and_log(ser, log, tag, duration_s):
    """Read whatever the board sends back over the next duration_s and
    log every complete line, prefixed with [tag].  Useful for showing
    boot banners + post-command status messages so the operator can
    follow exactly what's going on."""
    deadline = time.monotonic() + duration_s
    line = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if not chunk:
            time.sleep(0.02)
            continue
        line += chunk
        while b"\n" in line:
            ln, line = line.split(b"\n", 1)
            text = ln.rstrip(b"\r").decode("ascii", "replace")
            if text:
                log.info(f"      [{tag}] {text}")


def reset_boards(gm, fol, log):
    verbose_send(gm,  "reset", log, "GM ")
    verbose_send(fol, "reset", log, "FOL")
    log.info("  (waiting 4 s for boot + Harmony + LAN865x bring-up,")
    log.info("   logging both boards' boot banners ...)")
    deadline = time.monotonic() + 4.0
    line_gm = b""
    line_fol = b""
    while time.monotonic() < deadline:
        for ser, tag, line_buf_name in ((gm, "GM ", "g"), (fol, "FOL", "f")):
            chunk = ser.read(256)
            if not chunk:
                continue
            if tag == "GM ":
                line_gm += chunk
                while b"\n" in line_gm:
                    ln, line_gm = line_gm.split(b"\n", 1)
                    text = ln.rstrip(b"\r").decode("ascii", "replace")
                    if text:
                        log.info(f"      [{tag}] {text}")
            else:
                line_fol += chunk
                while b"\n" in line_fol:
                    ln, line_fol = line_fol.split(b"\n", 1)
                    text = ln.rstrip(b"\r").decode("ascii", "replace")
                    if text:
                        log.info(f"      [{tag}] {text}")
        time.sleep(0.02)


def set_modes(gm, fol, log):
    verbose_send(gm, "ptp_mode master", log, "GM ")
    log.info("  (draining 1 s of GM response ...)")
    drain_and_log(gm, log, "GM ", 1.0)
    verbose_send(fol, "ptp_mode follower", log, "FOL")


def wait_for_fine(fol, timeout_s, log):
    """Poll the follower's UART for 'PTP FINE'.  Returns elapsed time
    in seconds, or None on timeout.  Echoes every line received so the
    operator can see what's actually happening."""
    log.info(f"  Polling FOL UART for 'PTP FINE' (timeout {timeout_s:.0f} s) ...")
    # Don't reset the buffer — we want to catch any FINE message that
    # was already in flight before this function was called.
    deadline = time.monotonic() + timeout_s
    t_start = time.monotonic()
    line = b""
    while time.monotonic() < deadline:
        chunk = fol.read(256)
        if not chunk:
            time.sleep(0.02)
            continue
        line += chunk
        # Split on newlines and log each completed line
        while b"\n" in line:
            ln, line = line.split(b"\n", 1)
            text = ln.rstrip(b"\r").decode("ascii", "replace")
            if text:
                log.info(f"      [FOL] {text}")
                if RE_FINE.search(text):
                    elapsed = time.monotonic() - t_start
                    log.info(f"  -> PTP FINE seen after {elapsed:.1f} s")
                    return elapsed
    log.info("  -> TIMEOUT waiting for FINE")
    return None


# ---------------------------------------------------------------------------
# Edge analysis
# ---------------------------------------------------------------------------

def percentile(sorted_values, pct):
    if not sorted_values:
        return float("nan")
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] * (1 - (k - lo)) + sorted_values[hi] * (k - lo)


def mad(values):
    if not values:
        return float("nan")
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def median_period_us(rising):
    if len(rising) < 2:
        return float("nan")
    diffs = [(rising[i] - rising[i - 1]) * 1e6 for i in range(1, len(rising))]
    return statistics.median(diffs)


def cross_board_delta_us(rising_a, rising_b, bracket_s=0.499):
    """For each rising edge on Ch0 find the closest rising edge on Ch1
    within ±bracket_s.  Default bracket = 499 ms = just under half the
    1 Hz period, so we correctly pair even a near-180° phase offset
    (the pair is then the 'same' edge but wrapped half a cycle)."""
    if not rising_a or not rising_b:
        return []
    deltas = []
    j = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        cand = []
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = rising_b[k] - ta
                if abs(d) <= bracket_s:
                    cand.append(d)
        if cand:
            deltas.append(min(cand, key=abs) * 1e6)
    return deltas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--duration-s",   type=float, default=DEFAULT_DURATION_S)
    p.add_argument("--sample-rate",  type=int,   default=DEFAULT_SAMPLE_HZ)
    p.add_argument("--fine-timeout-s", type=float, default=DEFAULT_FINE_TO_S)
    p.add_argument("--settle-s",     type=float, default=DEFAULT_SETTLE_S)
    p.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESH_MS,
                   help="|median| gate for visual sync (default 50 ms)")
    p.add_argument("--no-prep", action="store_true",
                   help="skip reset+mode setup; assume boards already locked")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"pd10_sync_check_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 cross-board synchronicity check", log)
    log.info(f"  GM port      : {args.gm_port}  (Saleae Ch0)")
    log.info(f"  FOL port     : {args.fol_port}  (Saleae Ch1)")
    log.info(f"  Capture      : {args.duration_s:.1f} s @ "
             f"{args.sample_rate/1_000_000:.0f} MS/s "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  PASS gate    : |median delta| < {args.threshold_ms:.1f} ms")
    log.info(f"  Output       : {out_dir.resolve()}")

    try:
        gm  = open_port(args.gm_port)
        fol = open_port(args.fol_port)
    except Exception as exc:
        sys.exit(f"[ERROR] could not open serial ports: {exc}")

    # ----- Step 1: prep -----
    if args.no_prep:
        banner("Step 1 — prep SKIPPED (--no-prep, assuming already locked)", log)
    else:
        banner("Step 1 — reset boards + set master / follower mode", log)
        reset_boards(gm, fol, log)
        set_modes(gm, fol, log)

        # ----- Step 2: wait for FINE -----
        banner("Step 2 — wait for PTP FINE on the follower", log)
        elapsed = wait_for_fine(fol, args.fine_timeout_s, log)
        fine_ok = elapsed is not None
        if not fine_ok:
            log.info("  WARNING: follower never reported FINE within timeout.")
            log.info("           Capturing PD10 anyway so the operator can see")
            log.info("           the actual signal state — phase-sync verdict")
            log.info("           below should be treated as informational only.")
        log.info(f"  Settling {args.settle_s:.0f} s for cyclic_fire alignment ...")
        time.sleep(args.settle_s)

    gm.close(); fol.close()

    # ----- Step 3: Saleae capture -----
    banner(f"Step 3 — Saleae capture ({args.duration_s:.1f} s)", log)
    mgr, cap = start_saleae_capture(args.sample_rate, args.duration_s, log)
    cap.wait()
    csv_path = export_capture_csv(cap, out_dir, log)
    cap.close()

    # ----- Step 4: edge analysis -----
    banner("Step 4 — edge analysis", log)
    rising, _ = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  Channel 0 rising edges: {len(ra)}")
    log.info(f"  Channel 1 rising edges: {len(rb)}")

    period_a_us = median_period_us(ra)
    period_b_us = median_period_us(rb)
    log.info(f"  Ch0 median rising-edge period: {period_a_us:>10,.1f} µs   "
             f"(expect 1_000_000 µs for 1 Hz)")
    log.info(f"  Ch1 median rising-edge period: {period_b_us:>10,.1f} µs")
    if period_a_us == period_a_us and period_b_us == period_b_us:
        rate_match_ppm = abs(period_a_us - period_b_us) / period_a_us * 1e6
        log.info(f"  Rate match: {rate_match_ppm:.1f} ppm difference")

    deltas_us = cross_board_delta_us(ra, rb)
    if not deltas_us:
        log.info("  ERROR: no paired rising edges within ±300 ms of each other.")
        log.info("  Boards are likely > 300 ms out of phase or one PD10 is dead.")
        return 3

    deltas_sorted = sorted(deltas_us)
    median_us  = statistics.median(deltas_us)
    mad_us     = mad(deltas_us)
    p99_us     = percentile(deltas_sorted, 99)
    p1_us      = percentile(deltas_sorted, 1)
    spread_us  = deltas_sorted[-1] - deltas_sorted[0]
    abs_max_us = max(abs(d) for d in deltas_us)

    log.info("")
    log.info(f"  Cross-board PD10 rising-edge delta (Ch1 − Ch0):")
    log.info(f"    n       : {len(deltas_us)}")
    log.info(f"    min     : {deltas_sorted[0]:>+12,.1f} µs   "
             f"(= {deltas_sorted[0]/1000:+.3f} ms)")
    log.info(f"    p1      : {p1_us:>+12,.1f} µs")
    log.info(f"    median  : {median_us:>+12,.1f} µs   "
             f"(= {median_us/1000:+.3f} ms)")
    log.info(f"    p99     : {p99_us:>+12,.1f} µs")
    log.info(f"    max     : {deltas_sorted[-1]:>+12,.1f} µs   "
             f"(= {deltas_sorted[-1]/1000:+.3f} ms)")
    log.info(f"    spread  : {spread_us:>12,.1f} µs   "
             f"(= {spread_us/1000:.3f} ms)")
    log.info(f"    MAD     : {mad_us:>12,.1f} µs")
    log.info(f"    |max|   : {abs_max_us:>12,.1f} µs")

    # ----- Per-edge CSV -----
    with open(out_dir / "deltas_us.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_idx", "ch0_t_s", "delta_us"])
        for i, (ta, d) in enumerate(zip(ra, deltas_us)):
            w.writerow([i, f"{ta:.9f}", f"{d:+.4f}"])

    # ----- Verdict -----
    banner("Verdict", log)
    median_ms     = abs(median_us) / 1000.0
    pass_visible  = median_ms < args.threshold_ms
    log.info(f"  |median delta|  : {median_ms:>7.3f} ms")
    log.info(f"  Visual gate     : < {args.threshold_ms:.1f} ms  "
             f"-> {'PASS' if pass_visible else 'FAIL'}")
    if pass_visible:
        log.info(f"  Boards are visually synchronous "
                 f"(human eye doesn't notice < {args.threshold_ms:.0f} ms).")
    else:
        log.info(f"  Boards are visibly out of phase by ~{median_ms:.0f} ms.")
        if median_ms > 250:
            log.info("  > 250 ms means the LEDs land on opposite slots of the")
            log.info("  500 ms half-period rectangle (180° phase mismatch).")
        elif median_ms > 50:
            log.info("  Visible but not 180° — the boards' SW PTP_CLOCKs differ")
            log.info("  by this amount.  See README_PTP §R25 for the calibration")
            log.info("  constant PTP_FOL_ANCHOR_OFFSET_NS that compensates the")
            log.info("  LAN865x RX-pipeline delay; current value may need re-tune")
            log.info("  for this specific board pair.")

    # ----- Summary CSV -----
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "median_us", "mad_us", "p99_us", "max_abs_us",
                    "spread_us", "ch0_period_us", "ch1_period_us",
                    "verdict_visible"])
        w.writerow([len(deltas_us),
                    f"{median_us:+.3f}", f"{mad_us:.3f}",
                    f"{p99_us:+.3f}", f"{abs_max_us:.3f}",
                    f"{spread_us:.3f}",
                    f"{period_a_us:.3f}", f"{period_b_us:.3f}",
                    "PASS" if pass_visible else "FAIL"])
    log.info("")
    log.info(f"  Per-edge CSV : {out_dir/'deltas_us.csv'}")
    log.info(f"  Summary CSV  : {out_dir/'summary.csv'}")
    log.info(f"  Log file     : {log_path}")
    return 0 if pass_visible else 1


if __name__ == "__main__":
    sys.exit(main())
