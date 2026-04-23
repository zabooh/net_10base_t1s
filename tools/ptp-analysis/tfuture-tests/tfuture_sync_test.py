#!/usr/bin/env python3
"""tfuture Dual-Board Coordinated-Firing Test
================================================

Demonstrates that two HW-PTP-synchronised boards can be instructed to fire
at the same absolute PTP_CLOCK timestamp and physically fire within
hundreds of nanoseconds of each other — proving the full time-sync chain
from PTP anchor → PTP_CLOCK interpolation → application action.

Flow:
  1. Reset both boards, configure IPs, start HW-PTP, wait for FINE.
  2. Let HW-PTP stabilise for a few seconds.
  3. For each round:
       - Read GM's current PTP_CLOCK via clk_get
       - Compute a common target_ns = gm_now + <lead_ms>
       - Arm tfuture on BOTH boards with the same absolute target_ns
       - Wait <lead_ms>+200ms for both boards to fire
  4. Dump both ring buffers, pair records by target, compute:
       - self_jitter_GM  = actual_GM  − target  (module+spin precision on GM)
       - self_jitter_FOL = actual_FOL − target  (module+spin precision on FOL)
       - inter_board     = actual_GM − actual_FOL  (physical coincidence)
  5. Print robust + classical statistics.

The inter-board delta is the key number: it measures how closely two
boards physically fire when given the same "future target" — and that
delta is fundamentally bounded by HW-PTP sync accuracy on this platform.

Usage:
    python tfuture_sync_test.py --gm-port COM8 --fol-port COM10
    python tfuture_sync_test.py --rounds 20 --lead-ms 3000
    python tfuture_sync_test.py --csv out.csv
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

RE_CLK_GET  = re.compile(r"clk_get:\s+(\d+)\s+ns")
RE_ARM_OK   = re.compile(r"tfuture_at OK\s+target=(\d+)")
RE_ARM_FAIL = re.compile(r"tfuture_at FAIL")


# ---------------------------------------------------------------------------
def read_clk_ns(ser: serial.Serial, log: Logger) -> Optional[int]:
    """Query PTP_CLOCK_GetTime_ns() via the clk_get CLI command."""
    resp = send_command(ser, "clk_get", 2.0, log)
    m = RE_CLK_GET.search(resp)
    if not m:
        return None
    return int(m.group(1))


# ---------------------------------------------------------------------------
def arm_tfuture(ser: serial.Serial, target_ns: int,
                log: Logger) -> bool:
    """Arm tfuture at absolute target_ns; return True on success."""
    resp = send_command(ser, f"tfuture_at {target_ns}", 2.0, log)
    if RE_ARM_FAIL.search(resp):
        return False
    return bool(RE_ARM_OK.search(resp))


# ---------------------------------------------------------------------------
def dump_tfuture(ser: serial.Serial, log: Logger
                 ) -> List[Tuple[int, int, int]]:
    """Send tfuture_dump, parse '<target> <actual> <delta>' per line."""
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
                        try:
                            header_count = int(tok.split("=", 1)[1])
                        except ValueError:
                            header_count = -1
                log.info(f"    header: {line}")
                continue
            if line.startswith("tfuture_dump: end"):
                log.info(f"    footer: {line}")
                saw_end = True
                break
            if not saw_start:
                continue
            parts = line.split()
            if len(parts) == 3:
                try:
                    target = int(parts[0])
                    actual = int(parts[1])
                    delta  = int(parts[2])
                    samples.append((target, actual, delta))
                except ValueError:
                    continue
        if header_count > 0 and len(samples) >= header_count and idle_deadline is None:
            idle_deadline = time.monotonic() + 2.0

    if not saw_end:
        log.info(f"  WARNING: dump timed out after {len(samples)} samples"
                 f" (expected {header_count})")
    return samples


# ---------------------------------------------------------------------------
def percentile(sorted_vals: List[int], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k  = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def describe(name: str, values: List[int], log: Logger):
    if not values:
        log.info(f"  {name}: no samples")
        return
    sorted_v = sorted(values)
    median_v = statistics.median(values)
    mad_v    = statistics.median(abs(v - median_v) for v in values)
    robust_std = 1.4826 * mad_v
    classical_std = statistics.stdev(values) if len(values) > 1 else 0.0
    p05 = percentile(sorted_v, 0.05)
    p95 = percentile(sorted_v, 0.95)
    log.info(f"  {name}:")
    log.info(f"    n                 : {len(values)}")
    log.info(f"    median            : {median_v:+12.0f} ns   ({median_v/1000:+.2f} µs)")
    log.info(f"    MAD               : {mad_v:12.0f} ns   ({mad_v/1000:.2f} µs)")
    log.info(f"    robust stdev      : {robust_std:12.0f} ns   ({robust_std/1000:.2f} µs)  [= 1.4826 × MAD]")
    log.info(f"    classical mean    : {statistics.mean(values):+12.0f} ns")
    log.info(f"    classical stdev   : {classical_std:12.0f} ns   ({classical_std/1000:.2f} µs)")
    log.info(f"    min .. max        : {min(values):+12d} .. {max(values):+d} ns")
    log.info(f"    p05 .. p95        : {p05:+12.0f} .. {p95:+.0f} ns")


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
                   help="Number of fire rounds (default 20, max 256 per board buffer)")
    p.add_argument("--lead-ms",   default=2000, type=int,
                   help="Target lead time in ms (default 2000; must exceed CLI round-trip)")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s",  default=5.0, type=float,
                   help="Seconds to wait after FINE before first round (default 5)")
    p.add_argument("--no-reset",  action="store_true",
                   help="Skip board reset + PTP setup; assume PTP already FINE")
    p.add_argument("--csv",       default=None,
                   help="CSV output: round,target_ns,actual_gm_ns,actual_fol_ns,self_gm,self_fol,inter")
    p.add_argument("--log-file",  default=None)
    p.add_argument("--verbose",   action="store_true")
    args = p.parse_args()

    if args.rounds > 256:
        print("ERROR: --rounds exceeds ring buffer capacity (256)")
        return 1
    if args.lead_ms < 500:
        print("ERROR: --lead-ms too short (must be >= 500 to cover CLI round-trip)")
        return 1

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = args.log_file or f"tfuture_sync_test_{ts}.log"
    log = Logger(log_file=log_file, verbose=args.verbose)

    log.info("=" * 68)
    log.info("  tfuture Dual-Board Coordinated-Firing Test")
    log.info("=" * 68)
    log.info(f"Date           : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"GM  port/IP    : {args.gm_port} / {args.gm_ip}")
    log.info(f"FOL port/IP    : {args.fol_port} / {args.fol_ip}")
    log.info(f"Rounds         : {args.rounds}")
    log.info(f"Lead-ms/round  : {args.lead_ms}")
    log.info("")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

    gm_dump:  List[Tuple[int, int, int]] = []
    fol_dump: List[Tuple[int, int, int]] = []

    try:
        if not args.no_reset:
            log.info("--- Reset ---")
            for label, ser in [("GM", ser_gm), ("FOL", ser_fol)]:
                send_command(ser, "reset", 3.0, log)
                log.info(f"  [{label}] reset")
            log.info("  Waiting 8 s for boot...")
            time.sleep(8)

            log.info("--- Configure IPs ---")
            for label, ser, ip in [("GM", ser_gm, args.gm_ip),
                                    ("FOL", ser_fol, args.fol_ip)]:
                resp = send_command(ser, f"setip eth0 {ip} {args.netmask}", 3.0, log)
                ok = bool(RE_IP_SET.search(resp))
                log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")

            log.info("--- Enable HW-PTP, wait for FINE ---")
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm,  "ptp_mode master", 3.0, log)
            log.info(f"  Waiting for FOL FINE (timeout {args.conv_timeout:.0f}s)...")
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

        log.info(f"--- Stabilise {args.settle_s:.1f}s before first round ---")
        time.sleep(args.settle_s)

        # Clear any prior trace data on both boards
        send_command(ser_gm,  "tfuture_reset", 2.0, log)
        send_command(ser_fol, "tfuture_reset", 2.0, log)

        # ---- Firing rounds ---------------------------------------------
        log.info(f"\n--- Running {args.rounds} coordinated-firing rounds ---")
        arm_fail = 0
        for rnd in range(args.rounds):
            gm_now = read_clk_ns(ser_gm, log)
            if gm_now is None:
                log.info(f"  round {rnd}: clk_get failed — skipping")
                arm_fail += 1
                continue
            target_ns = gm_now + args.lead_ms * 1_000_000

            ok_gm  = arm_tfuture(ser_gm,  target_ns, log)
            ok_fol = arm_tfuture(ser_fol, target_ns, log)
            if not (ok_gm and ok_fol):
                log.info(f"  round {rnd:3d}: arm FAIL  (gm={ok_gm} fol={ok_fol})")
                arm_fail += 1
                continue

            wait_s = args.lead_ms / 1000.0 + 0.2
            sys.stdout.write(f"\r  round {rnd+1:3d}/{args.rounds}  target={target_ns} ...")
            sys.stdout.flush()
            time.sleep(wait_s)

        sys.stdout.write("\r" + " " * 60 + "\r")
        log.info(f"  armed rounds: {args.rounds - arm_fail}  (arm failures: {arm_fail})")

        # ---- Dump both boards ------------------------------------------
        log.info("\n--- Dumping GM ring buffer ---")
        gm_dump = dump_tfuture(ser_gm, log)
        log.info(f"  GM  returned {len(gm_dump)} records")

        log.info("\n--- Dumping FOL ring buffer ---")
        fol_dump = dump_tfuture(ser_fol, log)
        log.info(f"  FOL returned {len(fol_dump)} records")

        # ---- Pair by target --------------------------------------------
        # Use target_ns as the join key — it's identical across boards
        # (we sent the same absolute ns to both arms).
        gm_map  = {t: a for (t, a, _) in gm_dump}
        fol_map = {t: a for (t, a, _) in fol_dump}
        common_targets = sorted(gm_map.keys() & fol_map.keys())
        log.info(f"  matched pairs: {len(common_targets)}  "
                 f"(GM-only {len(gm_map) - len(common_targets)}, "
                 f"FOL-only {len(fol_map) - len(common_targets)})")

        if not common_targets:
            log.info("  FAIL: no matched pairs — something went wrong.")
            return 1

        self_gm      = []
        self_fol     = []
        inter_board  = []
        rows         = []
        for t in common_targets:
            ag = gm_map[t]
            af = fol_map[t]
            sg = ag - t
            sf = af - t
            ib = ag - af
            self_gm.append(sg)
            self_fol.append(sf)
            inter_board.append(ib)
            rows.append((t, ag, af, sg, sf, ib))

        # ---- Report ----------------------------------------------------
        log.info("")
        log.info("=" * 68)
        log.info("  Results")
        log.info("=" * 68)
        log.info("  All metrics in nanoseconds.")
        log.info("")
        log.info("  Module self-jitter (target → actual, per board):")
        log.info("  -- how precisely the firmware hit its own target tick")
        describe("self_jitter  GM  (actual_GM  − target)", self_gm,  log)
        describe("self_jitter  FOL (actual_FOL − target)", self_fol, log)
        log.info("")
        log.info("  Inter-board physical coincidence:")
        log.info("  -- how far apart the two boards physically fired")
        describe("inter_board  delta = actual_GM − actual_FOL", inter_board, log)

        log.info("")
        log.info("  Interpretation:")
        log.info("    - self_jitter is bounded by the 1-ms spin threshold + TC0 tick;")
        log.info("      typical ~17 ns resolution with ~1-2 µs worst-case.")
        log.info("    - inter_board = (self_GM − self_FOL) + PTP_CLOCK misalignment")
        log.info("      between the two boards at the firing moment.  If HW-PTP is")
        log.info("      at FINE (~50 ns sync at SFD), inter_board should show mostly")
        log.info("      the module's own jitter.")

        if args.csv:
            with open(args.csv, "w", encoding="utf-8") as f:
                f.write("round,target_ns,actual_gm_ns,actual_fol_ns,"
                        "self_jitter_gm,self_jitter_fol,inter_board\n")
                for i, (t, ag, af, sg, sf, ib) in enumerate(rows):
                    f.write(f"{i},{t},{ag},{af},{sg},{sf},{ib}\n")
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
