#!/usr/bin/env python3
"""PTP Software Clock Synchronisation Test
==========================================

Validates that PTP_CLOCK_GetTime_ns() on two boards produces the same
wallclock value after PTP convergence, by querying the 'ptp_time' CLI
command on both boards near-simultaneously via two parallel threads.

Scenario:
  0. Reset both boards.
  1. Set IP addresses.
  2. Ping connectivity check.
  3. Start PTP: board1 = Grandmaster, board2 = Follower (both SILENT — no
     verbose mode so there is no scroll-through output).
  4. Wait for Follower convergence (PTP FINE state).
  5. Collect N paired 'ptp_time' samples, correct for PC-thread start jitter
     using perf_counter_ns(), apply swap-symmetrisation on odd rounds.
  6. Remove 2-sigma outliers, compute mean / stdev / SEM, compare mean against
     threshold.

Expected (after FINE):
  |mean(WC_fol - WC_gm)| < threshold_us (default 5 µs)

Usage:
    python ptp_time_test.py --gm-port COM8 --fol-port COM10

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

DEFAULT_GM_PORT       = "COM8"
DEFAULT_FOL_PORT      = "COM10"
DEFAULT_GM_IP         = "192.168.0.30"
DEFAULT_FOL_IP        = "192.168.0.20"
DEFAULT_NETMASK       = "255.255.255.0"
DEFAULT_BAUDRATE      = 115200
DEFAULT_CMD_TIMEOUT   = 5.0
DEFAULT_CONV_TIMEOUT  = 60.0
DEFAULT_N             = 50
DEFAULT_PAUSE_MS      = 150
DEFAULT_THRESHOLD_US  = 5.0

RE_IP_SET      = re.compile(r"Set ip address OK|IP address set to")
RE_BUILD       = re.compile(r"\[APP\] Build:\s+(.+)")
RE_PING_REPLY  = re.compile(r"Ping:.*reply.*from|Reply from", re.IGNORECASE)
RE_PING_DONE   = re.compile(r"Ping: done\.")
RE_FOL_START   = re.compile(r"PTP Follower enabled")
RE_GM_START    = re.compile(r"PTP Grandmaster enabled")
RE_MATCHFREQ   = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC   = re.compile(r"Hard sync completed")
RE_COARSE      = re.compile(r"PTP COARSE")
RE_FINE        = re.compile(r"PTP FINE")
RE_DISABLED    = re.compile(r"PTP disabled")
RE_PTP_TIME    = re.compile(
    r"ptp_time:\s+(\d+):(\d+):(\d+)\.(\d{9})\s+drift=([+-]?\d+)ppb"
)

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
    parts = []
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
# Paired ptp_time query
# ---------------------------------------------------------------------------

def _parse_ptp_time(raw: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (wallclock_ns, drift_ppb) parsed from 'ptp_time' CLI output."""
    m = RE_PTP_TIME.search(raw)
    if not m:
        return None, None
    h   = int(m.group(1))
    mn  = int(m.group(2))
    s   = int(m.group(3))
    ns9 = int(m.group(4))
    drift = int(m.group(5))
    wc_ns = (h * 3600 + mn * 60 + s) * 1_000_000_000 + ns9
    return wc_ns, drift


def _query_board(ser: serial.Serial, result_dict: dict, key: str,
                 timeout_s: float = 1.0):
    """Send 'ptp_time', capture t_send_ns, store {raw, t_send_ns}."""
    ser.reset_input_buffer()
    t_send = time.perf_counter_ns()
    ser.write(b"ptp_time\r\n")

    resp     = b""
    deadline = time.perf_counter_ns() + int(timeout_s * 1e9)
    while time.perf_counter_ns() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        resp += chunk
        idx = resp.find(b"ptp_time:")
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
    One paired measurement.
    swap=True: FOL thread is started first (eliminates systematic ordering bias).

    Returns (diff_ns, drift_gm_ppb, drift_fol_ppb) or (None, None, None).
    diff_ns = (WC_fol - WC_gm) - (t_send_fol - t_send_gm)
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

    wc_gm,  d_gm  = _parse_ptp_time(res.get("gm",  {}).get("raw", ""))
    wc_fol, d_fol = _parse_ptp_time(res.get("fol", {}).get("raw", ""))

    if wc_gm is None or wc_fol is None:
        return None, None, None

    send_delta = res["fol"]["t_send_ns"] - res["gm"]["t_send_ns"]
    diff_ns    = (wc_fol - wc_gm) - send_delta
    return diff_ns, d_gm, d_fol


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class PTPTimeTest:

    def __init__(self, gm_port: str, fol_port: str,
                 gm_ip: str, fol_ip: str, netmask: str,
                 n: int, pause_ms: int, threshold_us: float,
                 conv_timeout: float, no_swap: bool,
                 log: Logger):
        self.gm_port      = gm_port
        self.fol_port     = fol_port
        self.gm_ip        = gm_ip
        self.fol_ip       = fol_ip
        self.netmask      = netmask
        self.n            = n
        self.pause_ms     = pause_ms
        self.threshold_us = threshold_us
        self.conv_timeout = conv_timeout
        self.no_swap      = no_swap
        self.log          = log

        self.gm_ser:  Optional[serial.Serial] = None
        self.fol_ser: Optional[serial.Serial] = None
        self.results: list = []
        self.gm_build: str = "unknown"
        self.fol_build: str = "unknown"

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple] = None

    # ------------------------------------------------------------------
    def connect(self):
        for label, port, attr in [("GM",  self.gm_port,  "gm_ser"),
                                   ("FOL", self.fol_port, "fol_ser")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def disconnect(self):
        for ser in (self.gm_ser, self.fol_ser):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    # ------------------------------------------------------------------
    def _start_conv(self, fol_ser: serial.Serial):
        self._conv_result = None
        self._conv_thread = threading.Thread(
            target=self._conv_worker, args=(fol_ser,), daemon=True)
        self._conv_thread.start()

    def _conv_worker(self, fol_ser: serial.Serial):
        try:
            self._conv_result = wait_for_pattern(
                fol_ser, RE_FINE, self.conv_timeout, self.log,
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
    def _record(self, name: str, passed: bool, detail: str):
        self.results.append((name, passed, detail))

    # ------------------------------------------------------------------
    def step_reset(self):
        self.log.info("\n--- Step 0: Reset ---")
        boot_buf: dict = {}
        for label, ser, key in [("GM",  self.gm_ser,  "gm"),
                                 ("FOL", self.fol_ser, "fol")]:
            self.log.info(f"  [{label}] reset")
            boot_buf[key] = send_command(ser, "reset", self.conv_timeout, self.log)
        self.log.info("  Waiting 8 s for boot...")
        time.sleep(8)
        # Drain any remaining boot output and extract firmware build timestamp
        for label, ser, key, attr in [("GM",  self.gm_ser,  "gm", "gm_build"),
                                      ("FOL", self.fol_ser, "fol", "fol_build")]:
            extra = b""
            while ser.in_waiting:
                extra += ser.read(ser.in_waiting)
                time.sleep(0.05)
            combined = boot_buf[key] + extra.decode("ascii", errors="replace")
            m = RE_BUILD.search(combined)
            build_ts = m.group(1).strip() if m else "unknown"
            setattr(self, attr, build_ts)
            self.log.info(f"  [{label}] firmware build: {build_ts}")
        self._record("Step 0: Reset", True,
                     f"GM build: {self.gm_build}  FOL build: {self.fol_build}")

    def step_ip(self) -> bool:
        self.log.info("\n--- Step 1: IP Configuration ---")
        passed = True
        for label, ser, ip in [("GM",  self.gm_ser,  self.gm_ip),
                                 ("FOL", self.fol_ser, self.fol_ip)]:
            resp = send_command(ser, f"setip eth0 {ip} {self.netmask}",
                                self.conv_timeout, self.log)
            ok = bool(RE_IP_SET.search(resp))
            self.log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")
            if not ok: passed = False
        self._record("Step 1: IP Configuration", passed, "")
        return passed

    def step_ping(self) -> bool:
        self.log.info("\n--- Step 2: Ping Connectivity ---")
        passed = True
        for src_label, src_ser, dst_ip in [
            ("GM  -> FOL", self.gm_ser,  self.fol_ip),
            ("FOL -> GM",  self.fol_ser, self.gm_ip),
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

    def step_start_ptp(self) -> bool:
        """Start PTP in SILENT mode (no 'v' suffix — no scroll-through output)."""
        self.log.info("\n--- Step 3: Start PTP (silent mode) ---")
        passed = True

        # FOL first
        self.log.info("  [FOL] ptp_mode follower  (silent)")
        resp = send_command(self.fol_ser, "ptp_mode follower",
                            self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [FOL] confirmed")
        else:
            self.log.info(f"  [FOL] no confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.fol_ser.reset_input_buffer()
        self._start_conv(self.fol_ser)

        # GM second
        self.log.info("  [GM ] ptp_mode master  (silent)")
        self.gm_ser.reset_input_buffer()
        self.gm_ser.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.gm_ser, RE_GM_START,
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

        self._record("Step 3: Start PTP (FINE convergence)", passed,
                     f"FINE@{elapsed:.1f}s {ms_str}" if matched else "FINE NOT reached")
        return passed

    def step_collect(self) -> bool:
        """Collect N paired ptp_time samples."""
        self.log.info(
            f"\n--- Step 4: Collect {self.n} paired ptp_time samples "
            f"(pause={self.pause_ms} ms) ---")

        if not self.no_swap:
            self.log.info(
                "  Swap-symmetrisation enabled (odd rounds: FOL-thread first)")

        samples:   List[int] = []
        drifts_gm: List[int] = []
        drifts_fol:List[int] = []
        n_err = 0

        for i in range(self.n):
            swap = (not self.no_swap) and (i % 2 == 1)
            diff, d_gm, d_fol = single_measurement(
                self.gm_ser, self.fol_ser, swap=swap)

            if diff is None:
                n_err += 1
                self.log.info(f"  [{i+1:3d}] ERROR (no ptp_time response)")
            else:
                samples.append(diff)
                drifts_gm.append(d_gm)
                drifts_fol.append(d_fol)
                tag = " [swap]" if swap else "       "
                self.log.info(
                    f"  [{i+1:3d}]{tag}  diff={diff/1000:+9.2f} µs"
                    f"  drift GM={d_gm:+d} FOL={d_fol:+d} ppb")

            if i < self.n - 1:
                time.sleep(self.pause_ms / 1000.0)

        passed = self._evaluate(samples, drifts_gm, drifts_fol, n_err)
        return passed

    def _evaluate(self, samples: List[int], drifts_gm: List[int],
                  drifts_fol: List[int], n_err: int) -> bool:
        self.log.info("")
        if len(samples) < 3:
            self.log.info(
                f"ERROR: too few valid samples ({len(samples)}/{self.n})")
            self._record("Step 4: ptp_time sample collection",
                         False, f"only {len(samples)} valid samples")
            return False

        # 2-sigma outlier removal
        mean0  = statistics.mean(samples)
        stdev0 = statistics.stdev(samples)
        clean  = [x for x in samples if abs(x - mean0) <= 2 * stdev0]
        n_out  = len(samples) - len(clean)

        mean_ns  = statistics.mean(clean)
        stdev_ns = statistics.stdev(clean)
        sem_ns   = stdev_ns / (len(clean) ** 0.5)

        mean_drift_gm  = statistics.mean(drifts_gm)  if drifts_gm  else 0.0
        mean_drift_fol = statistics.mean(drifts_fol) if drifts_fol else 0.0

        self.log.info("=" * 62)
        self.log.info(f"Samples    : {len(clean)}/{self.n} valid"
                      f"  ({n_err} errors, {n_out} outliers removed)")
        self.log.info(f"Mean diff  : {mean_ns:+.0f} ns  ({mean_ns/1000:+.3f} µs)")
        self.log.info(f"Stdev      : {stdev_ns:.0f} ns  ({stdev_ns/1000:.3f} µs)")
        self.log.info(f"SEM (±)    : {sem_ns:.0f} ns  ({sem_ns/1000:.3f} µs)")
        self.log.info(f"Drift GM   : {mean_drift_gm:+.0f} ppb")
        self.log.info(f"Drift FOL  : {mean_drift_fol:+.0f} ppb")
        self.log.info("=" * 62)
        self.log.info(
            "Note: high Stdev is expected (UART task-jitter ±500 µs).\n"
            "      The Mean is the relevant quality indicator.")

        threshold_ns = self.threshold_us * 1000.0
        passed = abs(mean_ns) < threshold_ns
        tag    = "PASS" if passed else "FAIL"
        self.log.info(
            f"{tag}  |mean| = {abs(mean_ns)/1000:.3f} µs"
            f"  (threshold {self.threshold_us} µs)")

        detail = (f"n={len(clean)} mean={mean_ns:+.0f}ns "
                  f"stdev={stdev_ns:.0f}ns sem={sem_ns:.0f}ns "
                  f"drift_gm={mean_drift_gm:+.0f}ppb "
                  f"drift_fol={mean_drift_fol:+.0f}ppb")
        self._record("Step 4: ptp_time synchronisation", passed, detail)
        return passed

    # ------------------------------------------------------------------
    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 62)
        log.info("  PTP Software Clock Synchronisation Test")
        log.info("=" * 62)
        log.info(f"Date           : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"GM  port       : {self.gm_port}  ({self.gm_ip})")
        log.info(f"FOL port       : {self.fol_port}  ({self.fol_ip})")
        log.info(f"Samples (N)    : {self.n}")
        log.info(f"Pause          : {self.pause_ms} ms")
        log.info(f"Threshold      : {self.threshold_us} µs")
        log.info(f"Conv. timeout  : {self.conv_timeout} s")
        log.info(f"Swap-symmetry  : {'disabled' if self.no_swap else 'enabled'}")
        log.info(f"PTP mode       : silent (no verbose output on boards)")
        log.info("")

        self.connect()
        try:
            self.step_reset()
            log.info(f"GM  firmware   : {self.gm_build}")
            log.info(f"FOL firmware   : {self.fol_build}")
            if not self.step_ip():        return self._report(start_time)
            if not self.step_ping():      return self._report(start_time)
            if not self.step_start_ptp(): return self._report(start_time)
            self.step_collect()
        except KeyboardInterrupt:
            log.info("\nInterrupted by user.")
        except Exception as exc:
            import traceback
            log.info(f"\nFATAL: {type(exc).__name__}: {exc}")
            log.info(traceback.format_exc())
        finally:
            self._cleanup()

        return self._report(start_time)

    def _cleanup(self):
        self.log.info("\n--- Cleanup ---")
        for label, ser in [("GM",  self.gm_ser),
                            ("FOL", self.fol_ser)]:
            if ser and ser.is_open:
                send_command(ser, "ptp_mode off", DEFAULT_CMD_TIMEOUT, self.log)
                self.log.info(f"  [{label}] PTP stopped")
        self.disconnect()

    def _report(self, start_time: datetime.datetime) -> int:
        log = self.log
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info("\n" + "=" * 62)
        log.info("  PTP Software Clock Test — Final Report")
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
        description="PTP Software Clock Synchronisation Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--gm-port",    default=DEFAULT_GM_PORT,
                   help=f"COM port of Grandmaster board (default: {DEFAULT_GM_PORT})")
    p.add_argument("--fol-port",   default=DEFAULT_FOL_PORT,
                   help=f"COM port of Follower board   (default: {DEFAULT_FOL_PORT})")
    p.add_argument("--gm-ip",      default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",     default=DEFAULT_FOL_IP)
    p.add_argument("--baudrate",   default=DEFAULT_BAUDRATE, type=int)
    p.add_argument("--n",          default=DEFAULT_N,          type=int,
                   help=f"Number of paired ptp_time samples (default: {DEFAULT_N})")
    p.add_argument("--pause-ms",   default=DEFAULT_PAUSE_MS,   type=int,
                   help=f"Pause between samples in ms (default: {DEFAULT_PAUSE_MS})")
    p.add_argument("--threshold-us", default=DEFAULT_THRESHOLD_US, type=float,
                   help=f"PASS/FAIL threshold for |mean| in µs (default: {DEFAULT_THRESHOLD_US})")
    p.add_argument("--convergence-timeout", default=DEFAULT_CONV_TIMEOUT, type=float,
                   help=f"Max wait for PTP FINE in s (default: {DEFAULT_CONV_TIMEOUT})")
    p.add_argument("--no-swap", action="store_true",
                   help="Disable swap-symmetrisation (odd rounds send FOL first)")
    p.add_argument("--log-file", default=None,
                   help="Write output to this file in addition to stdout")
    p.add_argument("--verbose", action="store_true",
                   help="Show raw serial debug output")
    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"ptp_time_test_{ts}.log"

    log = Logger(log_file=log_file, verbose=args.verbose)
    test = PTPTimeTest(
        gm_port      = args.gm_port,
        fol_port     = args.fol_port,
        gm_ip        = args.gm_ip,
        fol_ip       = args.fol_ip,
        netmask      = DEFAULT_NETMASK,
        n            = args.n,
        pause_ms     = args.pause_ms,
        threshold_us = args.threshold_us,
        conv_timeout = args.convergence_timeout,
        no_swap      = args.no_swap,
        log          = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
