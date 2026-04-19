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
RE_TI_TISUBN = re.compile(r"TI=(\d+)\s+TISUBN=0x([0-9A-Fa-f]+)")
RE_LAN_READ  = re.compile(r"LAN865X Read OK: Addr=0x([0-9A-Fa-f]+) Value=0x([0-9A-Fa-f]+)")

LEAD_MS = 2000
# Nominal LAN8651 clock period (25 MHz crystal → 40 ns).  Matches
# CLOCK_CYCLE_NS in firmware/src/filters.h.
CLOCK_CYCLE_NS_NOMINAL = 40.0

# LAN8651 CLOCK_INCREMENT register addresses (from
# firmware/src/PTP_FOL_task.h: MAC_TI / MAC_TISUBN).
MAC_TI_ADDR     = 0x00010077
MAC_TISUBN_ADDR = 0x0001006F


def lan_read_reg(ser, addr, log):
    """Read a LAN865x register via the lan_read CLI.  Returns the 32-bit value,
    or None on failure.  The firmware's state machine takes ~10-50 ms to complete
    the read and print the result, so we wait for the printed output."""
    resp = send_command(ser, f"lan_read 0x{addr:08X}", 2.0, log)
    for m in RE_LAN_READ.finditer(resp):
        if int(m.group(1), 16) == addr:
            return int(m.group(2), 16)
    return None


def read_clock_increment(ser, log):
    """Live readback of a board's LAN8651 CLOCK_INCREMENT registers
    (MAC_TI + MAC_TISUBN).  Returns (ti, tisubn_raw) or (None, None).
    Works regardless of whether PTP has been reset in this session."""
    tisubn = lan_read_reg(ser, MAC_TISUBN_ADDR, log)
    ti_reg = lan_read_reg(ser, MAC_TI_ADDR,     log)
    if tisubn is None or ti_reg is None:
        return (None, None)
    # MAC_TI low byte holds the integer TI value.
    ti = ti_reg & 0xFF
    return (ti, tisubn)


def decode_clock_increment_ppm(ti: int, tisubn_raw: int) -> float:
    """Decode the LAN8651 CLOCK_INCREMENT register pair as written by
    PTP_FOL_task.c into the effective ns-per-tick value, and return the
    deviation from nominal 40 ns in ppm.

    Firmware packing (PTP_FOL_task.c):
        calcSubInc_uint = ((calcSubInc_uint >> 8) & 0xFFFF) | ((calcSubInc_uint & 0xFF) << 24)
    Reversing: the original 24-bit unsigned fraction counter is
        orig = ((raw_low24) << 8) | (raw_high8)
    where raw_low24 = tisubn_raw & 0x00FFFFFF and raw_high8 = (tisubn_raw >> 24) & 0xFF.
    The original was calcSubInc × 2^24 where calcSubInc is the
    fractional ns portion of CLOCK_CYCLE_NS × rateRatioFIR.
    """
    raw_low24 = tisubn_raw & 0x00FFFFFF
    raw_high8 = (tisubn_raw >> 24) & 0xFF
    orig = (raw_low24 << 8) | raw_high8
    fraction_ns = orig / (1 << 24)
    effective_ns = ti + fraction_ns
    return (effective_ns - CLOCK_CYCLE_NS_NOMINAL) / CLOCK_CYCLE_NS_NOMINAL * 1e6


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

    # Captured during reset sequence (MATCHFREQ log line); None on --no-reset.
    fol_ti: int = None
    fol_tisubn_raw: int = None

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

            # Inline wait-for-FINE that also captures the TI/TISUBN values
            # emitted during UNINIT->MATCHFREQ transition.  These values
            # encode FOL's PI-calibrated LAN8651 CLOCK_INCREMENT, from which
            # we can back out FOL_LAN8651's crystal deviation vs. GM_LAN8651.
            buffer = ""
            start  = time.monotonic()
            matched = False
            while time.monotonic() - start < args.conv_timeout:
                chunk = ser_fol.read(256)
                if chunk:
                    decoded = chunk.decode("ascii", errors="replace")
                    buffer += decoded
                    for line in decoded.splitlines():
                        if line.strip():
                            log.info(f"    {line.rstrip()}")
                    if fol_ti is None:
                        mti = RE_TI_TISUBN.search(buffer)
                        if mti:
                            fol_ti         = int(mti.group(1))
                            fol_tisubn_raw = int(mti.group(2), 16)
                    if RE_FINE.search(buffer):
                        matched = True
                        break
                else:
                    time.sleep(0.05)
            if not matched:
                log.info("FAIL: FINE not reached"); return 1
            e = time.monotonic() - start
            log.info(f"  FINE in {e:.1f}s; stabilise {args.settle_s:.0f}s...")
            if fol_ti is not None:
                log.info(f"  Captured FOL calibration: TI={fol_ti} TISUBN=0x{fol_tisubn_raw:08X}")
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

        # --------  Crystal-deviation side-analysis  -----------------------
        # Reference frame: GM_LAN8651 crystal = 0 ppm.
        # drift_ppb ≈ δ_LAN − δ_TC0 (same board), where δ is crystal
        # deviation from nominal in ppm vs. the GM_LAN reference.
        #   δ_GM_TC0  = −drift_ppb_GM   (δ_GM_LAN = 0 by choice)
        # PI regulates FOL_TSU = GM_TSU, so FOL's drift_ppb gives
        #   δ_FOL_TC0 = −drift_ppb_FOL (in the GM_LAN reference frame)
        # δ_FOL_LAN comes from ratio of PI-calibrated CLOCK_INCREMENTs:
        #   FOL_TSU_rate = GM_TSU_rate
        #   f_FOL_LAN × CLOCK_INC_FOL = f_GM_LAN × CLOCK_INC_GM
        #   δ_FOL_LAN = (CLOCK_INC_GM / CLOCK_INC_FOL) − 1
        log.info("")
        log.info("  Reading CLOCK_INCREMENT registers live (MAC_TI/MAC_TISUBN)...")
        gm_ti, gm_tisubn   = read_clock_increment(ser_gm,  log)
        fol_ti_live, fol_tisubn_live = read_clock_increment(ser_fol, log)
        # Prefer live readback (works with --no-reset); fall back to
        # boot-log capture if live read fails (e.g. stack busy).
        gm_valid  = (gm_ti  is not None)
        fol_valid = (fol_ti_live is not None) or (fol_ti is not None)
        if not fol_valid and fol_ti is not None:
            fol_ti_live, fol_tisubn_live = fol_ti, fol_tisubn_raw
        elif fol_ti_live is None and fol_ti is not None:
            fol_ti_live, fol_tisubn_live = fol_ti, fol_tisubn_raw

        log.info("")
        log.info(f"  Crystal deviations  (reference: GM_LAN8651 = 0 ppm)")
        log.info(f"    GM  LAN8651 :        0 ppm   (reference)")
        log.info(f"    GM  SAME54  : {-gm_ppb_post/1000:+7.1f} ppm   "
                 f"(from GM drift_ppb)")
        log.info(f"    FOL SAME54  : {-fol_ppb_post/1000:+7.1f} ppm   "
                 f"(from FOL drift_ppb, PI makes FOL_TSU = GM_TSU)")

        if gm_valid and fol_ti_live is not None:
            # Effective CLOCK_INCREMENT in ns/tick on each board.
            from_gm  = CLOCK_CYCLE_NS_NOMINAL * (1.0 + decode_clock_increment_ppm(gm_ti,  gm_tisubn)     / 1e6)
            from_fol = CLOCK_CYCLE_NS_NOMINAL * (1.0 + decode_clock_increment_ppm(fol_ti_live, fol_tisubn_live) / 1e6)
            # δ_FOL_LAN in the GM_LAN reference frame.
            delta_fol_lan_ppm = (from_gm / from_fol - 1.0) * 1e6
            log.info(f"    FOL LAN8651 : {delta_fol_lan_ppm:+7.1f} ppm   "
                     f"(from live CLOCK_INCREMENT ratio: "
                     f"GM={from_gm:.4f}ns, FOL={from_fol:.4f}ns)")
            log.info(f"")
            log.info(f"  CLOCK_INCREMENT raw registers:")
            log.info(f"    GM : TI={gm_ti}  TISUBN=0x{gm_tisubn:08X}")
            log.info(f"    FOL: TI={fol_ti_live}  TISUBN=0x{fol_tisubn_live:08X}")
        elif fol_ti is not None:
            # Live readback failed; fall back to boot-log capture (assumes
            # GM is nominal 40 ns).  Will only happen in edge cases.
            delta_fol_lan_ppm = -decode_clock_increment_ppm(fol_ti, fol_tisubn_raw)
            log.info(f"    FOL LAN8651 : {delta_fol_lan_ppm:+7.1f} ppm   "
                     f"(from boot-log TISUBN; GM live readback unavailable — assuming nominal)")
        else:
            log.info(f"    FOL LAN8651 :  (no data — live readback failed and no boot-log capture)")

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
