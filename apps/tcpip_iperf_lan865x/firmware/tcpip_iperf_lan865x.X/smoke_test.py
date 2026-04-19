#!/usr/bin/env python3
"""Smoke Test — broad functional regression guard
================================================

Exercises every CLI command in app.c plus the critical end-to-end path
(PTP boot -> FINE lock -> tfuture arm/fire).  Designed to be run after
every refactoring commit to catch regressions early.

Target runtime: < 3 minutes (with --no-reset: ~45 s).

Each check returns PASS/FAIL.  Exit code 0 on all-pass, 1 on any fail.

Coverage:
  Phase 1 — Boot + PTP FINE (skipped with --no-reset)
  Phase 2 — CLI round-trip for all 26 commands (read-only / idempotent)
  Phase 3 — End-to-end: tfuture fires + PTP offset sanity

Usage:
    python smoke_test.py --gm-port COM8 --fol-port COM10
    python smoke_test.py --no-reset                 # skip reset+FINE
    python smoke_test.py --abort-on-fail            # stop at first failure
"""

import argparse
import datetime
import re
import statistics
import sys
import time
from typing import List, Tuple, Callable

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Regex patterns for command responses
# ---------------------------------------------------------------------------

RE_LAN_READ_OK      = re.compile(r"LAN865X Read OK:\s+Addr=0x[0-9A-Fa-f]+\s+Value=0x([0-9A-Fa-f]+)")
RE_PTP_MODE         = re.compile(r"PTP mode:\s+(master|follower|off)")
RE_PTP_STATUS       = re.compile(r"PTP mode\s*:\s*(master|follower|off)")
RE_PTP_TIME         = re.compile(r"ptp_time:\s+\d{2}:\d{2}:\d{2}\.\d+\s+drift=")
RE_PTP_TIME_INVALID = re.compile(r"ptp_time:\s+not valid")
RE_INTERVAL_SET     = re.compile(r"Sync interval set to")
RE_OFFSET_LINE      = re.compile(r"Offset:\s+([+-]?\d+)\s+ns\s+\(abs:\s+(\d+)\s+ns\)")
RE_TRACE_ONOFF      = re.compile(r"(PTP trace (enabled|disabled)|SW-NTP trace (enabled|disabled))")
RE_DST_MODE         = re.compile(r"PTP dst:\s+(broadcast|multicast)")
RE_CLK_SET_OK       = re.compile(r"clk_set ok")
RE_CLK_GET          = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")
RE_OFFSET_RESET     = re.compile(r"ptp_offset:\s+reset")
RE_SW_NTP_MODE      = re.compile(r"SW-NTP mode:\s+(off|master|follower)")
RE_SW_NTP_STATUS    = re.compile(r"SW-NTP mode\s*:\s+(off|master|follower)")
RE_SW_NTP_POLL      = re.compile(r"SW-NTP poll interval(?: set to)?:?\s+(\d+)\s+ms")
RE_SW_NTP_RESET     = re.compile(r"sw_ntp_offset:\s+reset")
RE_TFUT_STATE       = re.compile(r"tfuture state\s*:\s+(idle|pending|fired)")
RE_TFUT_AT_OK       = re.compile(r"tfuture_at OK")
RE_TFUT_AT_FAIL     = re.compile(r"tfuture_at FAIL")
RE_TFUT_IN_OK       = re.compile(r"tfuture_in OK")
RE_TFUT_CANCEL      = re.compile(r"tfuture cancelled")
RE_TFUT_RESET       = re.compile(r"tfuture:\s+trace reset")
RE_TFUT_DUMP_START  = re.compile(r"tfuture_dump:\s+start count=(\d+)")
RE_TFUT_DUMP_END    = re.compile(r"tfuture_dump:\s+end")
RE_LOOP_STATS       = re.compile(r"(loop_stats|Main loop|subsystem)", re.IGNORECASE)

# End-to-end sanity gates (generous bounds — tight gates live in dedicated tests)
GATE_FOL_SELF_JITTER_NS = 200_000   # 200 µs
GATE_PTP_OFFSET_ABS_NS  = 50_000    # 50 µs after FINE lock + settle

# ---------------------------------------------------------------------------
# Check framework
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name   = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        base   = f"  [{status}] {self.name}"
        return base + (f"  — {self.detail}" if self.detail else "")


class SmokeRunner:
    def __init__(self, ser_gm, ser_fol, log: Logger, abort_on_fail: bool):
        self.ser_gm  = ser_gm
        self.ser_fol = ser_fol
        self.log     = log
        self.abort   = abort_on_fail
        self.results: List[Result] = []

    def check(self, name: str, fn: Callable[[], Tuple[bool, str]]):
        try:
            passed, detail = fn()
        except Exception as exc:
            passed, detail = False, f"exception: {exc}"
        r = Result(name, passed, detail)
        self.results.append(r)
        self.log.info(str(r))
        if not passed and self.abort:
            raise SystemExit(self.summary() or 1)

    def summary(self) -> int:
        n_pass = sum(1 for r in self.results if r.passed)
        n_fail = sum(1 for r in self.results if not r.passed)
        self.log.info("")
        self.log.info("=" * 70)
        self.log.info(f"  Summary: {n_pass} PASS / {n_fail} FAIL  (total {len(self.results)})")
        self.log.info("=" * 70)
        if n_fail:
            self.log.info("  Failures:")
            for r in self.results:
                if not r.passed:
                    self.log.info(f"    - {r.name}: {r.detail}")
        return 0 if n_fail == 0 else 1


# ---------------------------------------------------------------------------
# Generic response-pattern helper
# ---------------------------------------------------------------------------

def expect(ser, cmd: str, pattern: re.Pattern, log: Logger,
           timeout: float = 2.0) -> Tuple[bool, str]:
    resp = send_command(ser, cmd, timeout, log)
    m = pattern.search(resp)
    if m:
        return True, m.group(0).strip()
    snippet = resp.strip().replace("\r\n", " | ")[:120]
    return False, f"no match for /{pattern.pattern}/; got: {snippet!r}"


# ---------------------------------------------------------------------------
# Phase 1 — Boot + PTP FINE
# ---------------------------------------------------------------------------

def phase1_boot_and_fine(run: SmokeRunner, args):
    log = run.log
    log.info("")
    log.info("--- Phase 1: Boot + PTP FINE ---")

    run.check("GM responsive",
              lambda: expect(run.ser_gm, "ptp_mode", re.compile(r"PTP mode:"), log, 3.0))
    run.check("FOL responsive",
              lambda: expect(run.ser_fol, "ptp_mode", re.compile(r"PTP mode:"), log, 3.0))

    log.info("  Issuing reset on both boards...")
    send_command(run.ser_gm,  "reset", 3.0, log)
    send_command(run.ser_fol, "reset", 3.0, log)
    time.sleep(8.0)

    run.check("GM setip",
              lambda: expect(run.ser_gm,  f"setip eth0 {args.gm_ip} {args.netmask}",
                             RE_IP_SET, log, 3.0))
    run.check("FOL setip",
              lambda: expect(run.ser_fol, f"setip eth0 {args.fol_ip} {args.netmask}",
                             RE_IP_SET, log, 3.0))

    send_command(run.ser_fol, "ptp_mode follower", 3.0, log)
    time.sleep(0.3)
    run.ser_fol.reset_input_buffer()
    send_command(run.ser_gm,  "ptp_mode master", 3.0, log)

    matched, elapsed, _ = wait_for_pattern(
        run.ser_fol, RE_FINE, args.conv_timeout, log,
        extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                        "HARD_SYNC": RE_HARD_SYNC,
                        "COARSE":    RE_COARSE},
        live_log=False)
    run.check("FOL reaches PTP FINE",
              lambda: (matched, f"after {elapsed:.1f}s" if matched
                       else f"timeout after {elapsed:.1f}s"))

    if matched:
        log.info(f"  Settling {args.settle_s:.0f} s for drift filter...")
        time.sleep(args.settle_s)


# ---------------------------------------------------------------------------
# Phase 2 — CLI round-trip for all 26 commands
# ---------------------------------------------------------------------------

def phase2_cli_coverage(run: SmokeRunner):
    log = run.log
    log.info("")
    log.info("--- Phase 2: CLI coverage ---")
    gm, fol = run.ser_gm, run.ser_fol

    # lan_regs_cli (read-only — address 0x000A0094 is OA_ID/STDCAP; any addr
    # that the driver accepts is fine, the point is the CLI round-trip).
    run.check("lan_read returns OK",
              lambda: expect(fol, "lan_read 0x00000000", RE_LAN_READ_OK, log, 3.0))
    # lan_write deliberately NOT exercised (would mutate LAN865x state).

    # ptp_cli — read-only / idempotent commands only
    run.check("ptp_mode (query)",
              lambda: expect(fol, "ptp_mode", RE_PTP_MODE, log))
    run.check("ptp_status",
              lambda: expect(fol, "ptp_status", RE_PTP_STATUS, log))
    run.check("ptp_time (FOL)",
              lambda: expect(fol, "ptp_time",
                             re.compile(RE_PTP_TIME.pattern + "|" + RE_PTP_TIME_INVALID.pattern),
                             log))
    run.check("ptp_interval set",
              lambda: expect(gm,  "ptp_interval 1000", RE_INTERVAL_SET, log))
    run.check("ptp_offset",
              lambda: expect(fol, "ptp_offset", RE_OFFSET_LINE, log))
    run.check("ptp_trace on",
              lambda: expect(fol, "ptp_trace on",  RE_TRACE_ONOFF, log))
    run.check("ptp_trace off",
              lambda: expect(fol, "ptp_trace off", RE_TRACE_ONOFF, log))
    run.check("ptp_dst (query)",
              lambda: expect(gm,  "ptp_dst", RE_DST_MODE, log))
    # ptp_reset skipped: would break the FINE state needed for Phase 3.

    # clk_set / clk_get  — clk_set would disturb PTP; only test clk_get here.
    run.check("clk_get (GM)",
              lambda: expect(gm,  "clk_get", RE_CLK_GET, log))
    run.check("clk_get (FOL)",
              lambda: expect(fol, "clk_get", RE_CLK_GET, log))

    # loop_stats_cli
    run.check("loop_stats",
              lambda: expect(fol, "loop_stats", RE_LOOP_STATS, log, 3.0))
    run.check("loop_stats reset",
              lambda: expect(fol, "loop_stats reset",
                             re.compile(r"loop_stats:\s+reset"), log))

    # ptp_offset_trace
    run.check("ptp_offset_reset",
              lambda: expect(fol, "ptp_offset_reset", RE_OFFSET_RESET, log))
    run.check("ptp_offset_dump",
              lambda: expect(fol, "ptp_offset_dump",
                             re.compile(r"(ptp_offset|no samples|\d+\s+\d)"), log, 5.0))

    # sw_ntp_cli
    run.check("sw_ntp_mode (query)",
              lambda: expect(fol, "sw_ntp_mode", RE_SW_NTP_MODE, log))
    run.check("sw_ntp_status",
              lambda: expect(fol, "sw_ntp_status", RE_SW_NTP_STATUS, log))
    run.check("sw_ntp_poll (query)",
              lambda: expect(fol, "sw_ntp_poll", RE_SW_NTP_POLL, log))
    run.check("sw_ntp_poll set",
              lambda: expect(fol, "sw_ntp_poll 1000", RE_SW_NTP_POLL, log))
    run.check("sw_ntp_trace on",
              lambda: expect(fol, "sw_ntp_trace on",  RE_TRACE_ONOFF, log))
    run.check("sw_ntp_trace off",
              lambda: expect(fol, "sw_ntp_trace off", RE_TRACE_ONOFF, log))
    run.check("sw_ntp_offset_reset",
              lambda: expect(fol, "sw_ntp_offset_reset", RE_SW_NTP_RESET, log))
    run.check("sw_ntp_offset_dump",
              lambda: expect(fol, "sw_ntp_offset_dump",
                             re.compile(r"(sw_ntp_offset|no samples|\d+)"), log, 5.0))

    # tfuture_cli
    run.check("tfuture_status (idle)",
              lambda: expect(fol, "tfuture_status", RE_TFUT_STATE, log))
    run.check("tfuture_reset",
              lambda: expect(fol, "tfuture_reset", RE_TFUT_RESET, log))
    run.check("tfuture_dump (empty OK)",
              lambda: expect(fol, "tfuture_dump", RE_TFUT_DUMP_END, log, 5.0))
    run.check("tfuture_cancel (idempotent)",
              lambda: expect(fol, "tfuture_cancel", RE_TFUT_CANCEL, log))


# ---------------------------------------------------------------------------
# Phase 3 — End-to-end: tfuture fires + PTP offset sanity
# ---------------------------------------------------------------------------

def phase3_end_to_end(run: SmokeRunner, rounds: int = 5):
    log = run.log
    log.info("")
    log.info("--- Phase 3: End-to-end ---")
    gm, fol = run.ser_gm, run.ser_fol

    # PTP offset should be small after FINE + settle
    def check_offset():
        resp = send_command(fol, "ptp_offset", 2.0, log)
        m = RE_OFFSET_LINE.search(resp)
        if not m:
            return False, f"no offset line: {resp[:80]!r}"
        abs_ns = int(m.group(2))
        ok = abs_ns < GATE_PTP_OFFSET_ABS_NS
        return ok, f"|offset|={abs_ns} ns  (gate {GATE_PTP_OFFSET_ABS_NS})"
    run.check("PTP offset < 50 µs", check_offset)

    # tfuture: arm both boards on identical target, fire, check FOL self-jitter
    send_command(gm,  "tfuture_reset", 2.0, log)
    send_command(fol, "tfuture_reset", 2.0, log)

    deltas_fol: List[int] = []
    arm_fails  = 0
    for i in range(rounds):
        resp = send_command(gm, "clk_get", 2.0, log)
        m    = RE_CLK_GET.search(resp)
        if not m:
            arm_fails += 1
            continue
        target = int(m.group(1)) + 2 * 1_000_000_000     # 2 s lead
        og = bool(RE_TFUT_AT_OK.search(send_command(gm,  f"tfuture_at {target}", 2.0, log)))
        of = bool(RE_TFUT_AT_OK.search(send_command(fol, f"tfuture_at {target}", 2.0, log)))
        if not (og and of):
            arm_fails += 1
        time.sleep(2.3)

    # Dump FOL trace, pick deltas
    fol.reset_input_buffer()
    fol.write(b"tfuture_dump\r\n")
    buf, saw_start, saw_end = "", False, False
    dl = time.monotonic() + 10.0
    while time.monotonic() < dl and not saw_end:
        c = fol.read(4096)
        if not c:
            time.sleep(0.02)
            continue
        buf += c.decode("ascii", errors="replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if line.startswith("tfuture_dump: start"):
                saw_start = True
                continue
            if line.startswith("tfuture_dump: end"):
                saw_end = True
                break
            if not saw_start:
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    deltas_fol.append(int(parts[2]))
                except ValueError:
                    pass

    run.check(f"tfuture arm success ({rounds} rounds)",
              lambda: (arm_fails == 0,
                       f"{rounds - arm_fails}/{rounds} rounds armed"))

    def check_jitter():
        if not deltas_fol:
            return False, "no deltas captured"
        med = statistics.median(deltas_fol)
        ok  = abs(med) < GATE_FOL_SELF_JITTER_NS
        return ok, (f"median={med:+d} ns  n={len(deltas_fol)}  "
                    f"(gate {GATE_FOL_SELF_JITTER_NS})")
    run.check("FOL self-jitter median < 200 µs", check_jitter)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",        default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",       default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",          default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",         default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",        default=DEFAULT_NETMASK)
    p.add_argument("--conv-timeout",   default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s",       default=5.0, type=float)
    p.add_argument("--rounds",         default=5, type=int,
                   help="tfuture fire rounds in Phase 3")
    p.add_argument("--no-reset",       action="store_true",
                   help="skip Phase 1 reset+FINE; assume PTP already running")
    p.add_argument("--abort-on-fail",  action="store_true",
                   help="stop at first FAIL")
    p.add_argument("--log-file",       default=None)
    p.add_argument("--verbose",        action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Logger(log_file=args.log_file or f"smoke_test_{ts}.log",
                 verbose=args.verbose)
    log.info("=" * 70)
    log.info("  Smoke Test — broad functional regression guard")
    log.info("=" * 70)
    log.info(f"  GM port : {args.gm_port}   FOL port: {args.fol_port}")
    log.info(f"  Reset   : {not args.no_reset}")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        print(f"ERROR: cannot open port: {exc}")
        return 1

    run = SmokeRunner(ser_gm, ser_fol, log, args.abort_on_fail)
    try:
        if not args.no_reset:
            phase1_boot_and_fine(run, args)
        phase2_cli_coverage(run)
        phase3_end_to_end(run, args.rounds)
    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except: pass
        rc = run.summary()
        log.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
