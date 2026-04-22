#!/usr/bin/env python3
"""pd10_phase_diag.py — diagnose the PD10 phase mismatch

When the demo's two PD10 outputs visibly drift ~180° apart even though
the PTP servo reports sub-µs lock, we want to know which of these is
true:

  (a) The PTP servo is fine but the SOFTWARE PTP_CLOCK on the two
      boards differs by ~N × 250 ms (cumulative software-anchor offset
      via PTP_FOL_ANCHOR_OFFSET_NS / PTP_GM_ANCHOR_OFFSET_NS),
      and the slot-bit math in standalone_demo lands on opposite parities.
  (b) The PTP servo isn't actually fine, and the MAC clocks themselves
      drift apart over time.
  (c) The decimator on the two boards samples at very different real
      moments (interrupt latency / cycle alignment).

This test measures BOTH:

  1. clk_get bracketing — interleaved GM/FOL/GM clk_get reads, compute
     the FOL PTP_CLOCK offset from GM PTP_CLOCK at the same real
     moment.  Any large delta here directly explains a phase mismatch.
  2. Saleae capture — actual PD10 rising edge cross-board delta.

If (1) ≈ (2), it's case (a).  If (1) ≪ (2), the decimator timing is
the issue.  If (1) is large and changes between captures, the servo
isn't holding lock.

Bench-friendly: brings the boards into master/follower mode itself
via the existing CLI (ptp_mode master/follower), waits for FINE,
runs the measurements, prints + logs results.

Usage:
    python pd10_phase_diag.py
    python pd10_phase_diag.py --gm-port COM10 --fol-port COM8
    python pd10_phase_diag.py --no-prep         # skip CLI prep, boards
                                                # already in master/follower
    python pd10_phase_diag.py --capture-s 10
"""

import argparse
import csv
import datetime
import re
import statistics
import sys
import time
from pathlib import Path

import serial

from cyclic_fire_hw_test import (                            # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from ptp_drift_compensate_test import (                       # noqa: E402
    Logger, open_port, send_command,
)


RE_CLK_GET = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")


def banner(title, log):
    log.info("")
    log.info("=" * 72)
    log.info(f"  {title}")
    log.info("=" * 72)


def get_clk(ser, log):
    resp = send_command(ser, "clk_get", 1.5, log=None)
    m = RE_CLK_GET.search(resp)
    if not m:
        return None
    return int(m.group(1))


def bracketing_round(ser_gm, ser_fol):
    """One bracketed measurement: GM, FOL, GM (with PC monotonic
    timestamps interleaved).  Returns (delta_ns_fol_minus_gm_interp,
    pc_window_us) or None if any read failed."""
    t0 = time.monotonic()
    g1 = get_clk(ser_gm, None)
    t1 = time.monotonic()
    f  = get_clk(ser_fol, None)
    t2 = time.monotonic()
    g2 = get_clk(ser_gm, None)
    t3 = time.monotonic()
    if g1 is None or f is None or g2 is None:
        return None
    rt_gm1 = (t0 + t1) / 2.0
    rt_fol = (t1 + t2) / 2.0
    rt_gm2 = (t2 + t3) / 2.0
    if rt_gm2 == rt_gm1:
        return None
    g_at_fol = g1 + (g2 - g1) * (rt_fol - rt_gm1) / (rt_gm2 - rt_gm1)
    delta_ns = f - g_at_fol
    pc_window_us = (t3 - t0) * 1e6
    return delta_ns, pc_window_us


def cross_board_pd10_us(rising_a, rising_b):
    if not rising_a or not rising_b:
        return []
    bracket = 0.300
    deltas = []
    j = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        cand = []
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = rising_b[k] - ta
                if abs(d) <= bracket:
                    cand.append(d)
        if cand:
            deltas.append(min(cand, key=abs) * 1e6)
    return deltas


def fmt_ns(ns):
    """Print a (possibly large) ns value as ms.µs for readability."""
    if abs(ns) >= 1_000_000_000:
        return f"{ns/1e9:+.6f} s   ({ns:+,} ns)"
    if abs(ns) >= 1_000_000:
        return f"{ns/1e6:+.3f} ms  ({ns:+,} ns)"
    if abs(ns) >= 1_000:
        return f"{ns/1e3:+.3f} µs  ({ns:+,} ns)"
    return f"{ns:+,} ns"


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default="COM10",
                   help="serial port of the master board (Saleae Ch0)")
    p.add_argument("--fol-port", default="COM8",
                   help="serial port of the follower board (Saleae Ch1)")
    p.add_argument("--no-prep",  action="store_true",
                   help="skip reset+ptp_mode prep — boards already locked")
    p.add_argument("--capture-s", type=float, default=5.0,
                   help="Saleae capture duration in seconds (default 5)")
    p.add_argument("--sample-rate", type=int, default=50_000_000,
                   help="Saleae sample rate (default 50 MS/s)")
    p.add_argument("--rounds", type=int, default=7,
                   help="clk_get bracketing rounds (default 7)")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"pd10_phase_diag_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 phase diagnosis: PTP_CLOCK delta vs visible PD10 delta", log)
    log.info(f"  GM port      : {args.gm_port}")
    log.info(f"  FOL port     : {args.fol_port}")
    log.info(f"  Capture      : {args.capture_s:.1f} s @ "
             f"{args.sample_rate/1_000_000:.0f} MS/s")
    log.info(f"  Rounds       : {args.rounds}")
    log.info(f"  Output       : {out_dir.resolve()}")

    # ----- Open ports + bring boards into master/follower -----
    try:
        gm  = open_port(args.gm_port)
        fol = open_port(args.fol_port)
    except Exception as exc:
        sys.exit(f"[ERROR] could not open serial ports: {exc}")

    if not args.no_prep:
        banner("Step 1 — Bring boards into master/follower via CLI", log)
        log.info("  Reset both ...")
        gm.write(b"reset\r\n"); fol.write(b"reset\r\n")
        time.sleep(4)
        gm.reset_input_buffer(); fol.reset_input_buffer()
        log.info("  ptp_mode master / follower ...")
        gm.write(b"ptp_mode master\r\n");   time.sleep(0.5)
        fol.write(b"ptp_mode follower\r\n")
        log.info("  Waiting 8 s for PTP FINE ...")
        time.sleep(8)
    else:
        log.info("  --no-prep: assuming boards already PTP-locked")

    # ----- Step 2: clk_get bracketing -----
    banner(f"Step 2 — clk_get bracketing ({args.rounds} rounds)", log)
    deltas_ns = []
    abs_clk_gm  = []   # for showing the raw values too
    abs_clk_fol = []
    for i in range(args.rounds):
        # also capture the raw clk_get strings for the log
        gm.reset_input_buffer()
        gm.write(b"clk_get\r\n")
        time.sleep(0.05)
        rg = gm.read(2048).decode("ascii", "replace")
        mg = RE_CLK_GET.search(rg)
        fol.reset_input_buffer()
        fol.write(b"clk_get\r\n")
        time.sleep(0.05)
        rf = fol.read(2048).decode("ascii", "replace")
        mf = RE_CLK_GET.search(rf)
        if mg and mf:
            abs_clk_gm.append(int(mg.group(1)))
            abs_clk_fol.append(int(mf.group(1)))
            log.info(f"  [{i+1:2}]  GM={int(mg.group(1)):>20,} ns   "
                     f"FOL={int(mf.group(1)):>20,} ns")
        # bracketed measurement for proper interpolated delta
        r = bracketing_round(gm, fol)
        if r is None:
            log.info(f"        bracketing round {i+1} failed")
            continue
        d, pc_us = r
        deltas_ns.append(d)
        log.info(f"        bracketed delta (FOL−GM_interp) = "
                 f"{fmt_ns(d)}   PC window {pc_us:.0f} µs")

    if not deltas_ns:
        sys.exit("[ERROR] all bracketing rounds failed")

    deltas_sorted = sorted(deltas_ns)
    median_clk_delta = statistics.median(deltas_ns)
    log.info("")
    log.info(f"  PTP_CLOCK delta (FOL − GM) — median across rounds: "
             f"{fmt_ns(median_clk_delta)}")
    log.info(f"  spread (max−min): {fmt_ns(deltas_sorted[-1] - deltas_sorted[0])}")

    # ----- Step 3: Saleae PD10 capture -----
    banner(f"Step 3 — Saleae PD10 capture ({args.capture_s:.1f} s)", log)
    mgr, cap = start_saleae_capture(args.sample_rate, args.capture_s, log)
    cap.wait()
    csv_path = export_capture_csv(cap, out_dir, log)
    cap.close()

    rising, _ = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  Channel 0 rising edges: {len(ra)}")
    log.info(f"  Channel 1 rising edges: {len(rb)}")
    pd10_deltas_us = cross_board_pd10_us(ra, rb)
    if not pd10_deltas_us:
        log.info("  WARN: no paired PD10 edges in capture")
        median_pd10_us = float("nan")
    else:
        pd10_sorted = sorted(pd10_deltas_us)
        median_pd10_us = statistics.median(pd10_deltas_us)
        log.info(f"  PD10 delta (Ch1 − Ch0) — median: {median_pd10_us:+.3f} µs   "
                 f"= {median_pd10_us/1000:+.3f} ms")
        log.info(f"  range min..max: {pd10_sorted[0]:+.3f} .. {pd10_sorted[-1]:+.3f} µs")

    # ----- Step 4: Diagnosis -----
    banner("Step 4 — Diagnosis", log)
    clk_delta_ms = median_clk_delta / 1e6
    log.info(f"  PTP_CLOCK FOL−GM (software clock):  {clk_delta_ms:+.3f} ms")
    if median_pd10_us == median_pd10_us:   # not NaN
        log.info(f"  PD10 phase Ch1−Ch0    (visible):     {median_pd10_us/1000:+.3f} ms")
    log.info("")

    # Compare: with the standalone_demo's 500 ms slot mod-2 logic, a
    # PTP_CLOCK delta of N ms produces a PD10 phase shift of N ms
    # too (the decimator samples each board's local PTP_CLOCK, so the
    # offset propagates 1:1).  Any difference between the two numbers
    # above is decimator-call-time jitter (~250 µs typical).
    half_period_ms = 500.0
    odd_slot_off = (abs(clk_delta_ms) % (2 * half_period_ms))
    if odd_slot_off > half_period_ms:
        odd_slot_off = 2 * half_period_ms - odd_slot_off
    log.info(f"  PTP_CLOCK delta mod 1000 ms, folded to [0..500]: "
             f"{odd_slot_off:.3f} ms")
    if odd_slot_off > 250:
        log.info("  → Boards land in OPPOSITE 500 ms slots → 180° LED phase.")
        log.info("    Root cause is the software-clock offset, not visible jitter.")
    elif odd_slot_off > 50:
        log.info("  → Boards in the SAME slot but visibly offset by tens of ms.")
    else:
        log.info("  → Boards' software clocks are aligned within 50 ms — slot-bit")
        log.info("    parity should match, LEDs should be in phase.")

    # CSV summary for cross-run comparison
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clk_delta_median_ms", "clk_delta_spread_ms",
                    "pd10_phase_median_ms", "pd10_phase_n",
                    "n_rounds"])
        w.writerow([f"{clk_delta_ms:+.3f}",
                    f"{(deltas_sorted[-1]-deltas_sorted[0])/1e6:.3f}",
                    f"{median_pd10_us/1000:+.3f}" if median_pd10_us == median_pd10_us else "NaN",
                    len(pd10_deltas_us),
                    len(deltas_ns)])
    log.info(f"\n  Summary CSV: {out_dir/'summary.csv'}")
    log.info(f"  Log file   : {log_path}")

    gm.close(); fol.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
