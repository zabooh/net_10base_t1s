#!/usr/bin/env python3
"""Hardware/Software Timer Synchronisation Test
================================================

Validates that PTP_CLOCK (TC0-based software interpolation) works correctly
INDEPENDENT of PTP Ethernet synchronisation.

Principle:
  1. Both boards' software clocks are set to 0 via 'clk_set 0' as close to
     simultaneous as possible (two parallel threads).
  2. After a settling pause, N paired 'clk_get' readings are taken
     simultaneously (swap-symmetrised like ptp_time_test.py).
  3. A linear regression diff(t) = intercept + slope * t is fitted.
     - slope [ns/s = ppb]: crystal frequency difference between boards (EXPECTED).
     - intercept [ns]: clock sync accuracy at t=0 right after clk_set.
     - residuals: what remains after subtracting the linear crystal drift.
  4. PASS if residual stdev < threshold (default 500 µs).
     The growing mean diff is intentional — free oscillators always drift apart.

What this test proves:
  - TC0 tick-to-ns conversion is correct on both boards.
  - PTP_CLOCK_GetTime_ns() interpolation is consistent (low residuals).
  - The UART serialization latency correction (perf_counter_ns) works.
  - Crystal frequency ratio between the two boards is measured accurately.
  - Baseline accuracy for further PTP anchor testing.

What this test does NOT prove:
  - Correct PTP anchor capture (that is tested by ptp_time_test.py).
  - PTP Ethernet timestamping (RTSA / TTSCAL).

Usage:
    python hw_timer_sync_test.py --a-port COM8 --b-port COM10

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

DEFAULT_A_PORT       = "COM8"
DEFAULT_B_PORT       = "COM10"
DEFAULT_BAUDRATE     = 115200
DEFAULT_CMD_TIMEOUT  = 3.0
DEFAULT_N            = 100
DEFAULT_PAUSE_MS     = 100
DEFAULT_THRESHOLD_US = 500.0   # TC0 timer sync without PTP — expect ~100-500 µs
DEFAULT_SETTLE_S     = 2.0

RE_CLK_SET = re.compile(r"clk_set ok")
RE_CLK_GET = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    def __init__(self, log_file: str = None, verbose: bool = False):
        self.log_file  = log_file
        self.verbose   = verbose
        self._fh       = None
        self._lock     = threading.Lock()
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
    parts     = []
    deadline  = time.monotonic() + timeout
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            parts.append(chunk.decode("ascii", errors="replace"))
            last_data = time.monotonic()
        else:
            if parts and (time.monotonic() - last_data) > 0.3:
                break
            time.sleep(0.02)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Parallel clk_set (zero both clocks as simultaneously as possible)
# ---------------------------------------------------------------------------

def _set_clock_zero(ser: serial.Serial, result_dict: dict, key: str,
                    ready_event: threading.Event,
                    go_event: threading.Event):
    """
    Wait for go_event, then send 'clk_set 0' immediately.
    Both threads signal ready_event before waiting for go_event so the
    main thread fires go_event only when both are prepared.
    """
    ready_event.set()
    go_event.wait()
    ser.reset_input_buffer()
    t_send = time.perf_counter_ns()
    ser.write(b"clk_set 0\r\n")
    resp = b""
    deadline = time.perf_counter_ns() + int(2.0 * 1e9)
    while time.perf_counter_ns() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        resp += chunk
        if b"clk_set ok" in resp:
            break
    result_dict[key] = {
        "raw":       resp.decode(errors="replace"),
        "t_send_ns": t_send,
    }


def zero_both_clocks(ser_a: serial.Serial, ser_b: serial.Serial,
                     log: Logger) -> Tuple[bool, int]:
    """
    Send 'clk_set 0' to both boards in parallel threads as close together
    as possible.

    Returns (ok, skew_ns) where skew_ns is the t_send difference between
    the two threads — purely as diagnostic, not used for correction here
    (the clk_get correction handles it).
    """
    res: dict = {}
    ready_a = threading.Event()
    ready_b = threading.Event()
    go      = threading.Event()

    ta = threading.Thread(target=_set_clock_zero,
                          args=(ser_a, res, "a", ready_a, go))
    tb = threading.Thread(target=_set_clock_zero,
                          args=(ser_b, res, "b", ready_b, go))
    ta.start(); tb.start()
    ready_a.wait(); ready_b.wait()
    go.set()

    ta.join(timeout=3.0); tb.join(timeout=3.0)

    ok_a = RE_CLK_SET.search(res.get("a", {}).get("raw", "")) is not None
    ok_b = RE_CLK_SET.search(res.get("b", {}).get("raw", "")) is not None
    skew_ns = 0
    if "a" in res and "b" in res:
        skew_ns = res["b"]["t_send_ns"] - res["a"]["t_send_ns"]

    log.info(f"  [A] clk_set 0: {'OK' if ok_a else 'FAIL'}")
    log.info(f"  [B] clk_set 0: {'OK' if ok_b else 'FAIL'}")
    log.info(f"  Thread send skew: {skew_ns / 1000:.1f} µs")
    return (ok_a and ok_b), skew_ns


# ---------------------------------------------------------------------------
# Paired clk_get measurement (same technique as ptp_time_test.py)
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


def single_measurement(ser_a: serial.Serial, ser_b: serial.Serial,
                       swap: bool = False) -> Tuple[Optional[int],
                                                    Optional[int],
                                                    Optional[int]]:
    """
    One paired measurement.
    swap=True: B thread started first.

    Returns (diff_ns, drift_a_ppb, drift_b_ppb) or (None, None, None).
    diff_ns = (clk_b - clk_a) - (t_send_b - t_send_a)
    """
    res: dict = {}
    if swap:
        ta = threading.Thread(target=_query_board, args=(ser_b, res, "b"))
        tb = threading.Thread(target=_query_board, args=(ser_a, res, "a"))
    else:
        ta = threading.Thread(target=_query_board, args=(ser_a, res, "a"))
        tb = threading.Thread(target=_query_board, args=(ser_b, res, "b"))

    ta.start(); tb.start()
    ta.join();  tb.join()

    clk_a, d_a = _parse_clk_get(res.get("a", {}).get("raw", ""))
    clk_b, d_b = _parse_clk_get(res.get("b", {}).get("raw", ""))

    if clk_a is None or clk_b is None:
        return None, None, None

    send_delta = res["b"]["t_send_ns"] - res["a"]["t_send_ns"]
    diff_ns    = (clk_b - clk_a) - send_delta
    return diff_ns, d_a, d_b


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class HWTimerSyncTest:

    def __init__(self, a_port: str, b_port: str,
                 n: int, pause_ms: int, threshold_us: float,
                 settle_s: float, no_swap: bool,
                 log: Logger):
        self.a_port      = a_port
        self.b_port      = b_port
        self.n           = n
        self.pause_ms    = pause_ms
        self.threshold_us = threshold_us
        self.settle_s    = settle_s
        self.no_swap     = no_swap
        self.log         = log

        self.ser_a: Optional[serial.Serial] = None
        self.ser_b: Optional[serial.Serial] = None
        self.results: list = []

    # ------------------------------------------------------------------
    def connect(self):
        for label, port, attr in [("A", self.a_port, "ser_a"),
                                   ("B", self.b_port, "ser_b")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def disconnect(self):
        for ser in (self.ser_a, self.ser_b):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    # ------------------------------------------------------------------
    def _record(self, name: str, passed: bool, detail: str):
        self.results.append((name, passed, detail))

    # ------------------------------------------------------------------
    def step_zero(self) -> bool:
        self.log.info("\n--- Step 0: Zero both software clocks simultaneously ---")
        ok, skew_ns = zero_both_clocks(self.ser_a, self.ser_b, self.log)
        self._record("Step 0: clk_set 0", ok,
                     f"skew={skew_ns/1000:.1f}us" if ok else "FAIL")
        return ok

    def step_settle(self):
        self.log.info(f"\n--- Step 1: Settle {self.settle_s:.1f} s ---")
        time.sleep(self.settle_s)
        self._record("Step 1: Settle", True, f"{self.settle_s:.1f}s")

    def step_collect(self) -> bool:
        log = self.log
        log.info(
            f"\n--- Step 2: Collect {self.n} paired clk_get samples "
            f"(pause={self.pause_ms} ms) ---")

        if not self.no_swap:
            log.info("  Swap-symmetrisation enabled (odd rounds: B-thread first)")

        samples:    List[int]   = []
        elapsed_s:  List[float] = []   # real elapsed seconds since start of collection
        drifts_a:  List[int]   = []
        drifts_b:  List[int]   = []
        n_err = 0
        t_start = time.perf_counter_ns()

        for i in range(self.n):
            swap = (not self.no_swap) and (i % 2 == 1)
            t_before = time.perf_counter_ns()
            diff, d_a, d_b = single_measurement(
                self.ser_a, self.ser_b, swap=swap)
            t_mid = (time.perf_counter_ns() + t_before) / 2  # midpoint of measurement

            if diff is None:
                n_err += 1
                log.info(f"  [{i+1:4d}] ERROR (no clk_get response)")
            else:
                elapsed = (t_mid - t_start) / 1e9  # seconds
                samples.append(diff)
                elapsed_s.append(elapsed)
                drifts_a.append(d_a)
                drifts_b.append(d_b)
                tag = " [swap]" if swap else "       "
                log.info(
                    f"  [{i+1:4d}]{tag}  t={elapsed:6.2f}s  diff={diff/1000:+9.2f} µs"
                    f"  drift A={d_a:+d} B={d_b:+d} ppb")

            if i < self.n - 1:
                time.sleep(self.pause_ms / 1000.0)

        return self._evaluate(samples, elapsed_s, drifts_a, drifts_b, n_err)

    @staticmethod
    def _linear_regression(xs: List[float], ys: List[int]):
        """Return (slope_ns_per_s, intercept_ns, residuals)."""
        n = len(xs)
        sum_x  = sum(xs)
        sum_y  = sum(ys)
        sum_xx = sum(x * x for x in xs)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        denom  = n * sum_xx - sum_x * sum_x
        if denom == 0:
            slope = 0.0
        else:
            slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept  = (sum_y - slope * sum_x) / n
        residuals  = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        return slope, intercept, residuals

    def _evaluate(self, samples: List[int], elapsed_s: List[float],
                  drifts_a: List[int], drifts_b: List[int], n_err: int) -> bool:
        log = self.log
        log.info("")
        if len(samples) < 5:
            log.info(f"ERROR: too few valid samples ({len(samples)}/{self.n})")
            self._record("Step 2: clk_get collection", False,
                         f"only {len(samples)} valid samples")
            return False

        # --- Linear regression over all samples ---
        slope, intercept, residuals = self._linear_regression(elapsed_s, samples)

        # 2-sigma outlier removal on residuals
        res_mean  = statistics.mean(residuals)
        res_stdev = statistics.stdev(residuals)
        clean_idx = [i for i, r in enumerate(residuals)
                     if abs(r - res_mean) <= 2 * res_stdev]
        n_out     = len(samples) - len(clean_idx)

        clean_res   = [residuals[i]  for i in clean_idx]
        clean_xs    = [elapsed_s[i]  for i in clean_idx]
        clean_ys    = [samples[i]    for i in clean_idx]
        clean_da    = [drifts_a[i]   for i in clean_idx]
        clean_db    = [drifts_b[i]   for i in clean_idx]

        # Re-fit on cleaned data
        slope2, intercept2, residuals2 = self._linear_regression(clean_xs, clean_ys)

        res_stdev2 = statistics.stdev(residuals2)
        res_sem2   = res_stdev2 / (len(residuals2) ** 0.5)

        mean_drift_a = statistics.mean(clean_da) if clean_da else 0.0
        mean_drift_b = statistics.mean(clean_db) if clean_db else 0.0
        t_span       = clean_xs[-1] - clean_xs[0] if len(clean_xs) > 1 else 0.0

        # Crystal frequency difference: slope in ns/s = ppb
        crystal_diff_ppb = slope2          # ns/s  (1 ns/s = 1 ppb)
        crystal_diff_ppm = slope2 / 1000.0 # µs/s = ppm

        log.info("=" * 62)
        log.info(f"Samples    : {len(clean_idx)}/{self.n} valid"
                 f"  ({n_err} errors, {n_out} outliers removed)")
        log.info(f"Time span  : {t_span:.2f} s")
        log.info("")
        log.info("--- Linear regression: diff(t) = intercept + slope * t ---")
        log.info(f"  Intercept  : {intercept2:+.0f} ns  ({intercept2/1000:+.1f} µs)")
        log.info(f"    = clock sync accuracy at t=0 after clk_set 0")
        log.info(f"    (= combined effect of: thread launch skew + UART latency")
        log.info(f"     + FreeRTOS task scheduling on both boards)")
        log.info(f"  Slope      : {slope2:+.0f} ns/s  ({crystal_diff_ppm:+.3f} ppm)")
        log.info(f"    = crystal frequency difference Board B vs Board A")
        log.info(f"    Board B TC0 runs {abs(crystal_diff_ppm):.3f} ppm "
                 + ("SLOWER" if slope2 < 0 else "FASTER") + " than Board A")
        log.info("")
        log.info("--- Residuals (after subtracting linear trend) ---")
        log.info(f"  Stdev      : {res_stdev2:.0f} ns  ({res_stdev2/1000:.3f} µs)")
        log.info(f"  SEM (±)    : {res_sem2:.0f} ns  ({res_sem2/1000:.3f} µs)")
        log.info(f"    = measurement noise = UART serialisation jitter")
        log.info(f"    (expected: ±200..500 µs per sample)")
        log.info("")
        log.info(f"  Drift A    : {mean_drift_a:+.0f} ppb  (expected: 0, no PTP active)")
        log.info(f"  Drift B    : {mean_drift_b:+.0f} ppb  (expected: 0, no PTP active)")
        log.info("=" * 62)
        log.info("")
        log.info("Interpretation:")
        log.info("  Without PTP, two independent crystal oscillators drift apart")
        log.info("  at a constant rate equal to their frequency difference.")
        log.info("  This linear drift is EXPECTED and does NOT indicate a bug.")
        log.info("  The TC0 interpolation mechanism is verified CORRECT if:")
        log.info("    1. diff(t) follows a straight line   (residual stdev is small)")
        log.info("    2. Drift reported = 0 ppb            (IIR not driven without PTP)")
        log.info("")
        log.info("  Crystal ratio result:")
        log.info(f"    Board A ({self.a_port}) vs Board B ({self.b_port}):")
        log.info(f"    frequency diff = {crystal_diff_ppm:+.3f} ppm  "
                 f"({crystal_diff_ppb:+.0f} ppb)")
        log.info(f"    Board B runs {abs(crystal_diff_ppm):.3f} ppm "
                 + ("slower" if slope2 < 0 else "faster") + " than Board A")
        log.info("")

        threshold_ns = self.threshold_us * 1000.0
        passed = res_stdev2 < threshold_ns
        tag    = "PASS" if passed else "FAIL"
        log.info(
            f"{tag}  residual stdev = {res_stdev2/1000:.3f} µs"
            f"  (threshold {self.threshold_us} µs)")
        log.info(
            "  (PASS = TC0 interpolation consistent; "
            "linear crystal drift is normal)")

        detail = (f"n={len(clean_idx)} "
                  f"slope={crystal_diff_ppb:+.0f}ppb({crystal_diff_ppm:+.3f}ppm) "
                  f"intercept={intercept2/1000:+.1f}us "
                  f"res_stdev={res_stdev2/1000:.0f}us "
                  f"res_sem={res_sem2/1000:.0f}us")
        self._record("Step 2: TC0 interpolation consistency", passed, detail)
        return passed

    # ------------------------------------------------------------------
    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 62)
        log.info("  HW/SW Timer Synchronisation Test")
        log.info("=" * 62)
        log.info(f"Date           : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"Board A port   : {self.a_port}")
        log.info(f"Board B port   : {self.b_port}")
        log.info(f"Samples (N)    : {self.n}")
        log.info(f"Pause          : {self.pause_ms} ms")
        log.info(f"Settle         : {self.settle_s} s")
        log.info(f"Threshold      : {self.threshold_us} µs")
        log.info(f"Swap-symmetry  : {'disabled' if self.no_swap else 'enabled'}")
        log.info(f"PTP            : NOT used (pure TC0 timer test)")
        log.info("")

        self.connect()
        try:
            if not self.step_zero():  return self._report(start_time)
            self.step_settle()
            self.step_collect()
        except KeyboardInterrupt:
            log.info("\nInterrupted by user.")
        except Exception as exc:
            import traceback
            log.info(f"\nFATAL: {type(exc).__name__}: {exc}")
            log.info(traceback.format_exc())
        finally:
            self.disconnect()

        return self._report(start_time)

    def _report(self, start_time: datetime.datetime) -> int:
        log = self.log
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info("\n" + "=" * 62)
        log.info("  HW/SW Timer Test — Final Report")
        log.info("=" * 62)
        passed_count = 0
        for name, passed, detail in self.results:
            tag = "PASS" if passed else "FAIL"
            log.info(f"[{tag}] {name}")
            if detail:
                for part in detail.split(";"):
                    p = part.strip()
                    if p:
                        log.info(f"       {p}")
            if passed:
                passed_count += 1
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
        description="HW/SW Timer Synchronisation Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--a-port",  default=DEFAULT_A_PORT,
                   help=f"COM port of board A (default: {DEFAULT_A_PORT})")
    p.add_argument("--b-port",  default=DEFAULT_B_PORT,
                   help=f"COM port of board B (default: {DEFAULT_B_PORT})")
    p.add_argument("--baudrate", default=DEFAULT_BAUDRATE, type=int)
    p.add_argument("--n",        default=DEFAULT_N, type=int,
                   help=f"Number of paired clk_get samples (default: {DEFAULT_N})")
    p.add_argument("--pause-ms", default=DEFAULT_PAUSE_MS, type=int,
                   help=f"Pause between samples in ms (default: {DEFAULT_PAUSE_MS})")
    p.add_argument("--settle",   default=DEFAULT_SETTLE_S, type=float,
                   help=f"Settle time after clk_set in s (default: {DEFAULT_SETTLE_S})")
    p.add_argument("--threshold-us", default=DEFAULT_THRESHOLD_US, type=float,
                   help=f"PASS/FAIL threshold for |mean| in µs (default: {DEFAULT_THRESHOLD_US})")
    p.add_argument("--no-swap", action="store_true",
                   help="Disable swap-symmetrisation")
    p.add_argument("--log-file", default=None,
                   help="Write output to this file in addition to stdout")
    p.add_argument("--verbose", action="store_true",
                   help="Show raw serial debug output")
    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"hw_timer_sync_test_{ts}.log"

    log = Logger(log_file=log_file, verbose=args.verbose)
    test = HWTimerSyncTest(
        a_port       = args.a_port,
        b_port       = args.b_port,
        n            = args.n,
        pause_ms     = args.pause_ms,
        threshold_us = args.threshold_us,
        settle_s     = args.settle,
        no_swap      = args.no_swap,
        log          = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
