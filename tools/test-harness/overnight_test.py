#!/usr/bin/env python3
"""Overnight Long-Running Stability Test
=======================================

Designed to run unattended (e.g. overnight).  Exercises the full PTP stack
over many hours while continuously monitoring sync quality.

Flow:
  1. Reset both boards, wait for [APP] Build: banner
  2. Configure IPs, verify ping
  3. ptp_trace on + sw_ntp_trace on  (full debug output)
  4. Start PTP — board A = Grandmaster, board B = Follower
  5. Wait for FOL FINE convergence
  6. [Optional, --saleae] arm cyclic_fire on both boards at 1 kHz so PD10
     toggles continuously — Saleae captures short windows per drift check.
  7. Monitoring loop (until Ctrl-C or --duration-h elapses):
       - every 20 s : paired clk_get on both boards (sync timestamps)
       - every  5 min: 10 s drift-compensation test (paired sampling +
                       linear regression to verify residual slope <
                       --slope-threshold-ppm).  If --saleae: additionally
                       capture 2 s of PD10 edges and analyse cross-board
                       phase (median, MAD, stdev).
  8. On exit: cyclic_stop both boards; print summary of drift + phase checks.

Everything — commands sent, responses received, trace output from both
boards, and test-script progress — is mirrored into a timestamped log
file.  Each line is prefixed with the host system time (millisecond
precision) so events can be correlated across the whole night.

Usage:
    python overnight_test.py --gm-port COM8 --fol-port COM10
    python overnight_test.py --duration-h 8 --saleae
    python overnight_test.py --clk-interval-s 20 --drift-interval-s 300

Requirements:
    pip install pyserial
    pip install logic2-automation   # only if using --saleae
"""

import argparse
import datetime
import os
import re
import shutil
import signal
import statistics
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "ptp-analysis", "ptp-drift-tests"))

from ptp_drift_compensate_test import (                          # noqa: E402
    open_port, send_command, wait_for_pattern, reset_and_wait_for_boot,
    SerialMux, TraceCollector, single_measurement_mux,
    collect_clk_get_samples, evaluate_samples, print_regression_report,
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    RE_PING_DONE, RE_PING_REPLY, RE_FOL_START, RE_GM_START,
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

# Pull the Saleae helpers from the HW test.  Import inside a try/except so
# the overnight test still runs when logic2-automation is not installed and
# --saleae is not requested.
_SALEAE_OK = True
_SALEAE_IMPORT_ERR: Optional[str] = None
try:
    from cyclic_fire_hw_test import (                            # noqa: E402
        start_saleae_capture, export_capture_csv, parse_edges,
        compute_phase_deltas, overlap_window, robust, percentile,
        RE_CYC_START_OK,
    )
except ImportError as _exc:
    _SALEAE_OK = False
    _SALEAE_IMPORT_ERR = str(_exc)

RE_CLK_GET_RESP = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")
RE_CYC_STOP_OK  = re.compile(r"cyclic_stop OK|cyclic stopped", re.IGNORECASE)

# Override the ptp_drift_compensate_test patterns: current firmware (ptp_cli.c)
# prints "PTP master STARTED (via CLI)" / "PTP follower STARTED (via CLI)" —
# the inherited patterns ("PTP Grandmaster enabled" / "PTP Follower enabled")
# are stale and never match, so setup failed at Step 5.
RE_GM_START  = re.compile(r"PTP master STARTED",   re.IGNORECASE)
RE_FOL_START = re.compile(r"PTP follower STARTED", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Timestamped logger
# ---------------------------------------------------------------------------

class TSLogger:
    """Mirrors stdout and a file; every line is prefixed with wallclock time."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._fh = open(log_path, "w", encoding="utf-8", buffering=1)

    @staticmethod
    def _ts() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _write(self, line: str):
        stamped = f"[{self._ts()}] {line}"
        safe = stamped.encode(sys.stdout.encoding or "utf-8",
                              errors="replace").decode(
                              sys.stdout.encoding or "utf-8")
        print(safe, flush=True)
        self._fh.write(stamped + "\n")
        # Explicit flush + fsync on every line so the log file on disk is
        # always up to date — even if the host is power-cycled mid-run
        # everything up to the last log line is preserved.
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except (OSError, ValueError):
            # fsync can fail on some platforms/redirects — flush alone is
            # still good enough in that case.
            pass

    # Provide both info() and debug() so we can drop this logger in wherever
    # ptp_drift_compensate_test's helpers expect a Logger.
    def info(self, msg: str):  self._write(msg)
    def debug(self, msg: str): self._write(f"  [DBG] {msg}")

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------------------
# Stop-flag (set by Ctrl-C so monitoring loop exits cleanly after the current
# step instead of dropping a stack trace into the log).
# ---------------------------------------------------------------------------

_STOP = False

def _sigint(_sig, _frm):
    global _STOP
    _STOP = True

signal.signal(signal.SIGINT, _sigint)


# ---------------------------------------------------------------------------
# Non-blocking keypress detection — any key press ends the monitoring loop
# cleanly and triggers the final report.  Windows: msvcrt.  Non-Windows:
# fall back to select() on stdin (only works when stdin is a tty).
# ---------------------------------------------------------------------------

try:
    import msvcrt   # Windows
    def key_pressed() -> bool:
        if msvcrt.kbhit():
            msvcrt.getch()   # consume so it doesn't leak to the shell
            return True
        return False
    _KEY_HINT = "press any key to stop & generate report"
except ImportError:
    import select
    def key_pressed() -> bool:
        try:
            if select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.read(1)
                return True
        except Exception:
            pass
        return False
    _KEY_HINT = "press Enter to stop & generate report"


# ---------------------------------------------------------------------------
# cyclic_fire arm / stop — gets PD10 toggling at a known rate on both boards
# so Saleae can measure cross-board phase any time during the night.
# ---------------------------------------------------------------------------

def _arm_one(ser, label: str, period_us: int, anchor_lead_ms: int,
             log: TSLogger) -> bool:
    """Issue one cyclic_start attempt on a single board, with a fresh
    anchor = board's own clk_get + anchor_lead_ms.  Returns True if the
    firmware replied 'cyclic_start OK'."""
    resp = send_command(ser, "clk_get", 2.0, log)
    m = RE_CLK_GET_RESP.search(resp)
    if not m:
        log.info(f"  [{label}] clk_get failed: {resp[:120]!r}")
        return False
    anchor = int(m.group(1)) + anchor_lead_ms * 1_000_000
    resp = send_command(ser, f"cyclic_start {period_us} {anchor}", 2.0, log)
    ok = bool(RE_CYC_START_OK.search(resp))
    log.info(f"  [{label}] cyclic_start {period_us} {anchor} -> "
             f"{'OK' if ok else 'FAIL'}  ({resp.strip()[-200:]!r})")
    return ok


def start_cyclic_fire(ser_gm, ser_fol, period_us: int, anchor_lead_ms: int,
                      log: TSLogger,
                      settle_s: float = 3.0,
                      max_retries: int = 5,
                      retry_pause_s: float = 2.0) -> bool:
    """Arm cyclic_fire on both boards.

    PTP_CLOCK is not always "valid" the instant after 'PTP FINE' prints —
    the servo still needs a few updates before anchor_wc is stable.  That
    is why a naive cyclic_start right after FINE fails with
    "PTP_CLOCK not valid".  We therefore:
      - settle for `settle_s` seconds before the first attempt
      - re-read clk_get (and recompute anchor) per attempt so a stale
        anchor never ends up in the past
      - retry up to `max_retries` times per board, pausing `retry_pause_s`
        between attempts
    Each board is armed independently with its own clk_get + anchor_lead
    — the PTP servo keeps their wallclocks aligned to µs, so the real-time
    moment of the first fire is essentially identical on both boards.
    Returns True only if BOTH boards replied 'cyclic_start OK'.
    """
    log.info("\n=== Arming cyclic_fire on both boards (PD10 runs all night) ===")
    log.info(f"  Settling {settle_s:.1f} s so PTP_CLOCK becomes valid after FINE")
    time.sleep(settle_s)

    for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
        last_ok = False
        for attempt in range(1, max_retries + 1):
            last_ok = _arm_one(ser, lbl, period_us, anchor_lead_ms, log)
            if last_ok:
                break
            if attempt < max_retries:
                log.info(f"  [{lbl}] cyclic_start retry {attempt}/{max_retries-1}"
                         f" after {retry_pause_s:.1f} s ...")
                time.sleep(retry_pause_s)
        if not last_ok:
            log.info(f"  [{lbl}] cyclic_start gave up after {max_retries} attempts")
            return False
    return True


def stop_cyclic_fire(ser_gm, ser_fol, log: TSLogger):
    """Best-effort cyclic_stop on both boards.  Errors logged but not raised."""
    for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
        try:
            resp = send_command(ser, "cyclic_stop", 2.0, log)
            log.info(f"  [{lbl}] cyclic_stop -> {resp.strip()!r}")
        except Exception as exc:
            log.info(f"  [{lbl}] cyclic_stop failed: {exc}")


# ---------------------------------------------------------------------------
# Saleae capture + analysis — runs for a few seconds and reports phase stats
# ---------------------------------------------------------------------------

def run_saleae_check(sample_rate: int, duration_s: float,
                     out_dir: Path, check_idx: int,
                     keep_csv: bool,
                     log: TSLogger) -> Optional[dict]:
    """Trigger a short Saleae capture, export CSV, parse edges, compute
    cross-board phase stats on rising edges.  Returns a dict with
    median/MAD/stdev in ns, or None on failure.
    Each capture lives in out_dir/check_<idx>/ — deleted afterwards
    unless keep_csv=True (saves disk over many hours)."""
    cap_dir = out_dir / f"check_{check_idx:04d}"
    cap_dir.mkdir(parents=True, exist_ok=True)
    mgr = capture = None
    try:
        mgr, capture = start_saleae_capture(sample_rate, duration_s, log)
        # start_saleae_capture begins a TimedCapture of duration_s — wait it out.
        time.sleep(duration_s + 0.2)
        capture.wait()
        csv_path = export_capture_csv(capture, cap_dir, log)
        rising, falling = parse_edges(csv_path)
    except Exception as exc:
        log.info(f"  SALEAE ERROR: {exc}")
        return None
    finally:
        try:
            if capture: capture.close()
        except Exception:
            pass

    log.info(f"  Saleae: GM rising={len(rising[0])} FOL rising={len(rising[1])}")
    if len(rising[0]) < 10 or len(rising[1]) < 10:
        log.info("  Saleae: too few edges — phase analysis skipped")
        if not keep_csv:
            shutil.rmtree(cap_dir, ignore_errors=True)
        return None

    t_lo, t_hi = overlap_window(rising[0], rising[1], guard_s=0.1)
    deltas = compute_phase_deltas(rising[0], rising[1], t_lo, t_hi)
    if len(deltas) < 5:
        log.info("  Saleae: too few paired edges — phase analysis skipped")
        if not keep_csv:
            shutil.rmtree(cap_dir, ignore_errors=True)
        return None

    # Deltas are in seconds (Saleae CSV uses seconds); convert to ns.
    deltas_ns = [d * 1e9 for d in deltas]
    med_ns, mad_ns = robust(deltas_ns)
    stdev_ns = statistics.stdev(deltas_ns) if len(deltas_ns) > 1 else 0.0
    sorted_d = sorted(deltas_ns)
    p05 = percentile(sorted_d,  5.0)
    p95 = percentile(sorted_d, 95.0)

    log.info(f"  Saleae phase (FOL - GM):  median={med_ns:+.0f} ns"
             f"  MAD={mad_ns:.0f} ns  stdev={stdev_ns:.0f} ns"
             f"  [p05..p95]={p05:+.0f}..{p95:+.0f} ns"
             f"  (n={len(deltas_ns)})")

    if not keep_csv:
        shutil.rmtree(cap_dir, ignore_errors=True)

    return {
        "n":        len(deltas_ns),
        "median_ns": med_ns,
        "mad_ns":    mad_ns,
        "stdev_ns":  stdev_ns,
        "p05_ns":    p05,
        "p95_ns":    p95,
    }


# ---------------------------------------------------------------------------
# Setup phase
# ---------------------------------------------------------------------------

def setup_boards(ser_gm, ser_fol, gm_ip, fol_ip, netmask,
                 conv_timeout, log: TSLogger) -> bool:
    log.info("=== Step 1: Reset both boards ===")
    gm_build  = reset_and_wait_for_boot(ser_gm,  label="GM",  log=log)
    fol_build = reset_and_wait_for_boot(ser_fol, label="FOL", log=log)
    log.info(f"  GM  build: {gm_build[0]} {gm_build[1]}")
    log.info(f"  FOL build: {fol_build[0]} {fol_build[1]}")

    log.info("\n=== Step 2: IP configuration ===")
    for lbl, ser, ip in [("GM", ser_gm, gm_ip), ("FOL", ser_fol, fol_ip)]:
        resp = send_command(ser, f"setip eth0 {ip} {netmask}",
                            conv_timeout, log)
        log.info(f"  [{lbl}] setip {ip} -> {resp.strip()!r}")
        if not RE_IP_SET.search(resp):
            log.info(f"  [{lbl}] setip FAILED")
            return False

    log.info("\n=== Step 3: Ping connectivity ===")
    for src_lbl, src_ser, dst_ip in [
        ("GM -> FOL", ser_gm,  fol_ip),
        ("FOL -> GM", ser_fol, gm_ip),
    ]:
        src_ser.reset_input_buffer()
        src_ser.write(f"ping {dst_ip}\r\n".encode("ascii"))
        matched, elapsed, ms = wait_for_pattern(
            src_ser, RE_PING_DONE, timeout=15.0, log=log,
            extra_patterns={"reply": RE_PING_REPLY}, live_log=True)
        ok = matched or ms.get("reply") is not None
        log.info(f"  [{src_lbl}] ping: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
        if not ok:
            return False

    log.info("\n=== Step 4: Enable all debug traces ===")
    for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
        for cmd in ("ptp_trace on", "sw_ntp_trace on", "clk_jump_log on"):
            resp = send_command(ser, cmd, conv_timeout, log)
            log.info(f"  [{lbl}] {cmd} -> {resp.strip()!r}")

    log.info("\n=== Step 5: Start PTP (GM = master, FOL = follower) ===")
    resp = send_command(ser_fol, "ptp_mode follower", conv_timeout, log)
    log.info(f"  [FOL] ptp_mode follower -> {resp.strip()!r}")
    if not RE_FOL_START.search(resp):
        log.info("  [FOL] follower NOT confirmed")
        return False

    time.sleep(0.5)
    ser_fol.reset_input_buffer()

    ser_gm.reset_input_buffer()
    ser_gm.write(b"ptp_mode master\r\n")
    gm_ok, _, _ = wait_for_pattern(ser_gm, RE_GM_START, timeout=conv_timeout,
                                    log=log, live_log=True)
    log.info(f"  [GM ] ptp_mode master -> {'OK' if gm_ok else 'FAIL'}")
    if not gm_ok:
        return False

    log.info(f"\n=== Step 6: Wait for FOL FINE (timeout={conv_timeout:.0f}s) ===")
    matched, elapsed, milestones = wait_for_pattern(
        ser_fol, RE_FINE, conv_timeout, log,
        extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                         "HARD_SYNC": RE_HARD_SYNC,
                         "COARSE":    RE_COARSE},
        live_log=True)
    ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
    if matched:
        log.info(f"  FOL FINE reached in {elapsed:.1f}s  ({ms_str})")
    else:
        log.info(f"  FOL FINE NOT reached within {conv_timeout:.0f}s ({ms_str})")
        return False

    return True


# ---------------------------------------------------------------------------
# Monitoring loop
# ---------------------------------------------------------------------------

def run_monitoring(mux_gm: SerialMux, mux_fol: SerialMux,
                   trace: TraceCollector,
                   duration_s: Optional[float],
                   clk_interval_s: float,
                   drift_interval_s: float,
                   drift_duration_s: float,
                   drift_pause_ms: int,
                   slope_threshold_ppm: float,
                   outlier_us: float,
                   saleae_enabled: bool,
                   saleae_interval_s: float,
                   saleae_sample_rate: int,
                   saleae_duration_s: float,
                   saleae_out_dir: Optional[Path],
                   saleae_keep_csv: bool,
                   log: TSLogger) -> Tuple[List[dict], List[dict]]:
    log.info("\n=== Step 7: Monitoring loop ===")
    log.info(f"  clk_get every {clk_interval_s:.0f} s")
    log.info(f"  drift check every {drift_interval_s:.0f} s"
             f" ({drift_duration_s:.0f} s duration, pause={drift_pause_ms} ms)")
    if saleae_enabled:
        log.info(f"  Saleae phase check every {saleae_interval_s:.0f} s"
                 f" ({saleae_duration_s:.1f} s @ "
                 f"{saleae_sample_rate/1e6:.0f} MS/s)")
    log.info(f"  Stop anytime: {_KEY_HINT}, or Ctrl-C")
    if duration_s is not None:
        log.info(f"  Auto-stop after {duration_s/3600:.2f} h")

    drift_results: List[dict] = []
    phase_results: List[dict] = []
    t_start        = time.monotonic()
    next_clk_t     = t_start
    next_drift_t   = t_start + drift_interval_s   # first drift after one interval
    next_saleae_t  = (t_start + saleae_interval_s) if saleae_enabled \
                     else float("inf")
    n_clk          = 0
    n_drift        = 0
    n_drift_pass   = 0
    n_saleae       = 0

    while not _STOP:
        if key_pressed():
            log.info(f"  Key press detected after {(time.monotonic()-t_start)/3600:.2f} h"
                     f" — stopping monitoring loop and generating report.")
            break
        now = time.monotonic()
        if duration_s is not None and now - t_start >= duration_s:
            log.info(f"  Duration reached ({duration_s/3600:.2f} h), stopping.")
            break

        # Drift check takes priority when both are due
        if now >= next_drift_t:
            n_drift += 1
            elapsed_h = (now - t_start) / 3600.0
            log.info(f"\n--- Drift check #{n_drift}  (t+{elapsed_h:.2f} h) ---")
            try:
                samples, elapsed_s, drifts_gm, drifts_fol = \
                    collect_clk_get_samples(
                        mux_gm, mux_fol, trace,
                        duration_s=drift_duration_s,
                        pause_ms=drift_pause_ms,
                        no_swap=False,
                        label=f"DRIFT#{n_drift}",
                        outlier_us=outlier_us,
                        log=log)
                stats = evaluate_samples(samples, elapsed_s,
                                         drifts_gm, drifts_fol,
                                         f"DRIFT#{n_drift}", log)
                if stats:
                    print_regression_report(stats, f"DRIFT#{n_drift}", log)
                    slope_ok = abs(stats["slope_ppm"]) < slope_threshold_ppm
                    if slope_ok:
                        n_drift_pass += 1
                    stats["check_index"]    = n_drift
                    stats["elapsed_h"]      = elapsed_h
                    stats["passed"]         = slope_ok
                    drift_results.append(stats)
                    log.info(f"  DRIFT#{n_drift}: {'PASS' if slope_ok else 'FAIL'}"
                             f"  (slope={stats['slope_ppm']:+.4f} ppm,"
                             f" threshold={slope_threshold_ppm} ppm)")
                else:
                    log.info(f"  DRIFT#{n_drift}: insufficient samples")
            except Exception as exc:
                log.info(f"  DRIFT#{n_drift} ERROR: {exc}")

            next_drift_t = time.monotonic() + drift_interval_s
            # Resync next_clk_t so we don't immediately fire clk_get right
            # after the drift check (which already spammed clk_get).
            next_clk_t = time.monotonic() + clk_interval_s
            continue

        # Saleae phase check runs on its own independent schedule (every
        # saleae_interval_s, default 60 s) — PD10 is toggling continuously
        # thanks to cyclic_start at setup time.
        if saleae_enabled and now >= next_saleae_t:
            n_saleae += 1
            elapsed_h = (now - t_start) / 3600.0
            log.info(f"\n--- Saleae phase check #{n_saleae}  (t+{elapsed_h:.2f} h) ---")
            try:
                phase = run_saleae_check(saleae_sample_rate,
                                          saleae_duration_s,
                                          saleae_out_dir,
                                          n_saleae,
                                          saleae_keep_csv,
                                          log)
                if phase is not None:
                    phase["check_index"] = n_saleae
                    phase["elapsed_h"]   = elapsed_h
                    phase_results.append(phase)
            except Exception as exc:
                log.info(f"  SALEAE#{n_saleae} ERROR: {exc}")
            next_saleae_t = time.monotonic() + saleae_interval_s
            continue

        if now >= next_clk_t:
            n_clk += 1
            diff, d_gm, d_fol, _ = single_measurement_mux(mux_gm, mux_fol,
                                                           swap=(n_clk % 2 == 1))
            trace_lines = trace.drain_all()
            if diff is None:
                log.info(f"  [CLK#{n_clk:5d}] ERROR (no clk_get response)")
            else:
                log.info(f"  [CLK#{n_clk:5d}] diff={diff/1000:+9.2f} us"
                         f"  drift GM={d_gm:+d} FOL={d_fol:+d} ppb")
            # Print any trace output that arrived since last iteration
            for _, lbl, txt in trace_lines:
                if txt.strip() and not txt.lstrip().startswith(">"):
                    log.info(f"    [TRACE][{lbl}] {txt}")
            next_clk_t = time.monotonic() + clk_interval_s
            continue

        # Sleep until whichever deadline comes first — but cap at 0.5 s so
        # keypress / Ctrl-C are noticed quickly.
        sleep_until = min(next_clk_t, next_drift_t, next_saleae_t)
        time.sleep(max(0.0, min(0.5, sleep_until - time.monotonic())))

    return drift_results, phase_results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(drift_results: List[dict], phase_results: List[dict],
                  log: TSLogger, slope_threshold_ppm: float):
    log.info("\n" + "=" * 66)
    log.info("OVERNIGHT TEST SUMMARY")
    log.info("=" * 66)
    if not drift_results:
        log.info("  No drift checks completed.")
        return

    n_pass = sum(1 for r in drift_results if r["passed"])
    n_tot  = len(drift_results)
    slopes = [r["slope_ppm"] for r in drift_results]

    log.info(f"  Drift checks : {n_pass}/{n_tot} PASS"
             f"  (threshold |slope| < {slope_threshold_ppm} ppm)")
    log.info(f"  Slope range  : {min(slopes):+.4f} .. {max(slopes):+.4f} ppm")
    log.info(f"  Slope mean   : {statistics.mean(slopes):+.4f} ppm")
    if len(slopes) > 1:
        log.info(f"  Slope stdev  : {statistics.stdev(slopes):.4f} ppm")

    log.info("")
    log.info("  DRIFT CHECKS")
    log.info(f"  idx  t[h]    slope[ppm]  res_stdev[us]  drift_FOL[ppb]  result")
    for r in drift_results:
        log.info(f"  {r['check_index']:3d}"
                 f"  {r['elapsed_h']:5.2f}"
                 f"  {r['slope_ppm']:+10.4f}"
                 f"  {r['res_stdev_ns']/1000:13.3f}"
                 f"  {r['mean_drift_fol']:+14.0f}"
                 f"    {'PASS' if r['passed'] else 'FAIL'}")

    if phase_results:
        meds   = [p["median_ns"] for p in phase_results]
        stdevs = [p["stdev_ns"]  for p in phase_results]
        log.info("")
        log.info(f"  SALEAE PHASE CHECKS  ({len(phase_results)} samples)")
        log.info(f"    median range : {min(meds):+.0f} .. {max(meds):+.0f} ns")
        log.info(f"    median mean  : {statistics.mean(meds):+.0f} ns")
        log.info(f"    stdev mean   : {statistics.mean(stdevs):.0f} ns")
        log.info("")
        log.info(f"  idx   t[h]  phase_med[ns]  phase_stdev[ns]  phase_MAD[ns]  n")
        for p in phase_results:
            log.info(f"  {p['check_index']:4d}"
                     f"  {p['elapsed_h']:5.2f}"
                     f"  {p['median_ns']:+13.0f}"
                     f"  {p['stdev_ns']:15.0f}"
                     f"  {p['mad_ns']:13.0f}"
                     f"  {p['n']:4d}")
    log.info("=" * 66)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gm-port",   default=DEFAULT_GM_PORT)
    ap.add_argument("--fol-port",  default=DEFAULT_FOL_PORT)
    ap.add_argument("--gm-ip",     default=DEFAULT_GM_IP)
    ap.add_argument("--fol-ip",    default=DEFAULT_FOL_IP)
    ap.add_argument("--netmask",   default=DEFAULT_NETMASK)

    ap.add_argument("--duration-h", type=float, default=None,
                    help="Max runtime in hours (default: run until Ctrl-C)")
    ap.add_argument("--clk-interval-s",   type=float, default=20.0,
                    help="Interval between clk_get samples (default 20 s)")
    ap.add_argument("--drift-interval-s", type=float, default=300.0,
                    help="Interval between drift compensation tests (default 300 s)")
    ap.add_argument("--drift-duration-s", type=float, default=10.0,
                    help="Duration of each drift test (default 10 s)")
    ap.add_argument("--drift-pause-ms",   type=int,   default=500,
                    help="Pause between drift-test samples (default 500 ms)")
    ap.add_argument("--slope-threshold-ppm", type=float, default=2.0,
                    help="PASS threshold for |slope| in ppm (default 2.0)")
    ap.add_argument("--outlier-us", type=float, default=1000.0,
                    help="Flag drift samples with |diff| > this in us")

    # Saleae options
    ap.add_argument("--saleae", action="store_true",
                    help="Use Saleae Logic 2 to capture PD10 edges on its own "
                         "schedule (see --saleae-interval-s).  Requires "
                         "logic2-automation and a running Logic 2 app with "
                         "scripting socket enabled.")
    ap.add_argument("--saleae-interval-s", type=float, default=60.0,
                    help="Interval between Saleae phase checks in seconds "
                         "(default 60 s = 1 min)")
    ap.add_argument("--saleae-sample-rate", type=int, default=50_000_000,
                    help="Saleae sample rate (default 50 MS/s)")
    ap.add_argument("--saleae-duration-s", type=float, default=2.0,
                    help="Saleae capture duration per check (default 2 s)")
    ap.add_argument("--cyclic-period-us",  type=int, default=1000,
                    help="cyclic_fire period for PD10 toggle (default 1000 us"
                         " = 1 kHz rectangle)")
    ap.add_argument("--cyclic-anchor-lead-ms", type=int, default=2000,
                    help="Anchor lead time for cyclic_start (default 2000 ms)")
    ap.add_argument("--saleae-out-dir", default=None,
                    help="Directory for Saleae CSV (default: overnight_saleae_<ts>/)")
    ap.add_argument("--saleae-keep-csv", action="store_true",
                    help="Keep per-capture CSV files (default: delete after parsing)")

    ap.add_argument("--conv-timeout", type=float, default=DEFAULT_CONV_TIMEOUT)
    ap.add_argument("--log-file", default=None,
                    help="Log file path (default: overnight_<YYYYmmdd_HHMMSS>.log)")
    args = ap.parse_args()

    if args.saleae and not _SALEAE_OK:
        print(f"ERROR: --saleae requested but Saleae automation not "
              f"available: {_SALEAE_IMPORT_ERR}")
        print("Install with: pip install logic2-automation")
        sys.exit(1)

    ts_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log_file or f"overnight_{ts_stamp}.log"
    log = TSLogger(log_path)
    log.info(f"Overnight test starting — log file: {log_path}")
    log.info(f"GM  port={args.gm_port}  ip={args.gm_ip}")
    log.info(f"FOL port={args.fol_port}  ip={args.fol_ip}")
    log.info(f"Saleae: {'ENABLED' if args.saleae else 'disabled'}")

    saleae_out_dir: Optional[Path] = None
    if args.saleae:
        saleae_out_dir = Path(args.saleae_out_dir or
                              f"overnight_saleae_{ts_stamp}")
        saleae_out_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"Saleae CSV dir: {saleae_out_dir.resolve()}"
                 f"  (keep={args.saleae_keep_csv})")

    duration_s = args.duration_h * 3600.0 if args.duration_h else None

    ser_gm = ser_fol = None
    mux_gm = mux_fol = None
    exit_code = 0
    try:
        log.info("Opening serial ports...")
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)

        if not setup_boards(ser_gm, ser_fol,
                            args.gm_ip, args.fol_ip, args.netmask,
                            args.conv_timeout, log):
            log.info("\nSETUP FAILED — aborting.")
            exit_code = 1
            return

        # If Saleae is enabled we need PD10 toggling continuously.  Arm
        # cyclic_fire on both boards BEFORE we switch to the SerialMux
        # reader (cyclic_start's confirmation line is easier to match on
        # the raw serial port).
        if args.saleae:
            if not start_cyclic_fire(ser_gm, ser_fol,
                                      args.cyclic_period_us,
                                      args.cyclic_anchor_lead_ms,
                                      log):
                log.info("\ncyclic_fire arm FAILED — continuing without Saleae")
                args.saleae = False

        # Setup finished on the FOL / GM serial objects directly.  Now wrap
        # them in SerialMux so background trace lines and clk_get replies
        # don't fight over reset_input_buffer().
        mux_gm  = SerialMux(ser_gm,  "GM",  log)
        mux_fol = SerialMux(ser_fol, "FOL", log)
        mux_gm.start(); mux_fol.start()
        trace = TraceCollector(log)
        trace.add_mux(mux_gm); trace.add_mux(mux_fol)
        log.info("  [MUX] Background trace readers started")

        drift_results, phase_results = run_monitoring(
            mux_gm, mux_fol, trace,
            duration_s=duration_s,
            clk_interval_s=args.clk_interval_s,
            drift_interval_s=args.drift_interval_s,
            drift_duration_s=args.drift_duration_s,
            drift_pause_ms=args.drift_pause_ms,
            slope_threshold_ppm=args.slope_threshold_ppm,
            outlier_us=args.outlier_us,
            saleae_enabled=args.saleae,
            saleae_interval_s=args.saleae_interval_s,
            saleae_sample_rate=args.saleae_sample_rate,
            saleae_duration_s=args.saleae_duration_s,
            saleae_out_dir=saleae_out_dir,
            saleae_keep_csv=args.saleae_keep_csv,
            log=log)

        print_summary(drift_results, phase_results, log,
                       args.slope_threshold_ppm)
        if any(not r["passed"] for r in drift_results):
            exit_code = 2   # completed but with drift failures

    except Exception as exc:
        log.info(f"\nFATAL: {exc}")
        import traceback
        log.info(traceback.format_exc())
        exit_code = 3
    finally:
        if mux_gm:  mux_gm.stop()
        if mux_fol: mux_fol.stop()
        # Best-effort: halt cyclic_fire so PD10 isn't left toggling when the
        # script exits.  Only meaningful if --saleae was actually used.
        if args.saleae and ser_gm and ser_fol and \
                ser_gm.is_open and ser_fol.is_open:
            try:
                stop_cyclic_fire(ser_gm, ser_fol, log)
            except Exception as exc:
                log.info(f"  cyclic_stop cleanup failed: {exc}")
        for s in (ser_gm, ser_fol):
            if s and s.is_open:
                try: s.close()
                except Exception: pass
        log.info("Log closed.")
        log.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
