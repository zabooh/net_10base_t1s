#!/usr/bin/env python3
"""pd10_sync_before_after_test.py — PD10 cross-board sync: before/after.

Two-phase Saleae capture that visualises what PTP synchronisation does
to the PD10 cyclic_fire output on two boards:

  Phase A — UNSYNCED
    Reset both boards, disable PTP (`ptp_mode off`), start cyclic_fire
    in free-run mode on each board (`cyclic_start_free <period_us>`).
    Each board drives PD10 from its own local TC0 crystal — no PTP
    discipline, no cross-board alignment.  Saleae captures both PD10
    channels for --unsync-duration-s.

  Phase B — SYNCED
    Stop cyclic_fire on both boards, put GM into `ptp_mode master` and
    FOL into `ptp_mode follower`, poll FOL UART for `PTP FINE`, settle,
    then start the PTP-synced cyclic_fire (`cyclic_start <period_us>`)
    on both boards.  Saleae captures both PD10 channels again for
    --sync-duration-s.

Per phase the script produces:

  - Ch0 interval histogram  (time deltas between consecutive PD10
                             transitions on Ch0 — any edge, low→high
                             or high→low.  Shows per-board jitter of
                             the rectangle half-period.)
  - Ch1 interval histogram  (same for Ch1)
  - Cross-board drift plot  (for each Ch0 rising edge, the offset to
                             the nearest Ch1 rising edge, unwrapped so
                             a cumulative drift stays monotonic rather
                             than wrapping at the period boundary)

At the end the script writes side-by-side comparison plots
(unsync vs sync) for the interval histograms and for the drift curves.
The drift comparison visualises the central product claim: unsynced
boards drift apart linearly at crystal-mismatch rate (tens of ppm ->
hundreds of µs per 10 s), synced boards stay bounded inside a few µs
MAD.

All output goes into pd10_sync_before_after_<ts>/ together with per-
phase CSVs (edges, intervals, drift) and a single-row summary.csv.

Usage:
    python pd10_sync_before_after_test.py
    python pd10_sync_before_after_test.py --gm-port COM10 --fol-port COM8
    python pd10_sync_before_after_test.py --unsync-duration-s 20
    python pd10_sync_before_after_test.py --period-us 1000
"""

import argparse
import csv
import datetime
import statistics
import sys
import time
from pathlib import Path

from cyclic_fire_hw_test import (                            # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from pd10_sync_check import (                                # noqa: E402
    SerialDrainer, banner, drain_and_log, mad, percentile,
    reset_boards, verbose_send, wait_for_fine,
)
from ptp_drift_compensate_test import Logger, open_port      # noqa: E402

DEFAULT_GM_PORT      = "COM10"          # Saleae Ch0 board
DEFAULT_FOL_PORT     = "COM8"           # Saleae Ch1 board
DEFAULT_PERIOD_US    = 1000             # PD10 rectangle full period (1 kHz)
DEFAULT_CYCLIC_US    = 0                # 0 = leave firmware default (500 us);
                                        # any positive value gets pushed via
                                        # the `demo_cyclic_period <us>` CLI
                                        # before Phase A starts.
DEFAULT_UNSYNC_S     = 10.0
DEFAULT_SYNC_S       = 10.0
DEFAULT_SAMPLE_HZ    = 50_000_000       # 20 ns resolution
DEFAULT_FINE_TO_S    = 30.0
DEFAULT_SETTLE_S     = 3.0

# PASS/FAIL gates.  The synced phase must pull the cross-board drift
# rate close to zero (< 5 ppm) and keep the per-edge scatter inside a
# 50 µs band — both well below what a locked-to-sub-µs PTP servo should
# deliver in this firmware.  The unsync gate is a sanity check that the
# test actually captured the before-state: crystal mismatch on these
# SAM E54 boards is always at least 20 ppm, so if we see less than that
# in UNSYNC the ptp_mode-off step didn't take effect.
GATE_SYNC_SLOPE_PPM   = 5.0
GATE_SYNC_MAD_US      = 50.0
GATE_UNSYNC_MIN_PPM   = 20.0


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def apply_cyclic_period(gm, fol, cyclic_period_us, log):
    """Optional: push a new cyclic_fire rectangle period to both boards
    via `demo_cyclic_period <us>`.  Controls fire_callback rate (fire
    runs every cyclic_period_us/2).  cyclic_period_us == 0 leaves the
    firmware default (500 µs → 250 µs fire rate) in place."""
    if cyclic_period_us <= 0:
        log.info("  (cyclic period: leaving firmware default, 500 us)")
        return
    verbose_send(gm,  f"demo_cyclic_period {cyclic_period_us}", log, "GM ")
    verbose_send(fol, f"demo_cyclic_period {cyclic_period_us}", log, "FOL")
    drain_and_log(gm,  log, "GM ", 0.3)
    drain_and_log(fol, log, "FOL", 0.3)


def start_free_run(gm, fol, period_us, log):
    """Phase A setup.  Both boards: PTP off so PTP_CLOCK free-runs on
    the local TC0 crystal (no Sync frames, no drift_ppb updates).  Demo
    keeps running with its default 250 µs fire_callback cadence; we
    just lower its PD10 slot from 500 ms to `period_us/2` µs so the 10 s
    Saleae capture yields ~20 000 transitions per channel instead of
    the default ~20.

    The demo's decimator reads PTP_CLOCK on every fire and derives PD10
    level from (wc_ns / slot_ns) & 1 — so PD10 tracks PTP_CLOCK
    directly.  This is why the sync phase reaches sub-µs accuracy:
    when two PTP_CLOCKs are locked, both boards' decimators compute
    identical slot parity.  The SQUARE-pattern cyclic_fire approach
    (earlier attempt, run 20260423_204939) drove PD10 from TC1 ISR
    compare-match time, which inherits TC1's local-crystal error and
    was observed to hold only ~116 ppm even after PTP FINE."""
    verbose_send(gm,  "ptp_mode off", log, "GM ")
    verbose_send(fol, "ptp_mode off", log, "FOL")
    drain_and_log(gm,  log, "GM ", 0.5)
    drain_and_log(fol, log, "FOL", 0.5)
    half_period_us = period_us // 2
    verbose_send(gm,  f"demo_pd10_slot {half_period_us}", log, "GM ")
    verbose_send(fol, f"demo_pd10_slot {half_period_us}", log, "FOL")
    drain_and_log(gm,  log, "GM ", 0.3)
    drain_and_log(fol, log, "FOL", 0.3)


def stop_cyclic(gm, fol, log):
    """No-op on the new workflow: we don't stop cyclic_fire between
    phases any more — the demo owns it, and its PTP_CLOCK-driven
    decimator works identically across the PTP mode change.  Kept as
    a placeholder in case the test grows a third phase that needs a
    clean cyclic restart."""
    (void_log := log)        # keep the signature stable
    _ = (gm, fol)


def start_synced(gm, fol, period_us, fine_timeout_s, settle_s, log):
    """Phase B setup.  PTP master/follower, wait for FINE, settle, then
    start the PTP-synced cyclic_fire on both boards."""
    verbose_send(gm, "ptp_mode master", log, "GM ")
    drain_and_log(gm, log, "GM ", 1.0)
    verbose_send(fol, "ptp_mode follower", log, "FOL")
    elapsed = wait_for_fine(fol, fine_timeout_s, log)
    fine_ok = elapsed is not None
    if not fine_ok:
        log.info("  WARNING: follower never reported FINE within timeout.")
        log.info("           Phase B will capture anyway — expect poor sync.")
    log.info(f"  Settling {settle_s:.0f} s for servo convergence ...")
    time.sleep(settle_s)
    # The demo's cyclic_fire + decimator is already running at the
    # slot we set in Phase A (demo_pd10_slot <half_period_us>).  Across
    # the ptp_mode transition, the decimator keeps firing and now reads
    # the PTP-synced wallclock instead of the local free-run one — so
    # PD10 on both boards snaps into sync without any cyclic_* command
    # being necessary.  No-op here.
    (void_period := period_us)
    _ = (gm, fol)
    return fine_ok


# ---------------------------------------------------------------------------
# Edge analysis
# ---------------------------------------------------------------------------

def all_edges_sorted(rising, falling, ch):
    """Merge rising[ch] and falling[ch] into one time-sorted list.  Used
    for per-channel interval statistics where both transition directions
    are informative (each fire_callback toggles the pin once, so the
    full edge stream shows the half-period spacing directly)."""
    merged = list(rising.get(ch, [])) + list(falling.get(ch, []))
    merged.sort()
    return merged


def intervals_us(edges):
    """Consecutive-transition deltas in µs.  For a steady rectangle this
    is the half-period distribution."""
    if len(edges) < 2:
        return []
    return [(edges[i] - edges[i - 1]) * 1e6 for i in range(1, len(edges))]


def unwrapped_cross_board_delta(rising_a, rising_b, period_us):
    """For each Ch0 rising edge find the nearest Ch1 rising edge, compute
    delta in µs, and unwrap period-wise so a cumulative linear drift
    (unsynced case) shows as a continuous line rather than zig-zagging
    between -T/2 and +T/2.

    Algorithm: nearest-edge gives a raw delta in (-T/2, +T/2] modulo T.
    We track the running 'wrap count' (integer number of full periods
    added so far) — whenever the raw delta jumps by more than T/2
    relative to the previous sample, we shift the wrap count up or down
    by one to keep the unwrapped curve continuous.

    Returns list of (ch0_time_s, delta_us) pairs."""
    if not rising_a or not rising_b:
        return []
    T_us = float(period_us)
    half_T = T_us / 2.0
    out = []
    j = 0
    prev_raw = None
    wraps = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        # Consider the two Ch1 edges bracketing ta; pick the nearest.
        best = None
        for k in (j, j + 1):
            if 0 <= k < len(rising_b):
                d = (rising_b[k] - ta) * 1e6     # µs
                if best is None or abs(d) < abs(best):
                    best = d
        if best is None:
            continue
        # Normalise into (-T/2, +T/2] in case "nearest" picked a neighbour
        # more than half a period away (can happen at the window edge).
        while best > half_T:
            best -= T_us
        while best <= -half_T:
            best += T_us
        if prev_raw is not None:
            step = best - prev_raw
            if step >  half_T:
                wraps -= 1
            elif step < -half_T:
                wraps += 1
        prev_raw = best
        out.append((ta, best + wraps * T_us))
    return out


def fit_linear_ppm(t_vals_s, d_vals_us):
    """Least-squares slope of the unwrapped drift curve, in ppm.
    Slope is µs per second -> *1e-6 -> seconds-per-second = unitless ppm
    already (µs/s is literally ppm).  Returns (slope_ppm, intercept_us)."""
    n = len(t_vals_s)
    if n < 2:
        return float("nan"), float("nan")
    mean_t = sum(t_vals_s) / n
    mean_d = sum(d_vals_us) / n
    num = sum((t_vals_s[i] - mean_t) * (d_vals_us[i] - mean_d) for i in range(n))
    den = sum((t_vals_s[i] - mean_t) ** 2 for i in range(n))
    if den == 0.0:
        return float("nan"), mean_d
    slope = num / den
    intercept = mean_d - slope * mean_t
    return slope, intercept


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _plt():
    """Lazy import — matplotlib stays optional so the whole run still
    writes CSVs if the user doesn't have it installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        return None


def plot_interval_histogram(intervals, label, png_path, log):
    plt = _plt()
    if plt is None:
        log.info("  (matplotlib not installed — skipping interval histogram)")
        return None
    if len(intervals) < 2:
        log.info(f"  ({label}: < 2 intervals — skipping histogram)")
        return None
    med = statistics.median(intervals)
    m   = mad(intervals)
    # Zoom the axis to median ± max(50 µs, 10·MAD) so the bulk is
    # visible.  Outliers that fall outside get clamped into the last bin.
    span = max(50.0, 10.0 * m) if m == m else 50.0
    lo, hi = med - span, med + span
    clamped = [min(max(v, lo), hi) for v in intervals]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(clamped, bins=60, color="#4a7cbf", edgecolor="black", alpha=0.85)
    ax.axvline(med, color="red", linestyle="--", linewidth=1.5,
               label=f"median = {med:.2f} µs")
    ax.axvline(med - m, color="orange", linestyle=":", linewidth=1.2,
               label=f"±MAD (±{m:.2f} µs)")
    ax.axvline(med + m, color="orange", linestyle=":", linewidth=1.2)
    ax.set_title(f"{label}  —  PD10 edge-to-edge interval (n={len(intervals)})")
    ax.set_xlabel("interval between consecutive transitions  (µs)")
    ax.set_ylabel("count")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(png_path, dpi=120)
    plt.close(fig)
    return png_path


def plot_drift(pairs, label, png_path, log):
    plt = _plt()
    if plt is None:
        log.info("  (matplotlib not installed — skipping drift plot)")
        return None
    if len(pairs) < 2:
        log.info(f"  ({label}: < 2 pairs — skipping drift plot)")
        return None
    t = [p[0] - pairs[0][0] for p in pairs]       # time since first Ch0 edge
    d = [p[1]              for p in pairs]
    slope_ppm, intercept = fit_linear_ppm(t, d)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(t, d, color="#4a7cbf", linewidth=1.0, marker=".", markersize=3,
            label="per-edge delta (Ch1 − Ch0)")
    if slope_ppm == slope_ppm:
        fit_line = [slope_ppm * ti + intercept for ti in t]
        ax.plot(t, fit_line, color="red", linestyle="--", linewidth=1.2,
                label=f"linear fit: {slope_ppm:+.2f} ppm")
    ax.set_title(f"{label}  —  cross-board drift (n={len(pairs)})")
    ax.set_xlabel("time since first Ch0 rising edge  (s)")
    ax.set_ylabel("unwrapped delta Ch1 − Ch0  (µs)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="best")
    plt.tight_layout()
    plt.savefig(png_path, dpi=120)
    plt.close(fig)
    return png_path


def plot_comparison_intervals(results, png_path, log):
    """2×2 grid: rows = Ch0/Ch1, columns = unsync/sync.  Same axis
    limits across the two phases for a given channel so the visual
    difference in spread is obvious at a glance."""
    plt = _plt()
    if plt is None:
        return None
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    for row, ch in enumerate((0, 1)):
        # Shared axis for row = ch
        all_vals = []
        for phase in ("unsync", "sync"):
            all_vals += results[phase][f"ch{ch}_intervals"]
        if not all_vals:
            continue
        med = statistics.median(all_vals)
        m   = mad(all_vals)
        span = max(50.0, 10.0 * m) if m == m else 50.0
        lo, hi = med - span, med + span
        for col, phase in enumerate(("unsync", "sync")):
            ax = axes[row][col]
            vals = results[phase][f"ch{ch}_intervals"]
            if not vals:
                ax.set_title(f"Ch{ch} / {phase.upper()}: no data")
                continue
            clamped = [min(max(v, lo), hi) for v in vals]
            vmed = statistics.median(vals)
            vmad = mad(vals)
            ax.hist(clamped, bins=60,
                    color="#4a7cbf" if phase == "unsync" else "#4caf50",
                    edgecolor="black", alpha=0.85, range=(lo, hi))
            ax.axvline(vmed, color="red", linestyle="--", linewidth=1.2)
            ax.set_title(f"Ch{ch}  /  {phase.upper()}   "
                         f"n={len(vals)}  median={vmed:.2f} µs  MAD={vmad:.2f} µs")
            ax.set_xlabel("interval between consecutive transitions  (µs)")
            ax.set_ylabel("count")
            ax.grid(True, linestyle=":", alpha=0.5)
            ax.set_xlim(lo, hi)
    fig.suptitle("PD10 per-board edge-interval distribution — UNSYNCED vs SYNCED",
                 fontsize=12)
    plt.tight_layout()
    plt.savefig(png_path, dpi=120)
    plt.close(fig)
    return png_path


def plot_comparison_drift(results, png_path, log):
    """1×2 grid: unsync drift vs sync drift, side by side.  Y-axis is
    shared across the two panels so the visual difference in amplitude
    is immediate."""
    plt = _plt()
    if plt is None:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=False)
    y_abs_max = 0.0
    for phase in ("unsync", "sync"):
        for _, d in results[phase]["drift"]:
            if abs(d) > y_abs_max:
                y_abs_max = abs(d)
    for col, phase in enumerate(("unsync", "sync")):
        ax = axes[col]
        pairs = results[phase]["drift"]
        if not pairs:
            ax.set_title(f"{phase.upper()}: no data")
            continue
        t0 = pairs[0][0]
        t = [p[0] - t0 for p in pairs]
        d = [p[1]      for p in pairs]
        slope_ppm, intercept = fit_linear_ppm(t, d)
        ax.plot(t, d, color="#4a7cbf" if phase == "unsync" else "#4caf50",
                linewidth=1.0, marker=".", markersize=3,
                label="delta Ch1 − Ch0")
        if slope_ppm == slope_ppm:
            fit_line = [slope_ppm * ti + intercept for ti in t]
            ax.plot(t, fit_line, color="red", linestyle="--", linewidth=1.2,
                    label=f"linear fit: {slope_ppm:+.2f} ppm")
        mad_us = mad(d)
        ax.set_title(f"{phase.upper()}  —  n={len(pairs)}  "
                     f"slope={slope_ppm:+.2f} ppm  MAD={mad_us:.2f} µs")
        ax.set_xlabel("time since first Ch0 rising edge  (s)")
        ax.set_ylabel("unwrapped delta Ch1 − Ch0  (µs)")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="best")
    fig.suptitle("PD10 cross-board drift — UNSYNCED vs SYNCED", fontsize=12)
    plt.tight_layout()
    plt.savefig(png_path, dpi=120)
    plt.close(fig)
    return png_path


# ---------------------------------------------------------------------------
# Per-phase analysis (writes CSVs + plots, returns a result dict)
# ---------------------------------------------------------------------------

def analyse_phase(phase_tag, csv_path, period_us, out_dir, log):
    """phase_tag ∈ {'unsync', 'sync'}.  Returns a result dict with the
    numbers used by the top-level summary + comparison plots."""
    rising, falling = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  {phase_tag}: Ch0 rising={len(ra)}  Ch1 rising={len(rb)}  "
             f"Ch0 falling={len(falling.get(0, []))}  "
             f"Ch1 falling={len(falling.get(1, []))}")

    edges0 = all_edges_sorted(rising, falling, 0)
    edges1 = all_edges_sorted(rising, falling, 1)
    iv0 = intervals_us(edges0)
    iv1 = intervals_us(edges1)

    def _stats(vals, name):
        if not vals:
            log.info(f"  {phase_tag} {name}: no data")
            return {}
        s = sorted(vals)
        d = {
            "n":      len(vals),
            "median": statistics.median(vals),
            "mad":    mad(vals),
            "p1":     percentile(s, 1),
            "p99":    percentile(s, 99),
            "min":    s[0],
            "max":    s[-1],
        }
        log.info(f"  {phase_tag} {name}:  n={d['n']}  median={d['median']:.2f} µs  "
                 f"MAD={d['mad']:.2f}  p1..p99={d['p1']:.2f}..{d['p99']:.2f}  "
                 f"min..max={d['min']:.2f}..{d['max']:.2f}")
        return d

    stats0 = _stats(iv0, "Ch0 intervals")
    stats1 = _stats(iv1, "Ch1 intervals")

    drift_pairs = unwrapped_cross_board_delta(ra, rb, period_us)
    if drift_pairs:
        t0 = drift_pairs[0][0]
        t = [p[0] - t0 for p in drift_pairs]
        d = [p[1]      for p in drift_pairs]
        slope_ppm, intercept = fit_linear_ppm(t, d)
        drift_mad_us = mad(d)
        log.info(f"  {phase_tag} drift:  n={len(drift_pairs)}  "
                 f"slope={slope_ppm:+.3f} ppm  MAD={drift_mad_us:.2f} µs  "
                 f"start={d[0]:+.1f} µs  end={d[-1]:+.1f} µs")
    else:
        slope_ppm, drift_mad_us = float("nan"), float("nan")
        log.info(f"  {phase_tag} drift: no paired rising edges")

    # Per-phase CSVs
    with open(out_dir / f"{phase_tag}_ch0_intervals_us.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["idx", "interval_us"])
        for i, v in enumerate(iv0): w.writerow([i, f"{v:.4f}"])
    with open(out_dir / f"{phase_tag}_ch1_intervals_us.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["idx", "interval_us"])
        for i, v in enumerate(iv1): w.writerow([i, f"{v:.4f}"])
    with open(out_dir / f"{phase_tag}_drift_us.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ch0_t_s", "delta_us"])
        for ta, d in drift_pairs:
            w.writerow([f"{ta:.9f}", f"{d:+.4f}"])

    # Per-phase plots
    plot_interval_histogram(iv0, f"{phase_tag.upper()} / Ch0",
                            out_dir / f"{phase_tag}_ch0_hist.png", log)
    plot_interval_histogram(iv1, f"{phase_tag.upper()} / Ch1",
                            out_dir / f"{phase_tag}_ch1_hist.png", log)
    plot_drift(drift_pairs, phase_tag.upper(),
               out_dir / f"{phase_tag}_drift.png", log)

    return {
        "ch0_intervals": iv0,
        "ch1_intervals": iv1,
        "drift":         drift_pairs,
        "stats0":        stats0,
        "stats1":        stats1,
        "slope_ppm":     slope_ppm,
        "drift_mad_us":  drift_mad_us,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--period-us",          type=int,   default=DEFAULT_PERIOD_US,
                   help="PD10 rectangle full period in µs (default 1000 = 1 kHz). "
                        "Passed to `demo_pd10_slot <period_us/2>` on both boards.")
    p.add_argument("--cyclic-period-us",    type=int,   default=DEFAULT_CYCLIC_US,
                   help="cyclic_fire sampling period in µs (default 0 = keep firmware "
                        "default of 500). Lowering this sharpens PD10 edge timing "
                        "(each edge lands within half a fire interval of the target "
                        "wallclock slot). Pushed via `demo_cyclic_period <us>` on "
                        "both boards before Phase A.")
    p.add_argument("--unsync-duration-s",  type=float, default=DEFAULT_UNSYNC_S)
    p.add_argument("--sync-duration-s",    type=float, default=DEFAULT_SYNC_S)
    p.add_argument("--sample-rate",        type=int,   default=DEFAULT_SAMPLE_HZ)
    p.add_argument("--fine-timeout-s",     type=float, default=DEFAULT_FINE_TO_S)
    p.add_argument("--settle-s",           type=float, default=DEFAULT_SETTLE_S)
    p.add_argument("--no-prep", action="store_true",
                   help="skip board reset; assume both boards are fresh-booted "
                        "and idle (no cyclic_fire running, no PTP mode set)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="background-drain both UARTs into the log throughout")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"pd10_sync_before_after_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 before/after sync test", log)
    log.info(f"  GM port         : {args.gm_port}  (Saleae Ch0)")
    log.info(f"  FOL port        : {args.fol_port}  (Saleae Ch1)")
    log.info(f"  PD10 period     : {args.period_us} µs  "
             f"({1_000_000.0/args.period_us:.1f} Hz rectangle)")
    if args.cyclic_period_us > 0:
        log.info(f"  cyclic period   : {args.cyclic_period_us} µs  "
                 f"(fire rate = {2000.0/args.cyclic_period_us:.1f} kHz)")
    else:
        log.info(f"  cyclic period   : firmware default (500 µs, fire rate 4 kHz)")
    log.info(f"  unsync capture  : {args.unsync_duration_s:.1f} s")
    log.info(f"  sync   capture  : {args.sync_duration_s:.1f} s")
    log.info(f"  sample rate     : {args.sample_rate/1_000_000:.0f} MS/s  "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  Output          : {out_dir.resolve()}")

    try:
        gm  = open_port(args.gm_port)
        fol = open_port(args.fol_port)
    except Exception as exc:
        sys.exit(f"[ERROR] could not open serial ports: {exc}")

    drainer = None

    # =========================================================================
    # PHASE A — UNSYNCED
    # =========================================================================
    banner("PHASE A — UNSYNCED  (ptp_mode off, cyclic_start_free)", log)
    if args.no_prep:
        log.info("  --no-prep: skipping reset, using current board state")
    else:
        reset_boards(gm, fol, log)
    apply_cyclic_period(gm, fol, args.cyclic_period_us, log)
    start_free_run(gm, fol, args.period_us, log)

    # Short settle so both boards are clearly in free-run before the capture
    log.info("  Settling 1 s before Saleae capture ...")
    time.sleep(1.0)

    if args.verbose:
        drainer = SerialDrainer([(gm, "GM "), (fol, "FOL")], log)
        drainer.start()

    banner(f"Phase A Saleae capture ({args.unsync_duration_s:.1f} s)", log)
    mgr_a, cap_a = start_saleae_capture(args.sample_rate,
                                        args.unsync_duration_s, log)
    cap_a.wait()
    unsync_dir = out_dir / "raw_unsync"
    csv_a = export_capture_csv(cap_a, unsync_dir, log)
    cap_a.close()

    if drainer is not None:
        drainer.stop()
        drainer = None

    # =========================================================================
    # PHASE B — SYNCED
    # =========================================================================
    banner("PHASE B — SYNCED  (ptp_mode master/follower, wait FINE, cyclic_start)",
           log)
    stop_cyclic(gm, fol, log)
    fine_ok = start_synced(gm, fol, args.period_us,
                           args.fine_timeout_s, args.settle_s, log)

    # Extra settle so the first cyclic_fire fires land after any role-change
    # anchor jump has fully propagated through PTP_CLOCK.
    log.info(f"  Extra {args.settle_s:.0f} s settle after cyclic_start "
             f"(swallow role-change anchor jump)")
    time.sleep(args.settle_s)

    if args.verbose:
        drainer = SerialDrainer([(gm, "GM "), (fol, "FOL")], log)
        drainer.start()

    banner(f"Phase B Saleae capture ({args.sync_duration_s:.1f} s)", log)
    mgr_b, cap_b = start_saleae_capture(args.sample_rate,
                                        args.sync_duration_s, log)
    cap_b.wait()
    sync_dir = out_dir / "raw_sync"
    csv_b = export_capture_csv(cap_b, sync_dir, log)
    cap_b.close()

    if drainer is not None:
        drainer.stop()
        drainer = None

    try: gm.close()
    except Exception: pass
    try: fol.close()
    except Exception: pass

    # =========================================================================
    # ANALYSIS
    # =========================================================================
    banner("Analysis — Phase A (UNSYNCED)", log)
    res_a = analyse_phase("unsync", csv_a, args.period_us, out_dir, log)
    banner("Analysis — Phase B (SYNCED)", log)
    res_b = analyse_phase("sync",   csv_b, args.period_us, out_dir, log)

    # =========================================================================
    # COMPARISON PLOTS
    # =========================================================================
    banner("Comparison plots (UNSYNCED vs SYNCED)", log)
    results = {"unsync": res_a, "sync": res_b}
    cmp_hist  = plot_comparison_intervals(results,
                                          out_dir / "comparison_intervals.png",
                                          log)
    cmp_drift = plot_comparison_drift(results,
                                      out_dir / "comparison_drift.png",
                                      log)
    if cmp_hist:  log.info(f"  wrote {cmp_hist}")
    if cmp_drift: log.info(f"  wrote {cmp_drift}")

    # =========================================================================
    # SUMMARY CSV
    # =========================================================================
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "phase",
            "ch0_n", "ch0_median_us", "ch0_mad_us",
            "ch1_n", "ch1_median_us", "ch1_mad_us",
            "drift_n", "drift_slope_ppm", "drift_mad_us",
        ])
        for tag, r in (("unsync", res_a), ("sync", res_b)):
            s0 = r["stats0"] or {}
            s1 = r["stats1"] or {}
            w.writerow([
                tag,
                s0.get("n", 0),
                f"{s0.get('median', float('nan')):.3f}",
                f"{s0.get('mad',    float('nan')):.3f}",
                s1.get("n", 0),
                f"{s1.get('median', float('nan')):.3f}",
                f"{s1.get('mad',    float('nan')):.3f}",
                len(r["drift"]),
                f"{r['slope_ppm']:+.3f}",
                f"{r['drift_mad_us']:.3f}",
            ])

    banner("Verdict", log)
    log.info(f"  UNSYNCED drift: slope={res_a['slope_ppm']:+.2f} ppm  "
             f"MAD={res_a['drift_mad_us']:.2f} µs  "
             f"(n={len(res_a['drift'])})")
    log.info(f"  SYNCED   drift: slope={res_b['slope_ppm']:+.2f} ppm  "
             f"MAD={res_b['drift_mad_us']:.2f} µs  "
             f"(n={len(res_b['drift'])})")
    if (res_a["drift_mad_us"] == res_a["drift_mad_us"] and
            res_b["drift_mad_us"] == res_b["drift_mad_us"] and
            res_b["drift_mad_us"] > 0.0):
        ratio = res_a["drift_mad_us"] / res_b["drift_mad_us"]
        log.info(f"  sync reduces cross-board drift MAD by {ratio:.1f}×")

    # --- Gate decisions ---------------------------------------------------
    checks = []
    checks.append((
        "PTP FINE reached",
        fine_ok,
        "follower reported PTP FINE" if fine_ok
        else "follower UART never showed PTP FINE within --fine-timeout-s",
    ))
    if res_b["slope_ppm"] == res_b["slope_ppm"]:   # not NaN
        sync_slope_ok = abs(res_b["slope_ppm"]) < GATE_SYNC_SLOPE_PPM
        checks.append((
            f"SYNC slope |<{GATE_SYNC_SLOPE_PPM:g} ppm|",
            sync_slope_ok,
            f"measured {res_b['slope_ppm']:+.2f} ppm",
        ))
    if res_b["drift_mad_us"] == res_b["drift_mad_us"]:
        sync_mad_ok = res_b["drift_mad_us"] < GATE_SYNC_MAD_US
        checks.append((
            f"SYNC MAD <{GATE_SYNC_MAD_US:g} us",
            sync_mad_ok,
            f"measured {res_b['drift_mad_us']:.2f} us",
        ))
    if res_a["slope_ppm"] == res_a["slope_ppm"]:
        unsync_slope_ok = abs(res_a["slope_ppm"]) > GATE_UNSYNC_MIN_PPM
        checks.append((
            f"UNSYNC slope |>{GATE_UNSYNC_MIN_PPM:g} ppm| (sanity)",
            unsync_slope_ok,
            f"measured {res_a['slope_ppm']:+.2f} ppm",
        ))

    log.info("")
    for name, ok, detail in checks:
        tag = "PASS" if ok else "FAIL"
        log.info(f"  [{tag}] {name:40s}  {detail}")
    overall_pass = all(ok for _, ok, _ in checks)
    log.info("")
    log.info(f"  OVERALL: {'PASS' if overall_pass else 'FAIL'}")

    log.info("")
    log.info(f"  Output dir : {out_dir.resolve()}")
    log.info(f"  Log file   : {log_path}")
    log.info(f"  Summary    : {out_dir/'summary.csv'}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
