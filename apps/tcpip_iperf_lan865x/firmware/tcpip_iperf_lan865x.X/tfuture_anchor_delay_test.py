#!/usr/bin/env python3
"""tfuture Anchor-Delay Sweep — Hypothesis 1 Test
===================================================

Tests the hypothesis that the tfuture self_jitter bias comes from the
mismatch between GM's anchor_wc (captured at TTSCA = Sync SFD on wire)
and GM's anchor_tick (captured ~6 ms later via SYS_TIME_Counter64Get()).

The firmware now has a CLI `ptp_gm_delay <ns>` that adds a configurable
offset to anchor_wc at each Sync TX.  If the hypothesis is correct,
there should be an anchor_delay value near +6_000_000 ns that minimises
the GM self_jitter bias.

Test sweeps anchor delay values and reports the GM/FOL self_jitter bias
at each one:

    delay    |  GM median bias  |  FOL median bias  |  inter_board
  ----------|-------------------|-------------------|-----------------
      0 ns  |      -1375 µs     |      -547 µs      |      -835 µs
  3000000   |      ?            |      ?            |      ?
  6000000   |      ?            |      ?            |      ?
  9000000   |      ?            |      ?            |      ?
  12000000  |      ?            |      ?            |      ?

Interpretation:
  * If the GM bias has a minimum somewhere in [3..9] ms, Hypothesis 1
    is confirmed.
  * If the bias is flat or monotonic in delay, the anchor-wc/tick gap
    is not the dominant cause and we must look elsewhere.

Usage:
    python tfuture_anchor_delay_test.py --gm-port COM8 --fol-port COM10
    python tfuture_anchor_delay_test.py --delays 0,3000000,6000000,9000000,12000000
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

LEAD_MS = 2000  # fixed at 2 s — consistent with Phase B of diagnose test


def read_clk_ns(ser, log):
    resp = send_command(ser, "clk_get", 2.0, log)
    m = RE_CLK_GET.search(resp)
    return int(m.group(1)) if m else None


def arm_tfuture(ser, target_ns, log):
    resp = send_command(ser, f"tfuture_at {target_ns}", 2.0, log)
    if RE_ARM_FAIL.search(resp):
        return False
    return bool(RE_ARM_OK.search(resp))


def read_drift_ppb(ser, log):
    resp = send_command(ser, "tfuture_status", 2.0, log)
    m = RE_DRIFT_PPB.search(resp)
    return int(m.group(1)) if m else None


def dump_tfuture(ser, log):
    ser.reset_input_buffer()
    ser.write(b"tfuture_dump\r\n")
    buffer, saw_start, saw_end, idle_deadline = "", False, False, None
    samples, header_count = [], -1
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not saw_end:
        chunk = ser.read(4096)
        if not chunk:
            if idle_deadline is not None and time.monotonic() > idle_deadline:
                break
            time.sleep(0.02); continue
        buffer += chunk.decode("ascii", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line: continue
            if line.startswith("tfuture_dump: start"):
                saw_start = True
                for tok in line.split():
                    if tok.startswith("count="):
                        try: header_count = int(tok.split("=", 1)[1])
                        except ValueError: pass
                continue
            if line.startswith("tfuture_dump: end"):
                saw_end = True; break
            if not saw_start: continue
            parts = line.split()
            if len(parts) == 3:
                try: samples.append((int(parts[0]), int(parts[1]), int(parts[2])))
                except ValueError: continue
        if header_count > 0 and len(samples) >= header_count and idle_deadline is None:
            idle_deadline = time.monotonic() + 2.0
    return samples


def robust_median(values):
    if not values: return 0
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return med, 1.4826 * mad


def run_one(delay_ns, rounds, ser_gm, ser_fol, log):
    log.info("")
    log.info("=" * 70)
    log.info(f"  Anchor delay = {delay_ns:+d} ns  ({delay_ns/1e6:+.3f} ms)")
    log.info("=" * 70)

    send_command(ser_gm, f"ptp_gm_delay {delay_ns}", 2.0, log)
    log.info("  Waiting 3 s for new anchor to propagate through 24 Sync cycles ...")
    time.sleep(3)

    gm_ppb  = read_drift_ppb(ser_gm,  log)
    fol_ppb = read_drift_ppb(ser_fol, log)
    log.info(f"  drift_ppb: GM={gm_ppb:+d}  FOL={fol_ppb:+d}")

    send_command(ser_gm,  "tfuture_reset", 2.0, log)
    send_command(ser_fol, "tfuture_reset", 2.0, log)

    arm_fail_gm = arm_fail_fol = 0
    wait_s = LEAD_MS / 1000.0 + 0.3
    for rnd in range(rounds):
        gm_now = read_clk_ns(ser_gm, log)
        if gm_now is None:
            arm_fail_gm += 1; time.sleep(wait_s); continue
        target_ns = gm_now + LEAD_MS * 1_000_000
        ok_gm  = arm_tfuture(ser_gm,  target_ns, log)
        ok_fol = arm_tfuture(ser_fol, target_ns, log)
        if not ok_gm:  arm_fail_gm += 1
        if not ok_fol: arm_fail_fol += 1
        sys.stdout.write(f"\r  round {rnd+1:3d}/{rounds}  gm={'OK ' if ok_gm else 'FAIL'}  fol={'OK ' if ok_fol else 'FAIL'}")
        sys.stdout.flush()
        time.sleep(wait_s)
    sys.stdout.write("\r" + " " * 70 + "\r")
    log.info(f"  arm failures: GM={arm_fail_gm}  FOL={arm_fail_fol}")

    gm_dump  = dump_tfuture(ser_gm,  log)
    fol_dump = dump_tfuture(ser_fol, log)
    gm_map   = {t: a for (t, a, _) in gm_dump}
    fol_map  = {t: a for (t, a, _) in fol_dump}
    common   = sorted(gm_map.keys() & fol_map.keys())

    self_gm  = [gm_map[t] - t for t in common]
    self_fol = [fol_map[t] - t for t in common]
    inter    = [gm_map[t] - fol_map[t] for t in common]

    gm_med,  gm_rob  = robust_median(self_gm)
    fol_med, fol_rob = robust_median(self_fol)
    int_med, int_rob = robust_median(inter)

    log.info(f"  matched pairs : {len(common)}")
    log.info(f"  GM  self bias : median={gm_med:+10.0f} ns  robust={gm_rob:>8.0f} ns")
    log.info(f"  FOL self bias : median={fol_med:+10.0f} ns  robust={fol_rob:>8.0f} ns")
    log.info(f"  inter_board   : median={int_med:+10.0f} ns  robust={int_rob:>8.0f} ns")

    return {
        "delay":   delay_ns,
        "gm_ppb":  gm_ppb,
        "fol_ppb": fol_ppb,
        "n":       len(common),
        "gm":      (gm_med, gm_rob),
        "fol":     (fol_med, fol_rob),
        "inter":   (int_med, int_rob),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",  default=DEFAULT_NETMASK)
    p.add_argument("--rounds",   default=15, type=int,
                   help="rounds per delay value (default 15)")
    p.add_argument("--delays",   default="0,3000000,6000000,9000000,12000000",
                   help="comma-separated anchor delay values in ns")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s", default=5.0, type=float)
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--log-file", default=None)
    p.add_argument("--verbose",  action="store_true")
    args = p.parse_args()

    delays = [int(x) for x in args.delays.split(",")]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = args.log_file or f"tfuture_anchor_delay_test_{ts}.log"
    log = Logger(log_file=log_file, verbose=args.verbose)

    log.info("=" * 70)
    log.info("  tfuture Anchor-Delay Sweep — Hypothesis 1 Test")
    log.info("=" * 70)
    log.info(f"Date         : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Delays (ns)  : {delays}")
    log.info(f"Rounds each  : {args.rounds}")
    log.info(f"Lead_ms      : {LEAD_MS}")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

    results: List[dict] = []
    try:
        if not args.no_reset:
            log.info("\n--- Reset + IP + PTP to FINE ---")
            for label, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
                send_command(ser, "reset", 3.0, log)
            time.sleep(8)
            for ser, ip in [(ser_gm, args.gm_ip), (ser_fol, args.fol_ip)]:
                send_command(ser, f"setip eth0 {ip} {args.netmask}", 3.0, log)
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm, "ptp_mode master", 3.0, log)
            matched, elapsed, _ = wait_for_pattern(
                ser_fol, RE_FINE, args.conv_timeout, log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                "HARD_SYNC": RE_HARD_SYNC,
                                "COARSE":    RE_COARSE},
                live_log=True)
            if not matched:
                log.info(f"  FAIL: FINE not reached"); return 1
            log.info(f"  FINE reached in {elapsed:.1f}s")
            log.info(f"--- Stabilise {args.settle_s:.1f}s ---")
            time.sleep(args.settle_s)

        for d in delays:
            results.append(run_one(d, args.rounds, ser_gm, ser_fol, log))

        # Reset to default on exit
        send_command(ser_gm, "ptp_gm_delay 0", 2.0, log)

        # Summary table
        log.info("")
        log.info("=" * 70)
        log.info("  Summary — GM self_jitter bias vs. anchor delay")
        log.info("=" * 70)
        log.info(f"  {'delay (ns)':>10} {'delay (ms)':>11}  "
                 f"{'GM median':>12} {'GM robust':>11}  "
                 f"{'FOL median':>12} {'FOL robust':>12}  "
                 f"{'inter':>12}")
        log.info("  " + "-" * 90)
        for r in results:
            log.info(f"  {r['delay']:>10d} {r['delay']/1e6:>+10.3f}   "
                     f"{r['gm'][0]:>+12.0f} {r['gm'][1]:>11.0f}  "
                     f"{r['fol'][0]:>+12.0f} {r['fol'][1]:>12.0f}  "
                     f"{r['inter'][0]:>+12.0f}")

        # Find the delay with the smallest |GM median|
        best = min(results, key=lambda r: abs(r["gm"][0]))
        worst = max(results, key=lambda r: abs(r["gm"][0]))
        log.info("")
        log.info(f"  GM bias magnitude:  min |GM|={abs(best['gm'][0]):.0f} ns @ delay={best['delay']} ns   "
                 f"max |GM|={abs(worst['gm'][0]):.0f} ns @ delay={worst['delay']} ns")

        # Interpretation
        log.info("")
        log.info("  Interpretation:")
        biases = [r["gm"][0] for r in results]
        if abs(max(biases) - min(biases)) > 0.5 * max(abs(b) for b in biases if b != 0):
            log.info("    → GM bias VARIES substantially with anchor delay")
            if best["delay"] != results[0]["delay"]:
                log.info(f"      → minimum bias at delay={best['delay']} ns  ⇒  "
                         f"anchor-wc/tick gap IS a real contributor")
                log.info("      ⇒  Hypothesis 1 partially/fully CONFIRMED")
        else:
            log.info("    → GM bias is roughly INSENSITIVE to anchor delay")
            log.info("      ⇒  Hypothesis 1 NOT confirmed — look elsewhere")

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
