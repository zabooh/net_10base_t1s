#!/usr/bin/env python3
"""SW-NTP Statistics — With vs. Without HW-PTP Clock Sync
============================================================

Runs the firmware-internal software-NTP client/server (implemented in
sw_ntp.c) in two phases, captures per-sample offsets from a firmware ring
buffer (no UART in the measurement path), and prints side-by-side
statistics.

Phase A — HW-PTP is OFF.  The two PTP_CLOCKs are free-running; their
          frequency ratio = crystal difference (≈ppm).  SW-NTP therefore
          measures a linearly-growing offset over time.  Slope reveals
          drift, residuals reveal SW-NTP jitter floor.

Phase B — HW-PTP is ON (master/follower, servo reached FINE).  The two
          PTP_CLOCKs are kept synchronous by the hardware.  SW-NTP's mean
          offset should hover near zero; residuals should equal Phase A.

Expected Finding
  * STDEV similar in both phases — SW-NTP jitter dominated by SPI +
    FreeRTOS + TCP/IP-stack latency, not by clock drift.
  * SLOPE (ns/s) very different — Phase A has the crystal drift, Phase B
    has essentially zero.
  * MEAN very different — Phase A wanders linearly, Phase B stays near 0.

Usage:
    python sw_ntp_vs_ptp_test.py --gm-port COM8 --fol-port COM10
    python sw_ntp_vs_ptp_test.py --capture-s 60 --poll-ms 1000
    python sw_ntp_vs_ptp_test.py --csv-a phase_a.csv --csv-b phase_b.csv
"""

import argparse
import datetime
import statistics
import sys
import time
from typing import List, Optional, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)
from hw_timer_sync_test import zero_both_clocks  # noqa: E402


# ---------------------------------------------------------------------------
# Dump parsing — format: "<offset_ns> <valid>" per line, framed by start/end.
# ---------------------------------------------------------------------------
def dump_sw_ntp_offsets(ser: serial.Serial,
                        log: Logger) -> List[Tuple[int, int]]:
    ser.reset_input_buffer()
    ser.write(b"sw_ntp_offset_dump\r\n")
    buffer        = ""
    deadline      = time.monotonic() + 30.0
    idle_deadline = None
    saw_start     = False
    saw_end       = False
    samples: List[Tuple[int, int]] = []
    header_count  = -1

    while time.monotonic() < deadline and not saw_end:
        chunk = ser.read(4096)
        if not chunk:
            if idle_deadline is not None and time.monotonic() > idle_deadline:
                break
            time.sleep(0.02)
            continue
        buffer += chunk.decode("ascii", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("sw_ntp_offset_dump: start"):
                saw_start = True
                for tok in line.split():
                    if tok.startswith("count="):
                        try:
                            header_count = int(tok.split("=", 1)[1])
                        except ValueError:
                            header_count = -1
                log.info(f"    header: {line}")
                continue
            if line.startswith("sw_ntp_offset_dump: end"):
                log.info(f"    footer: {line}")
                saw_end = True
                break
            if not saw_start:
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    off   = int(parts[0])
                    valid = int(parts[1])
                    samples.append((off, valid))
                except ValueError:
                    continue
        if header_count > 0 and len(samples) >= header_count and idle_deadline is None:
            idle_deadline = time.monotonic() + 2.0

    if not saw_end:
        log.info(f"  WARNING: dump timed out after {len(samples)} samples"
                 f" (expected {header_count})")
    return samples


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def percentile(sorted_vals: List[int], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k  = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def linear_regression(xs: List[float], ys: List[float]
                      ) -> Tuple[float, float, float]:
    """Ordinary least-squares y = intercept + slope * x.
       Returns (slope, intercept, residual_stdev)."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num    = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den    = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope     = num / den if den > 0 else 0.0
    intercept = mean_y - slope * mean_x
    # residuals
    resid = [ys[i] - (intercept + slope * xs[i]) for i in range(n)]
    if n > 1:
        res_stdev = statistics.stdev(resid)
    else:
        res_stdev = 0.0
    return slope, intercept, res_stdev


def summarize(samples: List[Tuple[int, int]], poll_ms: int
              ) -> Optional[dict]:
    """Compute summary metrics.  Only valid=1 entries count for offset stats;
    invalid entries are reported separately as timeouts.

    Both classical (mean/stdev) and robust (median/MAD/IQR) estimators are
    produced — robust ones are the primary numbers to compare, because the
    observed distribution has heavy-tailed outliers from occasional stack
    preemption events (a single 10-ms blocker can inflate stdev by 10×)."""
    valid_offsets = [s[0] for s in samples if s[1] == 1]
    timeouts      = sum(1 for s in samples if s[1] == 0)
    if not valid_offsets:
        return None
    # time axis: one sample per poll interval (seconds)
    step_s = poll_ms / 1000.0
    xs = [i * step_s for i in range(len(valid_offsets))]
    ys = [float(v)   for v in valid_offsets]
    slope, intercept, res_stdev = linear_regression(xs, ys)

    # Robust residual stdev: 1.4826 × MAD of residuals — immune to outliers.
    residuals = [ys[i] - (intercept + slope * xs[i]) for i in range(len(xs))]
    res_median = statistics.median(residuals)
    res_mad    = statistics.median(abs(r - res_median) for r in residuals)
    res_robust_stdev = 1.4826 * res_mad   # normal-distribution consistent scale

    sorted_v = sorted(valid_offsets)
    median_v = statistics.median(valid_offsets)
    mad_v    = statistics.median(abs(v - median_v) for v in valid_offsets)
    iqr_v    = percentile(sorted_v, 0.75) - percentile(sorted_v, 0.25)

    stats = {
        "n"               : len(valid_offsets),
        "timeouts"        : timeouts,
        # classical
        "mean"            : statistics.mean(valid_offsets),
        "stdev"           : statistics.stdev(valid_offsets) if len(valid_offsets) > 1 else 0.0,
        "min"             : min(valid_offsets),
        "max"             : max(valid_offsets),
        "abs_mean"        : statistics.mean(abs(v) for v in valid_offsets),
        # robust
        "median"          : median_v,
        "mad"             : mad_v,
        "robust_stdev"    : 1.4826 * mad_v,
        "iqr"             : iqr_v,
        "p05"             : percentile(sorted_v, 0.05),
        "p25"             : percentile(sorted_v, 0.25),
        "p50"             : percentile(sorted_v, 0.50),
        "p75"             : percentile(sorted_v, 0.75),
        "p95"             : percentile(sorted_v, 0.95),
        # regression
        "slope_ns_per_s"  : slope,
        "intercept_ns"    : intercept,
        "res_stdev"       : res_stdev,
        "res_robust_stdev": res_robust_stdev,
    }
    return stats


def print_phase_stats(label: str, stats: Optional[dict], log: Logger):
    log.info("")
    log.info("-" * 68)
    log.info(f"  {label}")
    log.info("-" * 68)
    if stats is None:
        log.info("  (no valid samples collected)")
        return
    log.info(f"    Samples          : {stats['n']}  (timeouts: {stats['timeouts']})")
    log.info(f"  Robust (outlier-immune):")
    log.info(f"    Median           : {stats['median']:+12.0f} ns  ({stats['median']/1000:+.2f} µs)")
    log.info(f"    MAD              : {stats['mad']:12.0f} ns  ({stats['mad']/1000:.2f} µs)")
    log.info(f"    Robust stdev     : {stats['robust_stdev']:12.0f} ns  ({stats['robust_stdev']/1000:.2f} µs)   "
             f"[= 1.4826 × MAD]")
    log.info(f"    IQR (p75-p25)    : {stats['iqr']:12.0f} ns  ({stats['iqr']/1000:.2f} µs)")
    log.info(f"    p05..p95         : {stats['p05']:+12.0f} .. {stats['p95']:+.0f} ns")
    log.info(f"  Classical (outlier-sensitive):")
    log.info(f"    Mean             : {stats['mean']:+12.0f} ns  ({stats['mean']/1000:+.2f} µs)")
    log.info(f"    Stdev            : {stats['stdev']:12.0f} ns  ({stats['stdev']/1000:.2f} µs)")
    log.info(f"    min .. max       : {stats['min']:+12d} .. {stats['max']:+d} ns")
    log.info(f"    |offset| mean    : {stats['abs_mean']:12.0f} ns")
    log.info(f"  Linear regression  y = intercept + slope · t:")
    log.info(f"    Slope            : {stats['slope_ns_per_s']:+12.2f} ns/s  "
             f"({stats['slope_ns_per_s']/1000:+.3f} ppm)")
    log.info(f"    Intercept        : {stats['intercept_ns']:+12.0f} ns")
    log.info(f"    Residual stdev   : {stats['res_stdev']:12.0f} ns  (classical)")
    log.info(f"    Residual robust  : {stats['res_robust_stdev']:12.0f} ns  "
             f"(= 1.4826 × MAD of residuals)")


def print_comparison(phase_a: Optional[dict], phase_b: Optional[dict], log: Logger):
    log.info("")
    log.info("=" * 68)
    log.info("  Phase Comparison — Interpretation")
    log.info("=" * 68)
    if phase_a is None or phase_b is None:
        log.info("  (skipped — one or both phases have no samples)")
        return

    slope_a     = phase_a["slope_ns_per_s"]
    slope_b     = phase_b["slope_ns_per_s"]
    med_a       = phase_a["median"]
    med_b       = phase_b["median"]
    rstd_a      = phase_a["robust_stdev"]
    rstd_b      = phase_b["robust_stdev"]
    res_rstd_a  = phase_a["res_robust_stdev"]
    res_rstd_b  = phase_b["res_robust_stdev"]

    log.info("  Robust metrics (median / MAD — ignore outliers):")
    log.info(f"    Slope              : {abs(slope_a):.1f}  →  {abs(slope_b):.1f} ns/s")
    if abs(slope_a) > 10 * max(abs(slope_b), 1.0):
        log.info("      → HW-PTP clearly flattens the drift (expected)")
    else:
        log.info("      → slope not significantly reduced — check HW-PTP really is FINE")

    log.info(f"    Median offset      : {med_a:+.0f} → {med_b:+.0f} ns")
    if abs(med_b) < abs(med_a) / 5 or abs(med_a) < 1000:
        log.info("      → median pulled toward 0 by HW-PTP (expected)")

    log.info(f"    Robust stdev       : {rstd_a:.0f} → {rstd_b:.0f} ns")
    log.info(f"    Residual robust std: {res_rstd_a:.0f} → {res_rstd_b:.0f} ns")
    denom = max(res_rstd_a, 1.0)
    ratio = res_rstd_b / denom
    if 0.5 <= ratio <= 2.0:
        log.info("      → SIMILAR in both phases — this is the expected finding:")
        log.info("        SW-NTP jitter floor is set by SPI + FreeRTOS +"
                 " TCP/IP stack,")
        log.info("        NOT by whether the underlying clocks are sync'd.")
    else:
        log.info(f"      → changed by {ratio:.2f}×  — unusual, investigate")

    # Outlier flags
    for label_phase, st in [("A", phase_a), ("B", phase_b)]:
        n        = st["n"]
        classic  = st["stdev"]
        robust   = st["robust_stdev"]
        if classic > 5 * max(robust, 1.0):
            log.info(f"  Phase {label_phase}: classical stdev is {classic/max(robust,1):.1f}×"
                     f" the robust stdev — heavy-tailed distribution; trust the robust number.")


def save_csv(samples: List[Tuple[int, int]], path: str, poll_ms: int):
    step_s = poll_ms / 1000.0
    with open(path, "w", encoding="utf-8") as f:
        f.write("index,time_s,offset_ns,valid\n")
        for i, (off, v) in enumerate(samples):
            f.write(f"{i},{i*step_s:.3f},{off},{v}\n")


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------
def run_phase(label: str,
              ser_gm: serial.Serial, ser_fol: serial.Serial,
              capture_s: float, poll_ms: int,
              log: Logger) -> List[Tuple[int, int]]:
    log.info("")
    log.info("=" * 68)
    log.info(f"  {label}")
    log.info("=" * 68)
    log.info(f"  Resetting SW-NTP ring buffer ...")
    send_command(ser_fol, "sw_ntp_offset_reset", 2.0, log)
    log.info(f"  Capturing offsets for {capture_s:.0f} s (no UART in measurement path)")
    t_start = time.monotonic()
    while time.monotonic() - t_start < capture_s:
        time.sleep(1.0)
        elapsed = time.monotonic() - t_start
        sys.stdout.write(f"\r    t={elapsed:6.1f}s / {capture_s:.0f}s")
        sys.stdout.flush()
    sys.stdout.write("\r" + " " * 40 + "\r")
    log.info(f"  Dumping ring buffer ...")
    samples = dump_sw_ntp_offsets(ser_fol, log)
    log.info(f"  Received {len(samples)} samples (expected ~{int(capture_s/(poll_ms/1000.0))}).")
    return samples


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",   default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",  default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",     default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",    default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",   default=DEFAULT_NETMASK)
    p.add_argument("--capture-s", default=60.0, type=float,
                   help="Seconds to capture per phase (default 60s ≈ 60 samples at 1 Hz)")
    p.add_argument("--poll-ms",   default=1000, type=int,
                   help="SW-NTP poll interval in ms (default 1000)")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float,
                   help="Max seconds to wait for HW-PTP FINE in Phase B (default 60)")
    p.add_argument("--no-reset",  action="store_true",
                   help="Skip board reset/IP-setup (assume already configured)")
    p.add_argument("--skip-phase-a", action="store_true",
                   help="Skip Phase A (no-HW-PTP) and only run Phase B")
    p.add_argument("--skip-phase-b", action="store_true",
                   help="Skip Phase B (with-HW-PTP) and only run Phase A")
    p.add_argument("--csv-a",     default=None,
                   help="CSV output for Phase A samples")
    p.add_argument("--csv-b",     default=None,
                   help="CSV output for Phase B samples")
    p.add_argument("--log-file",  default=None)
    p.add_argument("--verbose",   action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = args.log_file or f"sw_ntp_vs_ptp_test_{ts}.log"
    log = Logger(log_file=log_file, verbose=args.verbose)

    log.info("=" * 68)
    log.info("  SW-NTP vs HW-PTP Statistical Comparison")
    log.info("=" * 68)
    log.info(f"Date           : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"GM  port/IP    : {args.gm_port} / {args.gm_ip}")
    log.info(f"FOL port/IP    : {args.fol_port} / {args.fol_ip}")
    log.info(f"Capture/phase  : {args.capture_s:.0f} s")
    log.info(f"Poll interval  : {args.poll_ms} ms")
    log.info("")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

    phase_a_samples: List[Tuple[int, int]] = []
    phase_b_samples: List[Tuple[int, int]] = []

    try:
        # ---- Setup -----------------------------------------------------
        if not args.no_reset:
            log.info("--- Reset ---")
            for label, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
                send_command(ser, "reset", 3.0, log)
                log.info(f"  [{label}] reset")
            log.info("  Waiting 8 s for boot...")
            time.sleep(8)

            log.info("--- Configure IPs ---")
            for label, ser, ip in [("GM", ser_gm, args.gm_ip),
                                    ("FOL", ser_fol, args.fol_ip)]:
                resp = send_command(ser, f"setip eth0 {ip} {args.netmask}", 3.0, log)
                ok = bool(RE_IP_SET.search(resp))
                log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")

        # Always disable PTP explicitly before Phase A
        send_command(ser_gm,  "ptp_mode off", 2.0, log)
        send_command(ser_fol, "ptp_mode off", 2.0, log)

        # Seed both PTP_CLOCKs to 0 in parallel.  Without this the clock
        # is !valid and PTP_CLOCK_GetTime_ns() returns 0 on every call —
        # so all four SW-NTP timestamps are 0 and every offset comes out 0.
        # Thread skew (~100 µs) manifests as a small constant offset in the
        # first sample; crystal drift produces the slope we want to see.
        log.info("--- Seed PTP_CLOCKs (parallel clk_set 0) ---")
        ok_zero, skew_ns = zero_both_clocks(ser_gm, ser_fol, log)
        if not ok_zero:
            log.info("  FAIL: could not zero both PTP_CLOCKs")
            return 1

        # Configure SW-NTP poll interval on follower
        send_command(ser_fol, f"sw_ntp_poll {args.poll_ms}", 2.0, log)

        # Start SW-NTP master + follower
        log.info("--- Start SW-NTP master/follower ---")
        send_command(ser_gm, "sw_ntp_mode master", 2.0, log)
        time.sleep(0.3)
        send_command(ser_fol, f"sw_ntp_mode follower {args.gm_ip}", 2.0, log)
        log.info("  Waiting 3 s for first SW-NTP polls to round-trip ...")
        time.sleep(3)

        # ---- Phase A: no HW-PTP ---------------------------------------
        if not args.skip_phase_a:
            phase_a_samples = run_phase(
                "Phase A — HW-PTP OFF  (free-running crystals)",
                ser_gm, ser_fol, args.capture_s, args.poll_ms, log)

        # ---- Phase B: enable HW-PTP, wait for FINE --------------------
        if not args.skip_phase_b:
            log.info("")
            log.info("--- Enable HW-PTP, wait for FINE ---")
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm,  "ptp_mode master", 3.0, log)
            log.info(f"  Waiting for FOL FINE (timeout {args.conv_timeout:.0f}s)...")
            matched, elapsed, _ = wait_for_pattern(
                ser_fol, RE_FINE, args.conv_timeout, log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                "HARD_SYNC": RE_HARD_SYNC,
                                "COARSE":    RE_COARSE},
                live_log=True)
            if not matched:
                log.info(f"  FAIL: FINE not reached in {args.conv_timeout:.0f}s")
                return 1
            log.info(f"  FINE reached in {elapsed:.1f}s")
            log.info("  Allowing 5 s for HW-PTP to stabilise ...")
            time.sleep(5)

            phase_b_samples = run_phase(
                "Phase B — HW-PTP ON, FINE  (clocks synchronised)",
                ser_gm, ser_fol, args.capture_s, args.poll_ms, log)

        # ---- Stop SW-NTP and PTP -------------------------------------
        send_command(ser_fol, "sw_ntp_mode off", 2.0, log)
        send_command(ser_gm,  "sw_ntp_mode off", 2.0, log)

        # ---- Summaries ------------------------------------------------
        stats_a = summarize(phase_a_samples, args.poll_ms) if phase_a_samples else None
        stats_b = summarize(phase_b_samples, args.poll_ms) if phase_b_samples else None

        log.info("")
        log.info("=" * 68)
        log.info("  Results")
        log.info("=" * 68)
        if stats_a is not None:
            print_phase_stats("Phase A — HW-PTP OFF", stats_a, log)
        if stats_b is not None:
            print_phase_stats("Phase B — HW-PTP ON (FINE)", stats_b, log)
        print_comparison(stats_a, stats_b, log)

        if args.csv_a and phase_a_samples:
            save_csv(phase_a_samples, args.csv_a, args.poll_ms)
            log.info(f"\n  Phase A CSV saved: {args.csv_a}")
        if args.csv_b and phase_b_samples:
            save_csv(phase_b_samples, args.csv_b, args.poll_ms)
            log.info(f"  Phase B CSV saved: {args.csv_b}")

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass
        log.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
