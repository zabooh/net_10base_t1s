#!/usr/bin/env python3
"""pd10_filter_freeze_test.py — does the drift IIR cause the residual jitter?

Hypothesis: the cross-board PD10 jitter measured by pd10_sync_check.py
(~7 µs MAD on 10 s, ~39 µs MAD on 60 s) is dominated by the PTP_CLOCK
drift IIR filter wandering between Sync samples, NOT by the cyclic_fire
TC1-ISR backend itself.

Test design:

  Phase A — PTP fully active.  Both boards exchange Sync/FollowUp every
            125 ms; the follower's drift filter updates each anchor.
            Cross-board delta = (filter wander) + (TC1-ISR jitter)
                              + (constant phase offset).

  Phase B — Disable the master (`ptp_mode off`).  The follower stops
            receiving Sync frames, so PTP_CLOCK_Update() is never called
            again.  Both boards' s_drift_ppb and s_anchor_* freeze at
            their last filtered values.  Cross-board delta now contains
            only:
                - linear DRIFT trend  (residual frequency mismatch
                  between the two frozen drift_ppb values applied to
                  the two TC0s — typically 1-100 ppm depending on how
                  far the filter had converged)
                - TC1-ISR jitter only (no filter, no Sync)

Auswertung: linear-detrend the delta vs time, the MAD of the residual is
the pure ISR-only jitter floor.  If Phase B detrended MAD << Phase A
detrended MAD, the IIR filter is confirmed as the dominant jitter source.

Workflow (unattended, just have the boards + Saleae wired):

  1. Reset both boards via UART.
  2. Set GM to master, FOL to follower.
  3. Poll FOL UART for "PTP FINE".
  4. Settle a few seconds for cyclic_fire alignment.
  5. Phase A capture (--phase-a-s, default 30 s).
  6. Send `ptp_mode off` on GM, brief settle for last in-flight Sync.
  7. Phase B capture (--phase-b-s, default 30 s).
  8. For both phases: pair edges, linear-fit delta vs time, compute
     raw MAD + slope (ppm) + detrended MAD.
  9. Side-by-side report + verdict (filter dominant if reduction > 2x).

All output -> pd10_filter_freeze_<ts>/run_<ts>.log + per-phase CSV.

Usage:
    python pd10_filter_freeze_test.py
    python pd10_filter_freeze_test.py --phase-a-s 60 --phase-b-s 60
    python pd10_filter_freeze_test.py --no-prep   # skip reset+lock
"""

import argparse
import csv
import datetime
import re
import statistics
import sys
import time
from pathlib import Path

from cyclic_fire_hw_test import (                            # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from ptp_drift_compensate_test import Logger, open_port      # noqa: E402
from pd10_sync_check import (                                # noqa: E402
    DEFAULT_GM_PORT, DEFAULT_FOL_PORT, DEFAULT_SAMPLE_HZ,
    DEFAULT_FINE_TO_S, DEFAULT_SETTLE_S,
    banner, verbose_send, drain_and_log,
    reset_boards, set_modes, wait_for_fine,
    mad, percentile, median_period_us, cross_board_delta_us,
)

DEFAULT_PHASE_A_S    = 30.0
DEFAULT_PHASE_B_S    = 30.0
DEFAULT_OFF_SETTLE_S = 1.5     # let GM finish in-flight Sync after `ptp_mode off`
DEFAULT_DOMINANT_X   = 2.0     # filter dominant if A_mad / B_mad >= this


# ---------------------------------------------------------------------------
# Pairing that returns ch0 timestamps too (so we can fit delta vs time)
# ---------------------------------------------------------------------------

def pair_with_times(rising_a, rising_b, bracket_s=0.499):
    """Like cross_board_delta_us(), but returns parallel lists
    (ch0_t_s, delta_us) so the caller can run a linear regression."""
    if not rising_a or not rising_b:
        return [], []
    ts, deltas = [], []
    j = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        cand = []
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = rising_b[k] - ta
                if abs(d) <= bracket_s:
                    cand.append(d)
        if cand:
            ts.append(ta)
            deltas.append(min(cand, key=abs) * 1e6)
    return ts, deltas


# ---------------------------------------------------------------------------
# Linear regression + detrend analysis
# ---------------------------------------------------------------------------

def linear_fit(xs, ys):
    """Plain ordinary-least-squares — no numpy dependency.
    Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0, my
    slope = num / den
    return slope, my - slope * mx


def analyse_phase(label, ch0_t, deltas_us, log):
    """Print + return raw stats and detrended stats for one phase."""
    n = len(deltas_us)
    if n == 0:
        log.info(f"  [{label}] no paired edges — cannot analyse")
        return None
    raw_mad     = mad(deltas_us)
    raw_median  = statistics.median(deltas_us)
    raw_min     = min(deltas_us)
    raw_max     = max(deltas_us)
    if n >= 3:
        slope, intercept = linear_fit(ch0_t, deltas_us)
        residuals = [deltas_us[i] - (slope * ch0_t[i] + intercept) for i in range(n)]
        res_mad   = mad(residuals)
    else:
        slope     = 0.0
        intercept = raw_median
        residuals = [d - raw_median for d in deltas_us]
        res_mad   = raw_mad
    log.info(f"  [{label}]")
    log.info(f"    paired edges      : {n}")
    log.info(f"    raw median        : {raw_median:>+10.2f} µs")
    log.info(f"    raw min..max      : {raw_min:>+10.2f} .. {raw_max:>+10.2f} µs")
    log.info(f"    raw MAD           : {raw_mad:>10.2f} µs   "
             "(includes linear drift trend)")
    log.info(f"    linear slope      : {slope:>+10.3f} µs/s = "
             f"{slope:+.3f} ppm")
    log.info(f"    detrended MAD     : {res_mad:>10.2f} µs   "
             "<-- jitter floor around the trend")
    return {
        "n": n, "raw_median_us": raw_median, "raw_min_us": raw_min,
        "raw_max_us": raw_max, "raw_mad_us": raw_mad,
        "slope_us_per_s": slope, "intercept_us": intercept,
        "residual_mad_us": res_mad, "ch0_t": ch0_t, "deltas_us": deltas_us,
        "residuals_us": residuals,
    }


# ---------------------------------------------------------------------------
# Capture wrapper
# ---------------------------------------------------------------------------

def do_capture(label, duration_s, sample_rate, out_dir, log):
    banner(f"Saleae capture — {label} ({duration_s:.1f} s)", log)
    mgr, cap = start_saleae_capture(sample_rate, duration_s, log)
    cap.wait()
    sub = out_dir / label.replace(" ", "_")
    sub.mkdir(parents=True, exist_ok=True)
    csv_path = export_capture_csv(cap, sub, log)
    cap.close()
    rising, _ = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  Channel 0 rising edges: {len(ra)}")
    log.info(f"  Channel 1 rising edges: {len(rb)}")
    pa = median_period_us(ra)
    pb = median_period_us(rb)
    if pa == pa and pb == pb:
        log.info(f"  Ch0 period: {pa:>10,.1f} µs   "
                 f"Ch1 period: {pb:>10,.1f} µs   "
                 f"({abs(pa-pb)/pa*1e6:.1f} ppm)")
    return pair_with_times(ra, rb)


def write_phase_csv(out_dir, label, stats):
    p = out_dir / f"deltas_{label}.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_idx", "ch0_t_s", "delta_us", "residual_us"])
        for i, (t, d, r) in enumerate(zip(stats["ch0_t"],
                                          stats["deltas_us"],
                                          stats["residuals_us"])):
            w.writerow([i, f"{t:.9f}", f"{d:+.4f}", f"{r:+.4f}"])
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--phase-a-s",       type=float, default=DEFAULT_PHASE_A_S,
                   help="capture duration with PTP active (default 30)")
    p.add_argument("--phase-b-s",       type=float, default=DEFAULT_PHASE_B_S,
                   help="capture duration with drift filter frozen (default 30)")
    p.add_argument("--off-settle-s",    type=float, default=DEFAULT_OFF_SETTLE_S,
                   help="seconds after `ptp_mode off` before Phase B capture")
    p.add_argument("--sample-rate",     type=int,   default=DEFAULT_SAMPLE_HZ)
    p.add_argument("--fine-timeout-s",  type=float, default=DEFAULT_FINE_TO_S)
    p.add_argument("--settle-s",        type=float, default=DEFAULT_SETTLE_S,
                   help="seconds between FINE and Phase A capture")
    p.add_argument("--dominant-x",      type=float, default=DEFAULT_DOMINANT_X,
                   help="A/B detrended-MAD ratio >= this -> filter declared dominant")
    p.add_argument("--no-prep", action="store_true",
                   help="skip reset+mode setup; assume boards already locked")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"pd10_filter_freeze_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 drift-filter freeze test (Phase A vs Phase B)", log)
    log.info(f"  GM port      : {args.gm_port}  (Saleae Ch0)")
    log.info(f"  FOL port     : {args.fol_port}  (Saleae Ch1)")
    log.info(f"  Phase A      : {args.phase_a_s:.1f} s   PTP active "
             "(filter updating each Sync)")
    log.info(f"  Phase B      : {args.phase_b_s:.1f} s   drift filter frozen "
             "(`ptp_mode off` on GM)")
    log.info(f"  Sample rate  : {args.sample_rate/1_000_000:.0f} MS/s")
    log.info(f"  Verdict gate : A.detrended_MAD / B.detrended_MAD "
             f">= {args.dominant_x:.1f}x -> filter is dominant noise source")
    log.info(f"  Output       : {out_dir.resolve()}")

    try:
        gm  = open_port(args.gm_port)
        fol = open_port(args.fol_port)
    except Exception as exc:
        sys.exit(f"[ERROR] could not open serial ports: {exc}")

    # ----- Step 1: prep -----
    if args.no_prep:
        banner("Step 1 — prep SKIPPED (--no-prep, assuming already locked)", log)
    else:
        banner("Step 1 — reset boards + set master / follower mode", log)
        reset_boards(gm, fol, log)
        set_modes(gm, fol, log)

        banner("Step 2 — wait for PTP FINE on the follower", log)
        elapsed = wait_for_fine(fol, args.fine_timeout_s, log)
        if elapsed is None:
            log.info("  WARNING: follower never reported FINE within timeout.")
            log.info("           Continuing — Phase A may include warm-up noise.")
        log.info(f"  Settling {args.settle_s:.0f} s for cyclic_fire alignment ...")
        time.sleep(args.settle_s)

    # Close serial ports during Saleae captures so they don't add SPI
    # contention or USB-CDC IRQ load to the boards.
    gm.close(); fol.close()

    # ----- Phase A capture -----
    ch0_t_a, deltas_a = do_capture("phase_a_PTP_active",
                                   args.phase_a_s, args.sample_rate, out_dir, log)

    # ----- Disable PTP on master -----
    banner("Step 3 — disable PTP on GM (`ptp_mode off`) — drift filter freezes", log)
    try:
        gm = open_port(args.gm_port)
    except Exception as exc:
        sys.exit(f"[ERROR] could not reopen GM port: {exc}")
    verbose_send(gm, "ptp_mode off", log, "GM ")
    drain_and_log(gm, log, "GM ", 0.5)
    gm.close()
    log.info(f"  Settling {args.off_settle_s:.1f} s so the GM's last in-flight")
    log.info(f"  Sync drains and the FOL drift filter is firmly frozen ...")
    time.sleep(args.off_settle_s)

    # ----- Phase B capture -----
    ch0_t_b, deltas_b = do_capture("phase_b_filter_frozen",
                                   args.phase_b_s, args.sample_rate, out_dir, log)

    # ----- Analyse -----
    banner("Step 4 — per-phase analysis (raw + detrended)", log)
    stats_a = analyse_phase("Phase A — PTP active", ch0_t_a, deltas_a, log)
    log.info("")
    stats_b = analyse_phase("Phase B — drift filter frozen", ch0_t_b, deltas_b, log)

    if stats_a is None or stats_b is None:
        log.info("")
        log.info("  ERROR: missing edges in at least one phase, cannot compare.")
        log.info("         Inspect deltas_phase_*.csv in the output dir.")
        return 3

    write_phase_csv(out_dir, "phase_a_PTP_active",     stats_a)
    write_phase_csv(out_dir, "phase_b_filter_frozen",  stats_b)

    # ----- Side-by-side -----
    banner("Verdict — does freezing the IIR drop the jitter?", log)
    a_mad   = stats_a["residual_mad_us"]
    b_mad   = stats_b["residual_mad_us"]
    a_slope = stats_a["slope_us_per_s"]
    b_slope = stats_b["slope_us_per_s"]
    log.info(f"                          Phase A (PTP)    Phase B (frozen)")
    log.info(f"  paired edges         :  {stats_a['n']:>13d}    "
             f"{stats_b['n']:>13d}")
    log.info(f"  raw MAD (µs)         :  {stats_a['raw_mad_us']:>13.2f}    "
             f"{stats_b['raw_mad_us']:>13.2f}")
    log.info(f"  slope (ppm)          :  {a_slope:>+13.3f}    "
             f"{b_slope:>+13.3f}")
    log.info(f"  detrended MAD (µs)   :  {a_mad:>13.2f}    "
             f"{b_mad:>13.2f}")

    if b_mad > 0:
        ratio = a_mad / b_mad
    else:
        ratio = float("inf")
    log.info("")
    log.info(f"  detrended-MAD ratio A/B = {ratio:.2f}x")
    log.info(f"  threshold to declare filter dominant = {args.dominant_x:.2f}x")
    if ratio >= args.dominant_x:
        log.info("")
        log.info(f"  -> DRIFT IIR FILTER CONFIRMED AS DOMINANT JITTER SOURCE")
        log.info(f"     Freezing the filter (Phase B) reduced the per-edge")
        log.info(f"     jitter floor by {ratio:.2f}x.  The remaining "
                 f"{b_mad:.2f} µs MAD is the floor set by TC1-ISR latency +")
        log.info(f"     cyclic_fire decimator phase computation.")
        verdict = "FILTER DOMINANT"
    else:
        log.info("")
        log.info(f"  -> filter NOT dominant (reduction only {ratio:.2f}x < "
                 f"{args.dominant_x:.2f}x).")
        log.info(f"     Other noise sources (TC1-ISR, decimator, anchor jitter)")
        log.info(f"     contribute comparably to the filter.  Tightening the")
        log.info(f"     filter alone won't materially improve cross-board MAD;")
        log.info(f"     consider hardware GPIO triggers anchored to PTP_CLOCK.")
        verdict = "FILTER NOT DOMINANT"

    # Sanity note about Phase B slope: if very large (hundreds of ppm),
    # the filter hadn't converged when GM was disabled — that's still a
    # valid jitter-floor measurement (the slope is detrended out), but
    # worth flagging.
    abs_b_slope_ppm = abs(b_slope)
    if abs_b_slope_ppm > 100:
        log.info("")
        log.info(f"  Note: Phase B slope is {b_slope:+.1f} ppm — the filter")
        log.info(f"  hadn't fully converged when freezing.  Detrended MAD is")
        log.info(f"  unaffected, but for a lower-slope freeze try a longer")
        log.info(f"  --settle-s before Phase A so the filter has more time")
        log.info(f"  to lock onto the actual crystal mismatch.")

    # ----- Summary CSV -----
    summary = out_dir / "summary.csv"
    with open(summary, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "n", "raw_median_us", "raw_mad_us",
                    "slope_ppm", "detrended_mad_us"])
        for label, st in (("A_PTP_active", stats_a),
                          ("B_filter_frozen", stats_b)):
            w.writerow([label, st["n"],
                        f"{st['raw_median_us']:+.3f}",
                        f"{st['raw_mad_us']:.3f}",
                        f"{st['slope_us_per_s']:+.4f}",
                        f"{st['residual_mad_us']:.3f}"])
        w.writerow(["VERDICT", verdict, "",
                    f"ratio_A_over_B={ratio:.3f}",
                    f"threshold={args.dominant_x:.2f}", ""])

    log.info("")
    log.info(f"  Per-phase CSVs : {out_dir}/deltas_phase_*.csv")
    log.info(f"  Summary CSV    : {summary}")
    log.info(f"  Log file       : {log_path}")
    return 0 if verdict == "FILTER DOMINANT" else 1


if __name__ == "__main__":
    sys.exit(main())
