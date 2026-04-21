#!/usr/bin/env python3
"""drift_filter_analysis.py — characterise the FOL PTP_CLOCK drift-IIR filter
==============================================================================

Rapidly polls `clk_get` on both boards over serial and records the filter-
output drift_ppb values plus wallclock-ns timestamps.  Produces a CSV for
offline plotting and a text summary that answers:

  - Is the IIR filter output stationary (around a mean) or drifting?
  - How large is the filter noise (stddev, min/max, spread) over 1 min?
  - Does GM vs FOL show matched behaviour (both crystals similar) or not?
  - What's the implied cross-board rate residual (FOL wc - GM wc derivative
    vs PC time — directly tells us the leftover per-second drift that
    cyclic_fire_hw_test.py sees as µs/s in the delta-drift metric)?

The tool deliberately does NOT require firmware changes.  clk_get already
reports (wallclock_ns, drift_ppb); we just poll it fast and analyse.

Usage:
    python drift_filter_analysis.py                     # full run: reset + FINE + 30 s settle + 60 s sample
    python drift_filter_analysis.py --no-reset          # skip boot/PTP setup
    python drift_filter_analysis.py --settle-s 5 --sample-s 20     # quick run
    python drift_filter_analysis.py --gm-port COM10 --fol-port COM8
"""

import argparse
import csv
import datetime
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import List, Tuple

try:
    import serial  # noqa: F401
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    reset_and_wait_for_boot, sleep_with_countdown,
    RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

# Mirror the hw-test defaults — GM = COM10, FOL = COM8.
DEFAULT_GM_PORT  = "COM10"
DEFAULT_FOL_PORT = "COM8"

RE_CLK_GET = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def read_clk_get(ser, log: Logger, verbose: bool,
                 retries: int = 2, timeout: float = 2.0
                 ) -> Tuple[int, int, float]:
    """Send clk_get and return (wallclock_ns, drift_ppb, midpoint_pc_time_s).
    Retries up to `retries` times on empty or unparseable responses (e.g.,
    when the firmware CLI is momentarily busy with a cyclic_fire callback
    or a PTP servo register-write burst)."""
    last_resp = ""
    for attempt in range(retries + 1):
        t_start = time.monotonic()
        resp = send_command(ser, "clk_get", timeout, log if verbose else None)
        t_end = time.monotonic()
        m = RE_CLK_GET.search(resp)
        if m:
            return (int(m.group(1)), int(m.group(2)), 0.5 * (t_start + t_end))
        last_resp = resp
        time.sleep(0.05)   # short back-off before retry
    raise RuntimeError(f"clk_get failed after {retries+1} attempts, "
                       f"last response: {last_resp[:120]!r}")


def rapid_sample_loop(ser_gm, ser_fol, duration_s: float, log: Logger
                      ) -> List[Tuple[int, str, float, int, int]]:
    """Alternately poll clk_get on GM and FOL for `duration_s`.
    Returns a list of (sample_idx, board, pc_time_s, wc_ns, drift_ppb).
    Silently skips samples where clk_get fails entirely (rare); the
    sampling rate degrades gracefully."""
    records = []
    failures = 0
    t0 = time.monotonic()
    idx = 0
    try:
        while time.monotonic() - t0 < duration_s:
            board = "GM" if (idx & 1) == 0 else "FOL"
            ser   = ser_gm if (idx & 1) == 0 else ser_fol
            try:
                wc, drift, t_mid = read_clk_get(ser, log, verbose=False)
                records.append((idx, board, t_mid - t0, wc, drift))
            except RuntimeError as exc:
                failures += 1
                if failures <= 5:
                    log.info(f"  WARN sample {idx} ({board}): {exc}")
                elif failures == 6:
                    log.info(f"  WARN (further sample failures suppressed)")
            idx += 1
    except KeyboardInterrupt:
        log.info(f"  interrupted by user at sample {idx}")
    if failures:
        log.info(f"  total clk_get failures: {failures} / {idx}")
    return records


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def robust(vs: List[float]) -> Tuple[float, float]:
    if not vs:
        return (0.0, 0.0)
    m = statistics.median(vs)
    mad = statistics.median(abs(v - m) for v in vs)
    return (m, 1.4826 * mad)


def percentile(sorted_vs: List[float], p: float) -> float:
    if not sorted_vs:
        return 0.0
    k = (len(sorted_vs) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_vs) - 1)
    if f == c:
        return sorted_vs[f]
    return sorted_vs[f] + (sorted_vs[c] - sorted_vs[f]) * (k - f)


def analyse_per_board(board: str, rows, log: Logger):
    """rows: list of (sample_idx, pc_time_s, wc_ns, drift_ppb) for one board."""
    if not rows:
        log.info(f"  {board}: NO SAMPLES")
        return

    drifts = [r[3] for r in rows]
    sorted_drifts = sorted(drifts)
    mean_ppb = statistics.mean(drifts)
    std_ppb  = statistics.stdev(drifts) if len(drifts) > 1 else 0.0
    med, mad = robust(drifts)

    log.info(f"  {board}:")
    log.info(f"    n samples           : {len(drifts)}")
    log.info(f"    mean drift_ppb      : {mean_ppb:+12.1f}  ({mean_ppb/1000:+.3f} ppm)")
    log.info(f"    stddev              : {std_ppb:12.1f} ppb ({std_ppb/1000:.3f} ppm)")
    log.info(f"    median / MAD-std    : {med:+12.1f} / {mad:.1f} ppb "
             f"({med/1000:+.3f} / {mad/1000:.3f} ppm)")
    log.info(f"    min / max           : {min(drifts):+12d} / {max(drifts):+12d} "
             f"ppb (spread {max(drifts)-min(drifts)} = {(max(drifts)-min(drifts))/1000:.2f} ppm)")
    log.info(f"    p10 / p90           : {percentile(sorted_drifts,10):+12.1f} / "
             f"{percentile(sorted_drifts,90):+12.1f} ppb")

    # Autocorrelation at lag 1: do consecutive samples correlate more than random?
    if len(drifts) > 10:
        lag1 = [(drifts[i] - mean_ppb) * (drifts[i+1] - mean_ppb)
                for i in range(len(drifts)-1)]
        variance = sum((d - mean_ppb)**2 for d in drifts) / len(drifts)
        if variance > 0:
            ac1 = (sum(lag1) / len(lag1)) / variance
            log.info(f"    lag-1 autocorrelation: {ac1:+.3f}  "
                     f"({'strongly correlated — filter state drifts slowly' if ac1 > 0.7 else 'random walk' if ac1 > 0.3 else 'mostly uncorrelated noise'})")

    # First-half vs second-half to detect trend
    n_half = len(drifts) // 2
    if n_half > 5:
        med_first = statistics.median(drifts[:n_half])
        med_last  = statistics.median(drifts[-n_half:])
        dt = rows[-1][1] - rows[0][1]
        trend_ppb_per_s = (med_last - med_first) / dt if dt > 0 else 0
        log.info(f"    first-half median   : {med_first:+.0f} ppb")
        log.info(f"    last-half  median   : {med_last:+.0f} ppb  "
                 f"(drift over run: {med_last-med_first:+.0f} ppb, "
                 f"{trend_ppb_per_s:+.2f} ppb/s)")


def analyse_cross_board(gm_rows, fol_rows, log: Logger):
    """Estimate the cross-board wallclock drift rate: regress (FOL_wc - GM_wc_interp)
    vs pc_time and report the slope — this is the metric that cyclic_fire_hw_test.py
    sees as µs/s drift in the delta-drift section."""
    if len(gm_rows) < 3 or len(fol_rows) < 3:
        log.info("  not enough samples for cross-board analysis")
        return

    # Interpolate GM wc at each FOL pc_time, then compute FOL - GM_interp
    gm_t = [r[1] for r in gm_rows]
    gm_v = [r[2] for r in gm_rows]

    offsets = []  # (pc_time, fol_wc - gm_wc_interp)
    for (_, t_fol, wc_fol, _) in fol_rows:
        # Binary-search-ish linear interpolation in sorted gm_t
        if t_fol < gm_t[0] or t_fol > gm_t[-1]:
            continue
        # Find surrounding GM samples
        lo, hi = 0, len(gm_t) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if gm_t[mid] <= t_fol:
                lo = mid
            else:
                hi = mid
        if gm_t[hi] == gm_t[lo]:
            wc_gm_interp = gm_v[lo]
        else:
            frac = (t_fol - gm_t[lo]) / (gm_t[hi] - gm_t[lo])
            wc_gm_interp = gm_v[lo] + (gm_v[hi] - gm_v[lo]) * frac
        offsets.append((t_fol, wc_fol - wc_gm_interp))

    if len(offsets) < 3:
        log.info("  cross-board: insufficient overlap")
        return

    # Linear regression: offset vs time
    ts = [o[0] for o in offsets]
    os_ = [o[1] for o in offsets]
    t_mean = statistics.mean(ts)
    o_mean = statistics.mean(os_)
    num = sum((t - t_mean) * (o - o_mean) for t, o in offsets)
    den = sum((t - t_mean) ** 2 for t in ts)
    slope_ns_per_s = num / den if den > 0 else 0.0
    slope_ppm      = slope_ns_per_s / 1000.0   # ns/s ÷ 1e3 = µs/s = ppm

    # Residuals after removing the slope
    residuals = [o - (o_mean + slope_ns_per_s * (t - t_mean)) for t, o in offsets]
    res_std = statistics.stdev(residuals) if len(residuals) > 1 else 0.0

    log.info("")
    log.info(f"  Cross-board offset (FOL_wc - GM_wc_interp) vs PC time:")
    log.info(f"    samples             : {len(offsets)}")
    log.info(f"    mean offset         : {o_mean:+.0f} ns ({o_mean/1000:+.2f} µs)")
    log.info(f"    drift rate (slope)  : {slope_ns_per_s:+.1f} ns/s  "
             f"({slope_ppm:+.2f} µs/s = {slope_ppm:+.2f} ppm)")
    log.info(f"    residual stddev     : {res_std:.0f} ns ({res_std/1000:.2f} µs)")
    log.info(f"    → if slope > 10 ppm the PTP rate-sync has a real residual;")
    log.info(f"      this is what cyclic_fire_hw_test sees as 'delta drift rate'")


def setup_ptp(args, ser_gm, ser_fol, log: Logger) -> bool:
    """Reset both boards, confirm boot via build-banner, configure IPs,
    enable PTP, wait for FINE.  Aborts immediately if either board fails
    to emit '[APP] Build:' within 8 s of the reset command."""
    log.info("\n--- Reset + IP + PTP to FINE ---")
    try:
        gm_build  = reset_and_wait_for_boot(ser_gm,  "GM ", timeout=8.0, log=log)
        fol_build = reset_and_wait_for_boot(ser_fol, "FOL", timeout=8.0, log=log)
    except RuntimeError as exc:
        log.info(f"  ERROR: {exc}")
        log.info(f"  Aborting test — hard power-cycle both boards "
                 f"(USB unplug → 3 s wait → plug) and retry.")
        return False
    if gm_build != fol_build:
        log.info(f"  WARN: build mismatch — GM={gm_build} FOL={fol_build}  "
                 f"(test continues, but both boards should be flashed with the same firmware)")
    send_command(ser_gm,  f"setip eth0 {args.gm_ip} {args.netmask}", 3.0, log)
    send_command(ser_fol, f"setip eth0 {args.fol_ip} {args.netmask}", 3.0, log)
    send_command(ser_fol, "ptp_mode follower", 3.0, log)
    time.sleep(0.3)
    ser_fol.reset_input_buffer()
    send_command(ser_gm,  "ptp_mode master", 3.0, log)
    matched, elapsed, _ = wait_for_pattern(
        ser_fol, RE_FINE, args.conv_timeout, log,
        extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                        "HARD_SYNC": RE_HARD_SYNC,
                        "COARSE":    RE_COARSE})
    if not matched:
        log.info(f"  PTP FINE not reached in {elapsed:.1f} s — aborting")
        return False
    log.info(f"  PTP FINE reached after {elapsed:.1f} s")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",       default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",      default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",         default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",        default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",       default=DEFAULT_NETMASK)
    p.add_argument("--conv-timeout",  default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s",      default=30.0, type=float,
                   help="seconds to wait after FINE for the IIR filter to converge")
    p.add_argument("--sample-s",      default=60.0, type=float,
                   help="duration of the rapid-sample window")
    p.add_argument("--no-reset",      action="store_true",
                   help="skip boot+FINE, assume PTP already running")
    p.add_argument("--out-dir",       default=None)
    p.add_argument("--verbose",       action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"drift_filter_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Logger(log_file=str(out_dir / f"run_{ts}.log"),
                 verbose=args.verbose)

    log.info("=" * 70)
    log.info("  PTP Drift-IIR Filter Characterisation")
    log.info("=" * 70)
    log.info(f"  GM port        : {args.gm_port}   FOL port: {args.fol_port}")
    log.info(f"  settle         : {args.settle_s:.0f} s")
    log.info(f"  sample window  : {args.sample_s:.0f} s")
    log.info(f"  output dir     : {out_dir}")

    ser_gm = ser_fol = None
    rc = 0
    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)

        if not args.no_reset:
            if not setup_ptp(args, ser_gm, ser_fol, log):
                return 1
        else:
            # --no-reset path: make sure any leftover cyclic_fire from a
            # previous run is stopped (it doesn't block the CLI, but we
            # want a clean state for drift measurement) and verify both
            # boards are responsive before the sampling loop starts.
            log.info("")
            log.info("--- Pre-flight (no-reset path) ---")
            send_command(ser_gm,  "cyclic_stop", 2.0, log)
            send_command(ser_fol, "cyclic_stop", 2.0, log)
            try:
                wc_gm, d_gm, _ = read_clk_get(ser_gm,  log, verbose=True, timeout=3.0)
                wc_fo, d_fo, _ = read_clk_get(ser_fol, log, verbose=True, timeout=3.0)
                log.info(f"  GM  clk_get: wc={wc_gm} ns, drift={d_gm:+d} ppb")
                log.info(f"  FOL clk_get: wc={wc_fo} ns, drift={d_fo:+d} ppb")
            except RuntimeError as exc:
                log.info(f"  pre-flight FAIL: {exc}")
                log.info(f"  → both boards need to be in PTP-FINE state; "
                         f"either power-cycle + rerun without --no-reset, "
                         f"or check manually.")
                return 1

        log.info("")
        log.info(f"--- Settle {args.settle_s:.0f} s (IIR filter convergence) ---")
        sleep_with_countdown(args.settle_s,
                             label="IIR filter convergence",
                             log=log)

        log.info("")
        log.info(f"--- Rapid clk_get sampling for {args.sample_s:.0f} s ---")
        log.info("  (alternating GM / FOL, ~20 Hz effective per board)")
        t_start = time.monotonic()
        records = rapid_sample_loop(ser_gm, ser_fol, args.sample_s, log)
        t_end = time.monotonic()
        log.info(f"  collected {len(records)} samples in {t_end-t_start:.1f} s "
                 f"(~{len(records)/(t_end-t_start):.1f} Hz combined)")

        # Save CSV
        csv_path = (out_dir / f"drift_samples_{ts}.csv").resolve()
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["idx", "board", "pc_time_s", "wc_ns", "drift_ppb"])
            for r in records:
                w.writerow([r[0], r[1], f"{r[2]:.6f}", r[3], r[4]])
        log.info(f"  CSV → {csv_path}")

        # Split per board
        gm_rows  = [(r[0], r[2], r[3], r[4]) for r in records if r[1] == "GM"]
        fol_rows = [(r[0], r[2], r[3], r[4]) for r in records if r[1] == "FOL"]

        log.info("")
        log.info("--- Per-board drift_ppb statistics ---")
        analyse_per_board("GM ", gm_rows, log)
        log.info("")
        analyse_per_board("FOL", fol_rows, log)

        log.info("")
        log.info("--- Cross-board rate residual ---")
        analyse_cross_board(gm_rows, fol_rows, log)

        log.info("")
        log.info(f"  Raw CSV for offline plotting: {csv_path}")
        log.info(f"  Suggested columns to plot:")
        log.info(f"    - drift_ppb vs pc_time_s, split by board (shows filter jitter over time)")
        log.info(f"    - (FOL_wc - GM_wc_interp) vs pc_time_s (shows cumulative phase error)")

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except: pass
        log.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
