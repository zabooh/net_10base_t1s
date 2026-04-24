#!/usr/bin/env python3
"""Saleae Short-Drift Test — quickly detect residual PTP drift.

Flow (full runtime ~25-35 s):
  1. Reset both boards, wait for boot banner.
  2. setip + ping + ptp_trace on + sw_ntp_trace on.
  3. ptp_mode master on GM, ptp_mode follower on FOL, wait for FINE.
  4. Arm cyclic_fire at 1 kHz on PD10 of both boards (with retry).
  5. Capture `--capture-s` seconds of PD10 edges via Saleae (Ch0=GM, Ch1=FOL).
  6. Match each GM rising edge to its nearest FOL rising edge -> phase delta.
  7. Linear-regress delta(t) over the capture window:
        slope  = residual drift rate in ns/s (= ppb)
        sigma  = stdev of residuals (jitter floor)
  8. PASS if |slope| < --drift-threshold-ppm, else FAIL.

Why this works:
  PD10 toggles via cyclic_fire at an anchor derived from the shared PTP
  wallclock, so both boards SHOULD fire at the same real-time moment and
  the phase delta should stay constant through the capture.  Any growing
  delta over time IS residual clock-rate mismatch — i.e. PTP is not
  fully compensating the crystal drift.  Slope in ns/s is directly ppb.

Usage:
    python saleae_drift_test.py --gm-port COM8 --fol-port COM10
    python saleae_drift_test.py --capture-s 10 --drift-threshold-ppm 0.5
    python saleae_drift_test.py --no-reset   # assume boards already PTP FINE

Requires: pyserial, logic2-automation, Saleae Logic 2 app running with
          scripting socket enabled.
"""

import argparse
import datetime
import os
import shutil
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
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    RE_PING_DONE, RE_PING_REPLY,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

from cyclic_fire_hw_test import (                                # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
    compute_phase_pairs, overlap_window, robust, percentile,
    RE_CYC_START_OK,
)

# Firmware prints "PTP master STARTED" / "PTP follower STARTED" (see
# apps/.../ptp_cli.c).  Inherited patterns from ptp_drift_compensate_test
# are stale.
import re                                                         # noqa: E402
RE_GM_START     = re.compile(r"PTP master STARTED",   re.IGNORECASE)
RE_FOL_START    = re.compile(r"PTP follower STARTED", re.IGNORECASE)
RE_CLK_GET_RESP = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")


def ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class Log:
    def __init__(self, path: str):
        self._fh = open(path, "w", encoding="utf-8")
        self.path = path

    def info(self, msg: str):
        line = f"[{ts()}] {msg}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode(sys.stdout.encoding or "utf-8",
                              errors="replace").decode(
                              sys.stdout.encoding or "utf-8"),
                  flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()
        try: os.fsync(self._fh.fileno())
        except (OSError, ValueError): pass

    def debug(self, msg: str): self.info(f"  [DBG] {msg}")
    def close(self):
        if self._fh: self._fh.close(); self._fh = None


# ---------------------------------------------------------------------------
# Setup (same shape as overnight_test.py, just the minimum bits)
# ---------------------------------------------------------------------------

def bringup(ser_gm, ser_fol, gm_ip, fol_ip, netmask,
             conv_timeout: float, do_reset: bool, log: Log) -> bool:
    if do_reset:
        log.info("=== Reset both boards ===")
        gmb  = reset_and_wait_for_boot(ser_gm,  label="GM",  log=log)
        folb = reset_and_wait_for_boot(ser_fol, label="FOL", log=log)
        log.info(f"  GM  build: {gmb[0]} {gmb[1]}")
        log.info(f"  FOL build: {folb[0]} {folb[1]}")

        log.info("=== setip ===")
        for lbl, ser, ip in [("GM", ser_gm, gm_ip), ("FOL", ser_fol, fol_ip)]:
            resp = send_command(ser, f"setip eth0 {ip} {netmask}",
                                conv_timeout, log)
            if not RE_IP_SET.search(resp):
                log.info(f"  [{lbl}] setip FAILED: {resp.strip()!r}"); return False
            log.info(f"  [{lbl}] setip {ip} OK")

        # NOTE: trace is intentionally LEFT OFF for this short drift test.
        # With ptp_trace on the continuous firehose of Delay_Resp/Delay_Req
        # trace lines drowns the `cyclic_start OK` reply in the serial read
        # buffer, which the CLI-level send_command cannot reliably parse.
        # FINE / MATCHFREQ / HARD_SYNC / COARSE are printed independently
        # of ptp_trace, so bringup still sees them.

        # Disable standalone_demo's autopilot on both boards.  At boot the
        # demo auto-starts cyclic_fire in SILENT mode AND runs a watchdog
        # that restarts it if stopped — both of which would make our own
        # cyclic_start below fail with "already running".  See
        # standalone_demo.c:348 and demo_cli.c (demo_autopilot off:
        # unhooks user callback, stops cyclic_fire, disables watchdog).
        # Also issue an explicit cyclic_stop afterwards to be belt-and-braces,
        # and cyclic_status to log whatever state the firmware reports.
        log.info("=== Disable demo autopilot + explicit cyclic_stop ===")
        for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
            resp = send_command(ser, "demo_autopilot off", conv_timeout, log)
            log.info(f"  [{lbl}] demo_autopilot off -> {resp.strip()[-140:]!r}")
            resp = send_command(ser, "cyclic_stop", conv_timeout, log)
            log.info(f"  [{lbl}] cyclic_stop        -> {resp.strip()[-140:]!r}")
            resp = send_command(ser, "cyclic_status", conv_timeout, log)
            log.info(f"  [{lbl}] cyclic_status      -> {resp.strip()[-200:]!r}")

        log.info("=== Start PTP ===")
        resp = send_command(ser_fol, "ptp_mode follower", conv_timeout, log)
        if not RE_FOL_START.search(resp):
            log.info(f"  [FOL] follower NOT confirmed: {resp.strip()!r}"); return False
        time.sleep(0.5)
        ser_fol.reset_input_buffer()
        ser_gm.reset_input_buffer()
        ser_gm.write(b"ptp_mode master\r\n")
        ok, _, _ = wait_for_pattern(ser_gm, RE_GM_START, conv_timeout, log,
                                     live_log=True)
        if not ok:
            log.info("  [GM ] master NOT confirmed"); return False

        log.info(f"=== Wait for FOL FINE (timeout {conv_timeout:.0f}s) ===")
        matched, elapsed, ms = wait_for_pattern(
            ser_fol, RE_FINE, conv_timeout, log,
            extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                             "HARD_SYNC": RE_HARD_SYNC,
                             "COARSE":    RE_COARSE},
            live_log=True)
        if not matched:
            log.info(f"  FOL FINE NOT reached within {conv_timeout:.0f}s")
            return False
        log.info(f"  FOL FINE in {elapsed:.1f}s  "
                 f"({', '.join(f'{k}@{v:.1f}s' for k, v in ms.items())})")
    else:
        log.info("--no-reset: assuming both boards are already in PTP FINE")
    return True


RE_CYC_START_FAIL = re.compile(r"cyclic_start\s+FAIL", re.IGNORECASE)


def arm_cyclic(ser, label: str, period_us: int, lead_ms: int, log: Log) -> bool:
    # NOTE: lead_ms is kept as an argument for symmetry but we always pass
    # anchor=0 to cyclic_start.  Why: cyclic_fire uses the ISR-driven TC1
    # backend by default (cyclic_fire_isr.c).  That backend can only arm
    # targets within TC1's 16-bit window = 1 ms in the future at 60 MHz
    # (see cyclic_fire_isr.c TC1_MAX_ARM_TICKS).  A non-zero anchor N ms
    # ahead therefore fails silently with arm_backend() returning false
    # and the CLI printing the misleading "already running or PTP_CLOCK
    # not valid" (which is what bit this script for several iterations).
    # With anchor=0 cyclic_fire uses "now + period" internally, always
    # inside the TC1 window.  Phase alignment across boards still holds:
    # both boards' PTP clocks are µs-synced, so even though the serial
    # round-trip offsets when each board runs cyclic_fire_start, the
    # per-board "now + period" math produces targets that are µs-close
    # modulo period.  The Saleae drift measurement cares about phase
    # SLOPE over time (= residual drift rate), not absolute phase, so
    # any constant offset simply shifts the fit intercept, not the slope.
    _ = lead_ms   # intentionally ignored — see comment above

    resp = send_command(ser, f"cyclic_start {period_us} 0", 2.0, log)
    ok   = bool(RE_CYC_START_OK.search(resp))
    fail = bool(RE_CYC_START_FAIL.search(resp))
    if ok:
        verdict = "OK"
    elif fail:
        verdict = "FAIL (firmware rejected)"
    else:
        verdict = "FAIL (no reply matched)"
    log.info(f"  [{label}] cyclic_start {period_us} 0 -> {verdict}")
    if not ok:
        # Print the full response so we can see what actually came back.
        log.info(f"    [RESP] {resp.strip()!r}")
    return ok


def arm_cyclic_both(ser_gm, ser_fol, period_us: int, lead_ms: int,
                     settle_s: float, retries: int, retry_pause_s: float,
                     log: Log) -> bool:
    log.info(f"=== Arm cyclic_fire  (settle {settle_s:.1f}s after FINE) ===")
    time.sleep(settle_s)
    for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
        for attempt in range(1, retries + 1):
            if arm_cyclic(ser, lbl, period_us, lead_ms, log):
                break
            if attempt < retries:
                log.info(f"  [{lbl}] retry {attempt}/{retries-1} after "
                         f"{retry_pause_s:.1f}s")
                time.sleep(retry_pause_s)
        else:
            return False
    return True


def stop_cyclic(ser_gm, ser_fol, log: Log):
    for lbl, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
        try:
            send_command(ser, "cyclic_stop", 2.0, log)
        except Exception: pass


# ---------------------------------------------------------------------------
# Core measurement: Saleae capture + phase-slope analysis
# ---------------------------------------------------------------------------

def linear_regression(xs: List[float], ys: List[float]
                      ) -> Tuple[float, float, List[float]]:
    n = len(xs)
    sx  = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    slope = (n * sxy - sx * sy) / denom if denom else 0.0
    icpt  = (sy - slope * sx) / n
    resid = [y - (icpt + slope * x) for x, y in zip(xs, ys)]
    return slope, icpt, resid


def measure_drift(sample_rate: int, capture_s: float, out_dir: Path,
                   keep_csv: bool, log: Log
                   ) -> Optional[dict]:
    mgr = cap = None
    try:
        mgr, cap = start_saleae_capture(sample_rate, capture_s, log)
        time.sleep(capture_s + 0.3)
        cap.wait()
        csv = export_capture_csv(cap, out_dir, log)
        rising, _ = parse_edges(csv)
    except Exception as exc:
        log.info(f"  Saleae capture FAILED: {exc}")
        return None
    finally:
        try:
            if cap: cap.close()
        except Exception: pass

    log.info(f"  Edges: GM rising={len(rising[0])}  FOL rising={len(rising[1])}")
    if len(rising[0]) < 50 or len(rising[1]) < 50:
        log.info("  Too few edges — analysis aborted")
        if not keep_csv: shutil.rmtree(out_dir, ignore_errors=True)
        return None

    t_lo, t_hi = overlap_window(rising[0], rising[1], guard_s=0.05)
    pairs = compute_phase_pairs(rising[0], rising[1], t_lo, t_hi)
    if len(pairs) < 50:
        log.info("  Too few paired edges — analysis aborted"); return None

    # Centre time origin on the first pair for numerical stability.
    t0 = pairs[0][0]
    xs = [tg - t0 for tg, _, _ in pairs]                 # seconds
    ys_raw = [d * 1e9 for _, _, d in pairs]              # delta in ns

    # Unwrap phase deltas modulo the cyclic_fire period so that nearest-edge
    # matches that wrapped to the adjacent period don't pollute the fit.
    # Without this step a delta that naturally sits near +period/2 flips to
    # -period/2 once PTP nudges it past, producing period-sized jumps in
    # the regression input.  The pd10_sync doc discusses the same effect
    # and reports MAD (robust) instead of stdev (sensitive to wraps).
    period_ns = 1_000_000   # 1 kHz cyclic_fire
    # Rotate so the MEDIAN delta sits at 0, then wrap into (-P/2, +P/2].
    pivot = statistics.median(ys_raw)
    ys = [((y - pivot + period_ns / 2) % period_ns) - period_ns / 2 + pivot
          for y in ys_raw]

    slope_ns_per_s, icpt_ns, resid_ns = linear_regression(xs, ys)
    # 1 ns/s of phase drift ≡ 1 ppb of clock-rate mismatch.
    slope_ppb = slope_ns_per_s
    slope_ppm = slope_ppb / 1000.0

    # Robust + non-robust metrics so operators can see both.
    _, resid_mad_ns = robust(resid_ns)       # robust residual spread (MAD, ns)
    res_stdev       = statistics.stdev(resid_ns) if len(resid_ns) > 1 else 0.0
    med_ns, mad_ns  = robust(ys)             # raw phase delta median + MAD

    sorted_y = sorted(ys)
    p05 = percentile(sorted_y,  5.0); p95 = percentile(sorted_y, 95.0)

    # Count how many deltas got unwrapped more than one period from the
    # raw nearest-edge match — high count means the period is too short
    # for the per-edge jitter and the match is ambiguous.
    n_wrapped = sum(1 for raw, fixed in zip(ys_raw, ys)
                    if abs(raw - fixed) > period_ns / 4)

    log.info("")
    log.info(f"  === DRIFT ANALYSIS ({len(pairs)} paired edges over "
             f"{xs[-1]:.2f}s) ===")
    log.info(f"  Phase delta  : median={med_ns:+.0f} ns  MAD={mad_ns:.0f} ns"
             f"  [p05..p95]={p05:+.0f}..{p95:+.0f} ns")
    if n_wrapped > 0:
        log.info(f"  Edge wraps   : {n_wrapped}/{len(ys_raw)} samples "
                 f"({100.0*n_wrapped/len(ys_raw):.1f}%) unwrapped by 1 period")
    log.info(f"  Linear fit   : delta(t) = {icpt_ns:+.0f} ns  +  "
             f"{slope_ppb:+.1f} ns/s * t")
    log.info(f"  RESIDUAL DRIFT    : {slope_ppb:+.1f} ppb"
             f"  ({slope_ppm:+.4f} ppm)")
    log.info(f"  Fit residuals MAD : {resid_mad_ns:.0f} ns"
             f"   (stdev {res_stdev:.0f} ns)")

    if not keep_csv:
        shutil.rmtree(out_dir, ignore_errors=True)

    return {
        "n":             len(pairs),
        "capture_s":     xs[-1],
        "slope_ppb":     slope_ppb,
        "slope_ppm":     slope_ppm,
        "icpt_ns":       icpt_ns,
        "res_stdev":     res_stdev,
        "resid_mad_ns":  resid_mad_ns,
        "median_ns":     med_ns,
        "mad_ns":        mad_ns,
        "p05_ns":        p05,
        "p95_ns":        p95,
        "n_wrapped":     n_wrapped,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gm-port",   default="COM8")
    ap.add_argument("--fol-port",  default="COM10")
    ap.add_argument("--gm-ip",     default=DEFAULT_GM_IP)
    ap.add_argument("--fol-ip",    default=DEFAULT_FOL_IP)
    ap.add_argument("--netmask",   default=DEFAULT_NETMASK)
    ap.add_argument("--no-reset",  action="store_true",
                    help="skip board reset + PTP setup (boards already FINE)")

    ap.add_argument("--capture-s",  type=float, default=5.0,
                    help="Saleae capture duration (default 5 s)")
    ap.add_argument("--sample-rate", type=int, default=50_000_000,
                    help="Saleae sample rate (default 50 MS/s)")
    ap.add_argument("--period-us",  type=int, default=1000,
                    help="cyclic_fire rectangle period in us (default 1000 = 1 kHz)")
    ap.add_argument("--anchor-lead-ms", type=int, default=2000)
    ap.add_argument("--settle-s",   type=float, default=3.0,
                    help="Wait this long after FINE before cyclic_start")
    ap.add_argument("--cyclic-retries",  type=int, default=5)
    ap.add_argument("--retry-pause-s",   type=float, default=2.0)
    ap.add_argument("--drift-threshold-ppm", type=float, default=5.0,
                    help="PASS requires |slope| < this (default 5.0 ppm, "
                         "matches pd10_sync_before_after_tests doc gate)")
    ap.add_argument("--mad-threshold-us",    type=float, default=50.0,
                    help="PASS requires residuals MAD < this in us "
                         "(default 50 us, matches doc gate)")
    ap.add_argument("--conv-timeout", type=float, default=DEFAULT_CONV_TIMEOUT)

    ap.add_argument("--keep-csv", action="store_true")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--out-dir",  default=None)
    args = ap.parse_args()

    tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Log(args.log_file or f"saleae_drift_{tag}.log")
    out_dir = Path(args.out_dir or f"saleae_drift_{tag}")
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Saleae drift test starting  log={log.path}")
    log.info(f"GM={args.gm_port} ({args.gm_ip})  FOL={args.fol_port} ({args.fol_ip})")
    log.info(f"capture={args.capture_s:.1f}s  period={args.period_us} us"
             f"  threshold={args.drift_threshold_ppm} ppm")

    ser_gm = ser_fol = None
    exit_code = 0
    stats = None
    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)

        if not bringup(ser_gm, ser_fol,
                        args.gm_ip, args.fol_ip, args.netmask,
                        args.conv_timeout,
                        do_reset=not args.no_reset, log=log):
            log.info("SETUP FAILED")
            exit_code = 1
            return

        if not arm_cyclic_both(ser_gm, ser_fol,
                                args.period_us, args.anchor_lead_ms,
                                args.settle_s, args.cyclic_retries,
                                args.retry_pause_s, log):
            log.info("cyclic_start FAILED on at least one board")
            exit_code = 1
            return

        # Let signals stabilise before we start the capture.
        time.sleep(0.5)

        log.info(f"=== Saleae capture ({args.capture_s:.1f} s @ "
                 f"{args.sample_rate/1e6:.0f} MS/s) ===")
        stats = measure_drift(args.sample_rate, args.capture_s,
                               out_dir, args.keep_csv, log)

    except Exception as exc:
        import traceback
        log.info(f"FATAL: {exc}\n{traceback.format_exc()}")
        exit_code = 3
    finally:
        try:
            if ser_gm and ser_fol and ser_gm.is_open and ser_fol.is_open:
                stop_cyclic(ser_gm, ser_fol, log)
        except Exception: pass
        for s in (ser_gm, ser_fol):
            if s and s.is_open:
                try: s.close()
                except Exception: pass

    # ----- Verdict -----
    log.info("")
    log.info("=" * 66)
    if stats is None:
        log.info("VERDICT: NO MEASUREMENT (capture or analysis failed)")
        exit_code = exit_code or 2
    else:
        slope_ok = abs(stats["slope_ppm"]) < args.drift_threshold_ppm
        mad_us   = stats["resid_mad_ns"] / 1000.0
        mad_ok   = mad_us < args.mad_threshold_us
        passed   = slope_ok and mad_ok
        log.info(f"  slope : {stats['slope_ppm']:+.4f} ppm  "
                 f"(threshold {args.drift_threshold_ppm} ppm)   "
                 f"{'OK' if slope_ok else 'FAIL'}")
        log.info(f"  MAD   : {mad_us:.1f} us    "
                 f"(threshold {args.mad_threshold_us} us)      "
                 f"{'OK' if mad_ok else 'FAIL'}")
        log.info(f"VERDICT: {'PASS' if passed else 'FAIL'}")
        if not passed:
            exit_code = exit_code or 2
    log.info("=" * 66)
    log.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
