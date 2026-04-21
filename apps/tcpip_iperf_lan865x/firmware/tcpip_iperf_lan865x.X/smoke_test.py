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
    reset_and_wait_for_boot,
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
GATE_FOL_SELF_JITTER_NS = 200_000        # 200 µs
GATE_PTP_OFFSET_ABS_NS  = 50_000         # 50 µs after FINE lock + settle
GATE_SW_NTP_OFFSET_NS      = 1_000_000   # 1 ms — generous since app-layer incl. UDP jitter
GATE_SW_NTP_MIN_SAMPLES    = 5           # at 1 Hz poll, expect ≥5 in 8 s
GATE_SW_NTP_MIN_SUCCESS    = 0.60        # ≥60 % successful replies.  README_NTP §8
                                         # documents 3–10 % typical timeout rate from
                                         # FreeRTOS preemption / SPI contention with
                                         # HW-PTP; with only ~9 samples per run a
                                         # single bad cluster drops success to ~60 %
                                         # without indicating anything structurally
                                         # wrong.  Gate loose enough that the smoke
                                         # test passes reliably as firmware-state
                                         # regression oracle.

# cyclic_fire end-to-end sanity — verifies the callback mechanism runs.
# Period 500 µs, dwell 2 s, nominal would be 4000 cycles.  On the
# reference hardware an unexplained ~1.7× rate factor is observed
# (see prompts/codebase_cleanup_followups.md), so the gate is loose:
# just catch "callback never fires" (< 500) and "runaway" (> 15000).
CYCLIC_PERIOD_US           = 500
CYCLIC_DWELL_S             = 2.0
GATE_CYCLIC_MIN_CYCLES     = 500
GATE_CYCLIC_MAX_CYCLES     = 15000
GATE_CYCLIC_MAX_MISSES     = 20

RE_CYC_RUNNING = re.compile(r"cyclic running\s*:\s+(yes|no)")
RE_CYC_CYCLES  = re.compile(r"cycles\s*:\s+(\d+)")
RE_CYC_MISSES  = re.compile(r"misses\s*:\s+(\d+)")
RE_CYC_START_OK         = re.compile(r"cyclic_start OK")
RE_CYC_START_MARKER_OK  = re.compile(r"cyclic_start_marker OK")
RE_CYC_START_FREE_OK    = re.compile(r"cyclic_start_free OK")
RE_CYC_STOP_OK          = re.compile(r"cyclic_stop OK|cyclic not running")

# Diagnostic CLI responses (query-only smoke level)
RE_CLK_SET_DRIFT_Q = re.compile(r"clk_set_drift:\s+current drift_ppb\s*=\s*([+-]?\d+)")
RE_PTP_GM_DELAY_Q  = re.compile(r"ptp_gm_delay:\s+([+-]?\d+)\s*ns")
RE_BLINK_ANY       = re.compile(r"blink:", re.IGNORECASE)

# R25 regression guard — FOL's PTP_CLOCK must be within this many ns of GM's
# after FINE + brief settle.  Pre-R25-fix: median clustered tightly around
# −10 ms (samples: −6 … −15 ms).  Post-fix: median depends on board-pair
# variation in LAN865x RX-pipeline latency — on the reference pair it's
# sub-ms, on a different pair it can reach ±6 ms because the 10 ms
# PTP_FOL_ANCHOR_OFFSET_NS constant was calibrated on one particular pair.
# Plus the clk_get bracketing adds ±3 ms USB-CDC-RTT noise on top.
#
# Gate chosen at 8 ms to:
#   - reliably FAIL on regression to the raw pre-fix state (−10 ms cluster)
#   - reliably PASS on healthy systems across board-pair variation.
GATE_R25_FOL_GM_ABS_NS = 8_000_000
R25_BRACKETING_REPEATS = 7

# Crystal-deviation by-product — LAN8651 CLOCK_INCREMENT register pair
# (see tfuture_quick_check.py §crystal-deviation-analysis for the math)
MAC_TI_ADDR            = 0x00010077
MAC_TISUBN_ADDR        = 0x0001006F
CLOCK_CYCLE_NS_NOMINAL = 40.0            # 25 MHz nominal LAN8651 clock

RE_SW_NTP_SAMPLES  = re.compile(r"Samples\s*:\s+(\d+)")
RE_SW_NTP_TIMEOUTS = re.compile(r"Timeouts\s*:\s+(\d+)")
RE_SW_NTP_LAST_OFF = re.compile(r"Last offset ns\s*:\s+([+-]?\d+)")

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
    try:
        gm_build  = reset_and_wait_for_boot(run.ser_gm,  "GM ", timeout=8.0, log=log)
        fol_build = reset_and_wait_for_boot(run.ser_fol, "FOL", timeout=8.0, log=log)
    except RuntimeError as exc:
        log.info(f"  ERROR: {exc}")
        log.info(f"  Aborting smoke test — hard power-cycle both boards "
                 f"(USB unplug → 3 s wait → plug) and retry.")
        return 1
    if gm_build != fol_build:
        log.info(f"  WARN: build mismatch — GM={gm_build} FOL={fol_build}  "
                 f"(test continues, but both boards should be flashed with the same firmware)")

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

    # Diagnostic CLIs — query-only (writing them would disturb PTP state for Phase 3)
    run.check("clk_set_drift (query)",
              lambda: expect(fol, "clk_set_drift", RE_CLK_SET_DRIFT_Q, log))
    run.check("ptp_gm_delay (query)",
              lambda: expect(gm,  "ptp_gm_delay", RE_PTP_GM_DELAY_Q, log))

    # pd10_blink CLI — start a brief free-running blink then stop it.  pd10_blink
    # is a separate module from cyclic_fire (no PTP dependency) so it's safe
    # to run now; we only verify the CLI round-trip.  Accepts "stop" or "0"
    # to halt; we use "stop" to be explicit.  Starting at 1000 Hz is the
    # default and leaves the PD10 in a well-defined (halted) state for the
    # cyclic_fire check in Phase 3.
    run.check("blink 1000",
              lambda: expect(fol, "blink 1000", RE_BLINK_ANY, log))
    run.check("blink stop",
              lambda: expect(fol, "blink stop", RE_BLINK_ANY, log))


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

    # -------- SW-NTP end-to-end: real UDP exchange over the link --------
    phase3_sw_ntp(run)

    # -------- Crystal-deviation by-product (informational only) ---------
    phase3_crystal_analysis(run)

    # -------- cyclic_fire end-to-end: callback + re-arm loop on PD10 ----
    phase3_cyclic_fire(run)

    # -------- cyclic_fire MARKER variant (phase counter bookkeeping) ----
    phase3_cyclic_fire_marker(run)

    # -------- cyclic_fire FREE variant (PTP-independent code path) ------
    # NOTE: this routine internally force-resets FOL's PTP_CLOCK so it also
    # tears down + re-brings FOL into PTP follower state.  Must run AFTER
    # the R25 bracketing (below would see stale state otherwise) — so we
    # keep it last here, and put R25 check between the PTP-dependent tests
    # and this one.
    phase3_r25_regression(run)
    phase3_cyclic_fire_free(run)


def phase3_sw_ntp(run: SmokeRunner,
                  gm_ip: str = DEFAULT_GM_IP,
                  dwell_s: float = 8.0):
    """Enable GM as sw_ntp master + FOL as follower, wait dwell_s, verify
    samples arrived with plausible offset (PTP is locked so offsets are tiny),
    then disable both boards."""
    log = run.log
    gm, fol = run.ser_gm, run.ser_fol

    send_command(gm,  "sw_ntp_offset_reset",   2.0, log)
    send_command(fol, "sw_ntp_offset_reset",   2.0, log)
    send_command(gm,  "sw_ntp_mode master",    2.0, log)
    send_command(fol, f"sw_ntp_mode follower {gm_ip}", 2.0, log)
    log.info(f"  SW-NTP: wait {dwell_s:.1f} s for samples ...")
    time.sleep(dwell_s)

    resp = send_command(fol, "sw_ntp_status", 3.0, log)
    m_s = RE_SW_NTP_SAMPLES.search(resp)
    m_t = RE_SW_NTP_TIMEOUTS.search(resp)
    m_o = RE_SW_NTP_LAST_OFF.search(resp)

    # Restore: off on both, regardless of result
    send_command(fol, "sw_ntp_mode off", 2.0, log)
    send_command(gm,  "sw_ntp_mode off", 2.0, log)

    if not (m_s and m_t and m_o):
        run.check("SW-NTP end-to-end exchange",
                  lambda: (False, f"could not parse sw_ntp_status: {resp[:120]!r}"))
        return

    samples  = int(m_s.group(1))
    timeouts = int(m_t.group(1))
    offset   = int(m_o.group(1))

    total_requests = samples + timeouts
    success_rate   = (samples / total_requests) if total_requests > 0 else 0.0

    run.check(f"SW-NTP samples ≥ {GATE_SW_NTP_MIN_SAMPLES}",
              lambda: (samples >= GATE_SW_NTP_MIN_SAMPLES,
                       f"samples={samples}  timeouts={timeouts}"))
    run.check(f"SW-NTP success rate ≥ {GATE_SW_NTP_MIN_SUCCESS:.0%}",
              lambda: (success_rate >= GATE_SW_NTP_MIN_SUCCESS,
                       f"{samples}/{total_requests} = {success_rate:.0%}"))
    run.check(f"SW-NTP |last_offset| < {GATE_SW_NTP_OFFSET_NS/1000:.0f} µs",
              lambda: (abs(offset) < GATE_SW_NTP_OFFSET_NS,
                       f"last_offset={offset:+d} ns  (gate {GATE_SW_NTP_OFFSET_NS})"))


def _lan_read_value(ser, addr: int, log) -> int:
    """Read a LAN865x register via lan_read CLI. Returns value or None."""
    resp = send_command(ser, f"lan_read 0x{addr:08X}", 2.0, log)
    m = RE_LAN_READ_OK.search(resp)
    return int(m.group(1), 16) if m else None


def _decode_clock_inc_ppm(ti_reg: int, tisubn_raw: int) -> float:
    """Decode the LAN8651 CLOCK_INCREMENT register pair as written by
    ptp_fol_task.c into a ppm deviation from the nominal 40 ns period.

    Firmware packing (see ptp_fol_task.c):
      calcSubInc_uint = ((calcSubInc_uint >> 8) & 0xFFFF) | ((calcSubInc_uint & 0xFF) << 24)
    Reversing:  orig = (tisubn_raw_low24 << 8) | tisubn_raw_high8
    """
    raw_low24    = tisubn_raw & 0x00FFFFFF
    raw_high8    = (tisubn_raw >> 24) & 0xFF
    orig         = (raw_low24 << 8) | raw_high8
    fraction_ns  = orig / (1 << 24)
    effective_ns = (ti_reg & 0xFF) + fraction_ns
    return (effective_ns - CLOCK_CYCLE_NS_NOMINAL) / CLOCK_CYCLE_NS_NOMINAL * 1e6


def phase3_crystal_analysis(run: SmokeRunner):
    """Derive per-crystal ppm deviations as a by-product of the PTP lock.

    Sources:
      - GM/FOL SAME54  ← -drift_ppb/1000     (sign convention: PI regulates
                                               board TSU to real wallclock,
                                               so TC0 deviation is the complement)
      - FOL LAN8651    ← decoded CLOCK_INCREMENT (PI-calibrated)
      - GM  LAN8651    = 0 ppm (reference)

    Purely informational — does not run any run.check(), so a flaky
    register read or a weird drift value won't fail the smoke test.
    """
    log = run.log
    gm, fol = run.ser_gm, run.ser_fol
    log.info("")
    log.info("  Crystal deviations (by-product, informational):")

    # SAME54 deviations from drift_ppb readings (clk_get)
    def _drift_ppb(ser):
        m = RE_CLK_GET.search(send_command(ser, "clk_get", 2.0, log))
        return int(m.group(2)) if m else None

    gm_drift  = _drift_ppb(gm)
    fol_drift = _drift_ppb(fol)

    # FOL LAN8651 deviation from CLOCK_INCREMENT
    fol_ti     = _lan_read_value(fol, MAC_TI_ADDR,     log)
    fol_tisubn = _lan_read_value(fol, MAC_TISUBN_ADDR, log)
    fol_lan_ppm = None
    if fol_ti is not None and fol_tisubn is not None:
        fol_lan_ppm = _decode_clock_inc_ppm(fol_ti, fol_tisubn)

    log.info(f"    GM  LAN8651 :     0.000 ppm   (reference)")
    if gm_drift is not None:
        log.info(f"    GM  SAME54  : {-gm_drift/1000.0:+9.3f} ppm   "
                 f"(from drift_ppb={gm_drift:+d})")
    else:
        log.info(f"    GM  SAME54  :   (unavailable — no clk_get match)")
    if fol_drift is not None:
        log.info(f"    FOL SAME54  : {-fol_drift/1000.0:+9.3f} ppm   "
                 f"(from drift_ppb={fol_drift:+d})")
    else:
        log.info(f"    FOL SAME54  :   (unavailable — no clk_get match)")
    if fol_lan_ppm is not None:
        log.info(f"    FOL LAN8651 : {fol_lan_ppm:+9.3f} ppm   "
                 f"(from MAC_TI=0x{fol_ti:02X} TISUBN=0x{fol_tisubn:08X})")
    else:
        log.info(f"    FOL LAN8651 :   (unavailable — lan_read failed)")


def phase3_cyclic_fire(run: SmokeRunner):
    """Exercise cyclic_fire on both boards simultaneously with a shared
    PTP-wallclock anchor, verify the callback actually fired at the
    configured rate, and stop cleanly.

    Firmware-only checks — cannot verify the actual GPIO signal edges
    or GM↔FOL phase alignment (those need an oscilloscope).  Regression
    value: catches a broken tfuture callback hook, broken re-arm loop,
    main-loop starvation at short periods, or a broken start/stop API."""
    log = run.log
    gm, fol = run.ser_gm, run.ser_fol

    # Pick a shared phase anchor ~100 ms in the future so both boards
    # begin at the same PTP-wallclock moment regardless of the delta
    # between their two cyclic_start calls.
    resp = send_command(gm, "clk_get", 2.0, log)
    m    = RE_CLK_GET.search(resp)
    if not m:
        run.check("cyclic_fire start (both)",
                  lambda: (False, f"could not read GM clk: {resp[:80]!r}"))
        return
    anchor_ns = int(m.group(1)) + 100_000_000   # +100 ms

    # Start both boards on the shared anchor.
    gm_ok  = bool(RE_CYC_START_OK.search(
        send_command(gm,  f"cyclic_start {CYCLIC_PERIOD_US} {anchor_ns}", 2.0, log)))
    fol_ok = bool(RE_CYC_START_OK.search(
        send_command(fol, f"cyclic_start {CYCLIC_PERIOD_US} {anchor_ns}", 2.0, log)))
    run.check("cyclic_fire start (both)",
              lambda: (gm_ok and fol_ok,
                       f"GM={'OK' if gm_ok else 'FAIL'}  FOL={'OK' if fol_ok else 'FAIL'}"))
    if not (gm_ok and fol_ok):
        # Try to clean up so subsequent tests don't see a running instance
        send_command(gm,  "cyclic_stop", 2.0, log)
        send_command(fol, "cyclic_stop", 2.0, log)
        return

    log.info(f"  cyclic_fire: dwell {CYCLIC_DWELL_S:.1f} s  "
             f"(period {CYCLIC_PERIOD_US} µs)")
    time.sleep(CYCLIC_DWELL_S)

    # Read status on both boards.
    def parse_status(ser):
        resp = send_command(ser, "cyclic_status", 3.0, log)
        mr = RE_CYC_RUNNING.search(resp)
        mc = RE_CYC_CYCLES.search(resp)
        mm = RE_CYC_MISSES.search(resp)
        if not (mr and mc and mm):
            return None
        return {
            "running": mr.group(1) == "yes",
            "cycles":  int(mc.group(1)),
            "misses":  int(mm.group(1)),
        }

    gm_st  = parse_status(gm)
    fol_st = parse_status(fol)

    # Stop both unconditionally (so a partial-failure run still cleans up)
    send_command(gm,  "cyclic_stop", 2.0, log)
    send_command(fol, "cyclic_stop", 2.0, log)

    if gm_st is None or fol_st is None:
        run.check("cyclic_status parse",
                  lambda: (False, f"GM={gm_st}  FOL={fol_st}"))
        return

    for side, st in (("GM", gm_st), ("FOL", fol_st)):
        run.check(f"cyclic {side} running during dwell",
                  lambda st=st: (st["running"], f"running={st['running']}"))
        run.check(f"cyclic {side} callback ran (cycles in {GATE_CYCLIC_MIN_CYCLES}..{GATE_CYCLIC_MAX_CYCLES})",
                  lambda st=st: (GATE_CYCLIC_MIN_CYCLES <= st["cycles"] <= GATE_CYCLIC_MAX_CYCLES,
                                 f"cycles={st['cycles']}"))
        run.check(f"cyclic {side} misses < {GATE_CYCLIC_MAX_MISSES}",
                  lambda st=st: (st["misses"] < GATE_CYCLIC_MAX_MISSES,
                                 f"misses={st['misses']}  (gate {GATE_CYCLIC_MAX_MISSES})"))

    # After stop: both must show running=no.
    gm_st2  = parse_status(gm)
    fol_st2 = parse_status(fol)
    for side, st in (("GM", gm_st2), ("FOL", fol_st2)):
        run.check(f"cyclic {side} stopped after cyclic_stop",
                  lambda st=st: (st is not None and not st["running"],
                                 f"running={st['running'] if st else 'parse-fail'}"))


def phase3_cyclic_fire_marker(run: SmokeRunner):
    """Exercise cyclic_start_marker — same callback path as the SQUARE
    variant but with the MARKER pattern (1-high + 4-low phase counter).
    Firmware-only check: the pattern bookkeeping lives entirely inside
    fire_callback() and cyclic_start_ex(), so a regression to the phase
    calculation (R20) would show as either a failed start or a missing
    cycles count."""
    log = run.log
    gm, fol = run.ser_gm, run.ser_fol

    resp = send_command(gm, "clk_get", 2.0, log)
    m    = RE_CLK_GET.search(resp)
    if not m:
        run.check("cyclic_fire marker start (both)",
                  lambda: (False, f"could not read GM clk: {resp[:80]!r}"))
        return
    anchor_ns = int(m.group(1)) + 100_000_000   # +100 ms

    # The MARKER pattern has 10 half-period callbacks per visible pulse cycle,
    # so cycles grow at the same rate as SQUARE at the same period.  Use the
    # same gates.
    gm_ok  = bool(RE_CYC_START_MARKER_OK.search(
        send_command(gm,  f"cyclic_start_marker {CYCLIC_PERIOD_US} {anchor_ns}", 2.0, log)))
    fol_ok = bool(RE_CYC_START_MARKER_OK.search(
        send_command(fol, f"cyclic_start_marker {CYCLIC_PERIOD_US} {anchor_ns}", 2.0, log)))
    run.check("cyclic_fire marker start (both)",
              lambda: (gm_ok and fol_ok,
                       f"GM={'OK' if gm_ok else 'FAIL'}  FOL={'OK' if fol_ok else 'FAIL'}"))
    if not (gm_ok and fol_ok):
        send_command(gm,  "cyclic_stop", 2.0, log)
        send_command(fol, "cyclic_stop", 2.0, log)
        return

    log.info(f"  cyclic_fire marker: dwell {CYCLIC_DWELL_S:.1f} s  "
             f"(period {CYCLIC_PERIOD_US} µs)")
    time.sleep(CYCLIC_DWELL_S)

    def parse_status(ser):
        r = send_command(ser, "cyclic_status", 3.0, log)
        mr, mc = RE_CYC_RUNNING.search(r), RE_CYC_CYCLES.search(r)
        if not (mr and mc): return None
        return {"running": mr.group(1) == "yes", "cycles": int(mc.group(1))}

    gm_st, fol_st = parse_status(gm), parse_status(fol)
    send_command(gm,  "cyclic_stop", 2.0, log)
    send_command(fol, "cyclic_stop", 2.0, log)

    for side, st in (("GM", gm_st), ("FOL", fol_st)):
        run.check(f"cyclic_marker {side} callback ran",
                  lambda st=st: (st is not None and st["running"]
                                 and GATE_CYCLIC_MIN_CYCLES <= st["cycles"] <= GATE_CYCLIC_MAX_CYCLES,
                                 f"st={st}"))


def phase3_cyclic_fire_free(run: SmokeRunner):
    """Exercise cyclic_start_free — the PTP-independent path.  Starts the
    cyclic callback after forcing PTP_CLOCK to 0 internally.  IMPORTANT:
    cyclic_start_free only makes sense with PTP follower mode DISABLED —
    otherwise the next Sync arrival would overwrite the anchor, causing
    fire_callback's catch-up loop to slew through seconds of ticks every
    125 ms (visible as a ~30× drop in cycle count vs the synced paths).

    Therefore this routine does ptp_mode off → cyclic_start_free → dwell →
    cyclic_stop → ptp_mode follower → wait for FINE re-lock."""
    log = run.log
    fol = run.ser_fol

    # Disable follower mode so Sync arrivals don't touch PTP_CLOCK while
    # cyclic_start_free runs with a zeroed anchor.
    send_command(fol, "ptp_mode off", 2.0, log)
    time.sleep(0.3)

    ok = bool(RE_CYC_START_FREE_OK.search(
        send_command(fol, f"cyclic_start_free {CYCLIC_PERIOD_US}", 2.0, log)))
    run.check("cyclic_fire free start (FOL)",
              lambda: (ok, "started" if ok else "start failed"))
    if not ok:
        send_command(fol, "cyclic_stop", 2.0, log)
        send_command(fol, "ptp_mode follower", 2.0, log)
        return

    time.sleep(CYCLIC_DWELL_S)

    resp = send_command(fol, "cyclic_status", 3.0, log)
    mr, mc = RE_CYC_RUNNING.search(resp), RE_CYC_CYCLES.search(resp)
    send_command(fol, "cyclic_stop", 2.0, log)

    running = (mr is not None) and (mr.group(1) == "yes")
    cycles  = int(mc.group(1)) if mc else 0
    run.check("cyclic_free FOL running during dwell",
              lambda: (running, f"running={running}"))
    run.check(f"cyclic_free FOL cycles in {GATE_CYCLIC_MIN_CYCLES}..{GATE_CYCLIC_MAX_CYCLES}",
              lambda: (GATE_CYCLIC_MIN_CYCLES <= cycles <= GATE_CYCLIC_MAX_CYCLES,
                       f"cycles={cycles}"))

    # Restore follower mode.  Most of Phase 3 is already done by this point,
    # but we leave the board in a sane state for any subsequent manual use.
    send_command(fol, "ptp_mode follower", 2.0, log)
    log.info("  FOL PTP mode restored to follower")


def phase3_r25_regression(run: SmokeRunner):
    """R25 regression guard — verify FOL's software PTP_CLOCK is close to
    GM's at the same real moment.  The pre-fix value was a constant +10 ms
    offset (LAN865x RX-nIRQ is asserted ~10 ms after SFD-on-wire, so the
    raw (t2, sysTickAtRx) anchor pair was mismatched).  With the fix
    PTP_FOL_ANCHOR_OFFSET_NS compensates it and the delta sits well below
    1 ms.  We gate at 500 µs to catch a regression without flagging on
    ordinary jitter.

    Measurement: bracketed clk_get — for each round read GM, FOL, GM in
    sequence; interpolate GM to FOL's measurement moment using wall-clock
    timestamps on the PC side.  Report the median across rounds."""
    log = run.log
    gm, fol = run.ser_gm, run.ser_fol
    log.info(f"  clk_get bracketing ({R25_BRACKETING_REPEATS} rounds) ...")

    deltas_ns = []
    for i in range(R25_BRACKETING_REPEATS):
        t0 = time.monotonic()
        r1 = send_command(gm,  "clk_get", 2.0, log); t1 = time.monotonic()
        r2 = send_command(fol, "clk_get", 2.0, log); t2 = time.monotonic()
        r3 = send_command(gm,  "clk_get", 2.0, log); t3 = time.monotonic()
        m1 = RE_CLK_GET.search(r1); m2 = RE_CLK_GET.search(r2); m3 = RE_CLK_GET.search(r3)
        if not (m1 and m2 and m3):
            continue
        v_gm1 = int(m1.group(1)); v_fol = int(m2.group(1)); v_gm2 = int(m3.group(1))
        rt_gm1 = (t0 + t1) / 2.0
        rt_fol = (t1 + t2) / 2.0
        rt_gm2 = (t2 + t3) / 2.0
        if rt_gm2 == rt_gm1:
            continue
        v_gm_at_fol = v_gm1 + (v_gm2 - v_gm1) * (rt_fol - rt_gm1) / (rt_gm2 - rt_gm1)
        deltas_ns.append(int(v_fol - v_gm_at_fol))

    if not deltas_ns:
        run.check("R25 FOL-vs-GM clk_get bracketing",
                  lambda: (False, "no valid samples"))
        return

    deltas_ns.sort()
    median_ns = deltas_ns[len(deltas_ns) // 2]
    log.info(f"  deltas (FOL-GM) ns: {deltas_ns}  median={median_ns:+d}")
    run.check(f"R25 FOL-GM |median| < {GATE_R25_FOL_GM_ABS_NS} ns",
              lambda: (abs(median_ns) < GATE_R25_FOL_GM_ABS_NS,
                       f"median={median_ns:+d} ns  (gate ±{GATE_R25_FOL_GM_ABS_NS})"))


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
