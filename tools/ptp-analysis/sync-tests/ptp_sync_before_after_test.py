#!/usr/bin/env python3
"""PTP Synchronisation Before/After Test
=========================================

Demonstrates, in a single run, how PTP synchronises the hardware/software
timer (TC0-based software clock) between two boards:

  Phase 0 — FREE RUNNING (no PTP):
    Both clocks are zeroed simultaneously and left to free-run.
    Paired 'clk_get' readings are taken for --free-run-s seconds.
    Linear regression shows the raw crystal frequency difference as a
    growing linear drift.  drift_ppb reported by the firmware = 0 (IIR idle).

  Phase 1 — PTP SETUP:
    IP addresses are configured, ping connectivity verified.
    Board A becomes Grandmaster, Board B becomes Follower.
    The test waits until the Follower reaches PTP FINE state.

  Phase 2 — PTP ACTIVE (compensated):
    After an optional settle period both clocks are zeroed again and
    paired 'clk_get' readings are taken for --ptp-s seconds.
    Linear regression now shows a slope near 0 ppb (the IIR filter has
    slewed Board B's TC0 tick rate to match Board A).
    drift_ppb reported by the firmware ≈ crystal offset (IIR active).

  Final Comparison:
    Side-by-side table: slope before vs. after, drift reduction in %.

PASS criterion (PTP phase):
    |slope_ptp| < --slope-threshold-ppm  (default 2.0 ppm)
    residual stdev < --residual-threshold-us  (default 500 µs)

Usage:
    python ptp_sync_before_after_test.py --gm-port COM8 --fol-port COM10

    # Longer free-run and PTP phases:
    python ptp_sync_before_after_test.py --gm-port COM8 --fol-port COM10 \\
        --free-run-s 60 --ptp-s 120

Requirements:
    pip install pyserial
"""

import argparse
import datetime
import re
import statistics
import sys
import threading
import time
from typing import List, Optional, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GM_PORT             = "COM8"
DEFAULT_FOL_PORT            = "COM10"
DEFAULT_GM_IP               = "192.168.0.30"
DEFAULT_FOL_IP              = "192.168.0.20"
DEFAULT_NETMASK             = "255.255.255.0"
DEFAULT_BAUDRATE            = 115200
DEFAULT_CMD_TIMEOUT         = 5.0     # s — single command response wait
DEFAULT_CONV_TIMEOUT        = 60.0    # s — PTP FINE convergence wait
DEFAULT_FREE_RUN_S          = 60.0    # s — free-run (no PTP) collection duration
DEFAULT_PTP_S               = 60.0    # s — PTP-active collection duration
DEFAULT_PAUSE_MS            = 500     # ms — interval between clk_get pairs
DEFAULT_SETTLE_S            = 5.0     # s — settle after PTP FINE before re-zeroing
DEFAULT_SLOPE_THRESHOLD_PPM = 2.0     # ppm — PASS criterion (PTP phase slope)
DEFAULT_RESIDUAL_THRESHOLD_US = 500.0 # µs  — PASS criterion (residual stdev)

RE_IP_SET     = re.compile(r"Set ip address OK|IP address set to", re.IGNORECASE)
RE_BUILD      = re.compile(r"\[APP\] Build:\s+(.+)")
RE_PING_REPLY = re.compile(r"Ping:.*reply.*from|Reply from", re.IGNORECASE)
RE_PING_DONE  = re.compile(r"Ping: done\.", re.IGNORECASE)
RE_FOL_START  = re.compile(r"PTP Follower enabled", re.IGNORECASE)
RE_GM_START   = re.compile(r"PTP Grandmaster enabled", re.IGNORECASE)
RE_MATCHFREQ  = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC  = re.compile(r"Hard sync completed")
RE_COARSE     = re.compile(r"PTP COARSE")
RE_FINE       = re.compile(r"PTP FINE")
RE_CLK_SET    = re.compile(r"clk_set ok")
RE_CLK_GET    = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    def __init__(self, log_file: str = None, verbose: bool = False):
        self.log_file = log_file
        self.verbose  = verbose
        self._fh      = None
        self._lock    = threading.Lock()
        if log_file:
            self._fh = open(log_file, "w", encoding="utf-8")

    def _write(self, line: str):
        with self._lock:
            print(line)
            if self._fh:
                self._fh.write(line + "\n")
                self._fh.flush()

    def info(self, msg: str):  self._write(msg)
    def debug(self, msg: str):
        if self.verbose: self._write(f"  [DBG] {msg}")

    def close(self):
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


# ---------------------------------------------------------------------------
# Serial helpers
# ---------------------------------------------------------------------------

def open_port(port: str, baudrate: int = DEFAULT_BAUDRATE) -> serial.Serial:
    return serial.Serial(
        port=port, baudrate=baudrate,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.1,
    )


def send_command(ser: serial.Serial, cmd: str,
                 timeout: float = DEFAULT_CMD_TIMEOUT,
                 log: Logger = None) -> str:
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("ascii"))
    if log: log.debug(f"  >> {cmd}")
    parts    = []
    deadline  = time.monotonic() + timeout
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            parts.append(chunk.decode("ascii", errors="replace"))
            last_data = time.monotonic()
        else:
            if parts and (time.monotonic() - last_data) > 0.5:
                break
            time.sleep(0.05)
    return "".join(parts)


def wait_for_pattern(ser: serial.Serial, pattern: re.Pattern,
                     timeout: float, log: Logger = None,
                     extra_patterns: dict = None,
                     live_log: bool = False) -> Tuple[bool, float, dict]:
    if extra_patterns is None:
        extra_patterns = {}
    milestones: dict = {}
    buffer   = ""
    start    = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            decoded = chunk.decode("ascii", errors="replace")
            buffer += decoded
            if log:
                for line in decoded.splitlines():
                    if line.strip():
                        if live_log: log.info(f"    {line.rstrip()}")
                        else:        log.debug(f"  <- {line.rstrip()}")
            for label, pat in extra_patterns.items():
                if label not in milestones and pat.search(buffer):
                    milestones[label] = time.monotonic() - start
            if pattern.search(buffer):
                return True, time.monotonic() - start, milestones
        else:
            time.sleep(0.05)
    return False, time.monotonic() - start, milestones


# ---------------------------------------------------------------------------
# clk_get / clk_set helpers
# ---------------------------------------------------------------------------

def _parse_clk_get(raw: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (wallclock_ns, drift_ppb) or (None, None)."""
    m = RE_CLK_GET.search(raw)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _query_board(ser: serial.Serial, result_dict: dict, key: str,
                 timeout_s: float = 1.0):
    """Send 'clk_get', record t_send_ns, store {raw, t_send_ns}."""
    ser.reset_input_buffer()
    t_send = time.perf_counter_ns()
    ser.write(b"clk_get\r\n")
    resp     = b""
    deadline = time.perf_counter_ns() + int(timeout_s * 1e9)
    while time.perf_counter_ns() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        resp += chunk
        idx = resp.find(b"clk_get:")
        if idx >= 0 and b"\n" in resp[idx:]:
            break
    result_dict[key] = {
        "raw":       resp.decode(errors="replace"),
        "t_send_ns": t_send,
    }


def single_measurement(ser_gm: serial.Serial, ser_fol: serial.Serial,
                       swap: bool = False) -> Tuple[Optional[int],
                                                    Optional[int],
                                                    Optional[int]]:
    """
    One paired clk_get measurement (swap-symmetrised).
    Returns (diff_ns, drift_gm_ppb, drift_fol_ppb) or (None, None, None).
    diff_ns = (clk_fol - clk_gm) - (t_send_fol - t_send_gm)
    """
    res: dict = {}
    if swap:
        ta = threading.Thread(target=_query_board, args=(ser_fol, res, "fol"))
        tb = threading.Thread(target=_query_board, args=(ser_gm,  res, "gm"))
    else:
        ta = threading.Thread(target=_query_board, args=(ser_gm,  res, "gm"))
        tb = threading.Thread(target=_query_board, args=(ser_fol, res, "fol"))
    ta.start(); tb.start()
    ta.join();  tb.join()

    clk_gm,  d_gm  = _parse_clk_get(res.get("gm",  {}).get("raw", ""))
    clk_fol, d_fol = _parse_clk_get(res.get("fol", {}).get("raw", ""))

    if clk_gm is None or clk_fol is None:
        return None, None, None

    send_delta = res["fol"]["t_send_ns"] - res["gm"]["t_send_ns"]
    diff_ns    = (clk_fol - clk_gm) - send_delta
    return diff_ns, d_gm, d_fol


def _set_clock_zero(ser: serial.Serial, result_dict: dict, key: str,
                    ready_event: threading.Event, go_event: threading.Event):
    ready_event.set()
    go_event.wait()
    ser.reset_input_buffer()
    t_send = time.perf_counter_ns()
    ser.write(b"clk_set 0\r\n")
    resp     = b""
    deadline = time.perf_counter_ns() + int(2.0 * 1e9)
    while time.perf_counter_ns() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        resp += chunk
        if b"clk_set ok" in resp:
            break
    result_dict[key] = {"raw": resp.decode(errors="replace"), "t_send_ns": t_send}


def zero_both_clocks(ser_gm: serial.Serial, ser_fol: serial.Serial,
                     log: Logger) -> bool:
    """Send 'clk_set 0' to both boards in parallel threads."""
    res: dict = {}
    ready_gm  = threading.Event()
    ready_fol = threading.Event()
    go        = threading.Event()
    ta = threading.Thread(target=_set_clock_zero,
                          args=(ser_gm,  res, "gm",  ready_gm,  go))
    tb = threading.Thread(target=_set_clock_zero,
                          args=(ser_fol, res, "fol", ready_fol, go))
    ta.start(); tb.start()
    ready_gm.wait(); ready_fol.wait()
    go.set()
    ta.join(timeout=3.0); tb.join(timeout=3.0)
    ok_gm  = RE_CLK_SET.search(res.get("gm",  {}).get("raw", "")) is not None
    ok_fol = RE_CLK_SET.search(res.get("fol", {}).get("raw", "")) is not None
    if "gm" in res and "fol" in res:
        skew_ns = res["fol"]["t_send_ns"] - res["gm"]["t_send_ns"]
        log.info(f"  [GM ] clk_set 0: {'OK' if ok_gm  else 'FAIL'}")
        log.info(f"  [FOL] clk_set 0: {'OK' if ok_fol else 'FAIL'}")
        log.info(f"  Thread send skew: {skew_ns / 1000:.1f} µs")
    return ok_gm and ok_fol


# ---------------------------------------------------------------------------
# Sample collection & evaluation
# ---------------------------------------------------------------------------

def _linear_regression(xs: List[float], ys: List[float]):
    """Return (slope, intercept, residuals).  slope in same units as y/x."""
    n      = len(xs)
    sum_x  = sum(xs)
    sum_y  = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    denom  = n * sum_xx - sum_x * sum_x
    slope  = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
    intercept = (sum_y - slope * sum_x) / n
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    return slope, intercept, residuals


def collect_clk_get_samples(ser_gm: serial.Serial, ser_fol: serial.Serial,
                             duration_s: float, pause_ms: int,
                             no_swap: bool, label: str,
                             log: Logger) -> Tuple[List[int], List[float],
                                                   List[int], List[int]]:
    """
    Collect paired clk_get samples for *duration_s* seconds.
    Returns (diffs_ns, elapsed_s, drifts_gm, drifts_fol).
    """
    samples:    List[int]   = []
    elapsed_s:  List[float] = []
    drifts_gm:  List[int]   = []
    drifts_fol: List[int]   = []
    n_err   = 0
    t_start = time.perf_counter_ns()
    i       = 0

    log.info(f"  Collecting for {duration_s:.0f} s  (pause={pause_ms} ms) ...")

    while True:
        elapsed_now = (time.perf_counter_ns() - t_start) / 1e9
        if elapsed_now >= duration_s:
            break

        swap = (not no_swap) and (i % 2 == 1)
        t_before = time.perf_counter_ns()
        diff, d_gm, d_fol = single_measurement(ser_gm, ser_fol, swap=swap)
        t_mid = (time.perf_counter_ns() + t_before) / 2

        if diff is None:
            n_err += 1
            log.info(f"  [{label}][{i+1:4d}] ERROR (no clk_get response)")
        else:
            elapsed = (t_mid - t_start) / 1e9
            samples.append(diff)
            elapsed_s.append(elapsed)
            drifts_gm.append(d_gm)
            drifts_fol.append(d_fol)
            tag = " [swap]" if swap else "       "
            log.info(
                f"  [{label}][{i+1:4d}]{tag}  t={elapsed:7.2f}s"
                f"  diff={diff/1000:+9.2f} µs"
                f"  drift GM={d_gm:+d} FOL={d_fol:+d} ppb")

        i += 1
        remaining = duration_s - (time.perf_counter_ns() - t_start) / 1e9
        if remaining > 0:
            time.sleep(min(pause_ms / 1000.0, remaining))

    if n_err:
        log.info(f"  [{label}] Communication errors: {n_err}")

    return samples, elapsed_s, drifts_gm, drifts_fol


def evaluate_samples(samples: List[int], elapsed_s: List[float],
                     drifts_gm: List[int], drifts_fol: List[int],
                     label: str, log: Logger) -> Optional[dict]:
    """
    Fit linear regression, remove 2-sigma outliers, return stats dict or None.
    """
    log.info("")
    if len(samples) < 5:
        log.info(f"  [{label}] Too few valid samples ({len(samples)}). Aborting.")
        return None

    slope0, intercept0, residuals0 = _linear_regression(elapsed_s, samples)
    res_mean  = statistics.mean(residuals0)
    res_stdev = statistics.stdev(residuals0)
    clean_idx = [i for i, r in enumerate(residuals0)
                 if abs(r - res_mean) <= 2 * res_stdev]
    n_out = len(samples) - len(clean_idx)

    cxs   = [elapsed_s[i]  for i in clean_idx]
    cys   = [samples[i]    for i in clean_idx]
    cdgm  = [drifts_gm[i]  for i in clean_idx]
    cdfol = [drifts_fol[i] for i in clean_idx]

    slope, intercept, residuals = _linear_regression(cxs, cys)
    res_stdev2  = statistics.stdev(residuals)
    res_sem2    = res_stdev2 / (len(residuals) ** 0.5)

    t_span = cxs[-1] - cxs[0] if len(cxs) > 1 else 0.0
    mean_drift_gm   = statistics.mean(cdgm)  if cdgm  else 0.0
    mean_drift_fol  = statistics.mean(cdfol) if cdfol else 0.0
    stdev_drift_fol = statistics.stdev(cdfol) if len(cdfol) > 1 else 0.0

    return {
        "n_clean":         len(cxs),
        "n_total":         len(samples),
        "n_out":           n_out,
        "t_span":          t_span,
        "slope_ppb":       slope,          # ns/s = ppb
        "slope_ppm":       slope / 1000.0, # ppm
        "intercept_ns":    intercept,
        "res_stdev_ns":    res_stdev2,
        "res_sem_ns":      res_sem2,
        "mean_drift_gm":   mean_drift_gm,
        "mean_drift_fol":  mean_drift_fol,
        "stdev_drift_fol": stdev_drift_fol,
    }


def print_regression_report(stats: dict, label: str, log: Logger):
    log.info("=" * 68)
    log.info(f"[{label}]")
    log.info(f"  Samples    : {stats['n_clean']}/{stats['n_total']} valid"
             f"  ({stats['n_out']} outliers removed)")
    log.info(f"  Time span  : {stats['t_span']:.2f} s")
    log.info("")
    log.info("  Linear regression: diff(t) = intercept + slope * t")
    log.info(f"    Intercept : {stats['intercept_ns']:+.0f} ns"
             f"  ({stats['intercept_ns']/1000:+.1f} µs)")
    log.info(f"    Slope     : {stats['slope_ppb']:+.0f} ppb"
             f"  ({stats['slope_ppm']:+.4f} ppm)")
    log.info("")
    log.info("  Residuals (after subtracting linear trend)")
    log.info(f"    Stdev     : {stats['res_stdev_ns']:.0f} ns"
             f"  ({stats['res_stdev_ns']/1000:.3f} µs)")
    log.info(f"    SEM       : {stats['res_sem_ns']:.0f} ns"
             f"  ({stats['res_sem_ns']/1000:.3f} µs)")
    log.info("")
    log.info(f"  Drift GM   : {stats['mean_drift_gm']:+.0f} ppb  (mean)")
    log.info(f"  Drift FOL  : {stats['mean_drift_fol']:+.0f} ppb  (mean)"
             f"  ±{stats['stdev_drift_fol']:.0f} ppb  (stdev)")
    log.info("=" * 68)


# ---------------------------------------------------------------------------
# Main test class
# ---------------------------------------------------------------------------

class PTPSyncBeforeAfterTest:

    def __init__(self, gm_port: str, fol_port: str,
                 gm_ip: str, fol_ip: str, netmask: str,
                 free_run_s: float, ptp_s: float,
                 pause_ms: int, settle_s: float,
                 slope_threshold_ppm: float,
                 residual_threshold_us: float,
                 conv_timeout: float,
                 no_swap: bool, no_clk_set: bool, no_reset: bool,
                 log: Logger):
        self.gm_port              = gm_port
        self.fol_port             = fol_port
        self.gm_ip                = gm_ip
        self.fol_ip               = fol_ip
        self.netmask              = netmask
        self.free_run_s           = free_run_s
        self.ptp_s                = ptp_s
        self.pause_ms             = pause_ms
        self.settle_s             = settle_s
        self.slope_threshold_ppm  = slope_threshold_ppm
        self.residual_threshold_us = residual_threshold_us
        self.conv_timeout         = conv_timeout
        self.no_swap              = no_swap
        self.no_clk_set           = no_clk_set
        self.no_reset             = no_reset
        self.log                  = log

        self.ser_gm:  Optional[serial.Serial] = None
        self.ser_fol: Optional[serial.Serial] = None
        self.results: list = []

        self.gm_build:  str = "unknown"
        self.fol_build: str = "unknown"

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple] = None

        self._free_stats: Optional[dict] = None
        self._ptp_stats:  Optional[dict] = None

    # ------------------------------------------------------------------
    def _connect(self):
        for label, port, attr in [("GM",  self.gm_port,  "ser_gm"),
                                   ("FOL", self.fol_port, "ser_fol")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def _disconnect(self):
        for ser in (self.ser_gm, self.ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    def _record(self, name: str, passed: bool, detail: str = ""):
        self.results.append((name, passed, detail))

    # ------------------------------------------------------------------
    def _step_reset(self):
        self.log.info("\n--- Step 0: Reset boards ---")
        for label, ser in [("GM",  self.ser_gm),
                            ("FOL", self.ser_fol)]:
            self.log.info(f"  [{label}] reset")
            send_command(ser, "reset", self.conv_timeout, self.log)
        self.log.info("  Waiting 8 s for boot...")
        time.sleep(8)
        for label, ser, attr in [("GM",  self.ser_gm,  "gm_build"),
                                  ("FOL", self.ser_fol, "fol_build")]:
            extra = b""
            while ser.in_waiting:
                extra += ser.read(ser.in_waiting)
                time.sleep(0.05)
            combined = extra.decode("ascii", errors="replace")
            m = RE_BUILD.search(combined)
            build_ts = m.group(1).strip() if m else "unknown"
            setattr(self, attr, build_ts)
            self.log.info(f"  [{label}] firmware build: {build_ts}")
        self._record("Step 0: Reset", True,
                     f"GM={self.gm_build}  FOL={self.fol_build}")

    # ------------------------------------------------------------------
    def _phase_free_run(self) -> bool:
        """Phase 0: collect clk_get samples WITHOUT any PTP active."""
        log = self.log
        log.info(
            f"\n{'='*68}")
        log.info(
            f"  PHASE 0: FREE RUNNING — no PTP  ({self.free_run_s:.0f} s)")
        log.info(
            f"{'='*68}")
        log.info(
            "  Both clocks run independently.  Expected result:")
        log.info(
            "    slope  ≈ crystal frequency difference (1–10 ppm) — GROWING drift")
        log.info(
            "    drift  = 0 ppb  (PTP IIR filter not active)")
        log.info(
            "    residual stdev  = UART serialisation jitter (200–500 µs)")

        if not self.no_clk_set:
            log.info("\n  Zeroing both clocks simultaneously ...")
            ok = zero_both_clocks(self.ser_gm, self.ser_fol, log)
            if not ok:
                log.info("  WARNING: clk_set 0 failed on one or both boards")
            log.info("  Settling 2 s after clk_set ...")
            time.sleep(2.0)

        smpls, elps, dgm, dfol = collect_clk_get_samples(
            self.ser_gm, self.ser_fol,
            self.free_run_s, self.pause_ms,
            self.no_swap, "FREE", log)

        stats = evaluate_samples(smpls, elps, dgm, dfol, "FREE", log)
        if stats is None:
            self._record("Phase 0: Free-run (no PTP)", False, "too few samples")
            return False

        print_regression_report(stats, "PHASE 0 — FREE RUNNING (no PTP)", log)
        self._free_stats = stats

        log.info("")
        log.info("  Interpretation:")
        slope_ppb = stats["slope_ppb"]
        slope_ppm = stats["slope_ppm"]
        direction = "SLOWER" if slope_ppb < 0 else "FASTER"
        log.info(f"    Board FOL ({self.fol_port}) crystal runs "
                 f"{abs(slope_ppm):.4f} ppm {direction} than Board GM ({self.gm_port}).")
        log.info(f"    Without PTP the clocks diverge by "
                 f"~{abs(slope_ppb):.0f} ns every second.")
        log.info(f"    After 1 hour the difference would be "
                 f"~{abs(slope_ppb)*3600/1e6:.1f} ms.")
        log.info(
            "    PTP will now compensate this by slewing the Follower tick rate.")

        self._record(
            "Phase 0: Free-run (no PTP)",
            True,
            f"slope={slope_ppb:+.0f}ppb ({slope_ppm:+.4f}ppm)"
            f"  res_stdev={stats['res_stdev_ns']/1000:.0f}us"
            f"  drift_fol={stats['mean_drift_fol']:+.0f}ppb")
        return True

    # ------------------------------------------------------------------
    def _step_ip(self) -> bool:
        self.log.info("\n--- PTP Setup Step 1: IP Configuration ---")
        passed = True
        for label, ser, ip in [("GM",  self.ser_gm,  self.gm_ip),
                                 ("FOL", self.ser_fol, self.fol_ip)]:
            resp = send_command(ser, f"setip eth0 {ip} {self.netmask}",
                                self.conv_timeout, self.log)
            ok = bool(RE_IP_SET.search(resp))
            self.log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")
            if not ok: passed = False
        self._record("PTP Setup: IP Configuration", passed)
        return passed

    # ------------------------------------------------------------------
    def _start_conv_thread(self):
        self._conv_result = None
        self._conv_thread = threading.Thread(
            target=self._conv_worker, daemon=True)
        self._conv_thread.start()

    def _conv_worker(self):
        try:
            self._conv_result = wait_for_pattern(
                self.ser_fol, RE_FINE, self.conv_timeout, self.log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                 "HARD_SYNC": RE_HARD_SYNC,
                                 "COARSE":    RE_COARSE},
                live_log=True)
        except Exception as exc:
            self.log.info(f"  [CONV-THREAD ERROR] {exc}")
            self._conv_result = (False, self.conv_timeout, {})

    def _collect_conv(self) -> Tuple[bool, float, dict]:
        if self._conv_thread:
            self._conv_thread.join(timeout=self.conv_timeout + 2.0)
        return self._conv_result if self._conv_result else (False, self.conv_timeout, {})

    # ------------------------------------------------------------------
    def _step_start_ptp(self) -> bool:
        """Start PTP: Board GM = Grandmaster, Board FOL = Follower."""
        self.log.info("\n--- PTP Setup Step 3: Start PTP ---")
        passed = True

        # Follower first (silent mode)
        self.log.info("  [FOL] ptp_mode follower")
        resp = send_command(self.ser_fol, "ptp_mode follower",
                            self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [FOL] Follower enabled — confirmed")
        else:
            self.log.info(f"  [FOL] No confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.ser_fol.reset_input_buffer()
        self._start_conv_thread()

        # Grandmaster second
        self.log.info("  [GM ] ptp_mode master")
        self.ser_gm.reset_input_buffer()
        self.ser_gm.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.ser_gm, RE_GM_START,
                                       timeout=self.conv_timeout, log=self.log)
        self.log.info(f"  [GM ] {'Grandmaster enabled — confirmed' if gm_ok else 'NOT confirmed'}")
        if not gm_ok: passed = False

        self.log.info(
            f"  Waiting for Follower PTP FINE convergence "
            f"(timeout={self.conv_timeout:.0f}s)...")
        matched, elapsed, milestones = self._collect_conv()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if matched:
            self.log.info(f"  Follower reached FINE in {elapsed:.1f}s  ({ms_str})")
        else:
            self.log.info(
                f"  Follower FINE NOT reached within {self.conv_timeout:.0f}s"
                f"  ({ms_str or 'none'})")
            passed = False

        fine_detail = (f"FINE@{elapsed:.1f}s {ms_str}" if matched
                       else "FINE NOT reached")
        self._record("PTP Setup: FINE Convergence", passed, fine_detail)
        return passed

    # ------------------------------------------------------------------
    def _phase_ptp_active(self) -> bool:
        """Phase 1: collect clk_get samples WITH PTP active."""
        log = self.log
        log.info(
            f"\n{'='*68}")
        log.info(
            f"  PHASE 1: PTP ACTIVE — compensated  ({self.ptp_s:.0f} s)")
        log.info(
            f"{'='*68}")
        log.info(
            "  PTP re-anchors the Follower clock to GM time every ~125 ms (each Sync).")
        log.info(
            "  TC0 tick-rate (TISUBN) is corrected once at UNINIT→MATCHFREQ to compensate")
        log.info(
            "  crystal frequency offset.  In FINE state drift_ppb reports the residual")
        log.info(
            "  frequency error after TISUBN correction (typically ±0..20 ppb).")
        log.info(
            "  Expected result:")
        log.info(
            "    slope  ≈ 0 ppb  (re-anchoring eliminates linear drift)")
        log.info(
            "    drift  ≈ 0 ppb  (residual after TISUBN correction — small, non-zero)")
        log.info(
            "    residual stdev  = deviation between consecutive Sync anchors")

        if not self.no_clk_set:
            log.info(
                f"\n  Settling {self.settle_s:.0f} s after FINE "
                f"before re-zeroing clocks ...")
            time.sleep(self.settle_s)
            log.info("  Zeroing both clocks simultaneously ...")
            ok = zero_both_clocks(self.ser_gm, self.ser_fol, log)
            if not ok:
                log.info("  WARNING: clk_set 0 failed on one or both boards")
            log.info("  Settling 2 s after clk_set ...")
            time.sleep(2.0)
        else:
            if self.settle_s > 0:
                log.info(f"\n  Settling {self.settle_s:.0f} s after FINE ...")
                time.sleep(self.settle_s)

        smpls, elps, dgm, dfol = collect_clk_get_samples(
            self.ser_gm, self.ser_fol,
            self.ptp_s, self.pause_ms,
            self.no_swap, "PTP", log)

        stats = evaluate_samples(smpls, elps, dgm, dfol, "PTP", log)
        if stats is None:
            self._record("Phase 1: PTP active (compensated)", False, "too few samples")
            return False

        print_regression_report(stats, "PHASE 1 — PTP ACTIVE (compensated)", log)
        self._ptp_stats = stats

        threshold_ns = self.slope_threshold_ppm * 1000.0  # ppb = ns/s
        res_threshold_ns = self.residual_threshold_us * 1000.0
        slope_passed    = abs(stats["slope_ppb"]) < threshold_ns
        residual_passed = stats["res_stdev_ns"]   < res_threshold_ns
        passed          = slope_passed and residual_passed

        log.info("")
        tag = "PASS" if slope_passed else "FAIL"
        log.info(
            f"  {tag}  |slope| = {abs(stats['slope_ppm']):.4f} ppm"
            f"  (threshold {self.slope_threshold_ppm} ppm)")
        tag2 = "PASS" if residual_passed else "FAIL"
        log.info(
            f"  {tag2}  residual stdev = {stats['res_stdev_ns']/1000:.3f} µs"
            f"  (threshold {self.residual_threshold_us} µs)")

        detail = (f"slope={stats['slope_ppb']:+.0f}ppb ({stats['slope_ppm']:+.4f}ppm)"
                  f"  res_stdev={stats['res_stdev_ns']/1000:.0f}us"
                  f"  drift_fol={stats['mean_drift_fol']:+.0f}ppb")
        self._record("Phase 1: PTP active (compensated)", passed, detail)
        return passed

    # ------------------------------------------------------------------
    def _print_comparison(self):
        """Side-by-side summary of free-run vs. PTP-active results."""
        if self._free_stats is None or self._ptp_stats is None:
            return

        fr  = self._free_stats
        ptp = self._ptp_stats
        log = self.log

        log.info("\n" + "=" * 68)
        log.info("  BEFORE / AFTER COMPARISON — Effect of PTP Synchronisation")
        log.info("=" * 68)

        reduction = 0.0
        if abs(fr["slope_ppb"]) > 0:
            reduction = (1.0 - abs(ptp["slope_ppb"]) / abs(fr["slope_ppb"])) * 100.0

        log.info(f"  {'Metric':<30}  {'Free-run (no PTP)':>20}  {'PTP active':>16}")
        log.info(f"  {'-'*30}  {'-'*20}  {'-'*16}")
        log.info(f"  {'Slope (ppb)':<30}  "
                 f"{fr['slope_ppb']:>+18.0f}    "
                 f"{ptp['slope_ppb']:>+14.0f}")
        log.info(f"  {'Slope (ppm)':<30}  "
                 f"{fr['slope_ppm']:>+18.4f}    "
                 f"{ptp['slope_ppm']:>+14.4f}")
        log.info(f"  {'Residual stdev (µs)':<30}  "
                 f"{fr['res_stdev_ns']/1000:>18.3f}    "
                 f"{ptp['res_stdev_ns']/1000:>14.3f}")
        log.info(f"  {'Drift FOL mean (ppb)':<30}  "
                 f"{fr['mean_drift_fol']:>+18.0f}    "
                 f"{ptp['mean_drift_fol']:>+14.0f}")
        log.info(f"  {'Drift FOL stdev (ppb)':<30}  "
                 f"{fr['stdev_drift_fol']:>18.0f}    "
                 f"{ptp['stdev_drift_fol']:>14.0f}")
        log.info(f"  {'Time span (s)':<30}  "
                 f"{fr['t_span']:>18.1f}    "
                 f"{ptp['t_span']:>14.1f}")
        log.info("")
        log.info(f"  Slope reduction by PTP : {reduction:.1f} %")
        log.info("")
        log.info("  Interpretation:")
        log.info(f"    Without PTP the Follower clock drifted at "
                 f"{fr['slope_ppb']:+.0f} ppb ({fr['slope_ppm']:+.4f} ppm).")
        log.info( "    PTP synchronises the clock via periodic hard re-anchoring")
        log.info( "    (each Sync message ~125 ms).  TC0 tick-rate (TISUBN) is set once")
        log.info( "    at UNINIT→MATCHFREQ to compensate crystal frequency offset.")
        log.info( "    drift_ppb reports the residual frequency error after TISUBN correction")
        if reduction > 0:
            log.info(f"    The residual slope error was reduced by {reduction:.1f} %")
            log.info( "    because the anchor is reset to GM time every ~125 ms.")
            log.info(f"    Max drift between two Syncs: ~{abs(fr['slope_ppb'])*0.125/1000:.0f} µs"
                     f"  ({abs(fr['slope_ppb']):.0f} ppb × 125 ms)")
        log.info(f"    Residual stdev {ptp['res_stdev_ns']/1000:.3f} µs reflects the"
                 f" mean deviation between consecutive anchor updates.")
        log.info("=" * 68)

        passed = (reduction > 50.0 or
                  abs(ptp["slope_ppb"]) < self.slope_threshold_ppm * 1000.0)
        self._record(
            "Comparison: Drift reduction by PTP",
            passed,
            f"free={fr['slope_ppb']:+.0f}ppb"
            f"  ptp={ptp['slope_ppb']:+.0f}ppb"
            f"  reduction={reduction:.1f}%")

    # ------------------------------------------------------------------
    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 68)
        log.info("  PTP Synchronisation Before/After Test")
        log.info("=" * 68)
        log.info(f"Date                    : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"Board GM  port          : {self.gm_port}  IP {self.gm_ip}")
        log.info(f"Board FOL port          : {self.fol_port}  IP {self.fol_ip}")
        log.info(f"Free-run duration       : {self.free_run_s:.0f} s")
        log.info(f"PTP-active duration     : {self.ptp_s:.0f} s")
        log.info(f"Pause between samples   : {self.pause_ms} ms")
        log.info(f"Settle after FINE       : {self.settle_s:.0f} s")
        log.info(f"Slope threshold         : {self.slope_threshold_ppm} ppm")
        log.info(f"Residual threshold      : {self.residual_threshold_us} µs")
        log.info(f"PTP conv timeout        : {self.conv_timeout:.0f} s")
        log.info(f"Swap-symmetry           : {'disabled' if self.no_swap else 'enabled'}")
        log.info(f"clk_set 0 before phases : {'skipped' if self.no_clk_set else 'yes'}")
        log.info(f"Reset boards            : {'skipped' if self.no_reset else 'yes'}")
        log.info("")

        self._connect()
        try:
            if not self.no_reset:
                self._step_reset()

            # ---- Phase 0: Free-running, no PTP ----
            self._phase_free_run()

            # ---- PTP Setup ----
            log.info("\n" + "=" * 68)
            log.info("  PTP SETUP")
            log.info("=" * 68)
            if not self._step_ip():        return self._report(start_time)
            if not self._step_start_ptp(): return self._report(start_time)

            # ---- Phase 1: PTP active ----
            self._phase_ptp_active()

            # ---- Comparison ----
            self._print_comparison()

        except KeyboardInterrupt:
            log.info("\nInterrupted by user.")
        except Exception as exc:
            import traceback
            log.info(f"\nFATAL: {type(exc).__name__}: {exc}")
            log.info(traceback.format_exc())
        finally:
            self._disconnect()

        return self._report(start_time)

    # ------------------------------------------------------------------
    def _report(self, start_time: datetime.datetime) -> int:
        log     = self.log
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info("\n" + "=" * 68)
        log.info("  PTP Sync Before/After Test — Final Report")
        log.info("=" * 68)
        passed_count = 0
        for name, passed, detail in self.results:
            tag = "PASS" if passed else "FAIL"
            log.info(f"[{tag}] {name}")
            for part in detail.split("  "):
                p = part.strip()
                if p: log.info(f"       {p}")
            if passed: passed_count += 1
        total   = len(self.results)
        overall = "PASS" if passed_count == total else "FAIL"
        log.info("")
        log.info(f"Duration : {elapsed:.1f} s")
        log.info(f"Overall  : {overall} ({passed_count}/{total})")
        if log.log_file:
            log.info(f"Log      : {log.log_file}")
        return 0 if passed_count == total else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="PTP Synchronisation Before/After Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    # Ports / network
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT,
                   help=f"COM port of Board A / Grandmaster (default: {DEFAULT_GM_PORT})")
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT,
                   help=f"COM port of Board B / Follower (default: {DEFAULT_FOL_PORT})")
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP,
                   help=f"IP address for Grandmaster (default: {DEFAULT_GM_IP})")
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP,
                   help=f"IP address for Follower (default: {DEFAULT_FOL_IP})")
    p.add_argument("--netmask",  default=DEFAULT_NETMASK,
                   help=f"Netmask (default: {DEFAULT_NETMASK})")
    p.add_argument("--baudrate", default=DEFAULT_BAUDRATE, type=int)

    # Timing
    p.add_argument("--free-run-s", default=DEFAULT_FREE_RUN_S, type=float,
                   help=f"Duration of free-run phase (no PTP) in seconds"
                        f" (default: {DEFAULT_FREE_RUN_S})")
    p.add_argument("--ptp-s", default=DEFAULT_PTP_S, type=float,
                   help=f"Duration of PTP-active phase in seconds"
                        f" (default: {DEFAULT_PTP_S})")
    p.add_argument("--pause-ms", default=DEFAULT_PAUSE_MS, type=int,
                   help=f"Pause between clk_get pairs in ms (default: {DEFAULT_PAUSE_MS})")
    p.add_argument("--settle", default=DEFAULT_SETTLE_S, type=float,
                   help=f"Settle time after PTP FINE convergence in s"
                        f" (default: {DEFAULT_SETTLE_S})")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float,
                   help=f"PTP FINE convergence timeout in s (default: {DEFAULT_CONV_TIMEOUT})")

    # PASS criteria
    p.add_argument("--slope-threshold-ppm", default=DEFAULT_SLOPE_THRESHOLD_PPM,
                   type=float,
                   help=f"PASS: residual slope threshold for PTP phase in ppm"
                        f" (default: {DEFAULT_SLOPE_THRESHOLD_PPM})")
    p.add_argument("--residual-threshold-us", default=DEFAULT_RESIDUAL_THRESHOLD_US,
                   type=float,
                   help=f"PASS: residual stdev threshold in µs"
                        f" (default: {DEFAULT_RESIDUAL_THRESHOLD_US})")

    # Misc
    p.add_argument("--no-swap", action="store_true",
                   help="Disable swap-symmetrisation of clk_get pairs")
    p.add_argument("--no-clk-set", action="store_true",
                   help="Skip 'clk_set 0' before each collection phase")
    p.add_argument("--no-reset", action="store_true",
                   help="Skip board reset at startup (boards already running)")
    p.add_argument("--log-file", default=None,
                   help="Write output to this file in addition to stdout")
    p.add_argument("--verbose", action="store_true",
                   help="Show raw serial debug output")

    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"ptp_sync_before_after_test_{ts}.log"

    log = Logger(log_file=log_file, verbose=args.verbose)
    test = PTPSyncBeforeAfterTest(
        gm_port               = args.gm_port,
        fol_port              = args.fol_port,
        gm_ip                 = args.gm_ip,
        fol_ip                = args.fol_ip,
        netmask               = args.netmask,
        free_run_s            = args.free_run_s,
        ptp_s                 = args.ptp_s,
        pause_ms              = args.pause_ms,
        settle_s              = args.settle,
        slope_threshold_ppm   = args.slope_threshold_ppm,
        residual_threshold_us = args.residual_threshold_us,
        conv_timeout          = args.conv_timeout,
        no_swap               = args.no_swap,
        no_clk_set            = args.no_clk_set,
        no_reset              = args.no_reset,
        log                   = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
