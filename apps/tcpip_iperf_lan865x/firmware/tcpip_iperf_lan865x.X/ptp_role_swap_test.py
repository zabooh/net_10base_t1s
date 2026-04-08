#!/usr/bin/env python3
"""PTP Role-Swap Test
===================

Tests what happens when the Grandmaster and Follower roles are swapped
at runtime without resetting the boards.

Scenario:
  0. Reset both boards.
  1. Set IP addresses.
  2. Ping connectivity check.
  3. Phase 1 — board1=FOLLOWER, board2=GRANDMASTER.
     FOL activated first, then GM.
     Collect offset samples for --phase1-time seconds.
  4. Stop both boards with ptp_mode off.
  5. Wait --pause-time seconds (both clocks free-running).
  6. Phase 2 — roles swapped: board1=GRANDMASTER, board2=FOLLOWER.
     FOL (board2) activated first, then GM (board1).
     Wait for convergence, collect offset samples.

Expected (broken firmware):
  After the role swap the initial offset observed on the new follower (board2)
  will be very large (multiple seconds), because PTP_GM_Deinit() writes
  MAC_TI=0 which freezes the LAN865x PTP clock.  When board2 later becomes
  follower, its hardware RX-timestamp is computed using the frozen clock.

Expected (fixed firmware):
  The initial offset should be ≤ a few hundred nanoseconds (drift only).

Usage:
    python ptp_role_swap_test.py --board1-port COM8 --board2-port COM10
"""

import argparse
import datetime
import re
import statistics
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BOARD1_PORT    = "COM8"
DEFAULT_BOARD2_PORT    = "COM10"
DEFAULT_BOARD1_IP      = "192.168.0.30"
DEFAULT_BOARD2_IP      = "192.168.0.20"
DEFAULT_NETMASK        = "255.255.255.0"
DEFAULT_BAUDRATE       = 115200
DEFAULT_CMD_TIMEOUT    = 5.0
DEFAULT_CONV_TIMEOUT   = 30.0
DEFAULT_PHASE1_TIME    = 15.0   # seconds GM1 runs before stop
DEFAULT_PAUSE_TIME     = 5.0    # seconds both are off
DEFAULT_SAMPLES        = 10
OFFSET_THRESHOLD_NS    = 500    # pass criterion after role swap

RE_IP_SET       = re.compile(r"Set ip address OK|IP address set to")
RE_PING_REPLY   = re.compile(r"Ping:.*reply.*from|Reply from")
RE_PING_DONE    = re.compile(r"Ping: done\.")
RE_FOL_START    = re.compile(r"PTP Follower enabled")
RE_GM_START     = re.compile(r"PTP Grandmaster enabled")
RE_MATCHFREQ    = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC    = re.compile(r"Hard sync completed")
RE_COARSE       = re.compile(r"PTP COARSE")
RE_FINE         = re.compile(r"PTP FINE")
RE_DISABLED     = re.compile(r"PTP disabled")
RE_PTP_OFFSET   = re.compile(r"Offset(?:\s*ns)?\s*:\s*([+-]?\d+)\s*ns|Offset ns\s*:\s*([+-]?\d+)")


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


def send_command(ser: serial.Serial, cmd: str, timeout: float = DEFAULT_CMD_TIMEOUT,
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


def wait_for_pattern(ser: serial.Serial, pattern: re.Pattern, timeout: float,
                     log: Logger = None, extra_patterns: dict = None,
                     live_log: bool = False) -> Tuple[bool, float, dict]:
    if extra_patterns is None:
        extra_patterns = {}
    milestones: dict = {}
    buffer = ""
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
# Role-Swap Test
# ---------------------------------------------------------------------------

class PTPRoleSwapTest:

    def __init__(self, board1_port: str, board2_port: str,
                 board1_ip: str, board2_ip: str, netmask: str,
                 phase1_time: float, pause_time: float,
                 samples: int, conv_timeout: float, log: Logger):
        self.b1_port      = board1_port
        self.b2_port      = board2_port
        self.b1_ip        = board1_ip
        self.b2_ip        = board2_ip
        self.netmask      = netmask
        self.phase1_time  = phase1_time
        self.pause_time   = pause_time
        self.samples      = samples
        self.conv_timeout = conv_timeout
        self.log          = log

        self.b1_ser: Optional[serial.Serial] = None
        self.b2_ser: Optional[serial.Serial] = None
        self.results: list = []

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self):
        for label, port, attr in [("board1", self.b1_port, "b1_ser"),
                                   ("board2", self.b2_port, "b2_ser")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def disconnect(self):
        for ser in (self.b1_ser, self.b2_ser):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    def _reopen(self, attr: str, port: str):
        ser = getattr(self, attr)
        if ser and ser.is_open:
            try: ser.close()
            except Exception: pass
        time.sleep(0.3)
        try:
            setattr(self, attr, open_port(port))
            self.log.info(f"  {attr} reopened: {port}")
        except serial.SerialException as exc:
            self.log.info(f"  ERROR: cannot reopen {port}: {exc}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Convergence thread
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
    # Offset helpers
    # ------------------------------------------------------------------

    def _read_offset(self, fol_ser: serial.Serial) -> Optional[int]:
        fol_ser.reset_input_buffer()
        fol_ser.write(b"ptp_status\r\n")
        deadline = time.monotonic() + 2.0
        buf = ""
        while time.monotonic() < deadline:
            chunk = fol_ser.read(256)
            if chunk:
                buf += chunk.decode("ascii", errors="replace")
                m = RE_PTP_OFFSET.search(buf)
                if m:
                    return int(m.group(1) if m.group(1) is not None else m.group(2))
            else:
                time.sleep(0.05)
        return None

    def _collect_offsets(self, fol_ser: serial.Serial, n: int, interval: float,
                          label: str) -> List[int]:
        offsets = []
        for i in range(n):
            val = self._read_offset(fol_ser)
            if val is not None:
                offsets.append(val)
                self.log.info(f"  [{label}] sample {i+1:2d}/{n}: {val:+d} ns")
            else:
                self.log.info(f"  [{label}] sample {i+1}/{n}: no data")
            if i < n - 1:
                time.sleep(interval)
        return offsets

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def step_reset(self):
        self.log.info("\n--- Step 0: Reset ---")
        for label, ser in [("board1", self.b1_ser), ("board2", self.b2_ser)]:
            self.log.info(f"  [{label}] reset")
            send_command(ser, "reset", self.conv_timeout, self.log)
        self.log.info("  Waiting 8 s...")
        time.sleep(8)
        self._record("Step 0: Reset", True, "both reset")

    def step_ip(self) -> bool:
        self.log.info("\n--- Step 1: IP Configuration ---")
        passed = True
        for label, ser, ip in [("board1", self.b1_ser, self.b1_ip),
                                 ("board2", self.b2_ser, self.b2_ip)]:
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
            ("board1 -> board2", self.b1_ser, self.b2_ip),
            ("board2 -> board1", self.b2_ser, self.b1_ip),
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

    def step_phase1(self) -> bool:
        """Phase 1: board1=FOLLOWER, board2=GRANDMASTER. FOL first, then GM."""
        self.log.info(
            f"\n--- Phase 1: board1=FOL  board2=GM  (run {self.phase1_time:.0f} s) ---")
        passed = True

        # FOL first (board1)
        self.log.info("  [board1-FOL] ptp_mode follower")
        resp = send_command(self.b1_ser, "ptp_mode follower", self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [board1-FOL] confirmed")
        else:
            self.log.info(f"  [board1-FOL] no confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.b1_ser.reset_input_buffer()
        self._start_conv(self.b1_ser)

        # GM second (board2)
        self.log.info("  [board2-GM ] ptp_mode master")
        self.b2_ser.reset_input_buffer()
        self.b2_ser.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.b2_ser, RE_GM_START,
                                       timeout=self.conv_timeout, log=self.log)
        self.log.info(f"  [board2-GM ] {'confirmed' if gm_ok else 'NOT confirmed'}")
        if not gm_ok: passed = False

        self.log.info(f"  Waiting for board1 FINE (timeout={self.conv_timeout:.0f}s)...")
        matched, elapsed, milestones = self._collect_conv()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if matched:
            self.log.info(f"  board1 FINE in {elapsed:.1f}s  ({ms_str})")
        else:
            self.log.info(f"  board1 FINE NOT reached  ({ms_str or 'none'})")
            passed = False

        if not passed:
            self._record("Phase 1: board1=FOL board2=GM", False, "convergence failed")
            return False

        # Collect Phase 1 baseline offsets  (board1 is FOL)
        interval = max(0.5, self.phase1_time / self.samples)
        self.log.info(
            f"  Collecting {self.samples} baseline samples over {self.phase1_time:.0f}s...")
        offsets = self._collect_offsets(self.b1_ser, self.samples, interval, "Phase1-FOL")
        if offsets:
            mean_v  = statistics.mean(offsets)
            stdev_v = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
            detail  = (f"FINE@{elapsed:.1f}s {ms_str}; "
                       f"n={len(offsets)} mean={mean_v:+.1f}ns stdev={stdev_v:.1f}ns")
        else:
            detail = "no offsets collected"
            passed = False

        self._record("Phase 1: board1=FOL board2=GM", passed, detail)
        return passed

    def step_stop_both(self):
        """Stop PTP on both boards."""
        self.log.info("\n--- Stop PTP on both boards ---")
        for label, ser in [("board1", self.b1_ser), ("board2", self.b2_ser)]:
            ser.reset_input_buffer()
            ser.write(b"ptp_mode off\r\n")
            matched, _, _ = wait_for_pattern(ser, RE_DISABLED,
                                             timeout=self.conv_timeout, log=self.log)
            self.log.info(f"  [{label}] ptp_mode off: {'confirmed' if matched else 'WARNING not confirmed'}")
        self._record("Stop PTP", True, "both stopped")

    def step_pause(self):
        """Wait pause_time seconds — both MAC clocks free-running (or frozen if bug present)."""
        self.log.info(f"\n--- Pause {self.pause_time:.0f} s ---")
        self.log.info(
            "  (board1 clock: calibrated TI, free-running)")
        self.log.info(
            "  (board2 clock: if bug present, MAC_TI=0 from deinit — FROZEN)")
        time.sleep(self.pause_time)
        self.log.info("  Pause complete.")

    def step_phase2(self) -> bool:
        """Phase 2 (role swap): board1=GRANDMASTER, board2=FOLLOWER. FOL first, then GM."""
        self.log.info(
            "\n--- Phase 2 (ROLE SWAP): board1=GM  board2=FOL ---")
        passed = True

        # Reopen board2 serial to clear any stale IO state
        self._reopen("b2_ser", self.b2_port)

        # FOL first (board2 — was the GM before)
        self.log.info("  [board2-FOL] ptp_mode follower")
        resp = send_command(self.b2_ser, "ptp_mode follower", self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [board2-FOL] confirmed")
        else:
            self.log.info(f"  [board2-FOL] no confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.b2_ser.reset_input_buffer()
        self._start_conv(self.b2_ser)

        # GM second (board1 — was the FOL before)
        self.log.info("  [board1-GM ] ptp_mode master")
        self.b1_ser.reset_input_buffer()
        self.b1_ser.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.b1_ser, RE_GM_START,
                                       timeout=self.conv_timeout, log=self.log)
        self.log.info(f"  [board1-GM ] {'confirmed' if gm_ok else 'NOT confirmed'}")
        if not gm_ok: passed = False

        self.log.info(f"  Waiting for board2 FINE (timeout={self.conv_timeout:.0f}s)...")
        matched, elapsed, milestones = self._collect_conv()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if matched:
            self.log.info(f"  board2 FINE in {elapsed:.1f}s  ({ms_str})")
        else:
            self.log.info(f"  board2 FINE NOT reached within {self.conv_timeout:.0f}s  ({ms_str or 'none'})")
            passed = False

        # Collect Phase 2 offsets  (board2 is now FOL)
        # Collect regardless of convergence status so we can see the initial offset
        interval = 0.5
        n_samples = self.samples
        self.log.info(f"  Collecting {n_samples} post-swap samples...")
        offsets = self._collect_offsets(self.b2_ser, n_samples, interval, "Phase2-FOL")
        if offsets:
            mean_v  = statistics.mean(offsets)
            stdev_v = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
            within  = [v for v in offsets if abs(v) <= OFFSET_THRESHOLD_NS]
            pct     = 100.0 * len(within) / len(offsets)
            status  = "FINE reached" if matched else "FINE NOT reached"
            detail  = (f"{status} @{elapsed:.1f}s ({ms_str}); "
                       f"n={len(offsets)} mean={mean_v:+.1f}ns stdev={stdev_v:.1f}ns "
                       f"within±{OFFSET_THRESHOLD_NS}ns={len(within)}/{len(offsets)} ({pct:.0f}%)")
            if abs(mean_v) > 1_000_000:  # > 1ms — clearly the frozen-clock bug
                self.log.info(
                    f"\n  *** LARGE INITIAL OFFSET DETECTED: mean={mean_v:+.1f}ns ***")
                self.log.info(
                    "  *** Root cause: PTP_GM_Deinit() wrote MAC_TI=0, "
                    "freezing board2 PTP clock ***")
        else:
            detail = "no offsets collected"
            passed = False

        self._record("Phase 2: board1=GM board2=FOL (role swap)", passed, detail)
        return passed

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 62)
        log.info("  PTP Role-Swap Test")
        log.info("=" * 62)
        log.info(f"Date           : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"board1 port    : {self.b1_port} ({self.b1_ip})")
        log.info(f"board2 port    : {self.b2_port} ({self.b2_ip})")
        log.info(f"Phase 1 time   : {self.phase1_time:.0f} s   (board1=FOL, board2=GM)")
        log.info(f"Pause time     : {self.pause_time:.0f} s   (both off)")
        log.info(f"Phase 2        : board1=GM, board2=FOL  (role swap)")
        log.info(f"Conv. timeout  : {self.conv_timeout:.0f} s")
        log.info(f"Samples        : {self.samples}")
        log.info("")

        self.connect()
        try:
            self.step_reset()
            if not self.step_ip():   return self._report(start_time)
            if not self.step_ping(): return self._report(start_time)
            if not self.step_phase1(): return self._report(start_time)
            self.step_stop_both()
            self.step_pause()
            self.step_phase2()
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
        for label, ser in [("board1", self.b1_ser), ("board2", self.b2_ser)]:
            if ser and ser.is_open:
                send_command(ser, "ptp_mode off", self.conv_timeout, self.log)
                self.log.info(f"  [{label}] PTP stopped")
        self.disconnect()

    def _record(self, name: str, passed: bool, detail: str):
        self.results.append((name, passed, detail))

    def _report(self, start_time: datetime.datetime) -> int:
        log = self.log
        log.info("\n" + "=" * 62)
        log.info("  PTP Role-Swap Test — Final Report")
        log.info("=" * 62)
        passed_count = 0
        for name, passed, detail in self.results:
            tag = "PASS" if passed else "FAIL"
            log.info(f"[{tag}] {name}")
            if detail:
                for part in detail.split("; "):
                    log.info(f"       {part}")
            if passed:
                passed_count += 1
        total   = len(self.results)
        overall = "PASS" if passed_count == total else "FAIL"
        log.info("")
        log.info(f"Overall: {overall} ({passed_count}/{total})")
        if log.log_file:
            log.info(f"Log   : {log.log_file}")
        return 0 if passed_count == total else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="PTP Role-Swap Test for T1S 100BaseT Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--board1-port",  default=DEFAULT_BOARD1_PORT,
                   help=f"board1 COM port (Phase1=FOL, Phase2=GM) default: {DEFAULT_BOARD1_PORT}")
    p.add_argument("--board2-port",  default=DEFAULT_BOARD2_PORT,
                   help=f"board2 COM port (Phase1=GM,  Phase2=FOL) default: {DEFAULT_BOARD2_PORT}")
    p.add_argument("--board1-ip",    default=DEFAULT_BOARD1_IP)
    p.add_argument("--board2-ip",    default=DEFAULT_BOARD2_IP)
    p.add_argument("--phase1-time",  type=float, default=DEFAULT_PHASE1_TIME,
                   help=f"seconds board2 runs as GM in Phase1 (default: {DEFAULT_PHASE1_TIME})")
    p.add_argument("--pause-time",   type=float, default=DEFAULT_PAUSE_TIME,
                   help=f"seconds both boards are off between phases (default: {DEFAULT_PAUSE_TIME})")
    p.add_argument("--samples",      type=int,   default=DEFAULT_SAMPLES)
    p.add_argument("--convergence-timeout", type=float, default=DEFAULT_CONV_TIMEOUT)
    p.add_argument("--log-file",     default=None)
    p.add_argument("--verbose",      action="store_true")
    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"ptp_role_swap_{ts}.log"

    log = Logger(log_file=log_file, verbose=args.verbose)
    test = PTPRoleSwapTest(
        board1_port  = args.board1_port,
        board2_port  = args.board2_port,
        board1_ip    = args.board1_ip,
        board2_ip    = args.board2_ip,
        netmask      = DEFAULT_NETMASK,
        phase1_time  = args.phase1_time,
        pause_time   = args.pause_time,
        samples      = args.samples,
        conv_timeout = args.convergence_timeout,
        log          = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
