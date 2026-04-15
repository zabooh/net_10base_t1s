#!/usr/bin/env python3
"""PTP Delay Request/Response Protocol Verification Test
=========================================================

Verifies that the full IEEE 1588 Delay Request/Response exchange is executed
correctly between a Grandmaster (GM) and a Follower (FOL) over the T1S link.

Test sequence:
  1. Reset & IP configuration
  2. Network connectivity (ping)
  3. Start PTP (follower first, then grandmaster)
  4. Wait for FOL to reach FINE convergence state
  5. Enable PTP protocol trace on both nodes (ptp_trace on)
  6. Collect [TRACE] messages for --trace-time seconds
  7. Disable trace (ptp_trace off)
  8. Validate:
       a) FOL sent at least one Delay_Req          → [TRACE] DELAY_REQ_SENT
       b) GM  received at least one Delay_Req      → [TRACE] GM_DELAY_REQ_RECEIVED
       c) GM  sent at least one Delay_Resp         → [TRACE] GM_DELAY_RESP_SENT
       d) FOL received matching Delay_Resp         → [TRACE] DELAY_RESP_RECEIVED
       e) FOL computed a non-zero path delay       → [TRACE] DELAY_CALC delay=<N>
       f) No TX-busy skips on GM                  → [TRACE] GM_DELAY_RESP_SKIPPED_TX_BUSY absent
          (or at most --max-tx-busy-skips skips)
       g) Computed delay is within plausible range → 0 < delay < --max-delay-ns
  9. Report PASS / FAIL with per-assertion breakdown.
  10. Write timestamped log file  ptp_delay_YYYYMMDD_HHMMSS.log

Usage:
    python ptp_delay_test.py --gm-port COM10 --fol-port COM8

Requirements:
    pip install pyserial
"""

import argparse
import datetime
import faulthandler
import re
import sys
import threading
import time
from typing import List, Optional, Tuple

faulthandler.enable()

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_GM_PORT             = "COM10"
DEFAULT_FOL_PORT            = "COM8"
DEFAULT_GM_IP               = "192.168.0.20"
DEFAULT_FOL_IP              = "192.168.0.30"
DEFAULT_NETMASK             = "255.255.255.0"
DEFAULT_BAUDRATE            = 115200
DEFAULT_CMD_TIMEOUT         = 5.0
DEFAULT_CONVERGENCE_TIMEOUT = 30.0
DEFAULT_TRACE_TIME          = 5.0   # seconds to collect trace output
DEFAULT_MAX_DELAY_NS        = 10_000_000  # 10 ms — includes T1S PLCA beacon overhead
DEFAULT_MAX_TX_BUSY_SKIPS   = 0     # allow zero GM TX-busy skips by default

# Regex patterns shared with ptp_onoff_test.py
RE_IP_SET       = re.compile(r"Set ip address OK|IP address set to")
RE_PING_REPLY   = re.compile(r"Ping:.*reply.*from|Reply from")
RE_PING_DONE    = re.compile(r"Ping: done\.")
RE_FOL_START    = re.compile(r"PTP Follower enabled")
RE_GM_START     = re.compile(r"PTP Grandmaster enabled")
RE_MATCHFREQ    = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC    = re.compile(r"Hard sync completed")
RE_COARSE       = re.compile(r"PTP COARSE")
RE_FINE         = re.compile(r"PTP FINE")
RE_TRACE_ENABLED  = re.compile(r"PTP trace enabled")
RE_TRACE_DISABLED = re.compile(r"PTP trace disabled")

# [TRACE] patterns — matched against collected trace output
# DELAY_REQ_SENT: t3_sw= is the SW-clock fallback (firmware >= HW-t3 change)
RE_TRACE_DELAY_REQ_SENT     = re.compile(r"\[TRACE\] DELAY_REQ_SENT\s+seq=(\d+)\s+t3_sw=(-?\d+)")
RE_TRACE_GM_REQ_RECEIVED    = re.compile(r"\[TRACE\] GM_DELAY_REQ_RECEIVED\s+seq=(\d+)")
RE_TRACE_GM_RESP_SENT       = re.compile(r"\[TRACE\] GM_DELAY_RESP_SENT\s+seq=(\d+)")
RE_TRACE_GM_RESP_SKIPPED    = re.compile(r"\[TRACE\] GM_DELAY_RESP_SKIPPED_TX_BUSY\s+seq=(\d+)")
RE_TRACE_GM_RESP_FAILED     = re.compile(r"\[TRACE\] GM_DELAY_RESP_SEND_FAILED\s+seq=(\d+)")
RE_TRACE_RESP_RECEIVED      = re.compile(r"\[TRACE\] DELAY_RESP_RECEIVED(?:\s+seq=(\d+))?")
RE_TRACE_RESP_UNSOLICITED   = re.compile(r"\[TRACE\] DELAY_RESP_UNSOLICITED(?:\s+seq=(\d+))?")
RE_TRACE_RESP_WRONG_CLOCK   = re.compile(r"\[TRACE\] DELAY_RESP_WRONG_CLOCK(?:\s+seq=(\d+))?")
# DELAY_CALC: t3=<ns>(hw=<0|1>) — hw=1 means LAN865x HW timestamp used
RE_TRACE_DELAY_CALC         = re.compile(
    r"\[TRACE\] DELAY_CALC\s+"
    r"t1=(-?\d+)\s+t2=(-?\d+)\s+t3=(-?\d+)\(hw=(\d+)\)\s+t4=(-?\d+)\s+"
    r"fwd=(-?\d+)\s+bwd=(-?\d+)\s+delay=(-?\d+)"
)
# Groups: [0]=t1 [1]=t2 [2]=t3 [3]=hw_flag [4]=t4 [5]=fwd [6]=bwd [7]=delay


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    """Dual-writes to stdout and an optional log file.  Thread-safe."""

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

    def info(self, msg: str):
        self._write(msg)

    def debug(self, msg: str):
        if self.verbose:
            self._write(f"  [DBG] {msg}")

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
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
    )


def send_command(
    ser: serial.Serial,
    cmd: str,
    timeout: float = DEFAULT_CMD_TIMEOUT,
    log: Logger = None,
) -> str:
    """Send CLI command; collect response via 0.5 s quiet-period detection."""
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("ascii"))
    if log:
        log.debug(f"  >> {cmd}")

    parts     = []
    deadline  = time.monotonic() + timeout
    last_data = time.monotonic()

    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            decoded = chunk.decode("ascii", errors="replace")
            parts.append(decoded)
            last_data = time.monotonic()
            if log:
                log.debug(decoded.rstrip())
        else:
            if parts and (time.monotonic() - last_data) > 0.5:
                break
            time.sleep(0.05)

    return "".join(parts)


def wait_for_pattern(
    ser: serial.Serial,
    pattern: re.Pattern,
    timeout: float,
    log: Logger = None,
    extra_patterns: dict = None,
    live_log: bool = False,
) -> Tuple[bool, float, dict]:
    """Read from *ser* until *pattern* matches or *timeout* expires."""
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
                        if live_log:
                            log.info(f"    {line.rstrip()}")
                        else:
                            log.debug(f"  <- {line.rstrip()}")

            for label, pat in extra_patterns.items():
                if label not in milestones and pat.search(buffer):
                    milestones[label] = time.monotonic() - start

            if pattern.search(buffer):
                return True, time.monotonic() - start, milestones
        else:
            time.sleep(0.05)

    return False, time.monotonic() - start, milestones


def collect_for_duration(
    ser: serial.Serial,
    duration: float,
    log: Logger = None,
    label: str = "",
) -> str:
    """Read everything from *ser* for *duration* seconds, return accumulated text."""
    buf      = ""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            decoded = chunk.decode("ascii", errors="replace")
            buf += decoded
            if log:
                for line in decoded.splitlines():
                    if line.strip():
                        log.info(f"    [{label}] {line.rstrip()}")
        else:
            time.sleep(0.02)
    return buf


# ---------------------------------------------------------------------------
# Test agent
# ---------------------------------------------------------------------------

class PTPDelayTestAgent:
    """Verifies the Delay Request/Response exchange of the PTP protocol."""

    def __init__(
        self,
        gm_port: str,
        fol_port: str,
        gm_ip: str                = DEFAULT_GM_IP,
        fol_ip: str               = DEFAULT_FOL_IP,
        netmask: str              = DEFAULT_NETMASK,
        convergence_timeout: float = DEFAULT_CONVERGENCE_TIMEOUT,
        cmd_timeout: float        = DEFAULT_CMD_TIMEOUT,
        trace_time: float         = DEFAULT_TRACE_TIME,
        max_delay_ns: int         = DEFAULT_MAX_DELAY_NS,
        max_tx_busy_skips: int    = DEFAULT_MAX_TX_BUSY_SKIPS,
        log: Logger               = None,
    ):
        self.gm_port_name        = gm_port
        self.fol_port_name       = fol_port
        self.gm_ip               = gm_ip
        self.fol_ip              = fol_ip
        self.netmask             = netmask
        self.convergence_timeout = convergence_timeout
        self.cmd_timeout         = cmd_timeout
        self.trace_time          = trace_time
        self.max_delay_ns        = max_delay_ns
        self.max_tx_busy_skips   = max_tx_busy_skips
        self.log                 = log or Logger()

        self.gm_ser:  Optional[serial.Serial] = None
        self.fol_ser: Optional[serial.Serial] = None

        self._step_results: list = []

        # Convergence thread state
        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple]            = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        self.log.info(f"Connecting to GM  ({self.gm_port_name})...")
        try:
            self.gm_ser = open_port(self.gm_port_name)
            self.log.info(f"  GM  port open: {self.gm_port_name}")
        except serial.SerialException as exc:
            self.log.info(f"ERROR: Cannot open GM port {self.gm_port_name}: {exc}")
            sys.exit(1)

        self.log.info(f"Connecting to FOL ({self.fol_port_name})...")
        try:
            self.fol_ser = open_port(self.fol_port_name)
            self.log.info(f"  FOL port open: {self.fol_port_name}")
        except serial.SerialException as exc:
            self.log.info(f"ERROR: Cannot open FOL port {self.fol_port_name}: {exc}")
            if self.gm_ser:
                self.gm_ser.close()
            sys.exit(1)

    def disconnect(self):
        for ser in (self.gm_ser, self.fol_ser):
            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass

    def _record(self, name: str, passed: bool, detail: str = ""):
        self._step_results.append((name, passed, detail))
        status = "PASS" if passed else "FAIL"
        self.log.info(f"  >> {status}: {name}" + (f"  ({detail})" if detail else ""))

    # ------------------------------------------------------------------
    # Step 0 — Reset
    # ------------------------------------------------------------------

    def step_0_reset(self):
        step = "Step 0: Reset"
        self.log.info(f"\n--- {step} ---")
        parts = []
        for label, ser in [("GM ", self.gm_ser), ("FOL", self.fol_ser)]:
            self.log.info(f"  [{label}] reset")
            try:
                send_command(ser, "reset", self.cmd_timeout, self.log)
                parts.append(f"{label} reset sent")
            except Exception as exc:
                parts.append(f"{label} reset WARNING ({exc})")
                self.log.info(f"  [{label}] WARNING: reset failed (continuing): {exc}")
        self.log.info("  Waiting 8 s after reset...")
        time.sleep(8)
        self._record(step, True, "; ".join(parts))

    # ------------------------------------------------------------------
    # Step 1 — IP Configuration
    # ------------------------------------------------------------------

    def step_1_ip_config(self) -> bool:
        step = "Step 1: IP Configuration"
        self.log.info(f"\n--- {step} ---")
        passed = True
        parts  = []
        for label, ser, ip in [
            ("GM ", self.gm_ser, self.gm_ip),
            ("FOL", self.fol_ser, self.fol_ip),
        ]:
            cmd = f"setip eth0 {ip} {self.netmask}"
            self.log.info(f"  [{label}] {cmd}")
            resp = send_command(ser, cmd, self.cmd_timeout, self.log)
            if RE_IP_SET.search(resp):
                parts.append(f"{label} IP ok ({ip})")
            else:
                parts.append(f"{label} IP FAIL ({ip})")
                self.log.info(f"  [{label}] Unexpected response: {resp.strip()!r}")
                passed = False
        self._record(step, passed, "; ".join(parts))
        return passed

    # ------------------------------------------------------------------
    # Step 2 — Connectivity
    # ------------------------------------------------------------------

    def step_2_connectivity(self) -> bool:
        step = "Step 2: Network Connectivity"
        self.log.info(f"\n--- {step} ---")
        passed = True
        parts  = []
        for src_label, src_ser, dst_ip in [
            ("GM ->FOL", self.gm_ser, self.fol_ip),
            ("FOL->GM ", self.fol_ser, self.gm_ip),
        ]:
            cmd = f"ping {dst_ip}"
            self.log.info(f"  [{src_label}] {cmd}")
            src_ser.reset_input_buffer()
            src_ser.write((cmd + "\r\n").encode("ascii"))
            matched, elapsed, milestones = wait_for_pattern(
                src_ser, RE_PING_DONE, timeout=15.0, log=self.log,
                extra_patterns={"first_reply": RE_PING_REPLY}, live_log=True,
            )
            if matched:
                parts.append(f"{src_label} ok")
            elif milestones.get("first_reply") is not None:
                parts.append(f"{src_label} ok (partial)")
            else:
                parts.append(f"{src_label} FAIL")
                passed = False
        self._record(step, passed, "; ".join(parts))
        return passed

    # ------------------------------------------------------------------
    # Step 3 — PTP Start + Convergence
    # ------------------------------------------------------------------

    def step_3_start_ptp(self) -> bool:
        step = "Step 3: PTP Start + Convergence to FINE"
        self.log.info(f"\n--- {step} ---")
        passed = True
        parts  = []

        # Follower first (must complete before convergence thread starts
        # to avoid concurrent serial access on Windows)
        self.log.info("  [FOL] ptp_mode follower")
        resp = send_command(self.fol_ser, "ptp_mode follower", self.cmd_timeout, self.log)
        if RE_FOL_START.search(resp):
            parts.append("FOL start ok")
        else:
            parts.append("FOL start FAIL")
            self.log.info(f"  [FOL] Unexpected response: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)

        # Start convergence monitor — sole reader of fol_ser from here
        self.fol_ser.reset_input_buffer()
        self._start_convergence_thread()

        # Grandmaster second
        self.log.info("  [GM ] ptp_mode master")
        self.gm_ser.reset_input_buffer()
        self.gm_ser.write(b"ptp_mode master\r\n")
        gm_matched, _, _ = wait_for_pattern(
            self.gm_ser, RE_GM_START, timeout=self.cmd_timeout, log=self.log
        )
        if gm_matched:
            parts.append("GM start ok")
        else:
            parts.append("GM start FAIL")
            passed = False

        self.log.info(
            f"  Waiting for FOL FINE state (timeout={self.convergence_timeout}s)..."
        )
        matched, elapsed, milestones = self._collect_convergence_result()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if ms_str:
            self.log.info(f"  Milestones: {ms_str}")

        if matched:
            self.log.info(f"  FINE reached in {elapsed:.1f}s")
            parts.append(f"FINE@{elapsed:.1f}s")
        else:
            self.log.info(f"  FINE NOT reached within {self.convergence_timeout}s")
            parts.append(f"FINE NOT reached (milestones: {ms_str or 'none'})")
            passed = False

        self._record(step, passed, "; ".join(parts))
        return passed

    # ------------------------------------------------------------------
    # Convergence thread helpers
    # ------------------------------------------------------------------

    def _start_convergence_thread(self):
        self._conv_result = None
        self._conv_thread = threading.Thread(
            target=self._convergence_worker, daemon=True
        )
        self._conv_thread.start()

    def _convergence_worker(self):
        try:
            self._conv_result = wait_for_pattern(
                self.fol_ser,
                RE_FINE,
                timeout=self.convergence_timeout,
                log=self.log,
                extra_patterns={
                    "MATCHFREQ": RE_MATCHFREQ,
                    "HARD_SYNC": RE_HARD_SYNC,
                    "COARSE":    RE_COARSE,
                },
                live_log=True,
            )
        except Exception as exc:
            import traceback
            msg = (f"  [CONV-THREAD ERROR] {type(exc).__name__}: {exc}\n"
                   f"{traceback.format_exc()}")
            self.log.info(msg)
            self._conv_result = (False, self.convergence_timeout, {})

    def _collect_convergence_result(self) -> Tuple[bool, float, dict]:
        if self._conv_thread is not None:
            self._conv_thread.join(timeout=self.convergence_timeout + 2.0)
        if self._conv_result is not None:
            return self._conv_result
        return False, self.convergence_timeout, {}

    # ------------------------------------------------------------------
    # Step 4 — Parallel trace collection
    # ------------------------------------------------------------------

    def step_4_collect_trace(self) -> Tuple[str, str]:
        """Enable trace on both nodes, collect output for trace_time seconds.

        Returns:
            (fol_trace, gm_trace) — raw accumulated text from each node.
        """
        step = "Step 4: Enable PTP Trace"
        self.log.info(f"\n--- {step} ---")

        # Drive both nodes to enable trace.  The FOL serial port is currently
        # idle (convergence thread has finished); it is safe to use directly.
        for label, ser in [("GM ", self.gm_ser), ("FOL", self.fol_ser)]:
            self.log.info(f"  [{label}] ptp_trace on")
            resp = send_command(ser, "ptp_trace on", self.cmd_timeout, self.log)
            if RE_TRACE_ENABLED.search(resp):
                self.log.info(f"  [{label}] trace enabled confirmed")
            else:
                self.log.info(f"  [{label}] WARNING: trace enable not confirmed ({resp.strip()!r})")

        self._record(step, True, "trace on sent to GM and FOL")

        # Collect in parallel using threads so neither port starves
        self.log.info(
            f"\n--- Step 4b: Collecting trace output for {self.trace_time:.0f}s ---"
        )
        fol_buf: List[str] = [""]
        gm_buf:  List[str] = [""]

        def collect_fol():
            fol_buf[0] = collect_for_duration(
                self.fol_ser, self.trace_time, self.log, "FOL"
            )

        def collect_gm():
            gm_buf[0] = collect_for_duration(
                self.gm_ser, self.trace_time, self.log, "GM "
            )

        t_fol = threading.Thread(target=collect_fol, daemon=True)
        t_gm  = threading.Thread(target=collect_gm,  daemon=True)
        t_fol.start()
        t_gm.start()
        t_fol.join(timeout=self.trace_time + 5.0)
        t_gm.join(timeout=self.trace_time + 5.0)

        # Disable trace
        self.log.info("\n--- Step 4c: Disable PTP Trace ---")
        for label, ser in [("GM ", self.gm_ser), ("FOL", self.fol_ser)]:
            self.log.info(f"  [{label}] ptp_trace off")
            resp = send_command(ser, "ptp_trace off", self.cmd_timeout, self.log)
            if RE_TRACE_DISABLED.search(resp):
                self.log.info(f"  [{label}] trace disabled confirmed")
            else:
                self.log.info(f"  [{label}] WARNING: trace disable not confirmed")

        return fol_buf[0], gm_buf[0]

    # ------------------------------------------------------------------
    # Step 5 — Validate trace output
    # ------------------------------------------------------------------

    def step_5_validate_trace(
        self, fol_trace: str, gm_trace: str
    ) -> bool:
        """Run all assertions against collected trace text.

        Returns True if all assertions pass.
        """
        step = "Step 5: Validate Trace Assertions"
        self.log.info(f"\n--- {step} ---")
        all_ok = True

        # ---- Assertion A: FOL sent at least one Delay_Req ----------------
        req_sent_matches = RE_TRACE_DELAY_REQ_SENT.findall(fol_trace)
        a_ok = len(req_sent_matches) > 0
        self._record(
            "A: FOL DELAY_REQ_SENT",
            a_ok,
            f"count={len(req_sent_matches)}" if a_ok else "no DELAY_REQ_SENT trace found",
        )
        all_ok = all_ok and a_ok

        # ---- Assertion B: GM received at least one Delay_Req -------------
        gm_req_matches = RE_TRACE_GM_REQ_RECEIVED.findall(gm_trace)
        b_ok = len(gm_req_matches) > 0
        self._record(
            "B: GM GM_DELAY_REQ_RECEIVED",
            b_ok,
            f"count={len(gm_req_matches)}" if b_ok else "no GM_DELAY_REQ_RECEIVED trace found",
        )
        all_ok = all_ok and b_ok

        # ---- Assertion C: GM sent at least one Delay_Resp ----------------
        gm_resp_sent = RE_TRACE_GM_RESP_SENT.findall(gm_trace)
        c_ok = len(gm_resp_sent) > 0
        self._record(
            "C: GM GM_DELAY_RESP_SENT",
            c_ok,
            f"count={len(gm_resp_sent)}" if c_ok else "no GM_DELAY_RESP_SENT trace found",
        )
        all_ok = all_ok and c_ok

        # ---- Assertion D: FOL received matching Delay_Resp ---------------
        resp_recv = RE_TRACE_RESP_RECEIVED.findall(fol_trace)
        resp_unsol = RE_TRACE_RESP_UNSOLICITED.findall(fol_trace)
        resp_wrong = RE_TRACE_RESP_WRONG_CLOCK.findall(fol_trace)
        d_ok = len(resp_recv) > 0
        d_detail = f"received={len(resp_recv)}"
        if resp_unsol:
            d_detail += f" unsolicited={len(resp_unsol)}"
        if resp_wrong:
            d_detail += f" wrong_clock={len(resp_wrong)}"
        if not d_ok:
            d_detail = "no DELAY_RESP_RECEIVED trace found; " + d_detail
        self._record("D: FOL DELAY_RESP_RECEIVED", d_ok, d_detail)
        all_ok = all_ok and d_ok

        # ---- Assertion E: FOL computed delay (non-zero) ------------------
        calc_matches = RE_TRACE_DELAY_CALC.findall(fol_trace)
        e_ok = False
        e_delay_ns = None
        e_detail = "no DELAY_CALC trace found"
        if calc_matches:
            # Each match: (t1, t2, t3, hw_flag, t4, fwd, bwd, delay) — indices 0-7
            # A valid delay requires t4 != 0 (non-zero HW timestamp from GM)
            # and delay in a sane range (positive, less than 1 second).
            SANE_UPPER = 1_000_000_000  # 1 s
            valid_delays = [
                int(m[7]) for m in calc_matches
                if int(m[4]) != 0 and 0 < int(m[7]) < SANE_UPPER
            ]
            non_zero = [int(m[7]) for m in calc_matches if int(m[7]) != 0]
            e_ok = len(valid_delays) > 0
            if valid_delays:
                e_delay_ns = valid_delays[-1]  # most recent valid
                e_detail = (
                    f"count={len(calc_matches)} valid={len(valid_delays)} "
                    f"last_valid_delay={e_delay_ns} ns"
                )
            elif non_zero:
                e_delay_ns = None
                e_detail = (
                    f"count={len(calc_matches)} non_zero={len(non_zero)} "
                    f"but all invalid (t4=0 or out of range)"
                )
            else:
                e_detail = f"count={len(calc_matches)} but ALL delay=0"
        self._record("E: FOL DELAY_CALC non-zero delay", e_ok, e_detail)
        all_ok = all_ok and e_ok

        # ---- Assertion F: GM TX-busy skips within limit -------------------
        skips = RE_TRACE_GM_RESP_SKIPPED.findall(gm_trace)
        f_ok = len(skips) <= self.max_tx_busy_skips
        self._record(
            "F: GM TX-busy skips <= limit",
            f_ok,
            f"skips={len(skips)} limit={self.max_tx_busy_skips}",
        )
        all_ok = all_ok and f_ok

        # ---- Assertion G: delay in plausible range -----------------------
        if e_delay_ns is not None:
            g_ok = 0 < e_delay_ns < self.max_delay_ns
            self._record(
                "G: Delay in plausible range",
                g_ok,
                f"{e_delay_ns} ns (limit 0 < d < {self.max_delay_ns} ns)",
            )
            all_ok = all_ok and g_ok
        elif e_ok:
            # valid_delays exist but e_delay_ns wasn't set — shouldn't happen
            self._record(
                "G: Delay in plausible range",
                False,
                "internal error: valid_delays present but e_delay_ns is None",
            )
            all_ok = False
        else:
            # No valid delay value — G inconclusive (E already failed)
            self._record(
                "G: Delay in plausible range",
                False,
                "skipped: no valid delay value from DELAY_CALC (t4=0 or all invalid)",
            )
            all_ok = False

        # ---- Assertion H: at least one HW t3 captured (hw=1) --------------
        if calc_matches:
            hw_captures = [m for m in calc_matches if m[3] == "1"]
            h_ok = len(hw_captures) > 0
            self._record(
                "H: FOL t3 HW capture (hw=1 in DELAY_CALC)",
                h_ok,
                f"hw_captures={len(hw_captures)}/{len(calc_matches)}" if calc_matches
                else "no DELAY_CALC found",
            )
            all_ok = all_ok and h_ok

        # ---- Supplemental info (non-blocking) ----------------------------
        failed_sends = RE_TRACE_GM_RESP_FAILED.findall(gm_trace)
        if failed_sends:
            self.log.info(
                f"  [INFO] GM_DELAY_RESP_SEND_FAILED occurred {len(failed_sends)} time(s)"
            )

        return all_ok

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def step_6_cleanup(self):
        """Stop PTP on both nodes (best-effort)."""
        self.log.info("\n--- Step 6: Cleanup ---")
        for label, ser, cmd in [
            ("FOL", self.fol_ser, "ptp_mode off"),
            ("GM ", self.gm_ser,  "ptp_mode off"),
        ]:
            self.log.info(f"  [{label}] {cmd}")
            try:
                send_command(ser, cmd, self.cmd_timeout, self.log)
            except Exception as exc:
                self.log.info(f"  [{label}] WARNING: cleanup failed: {exc}")

    # ------------------------------------------------------------------
    # Final report
    # ------------------------------------------------------------------

    def print_report(self, overall: bool):
        self.log.info("\n" + "=" * 60)
        self.log.info("RESULTS SUMMARY")
        self.log.info("=" * 60)
        for name, passed, detail in self._step_results:
            status = "PASS" if passed else "FAIL"
            self.log.info(f"  [{status}] {name}" + (f"  — {detail}" if detail else ""))
        self.log.info("=" * 60)
        self.log.info(f"OVERALL: {'PASS' if overall else 'FAIL'}")
        self.log.info("=" * 60)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> bool:
        self.connect()
        overall = True
        try:
            self.step_0_reset()
            if not self.step_1_ip_config():
                overall = False
                self.log.info("ABORT: IP config failed")
                return overall
            if not self.step_2_connectivity():
                overall = False
                self.log.info("ABORT: connectivity failed")
                return overall
            if not self.step_3_start_ptp():
                overall = False
                self.log.info("ABORT: PTP did not reach FINE state")
                return overall

            fol_trace, gm_trace = self.step_4_collect_trace()

            if not self.step_5_validate_trace(fol_trace, gm_trace):
                overall = False

        finally:
            try:
                self.step_6_cleanup()
            except Exception:
                pass
            self.print_report(overall)
            self.disconnect()

        return overall


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PTP Delay Request/Response protocol verification test",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT,  help="GM  serial port")
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT, help="FOL serial port")
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP,    help="GM  IP address")
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP,   help="FOL IP address")
    p.add_argument("--netmask",  default=DEFAULT_NETMASK,  help="Network mask")
    p.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="Serial baud rate")
    p.add_argument(
        "--convergence-timeout", type=float, default=DEFAULT_CONVERGENCE_TIMEOUT,
        metavar="S", help="Seconds to wait for FOL FINE state"
    )
    p.add_argument(
        "--trace-time", type=float, default=DEFAULT_TRACE_TIME,
        metavar="S", help="Seconds to collect [TRACE] output"
    )
    p.add_argument(
        "--max-delay-ns", type=int, default=DEFAULT_MAX_DELAY_NS,
        metavar="NS", help="Maximum plausible one-way path delay in ns (assertion G)"
    )
    p.add_argument(
        "--max-tx-busy-skips", type=int, default=DEFAULT_MAX_TX_BUSY_SKIPS,
        metavar="N", help="Allowed GM TX-busy skip count (assertion F)"
    )
    p.add_argument(
        "--log-file", default=None,
        help="Override log file path (default: auto-generated timestamp)"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Show debug output")
    return p


def main():
    args = build_arg_parser().parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log_file or f"ptp_delay_{ts}.log"
    log = Logger(log_file=log_path, verbose=args.verbose)

    log.info("=" * 60)
    log.info("PTP Delay Request/Response Protocol Verification Test")
    log.info("=" * 60)
    log.info(f"  GM port          : {args.gm_port}")
    log.info(f"  FOL port         : {args.fol_port}")
    log.info(f"  GM IP            : {args.gm_ip}")
    log.info(f"  FOL IP           : {args.fol_ip}")
    log.info(f"  Trace time       : {args.trace_time} s")
    log.info(f"  Max delay        : {args.max_delay_ns} ns")
    log.info(f"  Max TX-busy skips: {args.max_tx_busy_skips}")
    log.info(f"  Log file         : {log_path}")
    log.info("")

    agent = PTPDelayTestAgent(
        gm_port             = args.gm_port,
        fol_port            = args.fol_port,
        gm_ip               = args.gm_ip,
        fol_ip              = args.fol_ip,
        netmask             = args.netmask,
        convergence_timeout = args.convergence_timeout,
        trace_time          = args.trace_time,
        max_delay_ns        = args.max_delay_ns,
        max_tx_busy_skips   = args.max_tx_busy_skips,
        log                 = log,
    )

    passed = agent.run()
    log.close()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
