#!/usr/bin/env python3
"""PTP Trace Debug Test — Convergence diagnostics with immediate ptp_trace
=========================================================================

Key differences from ptp_delay_test.py:
  - ptp_trace ON is activated IMMEDIATELY after PTP start (not only after FINE)
  - Both ports are read in background threads in parallel from the start
    → no data loss, no read race between convergence and trace capture
  - NO abort on missing FINE — trace and diagnostics continue
  - Convergence check polls the shared buffer (no direct ser.read())
  - Extended convergence timeout: default 60 s (instead of 30 s)
  - Detailed "stuck" diagnostics: last output lines + hypotheses
  - Assertions A–H from ptp_delay_test.py are run after the trace phase

Usage:
    python ptp_trace_debug_test.py --gm-port COM10 --fol-port COM8
    python ptp_trace_debug_test.py --gm-port COM10 --fol-port COM8 \\
        --convergence-timeout 90 --trace-time 20 -v

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
from typing import Dict, List, Optional, Tuple

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
DEFAULT_CONVERGENCE_TIMEOUT = 60.0    # extended vs. ptp_delay_test (30 s)
DEFAULT_TRACE_TIME          = 10.0    # extra seconds to collect after FINE (or timeout)
DEFAULT_MAX_DELAY_NS        = 10_000_000
DEFAULT_MAX_TX_BUSY_SKIPS   = 0

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

RE_IP_SET        = re.compile(r"Set ip address OK|IP address set to")
RE_PING_REPLY    = re.compile(r"Ping:.*reply.*from|Reply from")
RE_PING_DONE     = re.compile(r"Ping: done\.")
RE_FOL_START     = re.compile(r"PTP Follower enabled")
RE_GM_START      = re.compile(r"PTP Grandmaster enabled")
RE_MATCHFREQ     = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC     = re.compile(r"Hard sync completed")
RE_COARSE        = re.compile(r"PTP COARSE")
RE_FINE          = re.compile(r"PTP FINE")
RE_TRACE_ENABLED  = re.compile(r"PTP trace enabled")
RE_TRACE_DISABLED = re.compile(r"PTP trace disabled")

# [TRACE] patterns (identical to ptp_delay_test.py)
RE_TRACE_DELAY_REQ_SENT  = re.compile(r"\[TRACE\] DELAY_REQ_SENT\s+seq=(\d+)\s+t3_sw=(-?\d+)")
RE_TRACE_GM_REQ_RECEIVED = re.compile(r"\[TRACE\] GM_DELAY_REQ_RECEIVED\s+seq=(\d+)")
RE_TRACE_GM_RESP_SENT    = re.compile(r"\[TRACE\] GM_DELAY_RESP_SENT\s+seq=(\d+)")
RE_TRACE_GM_RESP_SKIPPED = re.compile(r"\[TRACE\] GM_DELAY_RESP_SKIPPED_TX_BUSY\s+seq=(\d+)")
RE_TRACE_GM_RESP_FAILED  = re.compile(r"\[TRACE\] GM_DELAY_RESP_SEND_FAILED\s+seq=(\d+)")
RE_TRACE_RESP_RECEIVED   = re.compile(r"\[TRACE\] DELAY_RESP_RECEIVED(?:\s+seq=(\d+))?")
RE_TRACE_RESP_UNSOLICITED= re.compile(r"\[TRACE\] DELAY_RESP_UNSOLICITED(?:\s+seq=(\d+))?")
RE_TRACE_RESP_WRONG_CLOCK= re.compile(r"\[TRACE\] DELAY_RESP_WRONG_CLOCK(?:\s+seq=(\d+))?")
RE_TRACE_DELAY_CALC      = re.compile(
    r"\[TRACE\] DELAY_CALC\s+"
    r"t1=(-?\d+)\s+t2=(-?\d+)\s+t3=(-?\d+)\(hw=(\d+)\)\s+t4=(-?\d+)\s+"
    r"fwd=(-?\d+)\s+bwd=(-?\d+)\s+delay=(-?\d+)"
)
# New patterns added by git-changes (2026-04-16)
RE_TRACE_WRONG_SEQ        = re.compile(r"\[TRACE\] DELAY_RESP_WRONG_SEQ\s+got=(\d+)\s+expected=(\d+)")
# State-machine diagnostic patterns
RE_SYNC_SEQID_MISMATCH    = re.compile(r"Sync seqId mismatch|FollowUp seqId mismatch|seqId out of sync")
RE_LARGE_SEQ_MISMATCH     = re.compile(r"Large sequence mismatch")
RE_GM_RESET               = re.compile(r"GM_RESET")
RE_T2_ZERO                = re.compile(r"t2=0\b")
RE_T3_HW_TIMEOUT          = re.compile(r"t3 HW capture timeout")
RE_T3_SW_FALLBACK         = re.compile(r"t3 HW not ready — SW fallback")


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    """Dual-writes to stdout and a log file. Thread-safe."""

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
# Serial helpers (pre-reader phase — direct port access)
# ---------------------------------------------------------------------------

def open_port(port: str, baudrate: int = DEFAULT_BAUDRATE) -> serial.Serial:
    return serial.Serial(
        port=port, baudrate=baudrate,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.1,
    )


def send_command_direct(ser: serial.Serial, cmd: str,
                        timeout: float = DEFAULT_CMD_TIMEOUT,
                        log: Logger = None) -> str:
    """Send CLI command via direct port read (use only BEFORE readers start)."""
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("ascii"))
    if log:
        log.debug(f">> {cmd}")
    parts     = []
    deadline  = time.monotonic() + timeout
    last_data = time.monotonic()
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            decoded = chunk.decode("ascii", errors="replace")
            parts.append(decoded)
            last_data = time.monotonic()
        else:
            if parts and (time.monotonic() - last_data) > 0.5:
                break
            time.sleep(0.05)
    return "".join(parts)


def wait_for_pattern_direct(ser: serial.Serial, pattern: re.Pattern,
                             timeout: float, log: Logger = None,
                             extra_patterns: dict = None,
                             live_log: bool = False) -> Tuple[bool, float, dict]:
    """Read from ser until pattern matches or timeout (no reader active)."""
    if extra_patterns is None:
        extra_patterns = {}
    milestones: dict = {}
    buf      = ""
    start    = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            decoded = chunk.decode("ascii", errors="replace")
            buf    += decoded
            if log:
                for line in decoded.splitlines():
                    if line.strip():
                        if live_log:
                            log.info(f"    {line.rstrip()}")
                        else:
                            log.debug(f"  <- {line.rstrip()}")
            for label, pat in extra_patterns.items():
                if label not in milestones and pat.search(buf):
                    milestones[label] = time.monotonic() - start
            if pattern.search(buf):
                return True, time.monotonic() - start, milestones
        else:
            time.sleep(0.05)
    return False, time.monotonic() - start, milestones


# ---------------------------------------------------------------------------
# Background Serial Reader
# ---------------------------------------------------------------------------

class SerialReader(threading.Thread):
    """Continuously reads a serial port into a timestamped buffer.
    Writes each line to the logger in real time.

    All serial access after readers.start() must go through send_cmd()
    or send_cmd_wait() — never read directly from the port."""

    def __init__(self, ser: serial.Serial, label: str, log: Logger):
        super().__init__(daemon=True)
        self._ser   = ser
        self._label = label
        self._log   = log
        self._buf   : List[Tuple[float, str]] = []
        self._lock  = threading.Lock()
        self._stop  = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except Exception:
                break
            if chunk:
                ts      = time.monotonic()
                decoded = chunk.decode("ascii", errors="replace")
                with self._lock:
                    self._buf.append((ts, decoded))
                for line in decoded.splitlines():
                    if line.strip():
                        self._log.info(f"    [{self._label}] {line.rstrip()}")
            else:
                time.sleep(0.01)

    # ------------------------------------------------------------------
    # Command helpers (use while reader is running)
    # ------------------------------------------------------------------

    def send_cmd(self, cmd: str) -> None:
        """Fire-and-forget: write cmd; response arrives via reader thread."""
        self._ser.write((cmd + "\r\n").encode("ascii"))
        self._log.info(f"    [{self._label}] >> {cmd}")

    def send_cmd_wait(self, cmd: str, pattern: re.Pattern,
                      timeout: float = DEFAULT_CMD_TIMEOUT) -> Tuple[bool, str]:
        """Send cmd; wait until *pattern* appears in NEW text after the send.
        Returns (matched, new_text_since_send)."""
        ts_send  = time.monotonic()
        self._ser.write((cmd + "\r\n").encode("ascii"))
        self._log.info(f"    [{self._label}] >> {cmd}")
        deadline = ts_send + timeout
        while time.monotonic() < deadline:
            new_text = self._get_text_since(ts_send)
            if pattern.search(new_text):
                return True, new_text
            time.sleep(0.05)
        return False, self._get_text_since(ts_send)

    # ------------------------------------------------------------------
    # Buffer access
    # ------------------------------------------------------------------

    def get_text(self) -> str:
        """All accumulated text (entire session)."""
        with self._lock:
            return "".join(t for _, t in self._buf)

    def _get_text_since(self, ts_start: float) -> str:
        with self._lock:
            return "".join(t for ts, t in self._buf if ts >= ts_start)

    def wait_for_with_milestones(self, pattern: re.Pattern, timeout: float,
                                  extra_patterns: Dict[str, re.Pattern]
                                  ) -> Tuple[bool, float, dict]:
        """Poll buffer until pattern matches.  Track milestone timestamps."""
        start     = time.monotonic()
        deadline  = start + timeout
        milestones: dict = {}
        while time.monotonic() < deadline:
            text = self.get_text()
            for label, pat in extra_patterns.items():
                if label not in milestones and pat.search(text):
                    milestones[label] = time.monotonic() - start
            if pattern.search(text):
                return True, time.monotonic() - start, milestones
        # Final scan on timeout
        text = self.get_text()
        for label, pat in extra_patterns.items():
            if label not in milestones and pat.search(text):
                milestones[label] = timeout
        return False, timeout, milestones

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# Diagnostic analysis
# ---------------------------------------------------------------------------

def analyze_stuck_state(fol_text: str, gm_text: str, log: Logger):
    """Diagnose why the FOL state machine is stuck."""
    log.info("\n" + "=" * 60)
    log.info("STUCK-STATE DIAGNOSE")
    log.info("=" * 60)

    # Counters
    gm_req_rcv     = len(RE_TRACE_GM_REQ_RECEIVED.findall(gm_text))
    gm_resp_sent   = len(RE_TRACE_GM_RESP_SENT.findall(gm_text))
    gm_resp_skip   = len(RE_TRACE_GM_RESP_SKIPPED.findall(gm_text))
    gm_resp_fail   = len(RE_TRACE_GM_RESP_FAILED.findall(gm_text))
    fol_req_sent   = len(RE_TRACE_DELAY_REQ_SENT.findall(fol_text))
    fol_resp_rcv   = len(RE_TRACE_RESP_RECEIVED.findall(fol_text))
    fol_resp_unsol = len(RE_TRACE_RESP_UNSOLICITED.findall(fol_text))
    fol_calc       = len(RE_TRACE_DELAY_CALC.findall(fol_text))
    wrong_seq      = RE_TRACE_WRONG_SEQ.findall(fol_text)
    seq_mismatch   = len(RE_SYNC_SEQID_MISMATCH.findall(fol_text))
    large_mismatch = len(RE_LARGE_SEQ_MISMATCH.findall(fol_text))
    gm_reset_cnt   = len(RE_GM_RESET.findall(fol_text))
    t3_hw_timeout  = len(RE_T3_HW_TIMEOUT.findall(fol_text))
    t3_sw_fallback = len(RE_T3_SW_FALLBACK.findall(fol_text))

    matched_matchfreq = bool(RE_MATCHFREQ.search(fol_text))
    matched_hardsync  = bool(RE_HARD_SYNC.search(fol_text))
    matched_coarse    = bool(RE_COARSE.search(fol_text))
    matched_fine      = bool(RE_FINE.search(fol_text))

    log.info("\n  --- Convergence Milestones (FOL) ---")
    log.info(f"  UNINIT->MATCHFREQ : {'YES' if matched_matchfreq else 'NO'}")
    log.info(f"  Hard sync         : {'YES' if matched_hardsync  else 'NO'}")
    log.info(f"  PTP COARSE        : {'YES' if matched_coarse    else 'NO'}")
    log.info(f"  PTP FINE          : {'YES' if matched_fine      else 'NO'}")

    log.info("\n  --- TRACE Counters ---")
    log.info(f"  GM  GM_DELAY_REQ_RECEIVED     : {gm_req_rcv}")
    log.info(f"  GM  GM_DELAY_RESP_SENT        : {gm_resp_sent}")
    log.info(f"  GM  GM_DELAY_RESP_SKIPPED     : {gm_resp_skip}")
    log.info(f"  GM  GM_DELAY_RESP_SEND_FAILED : {gm_resp_fail}")
    log.info(f"  FOL DELAY_REQ_SENT            : {fol_req_sent}")
    log.info(f"  FOL DELAY_RESP_RECEIVED       : {fol_resp_rcv}")
    log.info(f"  FOL DELAY_RESP_UNSOLICITED    : {fol_resp_unsol}")
    log.info(f"  FOL DELAY_CALC                : {fol_calc}")
    log.info(f"  FOL DELAY_RESP_WRONG_SEQ      : {len(wrong_seq)}")
    log.info(f"  FOL Sync/FollowUp seqId errors : {seq_mismatch}")
    log.info(f"  FOL Large-seq-Mismatch(Reset)  : {large_mismatch}")
    log.info(f"  FOL GM_RESET events            : {gm_reset_cnt}")
    log.info(f"  FOL t3 HW capture timeout      : {t3_hw_timeout}")
    log.info(f"  FOL t3 SW-Fallback used        : {t3_sw_fallback}")

    if wrong_seq:
        log.info(f"\n  DELAY_RESP_WRONG_SEQ examples (max 3):")
        for got, exp in wrong_seq[:3]:
            log.info(f"    got={got} expected={exp}")

    log.info("\n  --- Hypotheses ---")
    hypotheses = []

    if not matched_matchfreq:
        hypotheses.append(
            "[H1] No MATCHFREQ: FOL receives NO Sync/FollowUp frames.\n"
            "      -> GM EtherType filter OK? (0x88F7)\n"
            "      -> flags[0]=0x02 (twoStepFlag) newly set — FOL parser OK?\n"
            "      -> PTP_FOL_OnFrame() is called? (check EtherType dispatcher)\n"
            "      -> Firmware flashed with latest changes?"
        )
    elif not matched_hardsync:
        hypotheses.append(
            "[H2] MATCHFREQ OK, but no hard sync.\n"
            "      -> t2 (RX timestamp) at FOL plausible?\n"
            "      -> rateRatio filter not converging (too few Sync frames)?"
        )
    elif not matched_coarse:
        hypotheses.append(
            "[H3] Hard sync OK, but no COARSE.\n"
            "      -> Offset filter diverging?\n"
            "      -> TI/TISUBN register writes stalled (fol_reg_state != IDLE)?"
        )

    if fol_req_sent > 0 and gm_req_rcv == 0:
        hypotheses.append(
            "[H4] FOL sends Delay_Req but GM does NOT receive it.\n"
            "      -> Destination MAC in Delay_Req: broadcast (FF:FF:FF:FF:FF:FF) OK?\n"
            "      -> Check GM TXMPATL/TXMMSK filter configuration"
        )

    if len(wrong_seq) > 0:
        hypotheses.append(
            f"[H5] DELAY_RESP_WRONG_SEQ ({len(wrong_seq)}x): sequence ID check firing.\n"
            "      -> fol_delay_req_sent_seq_id correctly saved before fol_delay_req_seq_id++?\n"
            "      -> Git diff shows: save now BEFORE increment — should be OK."
        )

    if gm_resp_skip > 0:
        hypotheses.append(
            f"[H6] GM TX-busy skips: {gm_resp_skip}x — Delay_Resp was not sent."
        )

    if large_mismatch > 0:
        hypotheses.append(
            f"[H7] Large sequence mismatch ({large_mismatch}x) → resetSlaveNode() triggered.\n"
            "      -> GM Sync frames arriving irregularly?"
        )

    if not hypotheses:
        hypotheses.append("[H?] No obvious pattern — check complete log.")

    for h in hypotheses:
        for line in h.splitlines():
            log.info(f"  {line}")

    # Letzte Ausgaben
    fol_lines = [l.strip() for l in fol_text.splitlines() if l.strip()]
    if fol_lines:
        n = min(15, len(fol_lines))
        log.info(f"\n  --- Last {n} FOL output lines ---")
        for line in fol_lines[-n:]:
            log.info(f"    {line}")

    gm_lines = [l.strip() for l in gm_text.splitlines() if l.strip()]
    if gm_lines:
        n = min(15, len(gm_lines))
        log.info(f"\n  --- Last {n} GM output lines ---")
        for line in gm_lines[-n:]:
            log.info(f"    {line}")


# ---------------------------------------------------------------------------
# Trace assertions (A–H, same as ptp_delay_test.py assertion set)
# ---------------------------------------------------------------------------

def validate_trace_assertions(fol_trace: str, gm_trace: str,
                               max_delay_ns: int, max_tx_busy_skips: int,
                               results: list, log: Logger) -> bool:
    step = "Step 5: Validate Trace Assertions"
    log.info(f"\n--- {step} ---")
    all_ok = True

    def record(name, passed, detail=""):
        results.append((name, passed, detail))
        status = "PASS" if passed else "FAIL"
        log.info(f"  >> {status}: {name}" + (f"  ({detail})" if detail else ""))
        return passed

    # A: FOL sent Delay_Req
    req_sent = RE_TRACE_DELAY_REQ_SENT.findall(fol_trace)
    all_ok &= record("A: FOL DELAY_REQ_SENT",
                     len(req_sent) > 0,
                     f"count={len(req_sent)}" if req_sent else "no DELAY_REQ_SENT trace")

    # B: GM received Delay_Req
    gm_req = RE_TRACE_GM_REQ_RECEIVED.findall(gm_trace)
    all_ok &= record("B: GM GM_DELAY_REQ_RECEIVED",
                     len(gm_req) > 0,
                     f"count={len(gm_req)}" if gm_req else "no GM_DELAY_REQ_RECEIVED trace")

    # C: GM sent Delay_Resp
    gm_resp = RE_TRACE_GM_RESP_SENT.findall(gm_trace)
    all_ok &= record("C: GM GM_DELAY_RESP_SENT",
                     len(gm_resp) > 0,
                     f"count={len(gm_resp)}" if gm_resp else "no GM_DELAY_RESP_SENT trace")

    # D: FOL received Delay_Resp
    resp_rcv   = RE_TRACE_RESP_RECEIVED.findall(fol_trace)
    resp_unsol = RE_TRACE_RESP_UNSOLICITED.findall(fol_trace)
    resp_wrong = RE_TRACE_RESP_WRONG_CLOCK.findall(fol_trace)
    d_detail   = f"received={len(resp_rcv)}"
    if resp_unsol:
        d_detail += f" unsolicited={len(resp_unsol)}"
    if resp_wrong:
        d_detail += f" wrong_clock={len(resp_wrong)}"
    if not resp_rcv:
        d_detail = "no DELAY_RESP_RECEIVED trace; " + d_detail
    all_ok &= record("D: FOL DELAY_RESP_RECEIVED", len(resp_rcv) > 0, d_detail)

    # E: FOL computed non-zero delay
    calc_matches = RE_TRACE_DELAY_CALC.findall(fol_trace)
    e_ok       = False
    e_delay_ns = None
    e_detail   = "no DELAY_CALC trace"
    if calc_matches:
        SANE_UPPER  = 1_000_000_000
        valid_delays = [int(m[7]) for m in calc_matches
                        if int(m[4]) != 0 and 0 < int(m[7]) < SANE_UPPER]
        non_zero     = [int(m[7]) for m in calc_matches if int(m[7]) != 0]
        e_ok = len(valid_delays) > 0
        if valid_delays:
            e_delay_ns = valid_delays[-1]
            e_detail   = (f"count={len(calc_matches)} valid={len(valid_delays)} "
                          f"last_valid_delay={e_delay_ns} ns")
        elif non_zero:
            e_detail = (f"count={len(calc_matches)} non_zero={len(non_zero)} "
                        f"but all invalid (t4=0 or out of range)")
        else:
            e_detail = f"count={len(calc_matches)} but ALL delay=0"
    all_ok &= record("E: FOL DELAY_CALC non-zero delay", e_ok, e_detail)

    # F: GM TX-busy skips within limit
    skips = RE_TRACE_GM_RESP_SKIPPED.findall(gm_trace)
    all_ok &= record("F: GM TX-busy skips <= limit",
                     len(skips) <= max_tx_busy_skips,
                     f"skips={len(skips)} limit={max_tx_busy_skips}")

    # G: delay in plausible range
    if e_delay_ns is not None:
        all_ok &= record("G: Delay in plausible range",
                         0 < e_delay_ns < max_delay_ns,
                         f"{e_delay_ns} ns (limit 0 < d < {max_delay_ns} ns)")
    else:
        all_ok &= record("G: Delay in plausible range", False,
                         "skipped: no valid DELAY_CALC value (t4=0 or invalid)")

    # H: at least one HW t3 captured (hw=1)
    if calc_matches:
        hw_cap = [m for m in calc_matches if m[3] == "1"]
        all_ok &= record("H: FOL t3 HW-Capture (hw=1 in DELAY_CALC)",
                         len(hw_cap) > 0,
                         f"hw_captures={len(hw_cap)}/{len(calc_matches)}")

    # I: no DELAY_RESP_WRONG_SEQ (new assertion for git-change verification)
    wrong_seq = RE_TRACE_WRONG_SEQ.findall(fol_trace)
    i_ok = len(wrong_seq) == 0
    i_detail = ("no WRONG_SEQ — seq-ID check correct" if i_ok
                else f"{len(wrong_seq)}x WRONG_SEQ — fol_delay_req_sent_seq_id wrong?")
    all_ok &= record("I: No DELAY_RESP_WRONG_SEQ (IEEE 1588 §11.3.3)", i_ok, i_detail)

    # Supplemental
    failed = RE_TRACE_GM_RESP_FAILED.findall(gm_trace)
    if failed:
        log.info(f"  [INFO] GM_DELAY_RESP_SEND_FAILED: {len(failed)}x")

    return all_ok


# ---------------------------------------------------------------------------
# Test agent
# ---------------------------------------------------------------------------

class PTPTraceDebugAgent:

    def __init__(
        self,
        gm_port: str,
        fol_port: str,
        gm_ip: str               = DEFAULT_GM_IP,
        fol_ip: str              = DEFAULT_FOL_IP,
        netmask: str             = DEFAULT_NETMASK,
        convergence_timeout: float = DEFAULT_CONVERGENCE_TIMEOUT,
        cmd_timeout: float       = DEFAULT_CMD_TIMEOUT,
        trace_time: float        = DEFAULT_TRACE_TIME,
        max_delay_ns: int        = DEFAULT_MAX_DELAY_NS,
        max_tx_busy_skips: int   = DEFAULT_MAX_TX_BUSY_SKIPS,
        log: Logger              = None,
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
        self.gm_reader:  Optional[SerialReader] = None
        self.fol_reader: Optional[SerialReader] = None

        self._step_results: list = []

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

    def _start_readers(self):
        """Start background reader threads for both ports."""
        self.gm_reader  = SerialReader(self.gm_ser,  "GM ", self.log)
        self.fol_reader = SerialReader(self.fol_ser, "FOL", self.log)
        self.gm_reader.start()
        self.fol_reader.start()

    def _stop_readers(self):
        for r in (self.gm_reader, self.fol_reader):
            if r:
                r.stop()

    def disconnect(self):
        self._stop_readers()
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
    # Step 0 — Reset  (direct serial, no readers)
    # ------------------------------------------------------------------

    def step_0_reset(self):
        step = "Step 0: Reset"
        self.log.info(f"\n--- {step} ---")
        parts = []
        for label, ser in [("GM ", self.gm_ser), ("FOL", self.fol_ser)]:
            self.log.info(f"  [{label}] reset")
            try:
                send_command_direct(ser, "reset", self.cmd_timeout, self.log)
                parts.append(f"{label} reset sent")
            except Exception as exc:
                parts.append(f"{label} reset WARNING ({exc})")
                self.log.info(f"  [{label}] WARNING: reset failed (continuing): {exc}")
        self.log.info("  Waiting 8 s after reset...")
        time.sleep(8)
        self._record(step, True, "; ".join(parts))

    # ------------------------------------------------------------------
    # Step 1 — IP Configuration  (direct serial, no readers)
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
            cmd  = f"setip eth0 {ip} {self.netmask}"
            self.log.info(f"  [{label}] {cmd}")
            resp = send_command_direct(ser, cmd, self.cmd_timeout, self.log)
            if RE_IP_SET.search(resp):
                parts.append(f"{label} IP ok ({ip})")
            else:
                parts.append(f"{label} IP FAIL ({ip})")
                self.log.info(f"  [{label}] Unexpected response: {resp.strip()!r}")
                passed = False
        self._record(step, passed, "; ".join(parts))
        return passed

    # ------------------------------------------------------------------
    # Step 2 — Connectivity  (direct serial, no readers)
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
            matched, _, _ = wait_for_pattern_direct(
                src_ser, RE_PING_DONE, timeout=15.0, log=self.log,
                extra_patterns={"first_reply": RE_PING_REPLY}, live_log=True,
            )
            if matched:
                parts.append(f"{src_label} ok")
            else:
                parts.append(f"{src_label} FAIL")
                passed = False
        self._record(step, passed, "; ".join(parts))
        return passed

    # ------------------------------------------------------------------
    # Step 3 — PTP Start + ptp_trace ON + Convergence wait
    # ------------------------------------------------------------------

    def step_3_start_ptp_with_trace(self) -> Tuple[bool, bool]:
        """Start PTP on both nodes, enable trace IMMEDIATELY, wait for FINE.

        Returns (ptp_started_ok, fine_reached).
        Does NOT abort on FINE failure.
        """
        step = "Step 3: PTP Start + ptp_trace ON (immediately) + Convergence"
        self.log.info(f"\n--- {step} ---")
        ptp_ok = True
        parts  = []

        # --- Start background readers BEFORE any PTP command ---
        self.log.info("  Starting background serial readers...")
        self._start_readers()

        # --- Follower: ptp_mode follower ---
        self.log.info("  [FOL] ptp_mode follower")
        matched, _ = self.fol_reader.send_cmd_wait(
            "ptp_mode follower", RE_FOL_START, self.cmd_timeout
        )
        if matched:
            parts.append("FOL start ok")
        else:
            parts.append("FOL start FAIL (no 'PTP Follower enabled')")
            ptp_ok = False

        # --- Follower: ptp_trace on — IMMEDIATELY ---
        self.log.info("  [FOL] ptp_trace on  ← immediately, before GM start")
        matched_trace, _ = self.fol_reader.send_cmd_wait(
            "ptp_trace on", RE_TRACE_ENABLED, self.cmd_timeout
        )
        if matched_trace:
            self.log.info("  [FOL] trace enabled confirmed")
        else:
            self.log.info("  [FOL] WARNING: trace enable not confirmed")

        time.sleep(0.2)

        # --- GM: ptp_mode master ---
        self.log.info("  [GM ] ptp_mode master")
        matched, _ = self.gm_reader.send_cmd_wait(
            "ptp_mode master", RE_GM_START, self.cmd_timeout
        )
        if matched:
            parts.append("GM start ok")
        else:
            parts.append("GM start FAIL (no 'PTP Grandmaster enabled')")
            ptp_ok = False

        # --- GM: ptp_trace on — IMMEDIATELY ---
        self.log.info("  [GM ] ptp_trace on  ← immediately, parallel to FOL")
        matched_trace, _ = self.gm_reader.send_cmd_wait(
            "ptp_trace on", RE_TRACE_ENABLED, self.cmd_timeout
        )
        if matched_trace:
            self.log.info("  [GM ] trace enabled confirmed")
        else:
            self.log.info("  [GM ] WARNING: trace enable not confirmed")

        # --- Convergence wait (poll fol_reader buffer) ---
        self.log.info(
            f"\n  Waiting for FOL FINE state "
            f"(timeout={self.convergence_timeout}s, trace is active)..."
        )
        fine_reached, elapsed, milestones = self.fol_reader.wait_for_with_milestones(
            RE_FINE,
            self.convergence_timeout,
            extra_patterns={
                "MATCHFREQ": RE_MATCHFREQ,
                "HARD_SYNC": RE_HARD_SYNC,
                "COARSE":    RE_COARSE,
            },
        )
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if ms_str:
            self.log.info(f"  Milestones: {ms_str}")

        if fine_reached:
            self.log.info(f"  FINE reached in {elapsed:.1f}s")
            parts.append(f"FINE@{elapsed:.1f}s")
        else:
            self.log.info(
                f"  FINE NOT reached within {self.convergence_timeout}s "
                f"— trace analysis follows regardless"
            )
            parts.append(f"FINE NOT reached (milestones: {ms_str or 'none'})")

        self._record(step, fine_reached and ptp_ok,
                     "; ".join(parts))
        return ptp_ok, fine_reached

    # ------------------------------------------------------------------
    # Step 4 — Collect additional trace output (ptp_trace already ON)
    # ------------------------------------------------------------------

    def step_4_collect_more_trace(self, fine_reached: bool):
        """Collect additional trace_time seconds with ptp_trace still active."""
        label = "Step 4: Additional Trace Collection"
        self.log.info(f"\n--- {label} ---")
        action = "after FINE" if fine_reached else "after convergence timeout (no FINE)"
        self.log.info(
            f"  Collecting {self.trace_time:.0f}s additional trace output {action}..."
        )
        time.sleep(self.trace_time)
        self._record(label, True,
                     f"{self.trace_time:.0f}s trace collected ({action})")

    # ------------------------------------------------------------------
    # Step 5 — Disable trace and cleanup
    # ------------------------------------------------------------------

    def step_5_disable_trace_and_cleanup(self):
        """Disable ptp_trace and ptp_mode on both nodes."""
        self.log.info("\n--- Step 5: ptp_trace OFF + Cleanup ---")
        for label, reader in [("GM ", self.gm_reader), ("FOL", self.fol_reader)]:
            self.log.info(f"  [{label}] ptp_trace off")
            matched, _ = reader.send_cmd_wait(
                "ptp_trace off", RE_TRACE_DISABLED, self.cmd_timeout
            )
            if not matched:
                self.log.info(f"  [{label}] WARNING: trace disable not confirmed")

        for label, reader, cmd in [
            ("FOL", self.fol_reader, "ptp_mode off"),
            ("GM ", self.gm_reader,  "ptp_mode off"),
        ]:
            self.log.info(f"  [{label}] {cmd}")
            reader.send_cmd(cmd)
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Report
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
            # Phase 1: pre-reader steps (direct serial access)
            self.step_0_reset()
            if not self.step_1_ip_config():
                overall = False
                self.log.info("ABORT: IP config failed")
                return overall
            if not self.step_2_connectivity():
                overall = False
                self.log.info("ABORT: connectivity failed")
                return overall

            # Phase 2: readers active — trace ON immediately
            ptp_ok, fine_reached = self.step_3_start_ptp_with_trace()
            if not ptp_ok:
                overall = False

            # Phase 3: collect additional trace (even if FINE failed)
            self.step_4_collect_more_trace(fine_reached)

            # Phase 4: disable trace, cleanup
            self.step_5_disable_trace_and_cleanup()

            # Phase 5: analyze collected data
            fol_text = self.fol_reader.get_text() if self.fol_reader else ""
            gm_text  = self.gm_reader.get_text()  if self.gm_reader  else ""

            # Assertions A–I
            if not validate_trace_assertions(
                fol_text, gm_text,
                self.max_delay_ns, self.max_tx_busy_skips,
                self._step_results, self.log
            ):
                overall = False

            # Stuck-state diagnosis (always shown if FINE not reached)
            if not fine_reached:
                analyze_stuck_state(fol_text, gm_text, self.log)
            else:
                # Brief summary even on success
                req_cnt = len(RE_TRACE_DELAY_REQ_SENT.findall(fol_text))
                calc_cnt = len(RE_TRACE_DELAY_CALC.findall(fol_text))
                self.log.info(
                    f"\n  [OK] FINE reached. "
                    f"Delay_Req sent: {req_cnt}, "
                    f"DELAY_CALC: {calc_cnt}"
                )

        finally:
            try:
                self.step_5_disable_trace_and_cleanup()
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
        description="PTP Trace Debug Test — Convergence diagnostics with immediate ptp_trace",
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
        metavar="S", help="Seconds to wait for FOL FINE (default: 60)"
    )
    p.add_argument(
        "--trace-time", type=float, default=DEFAULT_TRACE_TIME,
        metavar="S", help="Additional trace seconds after FINE/timeout"
    )
    p.add_argument(
        "--max-delay-ns", type=int, default=DEFAULT_MAX_DELAY_NS,
        metavar="NS", help="Max plausible one-way path delay in ns (assertion G)"
    )
    p.add_argument(
        "--max-tx-busy-skips", type=int, default=DEFAULT_MAX_TX_BUSY_SKIPS,
        metavar="N", help="Allowed GM TX-busy skips (assertion F)"
    )
    p.add_argument(
        "--log-file", default=None,
        help="Log file (default: auto-generated timestamp)"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug output")
    return p


def main():
    args = build_arg_parser().parse_args()

    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log_file or f"ptp_trace_debug_{ts}.log"
    log      = Logger(log_file=log_path, verbose=args.verbose)

    log.info("=" * 60)
    log.info("PTP Trace Debug Test — Convergence Diagnostics")
    log.info("=" * 60)
    log.info(f"  GM port              : {args.gm_port}")
    log.info(f"  FOL port             : {args.fol_port}")
    log.info(f"  GM IP                : {args.gm_ip}")
    log.info(f"  FOL IP               : {args.fol_ip}")
    log.info(f"  Convergence timeout  : {args.convergence_timeout} s")
    log.info(f"  Trace time (extra)   : {args.trace_time} s")
    log.info(f"  Max delay            : {args.max_delay_ns} ns")
    log.info(f"  Max TX-busy skips    : {args.max_tx_busy_skips}")
    log.info(f"  Log file             : {log_path}")
    log.info("")

    agent = PTPTraceDebugAgent(
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
