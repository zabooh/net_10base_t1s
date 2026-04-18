#!/usr/bin/env python3
"""PTP Offset Capture + Statistical Analysis
===========================================

High-resolution capture of the real PTP offset values computed inside the
Follower firmware (from LAN865x hardware timestamps t1/t2/t3/t4).  Unlike
clk_get-based tests, this bypasses the UART/USB-CDC measurement floor --
offset values are stored in a firmware-internal ring buffer during the run
and dumped in one burst AFTER the measurement, so UART jitter does not
distort the samples.

Workflow
  1. Reset both boards, configure IPs, start PTP, wait for FINE.
  2. `ptp_offset_reset` on FOL (clear ring buffer).
  3. Wait --capture-s seconds for the ring buffer to fill (1024 samples at
     8 Hz Sync = 128 s max before wrap).
  4. `ptp_offset_dump` on FOL, parse the dump.
  5. Compute mean / stdev / min / max / percentiles separately per sync
     status (UNINIT / MATCHFREQ / HARDSYNC / COARSE / FINE).
  6. Optionally save to CSV for external plotting.

Usage:
    python ptp_offset_capture.py --gm-port COM8 --fol-port COM10
    python ptp_offset_capture.py --gm-port COM8 --fol-port COM10 --capture-s 90
    python ptp_offset_capture.py --gm-port COM8 --fol-port COM10 --csv out.csv
"""

import argparse
import datetime
import statistics
import sys
import time
from typing import List, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    RE_IP_SET, RE_BUILD, RE_PING_REPLY, RE_PING_DONE,
    RE_FOL_START, RE_GM_START, RE_MATCHFREQ, RE_HARD_SYNC,
    RE_COARSE, RE_FINE,
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

STATUS_NAMES = {0: "UNINIT", 1: "MATCHFREQ", 2: "HARDSYNC", 3: "COARSE", 4: "FINE"}


def dump_offsets(ser: serial.Serial, log: Logger) -> List[Tuple[int, int]]:
    """Send ptp_offset_dump, read the full response, return [(offset_ns, status)]."""
    ser.reset_input_buffer()
    ser.write(b"ptp_offset_dump\r\n")
    buffer   = ""
    # Wait up to 60 s total; with ~1024 lines and SYS_CONSOLE backpressure
    # the dump can take >15 s if UART is rate-limited.
    deadline        = time.monotonic() + 60.0
    idle_deadline   = None        # set after "end" is expected but not yet seen
    saw_start       = False
    saw_end         = False
    samples: List[Tuple[int, int]] = []
    header_count    = -1

    while time.monotonic() < deadline and not saw_end:
        chunk = ser.read(4096)
        if not chunk:
            # If we've seen all expected samples but no "end" marker yet,
            # give 2 s grace before giving up.
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
            if line.startswith("ptp_offset_dump: start"):
                saw_start = True
                for tok in line.split():
                    if tok.startswith("count="):
                        header_count = int(tok.split("=", 1)[1])
                log.info(f"    header: {line}")
                continue
            if line.startswith("ptp_offset_dump: end"):
                log.info(f"    footer: {line}")
                saw_end = True
                break
            if not saw_start:
                continue
            parts = line.split()
            if len(parts) == 2:
                try:
                    off    = int(parts[0])
                    status = int(parts[1])
                    samples.append((off, status))
                except ValueError:
                    continue
        if header_count > 0 and len(samples) >= header_count and idle_deadline is None:
            idle_deadline = time.monotonic() + 2.0

    if not saw_end:
        log.info(f"  WARNING: dump timed out after {len(samples)} samples"
                 f" (expected {header_count})")
    return samples


def percentile(sorted_vals: List[int], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def print_stats(samples: List[Tuple[int, int]], log: Logger):
    log.info("")
    log.info("=" * 68)
    log.info("  Offset Statistics")
    log.info("=" * 68)
    log.info(f"  Total samples collected : {len(samples)}")

    # Histogram by status
    by_status: dict = {}
    for off, st in samples:
        by_status.setdefault(st, []).append(off)

    log.info(f"  {'Status':<12}  {'count':>6}  {'mean':>10}  {'stdev':>8}  "
             f"{'min':>10}  {'max':>10}  {'p50':>10}  {'p95':>10}  {'|abs_mean|':>10}")
    log.info(f"  {'-'*12}  {'-'*6}  {'-'*10}  {'-'*8}  {'-'*10}  "
             f"{'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")
    for st in sorted(by_status.keys()):
        vals      = by_status[st]
        abs_vals  = [abs(v) for v in vals]
        sorted_v  = sorted(vals)
        mean_v    = statistics.mean(vals)
        stdev_v   = statistics.stdev(vals) if len(vals) > 1 else 0.0
        abs_mean  = statistics.mean(abs_vals)
        p50       = percentile(sorted_v, 0.50)
        p95       = percentile(sorted_v, 0.95)
        log.info(
            f"  {STATUS_NAMES.get(st, f'#{st}'):<12}  "
            f"{len(vals):>6}  "
            f"{mean_v:>+10.0f}  "
            f"{stdev_v:>8.0f}  "
            f"{min(vals):>+10}  "
            f"{max(vals):>+10}  "
            f"{p50:>+10.0f}  "
            f"{p95:>+10.0f}  "
            f"{abs_mean:>10.0f}")
    log.info("  (all values in nanoseconds)")
    log.info("=" * 68)

    fine = by_status.get(4, [])
    if fine:
        log.info("")
        log.info("  PTP FINE-state interpretation:")
        abs_fine = [abs(v) for v in fine]
        log.info(f"    |offset| mean = {statistics.mean(abs_fine):.0f} ns")
        log.info(f"    |offset| stdev = "
                 f"{statistics.stdev(abs_fine) if len(abs_fine) > 1 else 0.0:.0f} ns")
        log.info(f"    Worst case (max |offset|) = {max(abs_fine)} ns")
        log.info("  This is the REAL PTP sync accuracy, independent of UART jitter.")


def save_csv(samples: List[Tuple[int, int]], path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write("index,offset_ns,status\n")
        for i, (off, st) in enumerate(samples):
            f.write(f"{i},{off},{st}\n")


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",   default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",  default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",     default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",    default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",   default=DEFAULT_NETMASK)
    p.add_argument("--capture-s", default=90.0, type=float,
                   help="Seconds to let PTP run after FINE, before dumping "
                        "(default 90 s = ~720 samples at 8 Hz, < 1024 wrap limit)")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--no-reset",  action="store_true",
                   help="Skip board reset and PTP setup; assume FINE already running")
    p.add_argument("--csv",       default=None,
                   help="Optional CSV output path (index,offset_ns,status)")
    p.add_argument("--log-file",  default=None)
    p.add_argument("--verbose",   action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = args.log_file or f"ptp_offset_capture_{ts}.log"
    log = Logger(log_file=log_file, verbose=args.verbose)

    log.info("=" * 68)
    log.info("  PTP Offset Capture + Statistical Analysis")
    log.info("=" * 68)
    log.info(f"Date         : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"GM  port/IP  : {args.gm_port} / {args.gm_ip}")
    log.info(f"FOL port/IP  : {args.fol_port} / {args.fol_ip}")
    log.info(f"Capture time : {args.capture_s:.0f} s after FINE")
    log.info("")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

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

            log.info("--- Start PTP ---")
            send_command(ser_fol, "ptp_mode follower", 3.0, log)
            time.sleep(0.3)
            ser_fol.reset_input_buffer()
            send_command(ser_gm, "ptp_mode master", 3.0, log)

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

        log.info("\n--- Capture phase ---")
        log.info("  Resetting offset ring buffer ...")
        resp = send_command(ser_fol, "ptp_offset_reset", 2.0, log)
        log.debug(f"    {resp.strip()}")

        log.info(f"  Capturing offsets for {args.capture_s:.0f} s "
                 f"(no UART traffic during capture) ...")
        t_start = time.monotonic()
        while time.monotonic() - t_start < args.capture_s:
            time.sleep(1.0)
            elapsed = time.monotonic() - t_start
            sys.stdout.write(f"\r    t={elapsed:6.1f}s / {args.capture_s:.0f}s")
            sys.stdout.flush()
        sys.stdout.write("\r" + " " * 40 + "\r")

        log.info("  Dumping ring buffer ...")
        samples = dump_offsets(ser_fol, log)
        log.info(f"  Received {len(samples)} samples.")

        print_stats(samples, log)

        if args.csv:
            save_csv(samples, args.csv)
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
