#!/usr/bin/env python3
"""tfuture Quick Check — fast iteration test
=============================================

Minimal-time verification of tfuture self_jitter after a firmware
change.  Single phase at lead=2000 ms, 10 rounds, reports only the
metrics needed to judge "did the fix work":

    FOL self_jitter median   (target: |median| < 100 µs)
    GM  self_jitter median   (should stay where it was)
    Inter-board median
    FOL drift_ppb (post-run) (should be non-zero and stable)

With --no-reset, skips the reset+boot+FINE setup and assumes PTP is
already running (useful for repeated runs on the same firmware):
    ~25 s total vs. ~4 min for tfuture_diagnose_test.py.

Usage:
    python tfuture_quick_check.py --gm-port COM8 --fol-port COM10
    python tfuture_quick_check.py --no-reset --rounds 8
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


def verdict(fol_med: int, gm_med: int) -> str:
    """One-line pass/fail verdict."""
    if abs(fol_med) < 100_000 and abs(gm_med) < 100_000:
        return "PASS   — both boards within ±100 µs"
    if abs(fol_med) < 200_000 and abs(gm_med) < 100_000:
        return "MARGINAL — FOL improved but not < 100 µs"
    return "FAIL   — FOL still biased"


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",  default=DEFAULT_NETMASK)
    p.add_argument("--rounds",   default=10, type=int,
                   help="Number of rounds (default 10; minimum 5 for stable median)")
    p.add_argument("--no-reset", action="store_true",
                   help="Skip boot + PTP setup; assume PTP already FINE")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s", default=3.0, type=float,
                   help="Settle time after FINE (default 3 s, shorter than diagnose)")
    p.add_argument("--log-file", default=None)
    p.add_argument("--verbose",  action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Logger(log_file=args.log_file or f"tfuture_quick_check_{ts}.log",
                 verbose=args.verbose)

    log.info("=" * 60)
    log.info(f"  tfuture quick check — {args.rounds} rounds @ lead={LEAD_MS} ms")
    log.info("=" * 60)
    log.info(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    t_start = time.monotonic()
    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        print(f"ERROR: cannot open port: {exc}"); return 1

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
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                "HARD_SYNC": RE_HARD_SYNC,
                                "COARSE":    RE_COARSE},
                live_log=True)
            if not m:
                log.info("FAIL: FINE not reached"); return 1
            log.info(f"  FINE in {e:.1f}s; stabilise {args.settle_s:.0f}s...")
            time.sleep(args.settle_s)
        else:
            log.info("\n--- Skip reset (--no-reset) ---")

        # Read drift_ppb BEFORE the rounds
        gm_ppb_pre  = read_ppb(ser_gm,  log)
        fol_ppb_pre = read_ppb(ser_fol, log)
        log.info(f"  drift_ppb (pre)  : GM={gm_ppb_pre:+d}  FOL={fol_ppb_pre:+d}")

        send_command(ser_gm,  "tfuture_reset", 2.0, log)
        send_command(ser_fol, "tfuture_reset", 2.0, log)

        log.info(f"\n--- {args.rounds} firing rounds ---")
        failed_gm = failed_fol = 0
        wait_s = LEAD_MS / 1000.0 + 0.3
        for rnd in range(args.rounds):
            gm_now = read_clk_ns(ser_gm, log)
            if gm_now is None:
                failed_gm += 1; time.sleep(wait_s); continue
            target = gm_now + LEAD_MS * 1_000_000
            og = arm(ser_gm, target, log)
            of = arm(ser_fol, target, log)
            if not og: failed_gm += 1
            if not of: failed_fol += 1
            sys.stdout.write(f"\r  round {rnd+1:3d}/{args.rounds}  "
                             f"gm={'OK ' if og else 'FAIL'}  "
                             f"fol={'OK ' if of else 'FAIL'}")
            sys.stdout.flush()
            time.sleep(wait_s)
        sys.stdout.write("\r" + " " * 60 + "\r")
        log.info(f"  arm failures: GM={failed_gm}  FOL={failed_fol}")

        # Read drift_ppb AFTER
        gm_ppb_post  = read_ppb(ser_gm,  log)
        fol_ppb_post = read_ppb(ser_fol, log)
        log.info(f"  drift_ppb (post) : GM={gm_ppb_post:+d}  FOL={fol_ppb_post:+d}")

        gd = dump(ser_gm,  log)
        fd = dump(ser_fol, log)
        gm_map  = {t: a for (t, a, _) in gd}
        fol_map = {t: a for (t, a, _) in fd}
        common  = sorted(gm_map.keys() & fol_map.keys())

        sg = [gm_map[t] - t for t in common]
        sf = [fol_map[t] - t for t in common]
        ib = [gm_map[t] - fol_map[t] for t in common]
        gm_m,  gm_r  = robust(sg)
        fol_m, fol_r = robust(sf)
        ib_m,  ib_r  = robust(ib)

        elapsed = time.monotonic() - t_start
        log.info("")
        log.info("=" * 60)
        log.info(f"  Results  (n={len(common)}, elapsed {elapsed:.0f} s)")
        log.info("=" * 60)
        log.info(f"  GM  self_jitter  : median={gm_m:+8.0f} ns  robust={gm_r:>6.0f} ns")
        log.info(f"  FOL self_jitter  : median={fol_m:+8.0f} ns  robust={fol_r:>6.0f} ns")
        log.info(f"  Inter-board      : median={ib_m:+8.0f} ns  robust={ib_r:>6.0f} ns")
        log.info("")
        log.info(f"  GM  drift_ppb    : {gm_ppb_post:+d}   (baseline: ~+1 200 000 after primary fix)")
        log.info(f"  FOL drift_ppb    : {fol_ppb_post:+d}   (baseline pre-fix: ~0; after removing override: expect non-zero)")
        log.info("")
        log.info(f"  VERDICT: {verdict(fol_m, gm_m)}")

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except: pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
