#!/usr/bin/env python3
"""PTP Reproducibility Test
============================

Runs ptp_sync_before_after_test.py N times (default 5) and summarises all
results in a single comparison table.

Goal: verify that the PTP synchronisation test produces consistent results
across repeated runs (reproducibility check).

Usage:
    python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10

    # Custom number of runs and durations (forwarded to sub-test):
    python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10 \\
        --runs 3 --free-run-s 30 --ptp-s 30

Requirements:
    pip install pyserial
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Patterns to parse from individual run log files
# ---------------------------------------------------------------------------

# Phase 0 section marker
RE_PHASE0_HDR  = re.compile(r"PHASE 0 — FREE RUNNING")
RE_PHASE1_HDR  = re.compile(r"PHASE 1 — PTP ACTIVE")

# Slope line:  Slope     : -518511 ppb  (-518.5108 ppm)
RE_SLOPE       = re.compile(r"Slope\s*:\s*([+-]?\d+)\s*ppb\s*\(([+-]?[\d.]+)\s*ppm\)")

# Stdev line:  Stdev     : 388076 ns  (388.076 µs)
RE_STDEV       = re.compile(r"Stdev\s*:\s*\d+\s*ns\s*\(([\d.]+)\s*[µu]s\)")

# Drift FOL line:  Drift FOL  : +1 ppb  (mean)  ±6 ppb  (stdev)
RE_DRIFT_FOL   = re.compile(r"Drift FOL\s*:.*?\(mean\)\s*.*?([+-]?\d+)\s*ppb\s*\(stdev\)")

# FINE convergence:  Follower reached FINE in 2.7s
RE_FINE_TIME   = re.compile(r"Follower reached FINE in ([\d.]+)s")

# Slope reduction:  Slope reduction by PTP : 99.9 %
RE_REDUCTION   = re.compile(r"Slope reduction by PTP\s*:\s*([\d.]+)\s*%")

# Overall result:  Overall  : PASS (7/7)
RE_OVERALL     = re.compile(r"Overall\s*:\s*(PASS|FAIL)\s*\((\d+)/(\d+)\)")

# Duration:  Duration : 155.9 s
RE_DURATION    = re.compile(r"Duration\s*:\s*([\d.]+)\s*s")

# TISUBN value:  scheduling TI=40 TISUBN=0xFC00000C
RE_TISUBN      = re.compile(r"scheduling TI=\d+ TISUBN=(0x[0-9A-Fa-f]+)")


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------

def parse_run_log(log_path: str) -> dict:
    """Parse a ptp_sync_before_after_test log file and return a results dict."""
    result = {
        "log_file":        os.path.basename(log_path),
        "overall":         "FAIL",
        "passed":          0,
        "total":           0,
        "duration_s":      None,
        # Phase 0
        "free_slope_ppm":  None,
        "free_stdev_us":   None,
        # PTP convergence
        "fine_time_s":     None,
        "tisubn":          None,
        # Phase 1
        "ptp_slope_ppm":   None,
        "ptp_stdev_us":    None,
        "drift_fol_stdev": None,
        # Derived
        "reduction_pct":   None,
    }

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return result

    # We parse in two passes: first find section boundaries, then extract
    # values within the correct section.

    phase0_block = []
    phase1_block = []
    full_text    = "".join(lines)

    # --- TISUBN (only one occurrence expected) ---
    m = RE_TISUBN.search(full_text)
    if m:
        result["tisubn"] = m.group(1)

    # --- FINE convergence time ---
    m = RE_FINE_TIME.search(full_text)
    if m:
        result["fine_time_s"] = float(m.group(1))

    # --- Slope reduction ---
    m = RE_REDUCTION.search(full_text)
    if m:
        result["reduction_pct"] = float(m.group(1))

    # --- Overall ---
    m = RE_OVERALL.search(full_text)
    if m:
        result["overall"]  = m.group(1)
        result["passed"]   = int(m.group(2))
        result["total"]    = int(m.group(3))

    # --- Duration ---
    m = RE_DURATION.search(full_text)
    if m:
        result["duration_s"] = float(m.group(1))

    # --- Phase-specific blocks: split on section headers ---
    # Find report blocks like "[PHASE 0 — FREE RUNNING (no PTP)]"
    # and "[PHASE 1 — PTP ACTIVE (compensated)]"
    in_phase0 = False
    in_phase1 = False
    for line in lines:
        if RE_PHASE0_HDR.search(line):
            in_phase0 = True
            in_phase1 = False
        elif RE_PHASE1_HDR.search(line):
            in_phase1 = True
            in_phase0 = False
        if in_phase0:
            phase0_block.append(line)
        elif in_phase1:
            phase1_block.append(line)

    # Extract Phase 0 metrics
    p0_text = "".join(phase0_block)
    slopes0 = RE_SLOPE.findall(p0_text)
    stdevs0 = RE_STDEV.findall(p0_text)
    if slopes0:
        result["free_slope_ppm"] = float(slopes0[0][1])
    if stdevs0:
        result["free_stdev_us"] = float(stdevs0[0])

    # Extract Phase 1 metrics
    p1_text = "".join(phase1_block)
    slopes1 = RE_SLOPE.findall(p1_text)
    stdevs1 = RE_STDEV.findall(p1_text)
    if slopes1:
        result["ptp_slope_ppm"] = float(slopes1[0][1])
    if stdevs1:
        result["ptp_stdev_us"] = float(stdevs1[0])

    m = RE_DRIFT_FOL.search(p1_text)
    if m:
        result["drift_fol_stdev"] = int(m.group(1))

    return result


# ---------------------------------------------------------------------------
# Table printer
# ---------------------------------------------------------------------------

def _fmt(val, fmt: str, unit: str = "") -> str:
    if val is None:
        return "—"
    return format(val, fmt) + unit


def print_summary_table(run_results: list, log: object):
    W = 80
    log.write("=" * W + "\n")
    log.write("  REPRODUCIBILITY SUMMARY — all runs\n")
    log.write("=" * W + "\n\n")

    n = len(run_results)

    # Header
    col0  = 6   # Run #
    col1  = 10  # Free slope
    col2  = 9   # Free stdev
    col3  = 8   # FINE t
    col4  = 10  # PTP slope
    col5  = 9   # PTP stdev
    col6  = 8   # drift stdev
    col7  = 8   # reduction
    col8  = 8   # duration
    col9  = 6   # result

    hdr = (f"{'Run':>{col0}}  "
           f"{'Free(ppm)':>{col1}}  "
           f"{'FreeStd':>{col2}}  "
           f"{'FINE(s)':>{col3}}  "
           f"{'PTP(ppm)':>{col4}}  "
           f"{'PTPStd':>{col5}}  "
           f"{'dFOLstd':>{col6}}  "
           f"{'Reduc%':>{col7}}  "
           f"{'Dur(s)':>{col8}}  "
           f"{'Result':>{col9}}")
    sep = "-" * len(hdr)

    log.write(hdr + "\n")
    log.write(sep + "\n")

    valid_free_slopes  = []
    valid_ptp_slopes   = []
    valid_ptp_stdevs   = []
    valid_fine_times   = []
    valid_reductions   = []
    passed_count       = 0

    for i, r in enumerate(run_results, 1):
        fs  = r["free_slope_ppm"]
        fd  = r["free_stdev_us"]
        ft  = r["fine_time_s"]
        ps  = r["ptp_slope_ppm"]
        pd  = r["ptp_stdev_us"]
        ds  = r["drift_fol_stdev"]
        rc  = r["reduction_pct"]
        dur = r["duration_s"]
        ok  = r["overall"]

        row = (f"{i:>{col0}}  "
               f"{_fmt(fs, '+.3f'):>{col1}}  "
               f"{_fmt(fd, '.1f', 'µs'):>{col2}}  "
               f"{_fmt(ft, '.1f', 's'):>{col3}}  "
               f"{_fmt(ps, '+.4f'):>{col4}}  "
               f"{_fmt(pd, '.1f', 'µs'):>{col5}}  "
               f"{_fmt(ds, 'd', 'ppb') if ds is not None else '—':>{col6}}  "
               f"{_fmt(rc, '.1f', '%'):>{col7}}  "
               f"{_fmt(dur, '.0f', 's'):>{col8}}  "
               f"{ok:>{col9}}")
        log.write(row + "\n")

        if fs  is not None: valid_free_slopes.append(fs)
        if ps  is not None: valid_ptp_slopes.append(ps)
        if pd  is not None: valid_ptp_stdevs.append(pd)
        if ft  is not None: valid_fine_times.append(ft)
        if rc  is not None: valid_reductions.append(rc)
        if ok  == "PASS":   passed_count += 1

    log.write(sep + "\n\n")

    # Statistics row
    import statistics as _st

    def _stat(vals, fmt):
        if not vals:
            return "—"
        if len(vals) == 1:
            return format(vals[0], fmt)
        return f"{format(min(vals), fmt)} … {format(max(vals), fmt)}"

    def _mean_stdev(vals, fmt):
        if not vals:
            return "—"
        if len(vals) < 2:
            return f"mean={format(vals[0], fmt)}"
        return (f"mean={format(_st.mean(vals), fmt)}"
                f"  stdev={format(_st.stdev(vals), fmt)}")

    log.write("  Statistics over all runs:\n\n")
    log.write(f"    Free-run slope   : {_stat(valid_free_slopes, '+.3f')} ppm\n")
    log.write(f"                       {_mean_stdev(valid_free_slopes, '+.3f')} ppm\n")
    log.write(f"    PTP slope        : {_stat(valid_ptp_slopes, '+.4f')} ppm\n")
    log.write(f"                       {_mean_stdev(valid_ptp_slopes, '+.4f')} ppm\n")
    log.write(f"    PTP residual std : {_stat(valid_ptp_stdevs, '.1f')} µs\n")
    log.write(f"                       {_mean_stdev(valid_ptp_stdevs, '.1f')} µs\n")
    log.write(f"    FINE conv. time  : {_stat(valid_fine_times, '.1f')} s\n")
    log.write(f"    Reduction        : {_stat(valid_reductions, '.1f')} %\n")
    log.write(f"\n    Passed           : {passed_count}/{n}\n")

    log.write("\n" + "=" * W + "\n")
    overall = "PASS" if passed_count == n else "FAIL"
    log.write(f"  Overall reproducibility: {overall}  ({passed_count}/{n} runs passed)\n")
    log.write("=" * W + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="PTP Reproducibility Test — runs ptp_sync_before_after_test.py N times",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    p.add_argument("--runs",     default=5, type=int,
                   help="Number of repetitions (default: 5)")
    p.add_argument("--gm-port",  default="COM8",
                   help="COM port of Board A / Grandmaster (default: COM8)")
    p.add_argument("--fol-port", default="COM10",
                   help="COM port of Board B / Follower (default: COM10)")
    p.add_argument("--gm-ip",    default="192.168.0.30")
    p.add_argument("--fol-ip",   default="192.168.0.20")
    p.add_argument("--netmask",  default="255.255.255.0")
    p.add_argument("--baudrate", default=115200, type=int)
    p.add_argument("--free-run-s", default=60.0, type=float)
    p.add_argument("--ptp-s",      default=60.0, type=float)
    p.add_argument("--pause-ms",   default=500,  type=int)
    p.add_argument("--settle",     default=5.0,  type=float)
    p.add_argument("--conv-timeout", default=60.0, type=float)
    p.add_argument("--slope-threshold-ppm",    default=2.0,   type=float)
    p.add_argument("--residual-threshold-us",  default=500.0, type=float)
    p.add_argument("--no-swap",    action="store_true")
    p.add_argument("--no-clk-set", action="store_true")
    p.add_argument("--verbose",    action="store_true")

    args = p.parse_args()

    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = f"ptp_reproducibility_test_{ts}.log"

    # Build the base command for the sub-test (all forwarded args)
    sub_test = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ptp_sync_before_after_test.py")
    base_cmd = [
        sys.executable, sub_test,
        "--gm-port",  args.gm_port,
        "--fol-port", args.fol_port,
        "--gm-ip",    args.gm_ip,
        "--fol-ip",   args.fol_ip,
        "--netmask",  args.netmask,
        "--baudrate", str(args.baudrate),
        "--free-run-s", str(args.free_run_s),
        "--ptp-s",      str(args.ptp_s),
        "--pause-ms",   str(args.pause_ms),
        "--settle",     str(args.settle),
        "--conv-timeout",           str(args.conv_timeout),
        "--slope-threshold-ppm",    str(args.slope_threshold_ppm),
        "--residual-threshold-us",  str(args.residual_threshold_us),
    ]
    if args.no_swap:    base_cmd.append("--no-swap")
    if args.no_clk_set: base_cmd.append("--no-clk-set")
    if args.verbose:    base_cmd.append("--verbose")

    run_results = []
    run_logs    = []
    t_total_start = datetime.datetime.now()

    # Open reproducibility summary log
    with open(log_file, "w", encoding="utf-8") as summary_fh:

        def log_both(msg: str):
            print(msg)
            summary_fh.write(msg + "\n")
            summary_fh.flush()

        log_both("=" * 80)
        log_both("  PTP Reproducibility Test")
        log_both("=" * 80)
        log_both(f"Date        : {t_total_start.strftime('%Y-%m-%d %H:%M:%S')}")
        log_both(f"Runs        : {args.runs}")
        log_both(f"GM port     : {args.gm_port}  IP {args.gm_ip}")
        log_both(f"FOL port    : {args.fol_port}  IP {args.fol_ip}")
        log_both(f"Free-run    : {args.free_run_s:.0f} s")
        log_both(f"PTP active  : {args.ptp_s:.0f} s")
        log_both(f"Pause       : {args.pause_ms} ms")
        log_both("")

        for run_idx in range(1, args.runs + 1):
            run_ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            run_log = f"ptp_sync_before_after_test_{run_ts}.log"
            cmd     = base_cmd + ["--log-file", run_log]

            log_both(f"\n{'='*80}")
            log_both(f"  RUN {run_idx}/{args.runs}  →  {run_log}")
            log_both(f"{'='*80}\n")

            try:
                proc = subprocess.run(cmd, check=False)
                rc   = proc.returncode
            except Exception as exc:
                log_both(f"  ERROR launching sub-test: {exc}")
                rc = -1

            # Parse the log that was just written
            parsed = parse_run_log(run_log)
            parsed["run_idx"]  = run_idx
            parsed["log_file"] = run_log
            run_results.append(parsed)
            run_logs.append(run_log)

            status = parsed["overall"]
            log_both(f"\n  Run {run_idx} finished — {status}"
                     f"  (exit code {rc})"
                     f"  log: {run_log}")

        # ---- Final summary table ----
        log_both("")
        elapsed_total = (datetime.datetime.now() - t_total_start).total_seconds()
        print_summary_table(run_results, summary_fh)

        # Also print table to stdout
        print_summary_table(run_results, sys.stdout)

        log_both(f"\nTotal duration : {elapsed_total:.0f} s"
                 f"  ({elapsed_total/60:.1f} min)")
        log_both(f"Summary log    : {log_file}")
        log_both(f"Run logs       : {', '.join(run_logs)}")

    passed = sum(1 for r in run_results if r["overall"] == "PASS")
    return 0 if passed == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
