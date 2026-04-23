#!/usr/bin/env python3
"""tfuture FOL-side Drift-Forced Sweep — Hypothesis validation
===============================================================

Mirrors tfuture_drift_forced_test.py but targets the FOL board instead
of GM.  Forces FOL's PTP_CLOCK drift_ppb via the `clk_set_drift` CLI
on the FOL serial port, then measures FOL self_jitter at each value.

Context (see README_tfuture_bias_open.md §12):
  After the primary DRIFT_SANITY_PPB_ABS fix, GM is perfect but FOL
  still shows a bias that scales with lead_ms:
      bias = +1535 − 1084 × lead_s  (µs)
  FOL's drift_ppb filter reports ~0 throughout, so drift-correction
  is not helping.  The model: FOL's TC0 has an uncompensated rate
  mismatch of ~1084 ppm vs what PTP_CLOCK implicitly assumes
  (real-wallclock rate).  The filter measures a *different* quantity
  (FOL_TC0 vs GM_LAN865x) which happens to be ~0.

Hypothesis: forcing FOL drift_ppb to ≈ +1 084 000 ppb should zero the
proportional term.  The remaining bias at that sweet spot should be
≈ +1535 µs (the constant component, a separate issue).

Expected outcome at lead=2 s:
  - drift=0       → FOL bias ≈ −633 µs (baseline, matches diagnose-log)
  - drift=+500k   → FOL bias ≈ +1535 − 1084×2 + 500×2 = −633+1000 = +367 µs?
    Actually formula: bias_corrected = baseline_bias + drift_ppb * lead_s / 1000
    So at drift=+1084k, baseline bias of −1084×2=−2168 is cancelled,
    leaving the +1535 constant → expected bias ≈ +1535 µs.
  - drift=+1084k  → FOL bias ≈ +1535 µs   (SWEET SPOT)
  - drift=+1500k  → FOL bias ≈ +1535 + (1500-1084)×2 = +2367 µs (overshoot)

If a sweet spot with bias close to +1535 µs is found near the
predicted drift_ppb, the model is confirmed.  If not, reconsider.

Usage:
    python tfuture_drift_forced_fol_test.py --gm-port COM8 --fol-port COM10
    python tfuture_drift_forced_fol_test.py --drifts 0,500000,1000000,1084000,1500000
"""

import argparse
import datetime
import re
import statistics
import sys
import time
from typing import List

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

RE_CLK_GET   = re.compile(r"clk_get:\s+(\d+)\s+ns")
RE_ARM_OK    = re.compile(r"tfuture_at OK")
RE_ARM_FAIL  = re.compile(r"tfuture_at FAIL")
RE_DRIFT_PPB = re.compile(r"PTP_CLOCK drift\s*:\s*([+-]?\d+)")

LEAD_MS = 2000


def read_clk_ns(ser, log):
    resp = send_command(ser, "clk_get", 2.0, log)
    m = RE_CLK_GET.search(resp)
    return int(m.group(1)) if m else None


def arm(ser, target_ns, log):
    resp = send_command(ser, f"tfuture_at {target_ns}", 2.0, log)
    if RE_ARM_FAIL.search(resp): return False
    return bool(RE_ARM_OK.search(resp))


def read_ppb(ser, log):
    resp = send_command(ser, "tfuture_status", 2.0, log)
    m = RE_DRIFT_PPB.search(resp)
    return int(m.group(1)) if m else None


def dump(ser, log):
    ser.reset_input_buffer()
    ser.write(b"tfuture_dump\r\n")
    buffer, saw_start, saw_end, idle_d = "", False, False, None
    samples, hc = [], -1
    dl = time.monotonic() + 20.0
    while time.monotonic() < dl and not saw_end:
        c = ser.read(4096)
        if not c:
            if idle_d is not None and time.monotonic() > idle_d: break
            time.sleep(0.02); continue
        buffer += c.decode("ascii", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1); line = line.strip()
            if not line: continue
            if line.startswith("tfuture_dump: start"):
                saw_start = True
                for tok in line.split():
                    if tok.startswith("count="):
                        try: hc = int(tok.split("=",1)[1])
                        except: pass
                continue
            if line.startswith("tfuture_dump: end"):
                saw_end = True; break
            if not saw_start: continue
            p = line.split()
            if len(p) == 3:
                try: samples.append((int(p[0]), int(p[1]), int(p[2])))
                except: pass
        if hc > 0 and len(samples) >= hc and idle_d is None:
            idle_d = time.monotonic() + 2.0
    return samples


def robust(vs):
    if not vs: return 0, 0
    m = statistics.median(vs)
    mad = statistics.median(abs(v - m) for v in vs)
    return m, 1.4826 * mad


def run_one(drift_ppb, rounds, ser_gm, ser_fol, log):
    log.info("")
    log.info("=" * 70)
    log.info(f"  Forced FOL drift_ppb = {drift_ppb:+d}  ({drift_ppb/1000:.3f} ppm)")
    log.info("=" * 70)

    # Force drift on FOL.  GM keeps its auto-converged value
    # (~+1 200 000 ppb per the primary fix).
    send_command(ser_fol, f"clk_set_drift {drift_ppb}", 2.0, log)
    time.sleep(0.5)

    fol_ppb_now = read_ppb(ser_fol, log)
    gm_ppb_now  = read_ppb(ser_gm,  log)
    log.info(f"  drift_ppb read-back: GM={gm_ppb_now:+d}  FOL={fol_ppb_now:+d}")

    send_command(ser_gm,  "tfuture_reset", 2.0, log)
    send_command(ser_fol, "tfuture_reset", 2.0, log)

    failed_gm = failed_fol = 0
    wait_s = LEAD_MS / 1000.0 + 0.3
    for rnd in range(rounds):
        gm_now = read_clk_ns(ser_gm, log)
        if gm_now is None:
            failed_gm += 1; time.sleep(wait_s); continue
        target = gm_now + LEAD_MS * 1_000_000
        og = arm(ser_gm, target, log)
        of = arm(ser_fol, target, log)
        if not og: failed_gm += 1
        if not of: failed_fol += 1
        sys.stdout.write(f"\r  round {rnd+1:3d}/{rounds}  gm={'OK ' if og else 'FAIL'}  fol={'OK ' if of else 'FAIL'}")
        sys.stdout.flush()
        time.sleep(wait_s)
    sys.stdout.write("\r" + " " * 60 + "\r")
    log.info(f"  arm failures: GM={failed_gm}  FOL={failed_fol}")

    gd  = dump(ser_gm, log)
    fd  = dump(ser_fol, log)
    gm_map  = {t: a for (t, a, _) in gd}
    fol_map = {t: a for (t, a, _) in fd}
    common = sorted(gm_map.keys() & fol_map.keys())

    sg = [gm_map[t] - t for t in common]
    sf = [fol_map[t] - t for t in common]
    ib = [gm_map[t] - fol_map[t] for t in common]
    sg_m, sg_r = robust(sg); sf_m, sf_r = robust(sf); ib_m, ib_r = robust(ib)

    log.info(f"  matched pairs : {len(common)}")
    log.info(f"  GM  self bias : median={sg_m:+10.0f} ns  robust={sg_r:>8.0f} ns")
    log.info(f"  FOL self bias : median={sf_m:+10.0f} ns  robust={sf_r:>8.0f} ns")
    log.info(f"  inter_board   : median={ib_m:+10.0f} ns  robust={ib_r:>8.0f} ns")

    return {"drift": drift_ppb, "gm": (sg_m, sg_r), "fol": (sf_m, sf_r), "inter": (ib_m, ib_r), "n": len(common)}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",  default=DEFAULT_NETMASK)
    p.add_argument("--rounds",   default=15, type=int)
    # Bracketed around the predicted sweet spot (+1 084 000 ppb per
    # the linear fit bias = +1535 − 1084 × lead_s).
    p.add_argument("--drifts",   default="-500000,0,500000,800000,1000000,1084000,1200000,1500000",
                   help="comma-separated forced drift_ppb values (signed) for FOL")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s", default=5.0, type=float)
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--log-file", default=None)
    p.add_argument("--verbose",  action="store_true")
    args = p.parse_args()

    drifts = [int(x) for x in args.drifts.split(",")]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Logger(log_file=args.log_file or f"tfuture_drift_forced_fol_test_{ts}.log",
                 verbose=args.verbose)
    log.info("=" * 70)
    log.info("  tfuture FOL-side Drift-Forced Sweep")
    log.info("=" * 70)
    log.info(f"Drifts (ppb): {drifts}")
    log.info(f"Rounds each : {args.rounds}")
    log.info(f"Lead_ms     : {LEAD_MS}")
    log.info(f"Predicted sweet spot: +1 084 000 ppb (leaves +1535 µs constant residual)")

    try:
        ser_gm = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        print(f"ERROR: cannot open port: {exc}"); return 1

    results = []
    try:
        if not args.no_reset:
            log.info("\n--- Reset + IP + PTP to FINE ---")
            for ser in (ser_gm, ser_fol):
                send_command(ser, "reset", 3.0, log)
            time.sleep(8)
            for ser, ip in [(ser_gm, args.gm_ip), (ser_fol, args.fol_ip)]:
                send_command(ser, f"setip eth0 {ip} {args.netmask}", 3.0, log)
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm, "ptp_mode master", 3.0, log)
            m, e, _ = wait_for_pattern(
                ser_fol, RE_FINE, args.conv_timeout, log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ, "HARD_SYNC": RE_HARD_SYNC, "COARSE": RE_COARSE},
                live_log=True)
            if not m: print("FINE not reached"); return 1
            log.info(f"  FINE reached in {e:.1f}s")
            time.sleep(args.settle_s)

        for d in drifts:
            results.append(run_one(d, args.rounds, ser_gm, ser_fol, log))

        # Restore FOL drift to 0 on exit.  The PI-servo-driven automatic
        # filter value will reconverge naturally after the next Sync.
        send_command(ser_fol, "clk_set_drift 0", 2.0, log)

        log.info("")
        log.info("=" * 70)
        log.info("  Summary — FOL self_jitter bias vs. forced FOL drift_ppb")
        log.info("=" * 70)
        log.info(f"  {'drift (ppb)':>12} {'drift (ppm)':>12}  {'FOL median':>12}  {'GM median':>12}  {'inter':>12}")
        log.info("  " + "-" * 70)
        for r in results:
            log.info(f"  {r['drift']:>12d} {r['drift']/1000:>+12.3f}  "
                     f"{r['fol'][0]:>+12.0f}  {r['gm'][0]:>+12.0f}  {r['inter'][0]:>+12.0f}")

        best = min(results, key=lambda r: abs(r["fol"][0]))
        log.info("")
        log.info(f"  Minimum |FOL median|: {abs(best['fol'][0]):.0f} ns "
                 f"at forced drift_ppb = {best['drift']:+d}")
        log.info(f"  Corresponding GM median  : {best['gm'][0]:+.0f} ns")
        log.info(f"  Corresponding inter_board: {best['inter'][0]:+.0f} ns")

        log.info("")
        log.info("  Interpretation:")
        if abs(best["fol"][0]) < 600_000:
            # Sweet spot found, FOL bias pulled below ~600 us (near the
            # predicted +1535 us constant residual)
            predicted_drift = 1084000
            log.info(f"  ✓ Sweet spot found at drift_ppb = {best['drift']:+d}.")
            log.info(f"    Predicted = +{predicted_drift} ppb.")
            delta_from_predicted = best['drift'] - predicted_drift
            log.info(f"    Delta from prediction: {delta_from_predicted:+d} ppb.")
            if abs(delta_from_predicted) < 300_000:
                log.info("    → Hypothesis CONFIRMED: the FOL bias is rate-proportional,")
                log.info("      and FOL's drift filter measures the wrong quantity.")
                log.info("    → Next: implement a proper fix per §12.4 of bias-open README.")
            else:
                log.info("    → Sweet spot found but far from prediction.  Re-examine model.")
        else:
            log.info(f"  ✗ No clear sweet spot — |FOL bias| stays > 600 us for all tested drifts.")
            log.info(f"    → Model needs revision.  Look deeper at ptp_fol_task.c anchor path.")

        # Also report the bias at the predicted sweet spot if we tested it exactly
        for r in results:
            if r["drift"] == 1084000:
                log.info("")
                log.info(f"  At PREDICTED sweet spot (+1 084 000 ppb):")
                log.info(f"    FOL median bias = {r['fol'][0]:+.0f} ns")
                log.info(f"    Expected (from linear fit): ≈ +1535 µs  constant residual.")
                if 500_000 < r['fol'][0] < 3_000_000:
                    log.info(f"    → bias in expected range: constant term confirmed.")
                else:
                    log.info(f"    → bias outside expected range; investigate constant term.")
                break

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except: pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
