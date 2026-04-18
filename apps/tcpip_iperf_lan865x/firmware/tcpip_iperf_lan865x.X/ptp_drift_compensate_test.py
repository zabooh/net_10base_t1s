#!/usr/bin/env python3
"""PTP Drift Compensation Test
==============================

Validates that PTP synchronisation actively compensates the crystal frequency
difference between two boards, as seen through the TC0-based software clock
(clk_get CLI command).

Background:
  Without PTP two free-running TC0 clocks drift apart linearly at a rate equal
  to their crystal frequency difference (typically 1–10 ppm).  This is the
  baseline established by hw_timer_sync_test.py.

  With PTP active the Follower's IIR filter continuously adjusts the TC0 tick
  rate to match the Grandmaster.  The residual slope of diff(t) should collapse
  from ~crystal_diff_ppb toward ~0, and the drift_ppb field reported by clk_get
  should converge to a value that tracks the crystal offset.

Test phases:
  0. [Optional] Baseline — collect paired clk_get samples *before* PTP is
     started to measure the raw crystal drift (slope_baseline).
     Enable with --baseline-s N (N > 0).

  1. PTP Setup — set IP addresses, ping connectivity, start PTP:
     Board A = Grandmaster, Board B = Follower (silent mode).
     Wait for Follower FINE convergence.

  2. Compensated measurement — after an optional settle period collect N
     paired clk_get samples spaced --pause-ms ms apart.
     Fit linear regression diff(t) = intercept + slope * t.
       slope  -> residual clock rate error after PTP correction  [ns/s = ppb]
       drift  -> IIR correction value applied by firmware         [ppb]

  ptp_trace on is sent to both boards after FINE convergence.  All debug
  lines emitted by the firmware are captured in a background reader thread
  and printed alongside each sample.  Outliers (|diff| > --outlier-us) are
  flagged and the surrounding trace context is shown.

PASS criterion:
    |slope_ptp| < --slope-threshold-ppm  (default 2.0 ppm)

Usage:
    python ptp_drift_compensate_test.py --gm-port COM8 --fol-port COM10

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

DEFAULT_GM_PORT            = "COM8"
DEFAULT_FOL_PORT           = "COM10"
DEFAULT_GM_IP              = "192.168.0.30"
DEFAULT_FOL_IP             = "192.168.0.20"
DEFAULT_NETMASK            = "255.255.255.0"
DEFAULT_BAUDRATE           = 115200
DEFAULT_CMD_TIMEOUT        = 5.0
DEFAULT_CONV_TIMEOUT       = 60.0
DEFAULT_BASELINE_S         = 0.0
DEFAULT_DURATION_S         = 120.0   # 120s to capture rare 9ms outliers
DEFAULT_PAUSE_MS           = 500
DEFAULT_SETTLE_S           = 5.0
DEFAULT_SLOPE_THRESHOLD_PPM   = 2.0
DEFAULT_RESIDUAL_THRESHOLD_US = 500.0
DEFAULT_OUTLIER_US         = 1000.0  # flag samples above this as outliers

RE_IP_SET     = re.compile(r"Set ip address OK|IP address set to")
RE_BUILD      = re.compile(r"\[APP\] Build:\s+(.+)")
RE_PING_REPLY = re.compile(r"Ping:.*reply.*from|Reply from", re.IGNORECASE)
RE_PING_DONE  = re.compile(r"Ping: done\.")
RE_FOL_START  = re.compile(r"PTP Follower enabled")
RE_GM_START   = re.compile(r"PTP Grandmaster enabled")
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
            safe = line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
            print(safe, flush=True)
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
# Serial helpers (setup phase — used before SerialMux is active)
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
# SerialMux — background reader that separates clk_get from trace output
# ---------------------------------------------------------------------------

class SerialMux:
    """
    Background reader thread per serial port.

    clk_get responses (lines containing "clk_get:") go into _clk_queue.
    All other non-empty lines go into _trace_lines as (t_ns, label, line).

    This allows clk_get polling and ptp_trace output to coexist on the
    same serial port without reset_input_buffer() discarding trace lines.
    """

    def __init__(self, ser: serial.Serial, label: str, log: Logger):
        self.ser   = ser
        self.label = label
        self.log   = log
        self._buf  = ""
        self._clk_queue:   List[str]               = []
        self._trace_lines: List[Tuple[int, str, str]] = []  # (t_ns, label, text)
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._reader, daemon=True,
                                         name=f"mux-{self.label}")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _reader(self):
        while self._running:
            try:
                chunk = self.ser.read(256)
                if chunk:
                    self._buf += chunk.decode("ascii", errors="replace")
                    while "\n" in self._buf:
                        line, self._buf = self._buf.split("\n", 1)
                        line = line.rstrip()
                        if not line:
                            continue
                        t_ns = time.perf_counter_ns()
                        with self._lock:
                            if "clk_get:" in line:
                                self._clk_queue.append(line)
                            else:
                                self._trace_lines.append((t_ns, self.label, line))
                else:
                    time.sleep(0.002)
            except Exception:
                break

    def send_clk_get(self, timeout_s: float = 1.0) -> Tuple[int, Optional[str]]:
        """Send 'clk_get', return (t_send_ns, response_line_or_None)."""
        with self._lock:
            self._clk_queue.clear()
        t_send = time.perf_counter_ns()
        self.ser.write(b"clk_get\r\n")
        deadline = time.perf_counter_ns() + int(timeout_s * 1e9)
        while time.perf_counter_ns() < deadline:
            with self._lock:
                if self._clk_queue:
                    return t_send, self._clk_queue.pop(0)
            time.sleep(0.002)
        return t_send, None

    def drain_trace(self) -> List[Tuple[int, str, str]]:
        """Return and clear all accumulated trace lines."""
        with self._lock:
            lines = list(self._trace_lines)
            self._trace_lines.clear()
        return lines

    def send_and_wait(self, cmd: str, expect: str, timeout_s: float = 2.0) -> Tuple[int, bool]:
        """Send a command, wait for a trace line containing *expect*. Thread-safe."""
        with self._lock:
            # Remove any stale trace lines so we don't match old output
            self._trace_lines = [(t, l, x) for t, l, x in self._trace_lines
                                 if expect not in x]
        t_send = time.perf_counter_ns()
        self.ser.write((cmd + "\r\n").encode("ascii"))
        deadline = t_send + int(timeout_s * 1e9)
        while time.perf_counter_ns() < deadline:
            with self._lock:
                for t, l, x in self._trace_lines:
                    if expect in x:
                        return t_send, True
            time.sleep(0.005)
        return t_send, False

    def send_raw(self, cmd: str):
        """Send a command string without waiting for response."""
        self.ser.write((cmd + "\r\n").encode("ascii"))


# ---------------------------------------------------------------------------
# Shared trace collector (merges GM + FOL trace lines, sorted by time)
# ---------------------------------------------------------------------------

class TraceCollector:
    """Merges trace lines from multiple SerialMux instances."""

    def __init__(self, log: Logger):
        self.log = log
        self._muxes: List[SerialMux] = []
        self._all_lines: List[Tuple[int, str, str]] = []  # (t_ns, label, text)
        self._lock = threading.Lock()

    def add_mux(self, mux: SerialMux):
        self._muxes.append(mux)

    def drain_all(self) -> List[Tuple[int, str, str]]:
        """Drain all muxes, merge and sort by time."""
        lines = []
        for mux in self._muxes:
            lines.extend(mux.drain_trace())
        lines.sort(key=lambda x: x[0])
        with self._lock:
            self._all_lines.extend(lines)
        return lines

    def get_window(self, t_center_ns: int, before_ns: int, after_ns: int
                   ) -> List[Tuple[int, str, str]]:
        """Return stored trace lines within [t_center-before, t_center+after]."""
        lo = t_center_ns - before_ns
        hi = t_center_ns + after_ns
        with self._lock:
            return [(t, lbl, txt) for t, lbl, txt in self._all_lines
                    if lo <= t <= hi]

    def print_lines(self, lines: List[Tuple[int, str, str]], indent: str = "    "):
        for t_ns, lbl, txt in lines:
            self.log.info(f"{indent}[TRACE][{lbl}] {txt}")


# ---------------------------------------------------------------------------
# Measurement helpers using SerialMux
# ---------------------------------------------------------------------------

def _parse_clk_get(raw: str) -> Tuple[Optional[int], Optional[int]]:
    m = RE_CLK_GET.search(raw)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _query_mux(mux: SerialMux, result_dict: dict, key: str,
               timeout_s: float = 1.0):
    """Send clk_get via mux, store {t_send_ns, t_recv_ns, line}."""
    t_send, line = mux.send_clk_get(timeout_s)
    result_dict[key] = {"line": line or "", "t_send_ns": t_send}


def single_measurement_mux(mux_gm: SerialMux, mux_fol: SerialMux,
                            swap: bool = False
                            ) -> Tuple[Optional[int], Optional[int],
                                       Optional[int], int]:
    """
    One paired clk_get measurement via SerialMux.
    Returns (diff_ns, drift_gm_ppb, drift_fol_ppb, t_mid_ns).
    """
    res: dict = {}
    if swap:
        ta = threading.Thread(target=_query_mux, args=(mux_fol, res, "fol"))
        tb = threading.Thread(target=_query_mux, args=(mux_gm,  res, "gm"))
    else:
        ta = threading.Thread(target=_query_mux, args=(mux_gm,  res, "gm"))
        tb = threading.Thread(target=_query_mux, args=(mux_fol, res, "fol"))
    ta.start(); tb.start()
    ta.join();  tb.join()

    clk_gm,  d_gm  = _parse_clk_get(res.get("gm",  {}).get("line", ""))
    clk_fol, d_fol = _parse_clk_get(res.get("fol", {}).get("line", ""))

    t_mid = (res.get("gm",  {}).get("t_send_ns", 0) +
             res.get("fol", {}).get("t_send_ns", 0)) // 2

    if clk_gm is None or clk_fol is None:
        return None, None, None, t_mid

    send_delta = res["fol"]["t_send_ns"] - res["gm"]["t_send_ns"]
    diff_ns    = (clk_fol - clk_gm) - send_delta
    return diff_ns, d_gm, d_fol, t_mid


# ---------------------------------------------------------------------------
# Clock zero helpers (direct serial, used before mux is active)
# ---------------------------------------------------------------------------

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
        log.info(f"  Thread send skew: {skew_ns / 1000:.1f} us")
    return ok_gm and ok_fol


# ---------------------------------------------------------------------------
# Sample collection with trace annotation
# ---------------------------------------------------------------------------

def _linear_regression(xs: List[float], ys: List[float]):
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


def collect_clk_get_samples(mux_gm: SerialMux, mux_fol: SerialMux,
                             trace: TraceCollector,
                             duration_s: float, pause_ms: int,
                             no_swap: bool, label: str,
                             outlier_us: float,
                             log: Logger) -> Tuple[List[int], List[float],
                                                   List[int], List[int]]:
    """
    Collect paired clk_get samples for *duration_s* seconds.
    Prints ptp_trace lines after each sample.
    Outliers (|diff| > outlier_us) are flagged with surrounding trace context.
    Returns (diffs_ns, elapsed_s, drifts_gm, drifts_fol).
    """
    samples:    List[int]   = []
    elapsed_s:  List[float] = []
    drifts_gm:  List[int]   = []
    drifts_fol: List[int]   = []
    n_err   = 0
    t_start = time.perf_counter_ns()
    i       = 0
    outlier_ns = outlier_us * 1000.0

    log.info(f"  Collecting for {duration_s:.0f} s  (pause={pause_ms} ms,"
             f" outlier flag > {outlier_us:.0f} us) ...")

    while True:
        elapsed_now = (time.perf_counter_ns() - t_start) / 1e9
        if elapsed_now >= duration_s:
            break

        swap = (not no_swap) and (i % 2 == 1)
        t_before = time.perf_counter_ns()
        diff, d_gm, d_fol, t_mid = single_measurement_mux(mux_gm, mux_fol, swap)

        # Drain trace lines accumulated since last sample
        trace_lines = trace.drain_all()

        if diff is None:
            n_err += 1
            log.info(f"  [{label}][{i+1:4d}] ERROR (no clk_get response)")
            # Print any trace lines anyway
            if trace_lines:
                for _, lbl, txt in trace_lines:
                    log.info(f"    [TRACE][{lbl}] {txt}")
        else:
            elapsed = (t_mid - t_start) / 1e9
            samples.append(diff)
            elapsed_s.append(elapsed)
            drifts_gm.append(d_gm)
            drifts_fol.append(d_fol)

            is_outlier = abs(diff) > outlier_ns
            tag = " [swap]" if swap else "       "
            flag = "  *** OUTLIER ***" if is_outlier else ""
            log.info(
                f"  [{label}][{i+1:4d}]{tag}  t={elapsed:7.2f}s"
                f"  diff={diff/1000:+9.2f} us"
                f"  drift GM={d_gm:+d} FOL={d_fol:+d} ppb{flag}")

            # Always print trace lines after the sample line
            if trace_lines:
                for _, lbl, txt in trace_lines:
                    log.info(f"    [TRACE][{lbl}] {txt}")

            # For outliers: also show a wider window from stored history
            if is_outlier:
                log.info(f"  *** OUTLIER at t={elapsed:.2f}s:"
                         f" |diff|={abs(diff)/1000:.1f} us > {outlier_us:.0f} us ***")
                window = trace.get_window(t_mid,
                                          before_ns=int(2e9),
                                          after_ns=int(1e9))
                if window:
                    log.info(f"  Trace context (±2s window):")
                    for _, lbl, txt in window:
                        log.info(f"    [CTX][{lbl}] {txt}")

        i += 1
        remaining = duration_s - (time.perf_counter_ns() - t_start) / 1e9
        if remaining > 0:
            time.sleep(min(pause_ms / 1000.0, remaining))

    if n_err:
        log.info(f"  [{label}] Errors: {n_err}")

    return samples, elapsed_s, drifts_gm, drifts_fol


def evaluate_samples(samples: List[int], elapsed_s: List[float],
                     drifts_gm: List[int], drifts_fol: List[int],
                     label: str, log: Logger) -> Optional[dict]:
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
    cdga  = [drifts_gm[i]  for i in clean_idx]
    cdfol = [drifts_fol[i] for i in clean_idx]

    slope, intercept, residuals = _linear_regression(cxs, cys)
    res_stdev2 = statistics.stdev(residuals)
    res_sem2   = res_stdev2 / (len(residuals) ** 0.5)

    t_span = cxs[-1] - cxs[0] if len(cxs) > 1 else 0.0
    mean_drift_gm   = statistics.mean(cdga)   if cdga   else 0.0
    mean_drift_fol  = statistics.mean(cdfol)  if cdfol  else 0.0
    stdev_drift_fol = statistics.stdev(cdfol) if len(cdfol) > 1 else 0.0

    return {
        "n_clean":         len(cxs),
        "n_total":         len(samples),
        "n_out":           n_out,
        "t_span":          t_span,
        "slope_ppb":       slope,
        "slope_ppm":       slope / 1000.0,
        "intercept_ns":    intercept,
        "res_stdev_ns":    res_stdev2,
        "res_sem_ns":      res_sem2,
        "mean_drift_gm":   mean_drift_gm,
        "mean_drift_fol":  mean_drift_fol,
        "stdev_drift_fol": stdev_drift_fol,
    }


def print_regression_report(stats: dict, label: str, log: Logger):
    log.info("=" * 66)
    log.info(f"[{label}]")
    log.info(f"  Samples    : {stats['n_clean']}/{stats['n_total']} valid"
             f"  ({stats['n_out']} outliers removed)")
    log.info(f"  Time span  : {stats['t_span']:.2f} s")
    log.info("")
    log.info("  Linear regression: diff(t) = intercept + slope * t")
    log.info(f"    Intercept : {stats['intercept_ns']:+.0f} ns"
             f"  ({stats['intercept_ns']/1000:+.1f} us)")
    log.info(f"    Slope     : {stats['slope_ppb']:+.0f} ppb"
             f"  ({stats['slope_ppm']:+.4f} ppm)")
    log.info("")
    log.info("  Residuals (after subtracting linear trend)")
    log.info(f"    Stdev     : {stats['res_stdev_ns']:.0f} ns"
             f"  ({stats['res_stdev_ns']/1000:.3f} us)")
    log.info(f"    SEM       : {stats['res_sem_ns']:.0f} ns"
             f"  ({stats['res_sem_ns']/1000:.3f} us)")
    log.info("")
    log.info(f"  Drift GM   : {stats['mean_drift_gm']:+.0f} ppb  (mean)")
    log.info(f"  Drift FOL  : {stats['mean_drift_fol']:+.0f} ppb  (mean)"
             f"  +/-{stats['stdev_drift_fol']:.0f} ppb  (stdev)")
    log.info("=" * 66)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class PTPDriftCompensateTest:

    def __init__(self, gm_port: str, fol_port: str,
                 gm_ip: str, fol_ip: str, netmask: str,
                 baseline_s: float, duration_s: float,
                 pause_ms: int, settle_s: float,
                 slope_threshold_ppm: float,
                 residual_threshold_us: float,
                 outlier_us: float,
                 conv_timeout: float, no_swap: bool, no_clk_set: bool,
                 no_reset: bool, no_trace: bool,
                 log: Logger):
        self.gm_port              = gm_port
        self.fol_port             = fol_port
        self.gm_ip                = gm_ip
        self.fol_ip               = fol_ip
        self.netmask              = netmask
        self.baseline_s           = baseline_s
        self.duration_s           = duration_s
        self.pause_ms             = pause_ms
        self.settle_s             = settle_s
        self.slope_threshold_ppm  = slope_threshold_ppm
        self.residual_threshold_us = residual_threshold_us
        self.outlier_us           = outlier_us
        self.conv_timeout         = conv_timeout
        self.no_swap              = no_swap
        self.no_clk_set           = no_clk_set
        self.no_reset             = no_reset
        self.no_trace             = no_trace
        self.log                  = log

        self.ser_gm:  Optional[serial.Serial] = None
        self.ser_fol: Optional[serial.Serial] = None
        self.mux_gm:  Optional[SerialMux]     = None
        self.mux_fol: Optional[SerialMux]     = None
        self.trace:   Optional[TraceCollector] = None
        self.results: list = []
        self.gm_build:  str = "unknown"
        self.fol_build: str = "unknown"

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple] = None
        self._baseline_stats: Optional[dict] = None
        self._ptp_stats:      Optional[dict] = None

    # ------------------------------------------------------------------
    def connect(self):
        for label, port, attr in [("GM",  self.gm_port,  "ser_gm"),
                                   ("FOL", self.fol_port, "ser_fol")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def disconnect(self):
        self._stop_mux()
        for ser in (self.ser_gm, self.ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    def _start_mux(self):
        self.mux_gm  = SerialMux(self.ser_gm,  "GM",  self.log)
        self.mux_fol = SerialMux(self.ser_fol,  "FOL", self.log)
        self.trace   = TraceCollector(self.log)
        self.trace.add_mux(self.mux_gm)
        self.trace.add_mux(self.mux_fol)
        self.mux_gm.start()
        self.mux_fol.start()
        self.log.info("  [MUX] Background trace readers started (GM + FOL)")

    def _stop_mux(self):
        if self.mux_gm:
            self.mux_gm.stop()
            self.mux_gm = None
        if self.mux_fol:
            self.mux_fol.stop()
            self.mux_fol = None

    def _record(self, name: str, passed: bool, detail: str):
        self.results.append((name, passed, detail))

    # ------------------------------------------------------------------
    def step_reset(self):
        self.log.info("\n--- Step 0: Reset ---")
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
    def step_ip(self) -> bool:
        self.log.info("\n--- Step 1: IP Configuration ---")
        passed = True
        for label, ser, ip in [("GM",  self.ser_gm,  self.gm_ip),
                                 ("FOL", self.ser_fol, self.fol_ip)]:
            resp = send_command(ser, f"setip eth0 {ip} {self.netmask}",
                                self.conv_timeout, self.log)
            ok = bool(RE_IP_SET.search(resp))
            self.log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")
            if not ok: passed = False
        self._record("Step 1: IP Configuration", passed, "")
        return passed

    # ------------------------------------------------------------------
    def step_ping(self) -> bool:
        self.log.info("\n--- Step 2: Ping Connectivity ---")
        passed = True
        for src_label, src_ser, dst_ip in [
            ("GM  -> FOL", self.ser_gm,  self.fol_ip),
            ("FOL -> GM",  self.ser_fol, self.gm_ip),
        ]:
            src_ser.reset_input_buffer()
            src_ser.write(f"ping {dst_ip}\r\n".encode("ascii"))
            matched, elapsed, ms = wait_for_pattern(
                src_ser, RE_PING_DONE, timeout=15.0, log=self.log,
                extra_patterns={"reply": RE_PING_REPLY}, live_log=True)
            ok = matched or ms.get("reply") is not None
            self.log.info(f"  [{src_label}]: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
            if not ok: passed = False
        self._record("Step 2: Ping", passed, "")
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
    def step_start_ptp(self) -> bool:
        self.log.info("\n--- Step 3: Start PTP (silent mode) ---")
        passed = True

        self.log.info("  [FOL] ptp_mode follower  (silent)")
        resp = send_command(self.ser_fol, "ptp_mode follower",
                            self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [FOL] confirmed")
        else:
            self.log.info(f"  [FOL] no confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.ser_fol.reset_input_buffer()
        self._start_conv_thread()

        self.log.info("  [GM ] ptp_mode master  (silent)")
        self.ser_gm.reset_input_buffer()
        self.ser_gm.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.ser_gm, RE_GM_START,
                                       timeout=self.conv_timeout, log=self.log)
        self.log.info(f"  [GM ] {'confirmed' if gm_ok else 'NOT confirmed'}")
        if not gm_ok: passed = False

        self.log.info(f"  Waiting for FOL FINE (timeout={self.conv_timeout:.0f}s)...")
        matched, elapsed, milestones = self._collect_conv()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if matched:
            self.log.info(f"  FOL FINE in {elapsed:.1f}s  ({ms_str})")
        else:
            self.log.info(
                f"  FOL FINE NOT reached within {self.conv_timeout:.0f}s"
                f"  ({ms_str or 'none'})")
            passed = False

        fine_detail = (f"FINE@{elapsed:.1f}s {ms_str}" if matched
                       else "FINE NOT reached")
        self._record("Step 3: Start PTP (FINE convergence)", passed, fine_detail)
        return passed

    # ------------------------------------------------------------------
    def step_enable_trace(self):
        """Send ptp_trace on to both boards (unless --no-trace), then start SerialMux readers."""
        if self.no_trace:
            self.log.info("\n--- Step 4: Start mux readers (ptp_trace DISABLED) ---")
            self._start_mux()
            return
        self.log.info("\n--- Step 4: Enable ptp_trace + Start trace readers ---")
        for label, ser in [("GM", self.ser_gm), ("FOL", self.ser_fol)]:
            ser.reset_input_buffer()
            ser.write(b"ptp_trace on\r\n")
            time.sleep(0.2)
            resp = ser.read(ser.in_waiting).decode("ascii", errors="replace")
            self.log.info(f"  [{label}] ptp_trace on -> {resp.strip()!r}")
        self._start_mux()

    def step_disable_trace(self):
        """Stop mux readers, send ptp_trace off (unless --no-trace)."""
        self._stop_mux()
        if self.no_trace:
            return
        self.log.info("\n--- Disable ptp_trace ---")
        for label, ser in [("GM", self.ser_gm), ("FOL", self.ser_fol)]:
            if ser and ser.is_open:
                try:
                    ser.write(b"ptp_trace off\r\n")
                except Exception:
                    pass
                self.log.info(f"  [{label}] ptp_trace off sent")

    # ------------------------------------------------------------------
    def step_ptp_collect(self) -> bool:
        self.log.info(
            f"\n--- Phase 1: Compensated Measurement (PTP active, "
            f"{self.duration_s:.0f} s) ---")
        self.log.info(
            "  PTP IIR filter is now correcting the Follower TC0 tick rate.")
        self.log.info(
            "  Expected: slope ~= 0 ppb, drift_fol ~= crystal offset")

        if not self.no_clk_set:
            self.log.info(
                f"  Settling {self.settle_s:.0f} s after FINE before zeroing clocks...")
            time.sleep(self.settle_s)
            self.log.info("  Zeroing both clocks ...")
            if self.mux_gm and self.mux_fol:
                # Route through mux to avoid concurrent serial-read race condition.
                # Send BOTH in parallel using threads + rendezvous barrier so the
                # write() calls happen within a few us of each other (not sequentially
                # like the mux's own send_and_wait would when called one after another).
                res: dict = {}
                ready_gm  = threading.Event()
                ready_fol = threading.Event()
                go        = threading.Event()

                def _set_via_mux(mux, key, ready):
                    ready.set()
                    go.wait()
                    t, ok = mux.send_and_wait("clk_set 0", "clk_set ok")
                    res[key] = (t, ok)

                ta = threading.Thread(target=_set_via_mux,
                                      args=(self.mux_gm,  "gm",  ready_gm))
                tb = threading.Thread(target=_set_via_mux,
                                      args=(self.mux_fol, "fol", ready_fol))
                ta.start(); tb.start()
                ready_gm.wait(); ready_fol.wait()
                go.set()
                ta.join(timeout=3.0); tb.join(timeout=3.0)
                t_gm,  ok_gm  = res.get("gm",  (0, False))
                t_fol, ok_fol = res.get("fol", (0, False))
                skew_us = (t_fol - t_gm) / 1000
                self.log.info(f"  [GM ] clk_set 0: {'OK' if ok_gm  else 'FAIL'}")
                self.log.info(f"  [FOL] clk_set 0: {'OK' if ok_fol else 'FAIL'}")
                self.log.info(f"  Thread send skew: {skew_us:.1f} us")
                ok = ok_gm and ok_fol
            else:
                ok = zero_both_clocks(self.ser_gm, self.ser_fol, self.log)
            if not ok:
                self.log.info("  WARNING: clk_set 0 failed on one or both boards")
        else:
            if self.settle_s > 0:
                self.log.info(f"  Settling {self.settle_s:.0f} s after FINE ...")
                time.sleep(self.settle_s)

        # Drain any trace lines accumulated during settle
        if self.trace:
            self.trace.drain_all()

        # Reset firmware loop_stats counters on both boards so the max
        # we print at the end covers only the sampling window.
        if self.mux_gm and self.mux_fol:
            self.mux_gm.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
            self.mux_fol.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
            if self.trace:
                self.trace.drain_all()

        smpls, elps, dgm, dfol = collect_clk_get_samples(
            self.mux_gm, self.mux_fol, self.trace,
            self.duration_s, self.pause_ms,
            self.no_swap, "PTP", self.outlier_us, self.log)

        # Drain remaining trace lines after collection ends
        final_trace = self.trace.drain_all() if self.trace else []
        if final_trace:
            self.log.info("\n  --- Remaining trace lines after collection ---")
            for _, lbl, txt in final_trace:
                self.log.info(f"    [TRACE][{lbl}] {txt}")

        # Query per-subsystem loop timing to identify main-loop stalls.
        if self.mux_gm and self.mux_fol:
            self.log.info("\n  --- loop_stats (post-measurement) ---")
            for label, mux in [("GM", self.mux_gm), ("FOL", self.mux_fol)]:
                mux.send_and_wait("loop_stats", "TOTAL", timeout_s=2.0)
                time.sleep(0.1)  # give trailing lines time to arrive in mux
                lines = self.trace.drain_all() if self.trace else []
                for _, _, txt in lines:
                    if "loop_stats" in txt or any(k in txt for k in
                        ("SYS_CMD", "TCPIP", "LOG_FLUSH", "APP", "TOTAL", "subsystem")):
                        self.log.info(f"    [{label}] {txt}")

        stats = evaluate_samples(smpls, elps, dgm, dfol, "PTP", self.log)
        if stats is None:
            self._record("Phase 1: Compensated Measurement", False, "too few samples")
            return False

        print_regression_report(stats, "WITH PTP SYNC", self.log)
        self._ptp_stats = stats

        threshold_ns    = self.slope_threshold_ppm * 1000.0
        res_threshold_ns = self.residual_threshold_us * 1000.0
        slope_passed    = abs(stats["slope_ppb"]) < threshold_ns
        residual_passed = stats["res_stdev_ns"]   < res_threshold_ns
        passed          = slope_passed and residual_passed

        self.log.info("")
        tag = "PASS" if slope_passed else "FAIL"
        self.log.info(
            f"{tag}  |slope| = {abs(stats['slope_ppm']):.4f} ppm"
            f"  (threshold {self.slope_threshold_ppm} ppm)")
        tag2 = "PASS" if residual_passed else "FAIL"
        self.log.info(
            f"{tag2}  residual stdev = {stats['res_stdev_ns']/1000:.3f} us"
            f"  (threshold {self.residual_threshold_us} us)")

        detail = (f"slope={stats['slope_ppb']:+.0f}ppb({stats['slope_ppm']:+.4f}ppm) "
                  f"res_stdev={stats['res_stdev_ns']/1000:.0f}us "
                  f"drift_fol={stats['mean_drift_fol']:+.0f}ppb")
        self._record("Phase 1: Compensated Measurement", passed, detail)
        return passed

    # ------------------------------------------------------------------
    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 66)
        log.info("  PTP Drift Compensation Test")
        log.info("=" * 66)
        log.info(f"Date                : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"Board A (GM)  port  : {self.gm_port}  IP {self.gm_ip}")
        log.info(f"Board B (FOL) port  : {self.fol_port}  IP {self.fol_ip}")
        log.info(f"Baseline            : "
                 f"{'disabled' if self.baseline_s <= 0 else f'{self.baseline_s:.0f} s'}")
        log.info(f"Collection duration : {self.duration_s:.0f} s"
                 f"  (pause={self.pause_ms} ms)")
        log.info(f"Settle after FINE   : {self.settle_s:.0f} s")
        log.info(f"Slope threshold     : {self.slope_threshold_ppm} ppm")
        log.info(f"Residual threshold  : {self.residual_threshold_us} us")
        log.info(f"Outlier flag > {self.outlier_us:.0f} us")
        log.info(f"PTP conv timeout    : {self.conv_timeout:.0f} s")
        log.info(f"Swap-symmetry       : {'disabled' if self.no_swap else 'enabled'}")
        log.info(f"clk_set 0           : {'skipped' if self.no_clk_set else 'yes'}")
        log.info(f"reset boards        : {'skipped' if self.no_reset else 'yes'}")
        log.info("")

        self.connect()
        try:
            if not self.no_reset:
                self.step_reset()

            if not self.step_ip():   return self._report(start_time)
            if not self.step_ping(): return self._report(start_time)

            if not self.step_start_ptp(): return self._report(start_time)

            self.step_enable_trace()
            self.step_ptp_collect()
            self.step_disable_trace()

        except KeyboardInterrupt:
            log.info("\nInterrupted by user.")
        except Exception as exc:
            import traceback
            log.info(f"\nFATAL: {type(exc).__name__}: {exc}")
            log.info(traceback.format_exc())
        finally:
            self.disconnect()

        return self._report(start_time)

    # ------------------------------------------------------------------
    def _report(self, start_time: datetime.datetime) -> int:
        log     = self.log
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info("\n" + "=" * 66)
        log.info("  PTP Drift Compensation Test -- Final Report")
        log.info("=" * 66)
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
        description="PTP Drift Compensation Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",  default=DEFAULT_NETMASK)
    p.add_argument("--baudrate", default=DEFAULT_BAUDRATE, type=int)
    p.add_argument("--baseline-s", default=DEFAULT_BASELINE_S, type=float)
    p.add_argument("--duration-s", default=DEFAULT_DURATION_S, type=float,
                   help=f"Collection duration in s (default: {DEFAULT_DURATION_S})")
    p.add_argument("--pause-ms", default=DEFAULT_PAUSE_MS, type=int)
    p.add_argument("--settle",   default=DEFAULT_SETTLE_S, type=float)
    p.add_argument("--slope-threshold-ppm",   default=DEFAULT_SLOPE_THRESHOLD_PPM,   type=float)
    p.add_argument("--residual-threshold-us", default=DEFAULT_RESIDUAL_THRESHOLD_US, type=float)
    p.add_argument("--outlier-us", default=DEFAULT_OUTLIER_US, type=float,
                   help=f"Flag samples above this as outliers (default: {DEFAULT_OUTLIER_US} us)")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--no-swap",     action="store_true")
    p.add_argument("--no-clk-set",  action="store_true")
    p.add_argument("--no-reset",    action="store_true")
    p.add_argument("--no-trace",    action="store_true",
                   help="disable ptp_trace on firmware; mux still used for safe clk_get")
    p.add_argument("--log-file",    default=None)
    p.add_argument("--verbose",     action="store_true")
    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"ptp_drift_compensate_test_{ts}.log"

    log = Logger(log_file=log_file, verbose=args.verbose)
    test = PTPDriftCompensateTest(
        gm_port               = args.gm_port,
        fol_port              = args.fol_port,
        gm_ip                 = args.gm_ip,
        fol_ip                = args.fol_ip,
        netmask               = args.netmask,
        baseline_s            = args.baseline_s,
        duration_s            = args.duration_s,
        pause_ms              = args.pause_ms,
        settle_s              = args.settle,
        slope_threshold_ppm   = args.slope_threshold_ppm,
        residual_threshold_us = args.residual_threshold_us,
        outlier_us            = args.outlier_us,
        conv_timeout          = args.conv_timeout,
        no_swap               = args.no_swap,
        no_clk_set            = args.no_clk_set,
        no_reset              = args.no_reset,
        no_trace              = args.no_trace,
        log                   = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
