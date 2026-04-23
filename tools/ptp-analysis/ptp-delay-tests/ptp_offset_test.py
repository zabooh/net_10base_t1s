#!/usr/bin/env python3
"""PTP Offset Stability Test
=============================

Measures how the follower clock offset behaves over time after convergence
to the FINE state by passively reading the FIR-filtered [V] FINE stream
(verbose mode) rather than polling ptp_status.

The [V] FINE line emitted by the firmware on every Sync event is:
  [V] FINE      t1=HH:MM:SS.nnnnnnnnn  t2=HH:MM:SS.nnnnnnnnn  off=  +NNN ns  delay=NNN ns\r

This gives the FIR-filtered servo offset (~50 ns range), which is the
true quality measure of the PTP lock.  ptp_status would return the raw
per-cycle offset (±30 µs PLCA jitter) which is not meaningful.

Test sequence:
  1. Reset & IP configuration
  2. Network connectivity (ping)
  3. Start PTP: FOL with verbose mode ("ptp_mode follower v"), then GM
  4. Wait for FOL to reach FINE state (stream monitoring)
  5. Warmup phase: discard first --warmup-samples [V] FINE samples
  6. Measurement phase: collect --samples [V] FINE lines from stream
  7. Validate:
       a) All samples collected in FINE state
       b) Mean offset within ±--max-mean-ns
       c) Max absolute offset within --max-abs-ns
       d) Standard deviation within --max-stdev-ns
       e) No sample exceeds --max-peak-ns  (single-sample spike limit)
       f) Mean path delay was measured (non-zero)
  8. Report PASS / FAIL with statistics and per-assertion breakdown.
  9. Write timestamped log file  ptp_offset_YYYYMMDD_HHMMSS.log

Usage:
    python ptp_offset_test.py --gm-port COM10 --fol-port COM8

Requirements:
    pip install pyserial
"""

import argparse
import datetime
import faulthandler
import re
import statistics
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
DEFAULT_SAMPLE_TIMEOUT      = 5.0    # seconds to wait for one [V] FINE line
DEFAULT_WARMUP_SAMPLES      = 5      # [V] FINE samples discarded after convergence
DEFAULT_SAMPLES             = 30     # measurement samples
DEFAULT_MAX_MEAN_NS         = 200    # ±200 ns mean offset limit  (FIR-filtered)
DEFAULT_MAX_ABS_NS          = 500    # 500 ns max absolute offset (FIR-filtered)
DEFAULT_MAX_STDEV_NS        = 150    # 150 ns standard deviation  (FIR-filtered)
DEFAULT_MAX_PEAK_NS         = 1000   # 1 µs single-sample spike limit

# Regex patterns
RE_IP_SET       = re.compile(r"Set ip address OK|IP address set to")
RE_PING_REPLY   = re.compile(r"Ping:.*reply.*from|Reply from")
RE_PING_DONE    = re.compile(r"Ping: done\.")
RE_FOL_START    = re.compile(r"PTP Follower enabled")
RE_GM_START     = re.compile(r"PTP Grandmaster enabled")
RE_MATCHFREQ    = re.compile(r"UNINIT->MATCHFREQ")
RE_HARD_SYNC    = re.compile(r"Hard sync completed")
RE_COARSE       = re.compile(r"PTP COARSE")
RE_FINE         = re.compile(r"PTP FINE")

# [V] FINE stream line  (firmware: "[V] %s  t1=...  t2=...  off=%+10d ns  delay=%lld ns\r")
# off=%+10d produces e.g. "      +150" or "    -16915" — \s* eats leading spaces, [+-] the sign.
RE_V_FINE = re.compile(
    r"\[V\] FINE\s+"
    r"t1=\S+\s+"
    r"t2=\S+\s+"
    r"off=\s*([+-]\d+)\s+ns\s+"
    r"delay=(-?\d+)\s+ns"
)


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


# ---------------------------------------------------------------------------
# Offset sample dataclass
# ---------------------------------------------------------------------------

class OffsetSample:
    __slots__ = ("elapsed", "offset_ns", "abs_ns", "mean_delay_ns", "in_fine")

    def __init__(self, elapsed: float, offset_ns: int, abs_ns: int,
                 mean_delay_ns: Optional[int], in_fine: bool):
        self.elapsed       = elapsed
        self.offset_ns     = offset_ns
        self.abs_ns        = abs_ns
        self.mean_delay_ns = mean_delay_ns
        self.in_fine       = in_fine


# ---------------------------------------------------------------------------
# Test agent
# ---------------------------------------------------------------------------

class PTPOffsetTestAgent:
    """Collects and validates PTP follower clock offset samples."""

    def __init__(
        self,
        gm_port: str,
        fol_port: str,
        gm_ip: str                = DEFAULT_GM_IP,
        fol_ip: str               = DEFAULT_FOL_IP,
        netmask: str              = DEFAULT_NETMASK,
        convergence_timeout: float = DEFAULT_CONVERGENCE_TIMEOUT,
        cmd_timeout: float        = DEFAULT_CMD_TIMEOUT,
        sample_timeout: float     = DEFAULT_SAMPLE_TIMEOUT,
        warmup_samples: int       = DEFAULT_WARMUP_SAMPLES,
        samples: int              = DEFAULT_SAMPLES,
        max_mean_ns: int          = DEFAULT_MAX_MEAN_NS,
        max_abs_ns: int           = DEFAULT_MAX_ABS_NS,
        max_stdev_ns: int         = DEFAULT_MAX_STDEV_NS,
        max_peak_ns: int          = DEFAULT_MAX_PEAK_NS,
        log: Logger               = None,
    ):
        self.gm_port_name        = gm_port
        self.fol_port_name       = fol_port
        self.gm_ip               = gm_ip
        self.fol_ip              = fol_ip
        self.netmask             = netmask
        self.convergence_timeout = convergence_timeout
        self.cmd_timeout         = cmd_timeout
        self.sample_timeout      = sample_timeout
        self.warmup_samples      = warmup_samples
        self.samples             = samples
        self.max_mean_ns         = max_mean_ns
        self.max_abs_ns          = max_abs_ns
        self.max_stdev_ns        = max_stdev_ns
        self.max_peak_ns         = max_peak_ns
        self.log                 = log or Logger()

        self.gm_ser:  Optional[serial.Serial] = None
        self.fol_ser: Optional[serial.Serial] = None

        self._step_results: list = []
        self._serial_buf: str   = ""   # leftover bytes between stream reads

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple]            = None

    # ------------------------------------------------------------------
    # Connection
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

        # "v" flag enables verbose [V] FINE stream output
        self.log.info("  [FOL] ptp_mode follower v")
        resp = send_command(self.fol_ser, "ptp_mode follower v", self.cmd_timeout, self.log)
        if RE_FOL_START.search(resp):
            parts.append("FOL start ok (verbose)")
        else:
            parts.append("FOL start FAIL")
            passed = False

        time.sleep(0.5)
        self.fol_ser.reset_input_buffer()
        self._serial_buf = ""
        self._start_convergence_thread()

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
            parts.append("FINE NOT reached")
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
    # [V] FINE stream reader
    # ------------------------------------------------------------------

    def _collect_vfine_samples(
        self, count: int, phase: str = "sample"
    ) -> List[OffsetSample]:
        """Read [V] FINE lines from FOL serial stream; collect *count* samples.

        The firmware emits one [V] FINE line per Sync event (typically ~1 Hz).
        Lines end with \\r (not \\n) — handled via CR/LF split.
        *self._serial_buf* carries over unconsumed bytes between calls.
        """
        samples: List[OffsetSample] = []
        total_timeout = count * self.sample_timeout
        deadline      = time.monotonic() + total_timeout
        start         = time.monotonic()
        n             = count

        while len(samples) < n and time.monotonic() < deadline:
            chunk = self.fol_ser.read(256)
            if chunk:
                self._serial_buf += chunk.decode("ascii", errors="replace")

            # Split on \r or \n — firmware uses \r for [V] lines, \r\n for others
            while True:
                cr = self._serial_buf.find('\r')
                nl = self._serial_buf.find('\n')
                if cr == -1 and nl == -1:
                    break
                sep  = min(x for x in (cr, nl) if x != -1)
                line = self._serial_buf[:sep]
                self._serial_buf = self._serial_buf[sep + 1:]

                m = RE_V_FINE.search(line)
                if m:
                    offset_ns = int(m.group(1))
                    delay_ns  = int(m.group(2))
                    s = OffsetSample(
                        elapsed       = time.monotonic() - start,
                        offset_ns     = offset_ns,
                        abs_ns        = abs(offset_ns),
                        mean_delay_ns = delay_ns,
                        in_fine       = True,
                    )
                    samples.append(s)
                    idx = len(samples)
                    self.log.info(
                        f"  [{phase} {idx:3d}/{n}]"
                        f"  t={s.elapsed:6.1f}s"
                        f"  off={offset_ns:+7d} ns"
                        f"  delay={delay_ns:,} ns"
                    )
                elif line.strip():
                    self.log.debug(f"  <- {line.rstrip()}")
            else:
                if not chunk:
                    time.sleep(0.01)

        return samples

    # ------------------------------------------------------------------
    # Step 4 — Warmup
    # ------------------------------------------------------------------

    def step_4_warmup(self) -> bool:
        """Discard first N [V] FINE samples to let the servo settle after FINE."""
        step = "Step 4: Warmup Phase"
        n = self.warmup_samples
        self.log.info(f"\n--- {step} ({n} [V] FINE samples discarded) ---")
        collected = self._collect_vfine_samples(n, phase="warmup")
        got = len(collected)
        ok  = got == n
        if got < n:
            self.log.info(f"  WARNING: only {got}/{n} warmup samples received (timeout)")
        self._record(step, ok, f"{got}/{n} warmup samples discarded")
        return ok

    # ------------------------------------------------------------------
    # Step 5 — Measurement
    # ------------------------------------------------------------------

    def step_5_measure(self) -> List[OffsetSample]:
        """Collect N [V] FINE samples from the stream."""
        step = "Step 5: Measurement Phase"
        n = self.samples
        self.log.info(f"\n--- {step} ({n} [V] FINE samples, ~{n}s) ---")
        collected = self._collect_vfine_samples(n, phase="meas")
        self._record(step, len(collected) > 0,
                     f"{len(collected)}/{n} samples collected")
        return collected

    # ------------------------------------------------------------------
    # Step 6 — Statistics
    # ------------------------------------------------------------------

    def step_6_print_statistics(self, samples: List[OffsetSample]):
        """Print detailed statistics table."""
        self.log.info("\n--- Step 6: Statistics ---")
        if not samples:
            self.log.info("  No samples to analyze.")
            return

        offsets = [s.offset_ns for s in samples]
        mean    = statistics.mean(offsets)
        stdev   = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
        mn      = min(offsets)
        mx      = max(offsets)
        peak    = max(abs(mn), abs(mx))
        fine_count = sum(1 for s in samples if s.in_fine)

        delays = [s.mean_delay_ns for s in samples if s.mean_delay_ns is not None]
        delay_mean = statistics.mean(delays) if delays else None
        delay_stdev = statistics.stdev(delays) if len(delays) > 1 else 0.0

        self.log.info(f"  Samples        : {len(offsets)}")
        self.log.info(f"  In FINE state  : {fine_count}/{len(offsets)}")
        self.log.info(f"  Mean offset    : {mean:+.1f} ns")
        self.log.info(f"  Stdev offset   : {stdev:.1f} ns")
        self.log.info(f"  Min offset     : {mn:+d} ns")
        self.log.info(f"  Max offset     : {mx:+d} ns")
        self.log.info(f"  Peak |offset|  : {peak} ns")
        if delay_mean is not None:
            self.log.info(f"  Mean delay     : {delay_mean:,.0f} ns  (stdev={delay_stdev:.0f} ns)")
        else:
            self.log.info(f"  Mean delay     : n/a")

        # Histogram (10 ns buckets, ±100 ns range, overflow buckets)
        self.log.info("")
        self.log.info("  Offset histogram (10 ns bins):")
        BUCKET_W = 10
        HALF = 10   # show ±100 ns in 20 buckets
        buckets = [0] * (2 * HALF + 1)
        underflow = 0
        overflow  = 0
        for v in offsets:
            idx = int(v / BUCKET_W)
            if idx < -HALF:
                underflow += 1
            elif idx > HALF:
                overflow += 1
            else:
                buckets[idx + HALF] += 1
        bar_max = max(buckets) if max(buckets) > 0 else 1
        if underflow:
            self.log.info(f"  < {-HALF*BUCKET_W:+4d} ns : {'#' * min(underflow, 40)} ({underflow})")
        for i, cnt in enumerate(buckets):
            lo = (i - HALF) * BUCKET_W
            hi = lo + BUCKET_W
            bar = '#' * int(cnt * 40 / bar_max) if cnt else ''
            self.log.info(f"  [{lo:+4d},{hi:+4d}) ns: {bar:<40s} ({cnt})")
        if overflow:
            self.log.info(f"  > {HALF*BUCKET_W:+4d} ns  : {'#' * min(overflow, 40)} ({overflow})")

        return mean, stdev, peak, fine_count, delay_mean

    # ------------------------------------------------------------------
    # Step 7 — Assertions
    # ------------------------------------------------------------------

    def step_7_validate(self, samples: List[OffsetSample]) -> bool:
        step = "Step 7: Validate Offset Assertions"
        self.log.info(f"\n--- {step} ---")
        all_ok = True

        if not samples:
            self._record(step, False, "no samples collected")
            return False

        offsets    = [s.offset_ns for s in samples]
        mean       = statistics.mean(offsets)
        stdev      = statistics.stdev(offsets) if len(offsets) > 1 else 0.0
        peak       = max(abs(v) for v in offsets)
        fine_count = sum(1 for s in samples if s.in_fine)
        delays     = [s.mean_delay_ns for s in samples if s.mean_delay_ns is not None]

        # ---- A: all samples collected from FINE state -------------------
        a_ok = fine_count == len(samples)
        self._record(
            "A: All samples in FINE state",
            a_ok,
            f"{fine_count}/{len(samples)} in FINE",
        )
        all_ok = all_ok and a_ok

        # ---- B: mean offset within ±max_mean_ns -------------------------
        b_ok = abs(mean) <= self.max_mean_ns
        self._record(
            "B: Mean offset within limit",
            b_ok,
            f"mean={mean:+.1f} ns  limit=±{self.max_mean_ns} ns",
        )
        all_ok = all_ok and b_ok

        # ---- C: max absolute offset within max_abs_ns -------------------
        max_abs = max(s.abs_ns for s in samples)
        c_ok = max_abs <= self.max_abs_ns
        self._record(
            "C: Max absolute offset within limit",
            c_ok,
            f"max_abs={max_abs} ns  limit={self.max_abs_ns} ns",
        )
        all_ok = all_ok and c_ok

        # ---- D: standard deviation within max_stdev_ns ------------------
        d_ok = stdev <= self.max_stdev_ns
        self._record(
            "D: Stdev within limit",
            d_ok,
            f"stdev={stdev:.1f} ns  limit={self.max_stdev_ns} ns",
        )
        all_ok = all_ok and d_ok

        # ---- E: no single sample exceeds max_peak_ns --------------------
        spikes = [v for v in offsets if abs(v) > self.max_peak_ns]
        e_ok = len(spikes) == 0
        self._record(
            "E: No single-sample spike above limit",
            e_ok,
            f"spikes={len(spikes)}  limit=±{self.max_peak_ns} ns"
            + (f"  values={sorted(spikes)}" if spikes else ""),
        )
        all_ok = all_ok and e_ok

        # ---- F: mean path delay was measured (non-zero) -----------------
        f_ok = len(delays) > 0 and any(d != 0 for d in delays)
        delay_str = f"{statistics.mean(delays):,.0f} ns" if delays else "n/a"
        self._record(
            "F: Mean path delay measured",
            f_ok,
            f"delay={delay_str}",
        )
        all_ok = all_ok and f_ok

        return all_ok

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def step_8_cleanup(self):
        self.log.info("\n--- Step 8: Cleanup ---")
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

            self.step_4_warmup()
            samples = self.step_5_measure()
            self.step_6_print_statistics(samples)
            if not self.step_7_validate(samples):
                overall = False

        finally:
            try:
                self.step_8_cleanup()
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
        description="PTP follower offset stability test",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT,  help="GM  serial port")
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT, help="FOL serial port")
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP,    help="GM  IP address")
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP,   help="FOL IP address")
    p.add_argument("--netmask",  default=DEFAULT_NETMASK,  help="Network mask")
    p.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    p.add_argument(
        "--convergence-timeout", type=float, default=DEFAULT_CONVERGENCE_TIMEOUT,
        metavar="S", help="Seconds to wait for FINE state"
    )
    p.add_argument(
        "--sample-timeout", type=float, default=DEFAULT_SAMPLE_TIMEOUT,
        metavar="S", help="Max seconds to wait for one [V] FINE line"
    )
    p.add_argument(
        "--warmup-samples", type=int, default=DEFAULT_WARMUP_SAMPLES,
        metavar="N", help="Samples discarded after FINE convergence"
    )
    p.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES,
        metavar="N", help="Number of measurement samples"
    )
    p.add_argument(
        "--max-mean-ns", type=int, default=DEFAULT_MAX_MEAN_NS,
        metavar="NS", help="Max allowed |mean offset| in ns (assertion B)"
    )
    p.add_argument(
        "--max-abs-ns", type=int, default=DEFAULT_MAX_ABS_NS,
        metavar="NS", help="Max allowed absolute offset in ns (assertion C)"
    )
    p.add_argument(
        "--max-stdev-ns", type=int, default=DEFAULT_MAX_STDEV_NS,
        metavar="NS", help="Max allowed offset standard deviation in ns (assertion D)"
    )
    p.add_argument(
        "--max-peak-ns", type=int, default=DEFAULT_MAX_PEAK_NS,
        metavar="NS", help="Max allowed single-sample |offset| in ns (assertion E)"
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
    log_path = args.log_file or f"ptp_offset_{ts}.log"
    log = Logger(log_file=log_path, verbose=args.verbose)

    log.info("=" * 60)
    log.info("PTP Follower Offset Stability Test")
    log.info("=" * 60)
    log.info(f"  GM port            : {args.gm_port}")
    log.info(f"  FOL port           : {args.fol_port}")
    log.info(f"  Warmup samples     : {args.warmup_samples}  (discarded after FINE)")
    log.info(f"  Measure samples    : {args.samples}  (~{args.samples}s, one per Sync)")
    log.info(f"  Max mean offset    : ±{args.max_mean_ns} ns  (FIR-filtered)")
    log.info(f"  Max abs offset     : {args.max_abs_ns} ns  (FIR-filtered)")
    log.info(f"  Max stdev          : {args.max_stdev_ns} ns  (FIR-filtered)")
    log.info(f"  Max peak           : {args.max_peak_ns} ns  (FIR-filtered)")
    log.info(f"  Log file           : {log_path}")
    log.info("")

    agent = PTPOffsetTestAgent(
        gm_port             = args.gm_port,
        fol_port            = args.fol_port,
        gm_ip               = args.gm_ip,
        fol_ip              = args.fol_ip,
        netmask             = args.netmask,
        convergence_timeout = args.convergence_timeout,
        sample_timeout      = args.sample_timeout,
        warmup_samples      = args.warmup_samples,
        samples             = args.samples,
        max_mean_ns         = args.max_mean_ns,
        max_abs_ns          = args.max_abs_ns,
        max_stdev_ns        = args.max_stdev_ns,
        max_peak_ns         = args.max_peak_ns,
        log                 = log,
    )

    passed = agent.run()
    log.close()
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
