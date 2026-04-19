#!/usr/bin/env python3
"""tfuture Bias Diagnosis — lead-time scan + drift-correction toggle
====================================================================

Investigates the systematic ~1 ms self_jitter bias observed in
tfuture_sync_test.py by running four phases back-to-back:

  Phase A: lead_ms = 500,  drift correction ON   (scale test 1/3)
  Phase B: lead_ms = 1000, drift correction ON   (scale test 2/3)
  Phase C: lead_ms = 2000, drift correction ON   (baseline)
  Phase D: lead_ms = 2000, drift correction OFF  (drift-hypothesis test)

Expected outcomes:
  * If bias scales linearly with lead_ms (A<B<C), the cause is
    proportional to elapsed time — most likely a drift-related issue.
  * If bias is constant across A/B/C, the cause is a fixed offset,
    unrelated to the drift computation.
  * If Phase D bias ≪ Phase C bias, the drift-correction term in
    compute_target_tick() is the culprit.  If D ≈ C, the drift term
    is not responsible; look elsewhere (anchor dynamics, timing of
    PTP_CLOCK_GetTime_ns vs SYS_TIME_Counter64Get, etc.).

Also logs drift_ppb from both boards before and after each phase so
the readings can be correlated with the observed bias magnitude.

Usage:
    python tfuture_diagnose_test.py --gm-port COM8 --fol-port COM10
    python tfuture_diagnose_test.py --rounds 30 --csv diag.csv
"""

import argparse
import datetime
import re
import statistics
import sys
import time
from typing import List, Optional, Tuple

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

RE_CLK_GET        = re.compile(r"clk_get:\s+(\d+)\s+ns")
RE_ARM_OK         = re.compile(r"tfuture_at OK\s+target=(\d+)")
RE_ARM_FAIL       = re.compile(r"tfuture_at FAIL")
RE_DRIFT_PPB      = re.compile(r"PTP_CLOCK drift\s*:\s*([+-]?\d+)")
RE_DRIFT_CORRECT  = re.compile(r"drift correction\s*:\s*(ON|OFF)")


def read_clk_ns(ser: serial.Serial, log: Logger) -> Optional[int]:
    resp = send_command(ser, "clk_get", 2.0, log)
    m = RE_CLK_GET.search(resp)
    return int(m.group(1)) if m else None


def arm_tfuture(ser: serial.Serial, target_ns: int, log: Logger,
                label: str = "") -> bool:
    resp = send_command(ser, f"tfuture_at {target_ns}", 2.0, log)
    if RE_ARM_FAIL.search(resp):
        log.debug(f"    arm FAIL [{label}]  target={target_ns}  resp={resp.strip()!r}")
        return False
    if RE_ARM_OK.search(resp):
        return True
    log.debug(f"    arm NORESP [{label}]  target={target_ns}  resp={resp.strip()!r}")
    return False


def read_drift_ppb(ser: serial.Serial, log: Logger
                   ) -> Tuple[Optional[int], Optional[str]]:
    """Returns (drift_ppb, drift_correction_state)."""
    resp = send_command(ser, "tfuture_status", 2.0, log)
    mppb = RE_DRIFT_PPB.search(resp)
    mdrc = RE_DRIFT_CORRECT.search(resp)
    return (int(mppb.group(1)) if mppb else None,
            mdrc.group(1)        if mdrc else None)


def set_drift(ser: serial.Serial, on: bool, log: Logger):
    cmd = "tfuture_drift on" if on else "tfuture_drift off"
    send_command(ser, cmd, 2.0, log)


def dump_tfuture(ser: serial.Serial, log: Logger
                 ) -> List[Tuple[int, int, int]]:
    ser.reset_input_buffer()
    ser.write(b"tfuture_dump\r\n")
    buffer        = ""
    deadline      = time.monotonic() + 20.0
    idle_deadline = None
    saw_start     = False
    saw_end       = False
    samples: List[Tuple[int, int, int]] = []
    header_count  = -1
    while time.monotonic() < deadline and not saw_end:
        chunk = ser.read(4096)
        if not chunk:
            if idle_deadline is not None and time.monotonic() > idle_deadline:
                break
            time.sleep(0.02)
            continue
        buffer += chunk.decode("ascii", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("tfuture_dump: start"):
                saw_start = True
                for tok in line.split():
                    if tok.startswith("count="):
                        try: header_count = int(tok.split("=", 1)[1])
                        except ValueError: header_count = -1
                continue
            if line.startswith("tfuture_dump: end"):
                saw_end = True
                break
            if not saw_start:
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    samples.append((int(parts[0]), int(parts[1]), int(parts[2])))
                except ValueError:
                    continue
        if header_count > 0 and len(samples) >= header_count and idle_deadline is None:
            idle_deadline = time.monotonic() + 2.0
    return samples


def percentile(sorted_vals, p):
    if not sorted_vals: return 0.0
    k  = (len(sorted_vals) - 1) * p
    lo = int(k); hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def describe(values: List[int]) -> dict:
    """Return stats dict; returns zeros (n=0) instead of None when empty, so
    downstream formatting never NoneTypes out."""
    if not values:
        return {"n": 0, "median": 0, "mad": 0, "robust": 0,
                "mean": 0, "stdev": 0, "min": 0, "max": 0,
                "p05": 0, "p95": 0}
    sv = sorted(values)
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return {
        "n"        : len(values),
        "median"   : med,
        "mad"      : mad,
        "robust"   : 1.4826 * mad,
        "mean"     : statistics.mean(values),
        "stdev"    : statistics.stdev(values) if len(values) > 1 else 0.0,
        "min"      : min(values),
        "max"      : max(values),
        "p05"      : percentile(sv, 0.05),
        "p95"      : percentile(sv, 0.95),
    }


# ---------------------------------------------------------------------------
def run_phase(label: str, lead_ms: int, drift_on: bool, rounds: int,
              ser_gm: serial.Serial, ser_fol: serial.Serial,
              log: Logger) -> dict:
    log.info("")
    log.info("=" * 70)
    log.info(f"  {label}   lead_ms={lead_ms}   drift_correction={'ON' if drift_on else 'OFF'}")
    log.info("=" * 70)

    # Configure drift correction on BOTH boards
    set_drift(ser_gm,  drift_on, log)
    set_drift(ser_fol, drift_on, log)

    # Read drift_ppb BEFORE phase (as a snapshot of the servo state)
    gm_ppb_pre, _  = read_drift_ppb(ser_gm,  log)
    fol_ppb_pre, _ = read_drift_ppb(ser_fol, log)
    log.info(f"  drift_ppb (pre)  : GM={gm_ppb_pre:+d}  FOL={fol_ppb_pre:+d}")

    # Clear ring buffers
    send_command(ser_gm,  "tfuture_reset", 2.0, log)
    send_command(ser_fol, "tfuture_reset", 2.0, log)

    arm_fail_gm  = 0
    arm_fail_fol = 0
    wait_s = lead_ms / 1000.0 + 0.3
    for rnd in range(rounds):
        gm_now = read_clk_ns(ser_gm, log)
        if gm_now is None:
            arm_fail_gm += 1
            time.sleep(wait_s)   # still wait, so any pending fires complete
            continue
        target_ns = gm_now + lead_ms * 1_000_000
        ok_gm  = arm_tfuture(ser_gm,  target_ns, log, "GM")
        ok_fol = arm_tfuture(ser_fol, target_ns, log, "FOL")
        if not ok_gm:  arm_fail_gm += 1
        if not ok_fol: arm_fail_fol += 1
        # ALWAYS wait wait_s: even if one arm failed, the OTHER may have
        # succeeded and is pending.  Skipping the wait leaves state PENDING
        # which makes subsequent arms fail too.
        sys.stdout.write(f"\r  round {rnd+1:3d}/{rounds}  target={target_ns}"
                         f"  gm={'OK ' if ok_gm else 'FAIL'}"
                         f"  fol={'OK ' if ok_fol else 'FAIL'}")
        sys.stdout.flush()
        time.sleep(wait_s)
    sys.stdout.write("\r" + " " * 70 + "\r")
    log.info(f"  arm failures: GM={arm_fail_gm}  FOL={arm_fail_fol}")

    # Read drift_ppb AFTER phase
    gm_ppb_post, _  = read_drift_ppb(ser_gm,  log)
    fol_ppb_post, _ = read_drift_ppb(ser_fol, log)
    log.info(f"  drift_ppb (post) : GM={gm_ppb_post:+d}  FOL={fol_ppb_post:+d}")

    # Dump both ring buffers
    gm_dump  = dump_tfuture(ser_gm,  log)
    fol_dump = dump_tfuture(ser_fol, log)
    log.info(f"  dumps: GM={len(gm_dump)} records  FOL={len(fol_dump)} records")

    gm_map  = {t: a for (t, a, _) in gm_dump}
    fol_map = {t: a for (t, a, _) in fol_dump}
    common  = sorted(gm_map.keys() & fol_map.keys())
    self_gm   = [gm_map[t] - t for t in common]
    self_fol  = [fol_map[t] - t for t in common]
    inter     = [gm_map[t] - fol_map[t] for t in common]

    stats = {
        "label"       : label,
        "lead_ms"     : lead_ms,
        "drift_on"    : drift_on,
        "gm_ppb_pre"  : gm_ppb_pre,
        "gm_ppb_post" : gm_ppb_post,
        "fol_ppb_pre" : fol_ppb_pre,
        "fol_ppb_post": fol_ppb_post,
        "n_matched"   : len(common),
        "self_gm"     : describe(self_gm),
        "self_fol"    : describe(self_fol),
        "inter"       : describe(inter),
        "raw"         : [(t, gm_map[t], fol_map[t]) for t in common],
    }
    return stats


def print_phase_row(phase: dict, log: Logger):
    sg = phase["self_gm"]
    sf = phase["self_fol"]
    ib = phase["inter"]
    log.info(
        f"  {phase['label']:<9s} lead={phase['lead_ms']:>5d}ms  "
        f"drift={'ON ' if phase['drift_on'] else 'OFF'}  "
        f"n={phase['n_matched']:>3d}  | "
        f"GM self median={sg['median']:+10.0f} ns (robust={sg['robust']:>7.0f})  | "
        f"FOL self median={sf['median']:+10.0f} ns (robust={sf['robust']:>7.0f})  | "
        f"inter median={ib['median']:+10.0f} ns (robust={ib['robust']:>7.0f})")


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",   default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",  default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",     default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",    default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",   default=DEFAULT_NETMASK)
    p.add_argument("--rounds",    default=20, type=int,
                   help="rounds per phase (default 20, max 256 per board buffer)")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s",  default=5.0, type=float)
    p.add_argument("--no-reset",  action="store_true",
                   help="Skip board reset + PTP setup; assume PTP already FINE")
    p.add_argument("--csv",       default=None,
                   help="CSV output: phase,lead_ms,drift_on,target,actual_gm,actual_fol,self_gm,self_fol,inter")
    p.add_argument("--log-file",  default=None)
    p.add_argument("--verbose",   action="store_true")
    args = p.parse_args()

    if args.rounds > 256:
        print("ERROR: --rounds exceeds ring buffer capacity (256)")
        return 1

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = args.log_file or f"tfuture_diagnose_test_{ts}.log"
    log = Logger(log_file=log_file, verbose=args.verbose)

    log.info("=" * 70)
    log.info("  tfuture Bias Diagnosis — 4-phase lead-scan + drift-toggle")
    log.info("=" * 70)
    log.info(f"Date         : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"GM  port/IP  : {args.gm_port} / {args.gm_ip}")
    log.info(f"FOL port/IP  : {args.fol_port} / {args.fol_ip}")
    log.info(f"Rounds/phase : {args.rounds}")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

    phases: List[dict] = []

    try:
        if not args.no_reset:
            log.info("\n--- Reset + IP + PTP to FINE ---")
            for label, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
                send_command(ser, "reset", 3.0, log)
            time.sleep(8)
            for label, ser, ip in [("GM", ser_gm, args.gm_ip),
                                    ("FOL", ser_fol, args.fol_ip)]:
                send_command(ser, f"setip eth0 {ip} {args.netmask}", 3.0, log)
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm,  "ptp_mode master", 3.0, log)
            matched, elapsed, _ = wait_for_pattern(
                ser_fol, RE_FINE, args.conv_timeout, log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                "HARD_SYNC": RE_HARD_SYNC,
                                "COARSE":    RE_COARSE},
                live_log=True)
            if not matched:
                log.info(f"  FAIL: FINE not reached in {args.conv_timeout:.0f}s")
                return 1
            log.info(f"  FINE reached in {elapsed:.1f}s")

        log.info(f"--- Stabilise {args.settle_s:.1f}s ---")
        time.sleep(args.settle_s)

        # --------  4 phases  ----------------------------------------
        # Lead values chosen with factor-2 ratios for linearity check, all
        # comfortably above the ~300 ms per-send_command CLI round-trip.
        phases.append(run_phase("Phase A", 1000, True,  args.rounds, ser_gm, ser_fol, log))
        phases.append(run_phase("Phase B", 2000, True,  args.rounds, ser_gm, ser_fol, log))
        phases.append(run_phase("Phase C", 4000, True,  args.rounds, ser_gm, ser_fol, log))
        phases.append(run_phase("Phase D", 2000, False, args.rounds, ser_gm, ser_fol, log))

        # --------  Summary  -----------------------------------------
        log.info("")
        log.info("=" * 70)
        log.info("  Summary — per phase")
        log.info("=" * 70)
        for ph in phases:
            print_phase_row(ph, log)

        # Drift-ppb summary
        log.info("")
        log.info("  drift_ppb readings per phase (pre / post):")
        for ph in phases:
            log.info(f"    {ph['label']}: "
                     f"GM  {ph['gm_ppb_pre']:+d} / {ph['gm_ppb_post']:+d}    "
                     f"FOL {ph['fol_ppb_pre']:+d} / {ph['fol_ppb_post']:+d}")

        # Interpretation
        log.info("")
        log.info("=" * 70)
        log.info("  Interpretation")
        log.info("=" * 70)
        A, B, C, D = phases
        sg_a = A["self_gm"]["median"]; sg_b = B["self_gm"]["median"]
        sg_c = C["self_gm"]["median"]; sg_d = D["self_gm"]["median"]
        sf_a = A["self_fol"]["median"]; sf_b = B["self_fol"]["median"]
        sf_c = C["self_fol"]["median"]; sf_d = D["self_fol"]["median"]

        # Test 1: linearity A→B→C
        log.info("")
        log.info("  Test 1: does bias scale linearly with lead_ms?")
        log.info(f"    GM  self_jitter median @ 1000/2000/4000 ms: {sg_a:+.0f} / {sg_b:+.0f} / {sg_c:+.0f} ns")
        log.info(f"    FOL self_jitter median @ 1000/2000/4000 ms: {sf_a:+.0f} / {sf_b:+.0f} / {sf_c:+.0f} ns")
        # rough linearity check: does sg_c ≈ 4× sg_a and 2× sg_b?
        ratio_ca = (sg_c / sg_a) if sg_a != 0 else float("nan")
        ratio_cb = (sg_c / sg_b) if sg_b != 0 else float("nan")
        log.info(f"    GM  ratios: C/A={ratio_ca:.2f} (ideal 4.0 if linear)  "
                 f"C/B={ratio_cb:.2f} (ideal 2.0 if linear)")
        if abs(ratio_ca - 4.0) < 1.0 and abs(ratio_cb - 2.0) < 0.5:
            log.info("    → bias scales linearly with lead_ms  ⇒  proportional effect (drift-like)")
        else:
            log.info("    → bias does NOT scale linearly  ⇒  fixed offset or mixed cause")

        # Test 3: does disabling drift correction help?
        log.info("")
        log.info("  Test 3: does disabling drift correction change the bias?")
        log.info(f"    GM  median:  drift-on {sg_c:+.0f} ns  →  drift-off {sg_d:+.0f} ns")
        log.info(f"    FOL median:  drift-on {sf_c:+.0f} ns  →  drift-off {sf_d:+.0f} ns")
        dg_abs_change = abs(sg_c) - abs(sg_d)
        df_abs_change = abs(sf_c) - abs(sf_d)
        if dg_abs_change > abs(sg_c) * 0.5:
            log.info("    → disabling drift correction REDUCED GM bias substantially"
                     " ⇒ drift term is implicated")
        elif dg_abs_change < -abs(sg_c) * 0.5:
            log.info("    → disabling drift correction made GM bias WORSE"
                     " ⇒ drift correction was working correctly; something else is biasing")
        else:
            log.info("    → disabling drift correction had little effect"
                     " ⇒ drift term is NOT the main cause; look elsewhere")

        if args.csv:
            with open(args.csv, "w", encoding="utf-8") as f:
                f.write("phase,lead_ms,drift_on,target_ns,actual_gm_ns,actual_fol_ns,"
                        "self_gm,self_fol,inter\n")
                for ph in phases:
                    for (t, ag, af) in ph["raw"]:
                        f.write(f"{ph['label']},{ph['lead_ms']},"
                                f"{1 if ph['drift_on'] else 0},"
                                f"{t},{ag},{af},{ag-t},{af-t},{ag-af}\n")
            log.info(f"\n  CSV saved: {args.csv}")

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass
        log.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
