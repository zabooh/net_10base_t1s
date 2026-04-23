#!/usr/bin/env python3
"""pd10_sync_check.py — automated PD10 synchronicity test

Workflow (no operator interaction needed once boards + Saleae are wired):

  1. Reset both boards via UART (serial reset command).
  2. Set one to PTP master and the other to follower via the existing
     `ptp_mode master / follower` CLI.
  3. Poll the follower's UART for "PTP FINE" — actual servo-locked
     state (not a fixed-time guess).  Times out after --fine-timeout-s.
  4. Settle a few seconds for the demo's cyclic_fire to align.
  5. Capture both PD10 channels on Saleae for --duration-s.
  6. Pair every Ch0 rising edge with the nearest Ch1 rising edge
     (within ±300 ms bracket) and report:
        - mean / median / spread of cross-board edge delta in µs
        - frequency match (each channel's median rising-edge period)
        - PASS/FAIL gate: |median| < THRESHOLD_VISIBLE_MS (50 ms = the
          rough human-visual perception threshold for offset blink)

All output is tee'd into pd10_sync_check_<ts>/run_<ts>.log together
with a per-edge CSV + a one-row summary.csv.

Usage:
    python pd10_sync_check.py                              # default 10 s
    python pd10_sync_check.py --gm-port COM10 --fol-port COM8
    python pd10_sync_check.py --duration-s 30 --threshold-ms 5
"""

import argparse
import csv
import datetime
import re
import statistics
import sys
import threading
import time
from pathlib import Path

import serial

from cyclic_fire_hw_test import (                            # noqa: E402
    start_saleae_capture, export_capture_csv, parse_edges,
)
from ptp_drift_compensate_test import Logger, open_port      # noqa: E402

DEFAULT_GM_PORT     = "COM10"   # Saleae Ch0 board
DEFAULT_FOL_PORT    = "COM8"    # Saleae Ch1 board
DEFAULT_DURATION_S  = 10.0
DEFAULT_SAMPLE_HZ   = 50_000_000
DEFAULT_FINE_TO_S   = 30.0   # lock typically 12-18 s on this hardware
DEFAULT_SETTLE_S    = 3.0
DEFAULT_THRESH_MS   = 50.0     # human-visual blink-asynchrony threshold

RE_FINE = re.compile(r"PTP\s+FINE", re.IGNORECASE)


def banner(title, log):
    log.info("")
    log.info("=" * 72)
    log.info(f"  {title}")
    log.info("=" * 72)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def verbose_send(ser, cmd, log, tag):
    """Log + send a command line to one of the boards."""
    log.info(f"  >>> [{tag}] {cmd}")
    ser.write((cmd + "\r\n").encode("ascii"))


def drain_and_log(ser, log, tag, duration_s):
    """Read whatever the board sends back over the next duration_s and
    log every complete line, prefixed with [tag].  Useful for showing
    boot banners + post-command status messages so the operator can
    follow exactly what's going on."""
    deadline = time.monotonic() + duration_s
    line = b""
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if not chunk:
            time.sleep(0.02)
            continue
        line += chunk
        while b"\n" in line:
            ln, line = line.split(b"\n", 1)
            text = ln.rstrip(b"\r").decode("ascii", "replace")
            if text:
                log.info(f"      [{tag}] {text}")


def reset_boards(gm, fol, log):
    verbose_send(gm,  "reset", log, "GM ")
    verbose_send(fol, "reset", log, "FOL")
    log.info("  (waiting 4 s for boot + Harmony + LAN865x bring-up,")
    log.info("   logging both boards' boot banners ...)")
    deadline = time.monotonic() + 4.0
    line_gm = b""
    line_fol = b""
    while time.monotonic() < deadline:
        for ser, tag, line_buf_name in ((gm, "GM ", "g"), (fol, "FOL", "f")):
            chunk = ser.read(256)
            if not chunk:
                continue
            if tag == "GM ":
                line_gm += chunk
                while b"\n" in line_gm:
                    ln, line_gm = line_gm.split(b"\n", 1)
                    text = ln.rstrip(b"\r").decode("ascii", "replace")
                    if text:
                        log.info(f"      [{tag}] {text}")
            else:
                line_fol += chunk
                while b"\n" in line_fol:
                    ln, line_fol = line_fol.split(b"\n", 1)
                    text = ln.rstrip(b"\r").decode("ascii", "replace")
                    if text:
                        log.info(f"      [{tag}] {text}")
        time.sleep(0.02)


def set_modes(gm, fol, log):
    verbose_send(gm, "ptp_mode master", log, "GM ")
    log.info("  (draining 1 s of GM response ...)")
    drain_and_log(gm, log, "GM ", 1.0)
    verbose_send(fol, "ptp_mode follower", log, "FOL")


def wait_for_fine(fol, timeout_s, log):
    """Poll the follower's UART for 'PTP FINE'.  Returns elapsed time
    in seconds, or None on timeout.  Echoes every line received so the
    operator can see what's actually happening."""
    log.info(f"  Polling FOL UART for 'PTP FINE' (timeout {timeout_s:.0f} s) ...")
    # Don't reset the buffer — we want to catch any FINE message that
    # was already in flight before this function was called.
    deadline = time.monotonic() + timeout_s
    t_start = time.monotonic()
    line = b""
    while time.monotonic() < deadline:
        chunk = fol.read(256)
        if not chunk:
            time.sleep(0.02)
            continue
        line += chunk
        # Split on newlines and log each completed line
        while b"\n" in line:
            ln, line = line.split(b"\n", 1)
            text = ln.rstrip(b"\r").decode("ascii", "replace")
            if text:
                log.info(f"      [FOL] {text}")
                if RE_FINE.search(text):
                    elapsed = time.monotonic() - t_start
                    log.info(f"  -> PTP FINE seen after {elapsed:.1f} s")
                    return elapsed
    log.info("  -> TIMEOUT waiting for FINE")
    return None


# ---------------------------------------------------------------------------
# Edge analysis
# ---------------------------------------------------------------------------

def percentile(sorted_values, pct):
    if not sorted_values:
        return float("nan")
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] * (1 - (k - lo)) + sorted_values[hi] * (k - lo)


def mad(values):
    if not values:
        return float("nan")
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def median_period_us(rising):
    if len(rising) < 2:
        return float("nan")
    diffs = [(rising[i] - rising[i - 1]) * 1e6 for i in range(1, len(rising))]
    return statistics.median(diffs)


class SerialDrainer:
    """Background reader that continuously drains one or more serial ports
    and tee's every received line into the shared logger, prefixed with
    a per-port tag.  Used by --verbose to surface board chatter that
    would otherwise silently sit in the kernel UART buffer between the
    explicit polling phases (settle, Saleae capture, analysis).

    Start ONLY after any synchronous reader (wait_for_fine, drain_and_log,
    etc.) has finished — two threads racing for bytes from the same port
    causes interleaved/missing characters."""

    def __init__(self, ports_with_tags, log):
        self._ports = ports_with_tags          # list of (serial.Serial, tag_str)
        self._log   = log
        self._stop  = threading.Event()
        self._lock  = threading.Lock()         # serialise log.info from N threads
        self._threads = []

    def _drain_one(self, ser, tag):
        """Non-blocking drain loop: poll in_waiting (byte count currently
        in the OS receive buffer) and only read what's actually there.

        The earlier version used ser.read(256), which on Windows can block
        until the full 256-byte request is satisfied — a known pyserial
        behaviour where the configured 0.1 s timeout is not always honoured
        precisely during concurrent gRPC / Saleae traffic.  Observed: an
        entire 60 s Saleae capture passed without any drained lines even
        though the boards were printing re-sync events.

        Polling in_waiting + short sleep is robust across platforms and
        never blocks, so no board chatter can ever be lost between the
        FINE lock and the end-of-run shutdown."""
        line_buf = b""
        while not self._stop.is_set():
            try:
                n = ser.in_waiting
            except Exception:
                # port closed under us — exit cleanly
                return
            if n == 0:
                # Idle: short sleep so we don't spin-poll at 100 % CPU.
                time.sleep(0.01)
                continue
            try:
                chunk = ser.read(n)
            except Exception:
                return
            if not chunk:
                continue
            line_buf += chunk
            while b"\n" in line_buf:
                ln, line_buf = line_buf.split(b"\n", 1)
                text = ln.rstrip(b"\r").decode("ascii", "replace")
                if text:
                    with self._lock:
                        self._log.info(f"      [{tag}] {text}")

    def start(self):
        for ser, tag in self._ports:
            t = threading.Thread(target=self._drain_one,
                                 args=(ser, tag), daemon=True,
                                 name=f"drain-{tag}")
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)


def write_histogram(deltas_us, median_us, mad_us, out_dir, log):
    """Render a PNG histogram next to deltas_us.csv.  matplotlib is
    optional — if it isn't installed we just skip and print a hint,
    rather than failing the whole run."""
    try:
        import matplotlib
        matplotlib.use("Agg")   # no display needed; write PNG only
        import matplotlib.pyplot as plt
    except ImportError:
        log.info("  (matplotlib not installed — skipping histogram PNG)")
        log.info("  install with: python -m pip install matplotlib")
        return None

    n = len(deltas_us)
    if n < 2:
        log.info("  (< 2 edges — skipping histogram)")
        return None

    bin_width = 5.0     # us — good granularity for 60-edge, 10s..60s captures
    lo = min(deltas_us) - bin_width
    hi = max(deltas_us) + bin_width
    bins = [lo + i * bin_width
            for i in range(int((hi - lo) / bin_width) + 2)]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.hist(deltas_us, bins=bins, edgecolor="black", color="#4a7cbf", alpha=0.85)
    ax.axvline(median_us, color="red",   linestyle="--", linewidth=1.5,
               label=f"median = {median_us:+.1f} us")
    ax.axvline(median_us - mad_us, color="orange", linestyle=":", linewidth=1.2,
               label=f"median +/- MAD (+/-{mad_us:.1f} us)")
    ax.axvline(median_us + mad_us, color="orange", linestyle=":", linewidth=1.2)

    spread_us = max(deltas_us) - min(deltas_us)
    ax.set_title(f"Cross-board PD10 rising-edge delta (n={n})\n"
                 f"MAD = {mad_us:.1f} us   spread = {spread_us:.1f} us")
    ax.set_xlabel("delta = Ch1 - Ch0  (us)")
    ax.set_ylabel("count")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend()
    plt.tight_layout()

    png = out_dir / "histogram.png"
    plt.savefig(png, dpi=120)
    plt.close(fig)
    return png


def cross_board_delta_us(rising_a, rising_b, bracket_s=0.499):
    """For each rising edge on Ch0 find the closest UNUSED rising edge on
    Ch1 within ±bracket_s.  Default bracket = 499 ms = just under half
    the 1 Hz period, so we correctly pair even a near-180° phase offset
    (the pair is then the 'same' edge but wrapped half a cycle).

    Each Ch1 edge can be used AT MOST ONCE.  Without this constraint a
    surplus Ch0 edge (e.g. a glitch right after cyclic_fire start, or
    one extra edge captured at a window boundary) gets paired with the
    SAME Ch1 edge as its neighbour, producing one correct pair plus
    one massively-wrong artefact (observed: +280 us / -719 us at the
    start of pd10_sync_check_20260423_184334).
    """
    if not rising_a or not rising_b:
        return []
    deltas = []
    used = set()           # indices into rising_b that have already been paired
    j = 0
    for ta in rising_a:
        while j < len(rising_b) - 1 and rising_b[j + 1] < ta:
            j += 1
        best_k = None
        best_d = None
        for k in (j, j + 1):
            if 0 <= k < len(rising_b) and k not in used:
                d = rising_b[k] - ta
                if abs(d) <= bracket_s and (best_d is None or abs(d) < abs(best_d)):
                    best_k = k
                    best_d = d
        if best_k is not None:
            used.add(best_k)
            deltas.append(best_d * 1e6)
    return deltas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--duration-s",   type=float, default=DEFAULT_DURATION_S)
    p.add_argument("--sample-rate",  type=int,   default=DEFAULT_SAMPLE_HZ)
    p.add_argument("--fine-timeout-s", type=float, default=DEFAULT_FINE_TO_S)
    p.add_argument("--settle-s",     type=float, default=DEFAULT_SETTLE_S)
    p.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESH_MS,
                   help="|median| gate for visual sync (default 50 ms)")
    p.add_argument("--no-prep", action="store_true",
                   help="skip reset+mode setup; assume boards already locked")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="log every command sent to the boards AND every line "
                        "received from either UART throughout the entire run "
                        "(boot, prep, settle, capture, analysis).  Default: "
                        "only show output during prep + FINE polling.")
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"pd10_sync_check_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"run_{ts}.log"
    log = Logger(log_file=str(log_path))

    banner("PD10 cross-board synchronicity check", log)
    log.info(f"  GM port      : {args.gm_port}  (Saleae Ch0)")
    log.info(f"  FOL port     : {args.fol_port}  (Saleae Ch1)")
    log.info(f"  Capture      : {args.duration_s:.1f} s @ "
             f"{args.sample_rate/1_000_000:.0f} MS/s "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  PASS gate    : |median delta| < {args.threshold_ms:.1f} ms")
    log.info(f"  Verbose      : {'ON (full target chatter)' if args.verbose else 'off'}")
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

        # ----- Step 2: wait for FINE -----
        banner("Step 2 — wait for PTP FINE on the follower", log)
        elapsed = wait_for_fine(fol, args.fine_timeout_s, log)
        fine_ok = elapsed is not None
        if not fine_ok:
            log.info("  WARNING: follower never reported FINE within timeout.")
            log.info("           Capturing PD10 anyway so the operator can see")
            log.info("           the actual signal state — phase-sync verdict")
            log.info("           below should be treated as informational only.")
        log.info(f"  Settling {args.settle_s:.0f} s for cyclic_fire alignment ...")
        time.sleep(args.settle_s)

    # Enable [CLK] anchor-JUMP log on both boards so the run log captures
    # every sanity-rejected sample (firmware default is OFF).  Put this AFTER
    # the FINE/settle phase so the cascade of rejections during the initial
    # hard-sync isn't logged — only steady-state events.  The setting is
    # board-side only; toggling via CLI doesn't disturb PTP.
    log.info("  Enabling [CLK] anchor-JUMP diagnostic log on GM + FOL ...")
    verbose_send(gm,  "clk_jump_log on", log, "GM ")
    verbose_send(fol, "clk_jump_log on", log, "FOL")
    drain_and_log(gm,  log, "GM ", 0.2)
    drain_and_log(fol, log, "FOL", 0.2)

    # In verbose mode keep the serial ports open and run a background
    # drainer so any board chatter during settle / Saleae capture /
    # analysis still ends up in the run log.  In default mode we close
    # the ports here (no UART traffic between phases is expected).
    drainer = None
    if args.verbose:
        log.info("  Verbose mode: starting background UART drainers "
                 "(GM, FOL) — every received line is tee'd into the run log.")
        drainer = SerialDrainer([(gm, "GM "), (fol, "FOL")], log)
        drainer.start()
    else:
        gm.close(); fol.close()

    # ----- Step 3: Saleae capture -----
    banner(f"Step 3 — Saleae capture ({args.duration_s:.1f} s)", log)
    mgr, cap = start_saleae_capture(args.sample_rate, args.duration_s, log)
    cap.wait()
    csv_path = export_capture_csv(cap, out_dir, log)
    cap.close()

    # ----- Step 4: edge analysis -----
    banner("Step 4 — edge analysis", log)
    rising, _ = parse_edges(csv_path)
    ra = rising.get(0, [])
    rb = rising.get(1, [])
    log.info(f"  Channel 0 rising edges: {len(ra)}")
    log.info(f"  Channel 1 rising edges: {len(rb)}")

    period_a_us = median_period_us(ra)
    period_b_us = median_period_us(rb)
    log.info(f"  Ch0 median rising-edge period: {period_a_us:>10,.1f} µs   "
             f"(expect 1_000_000 µs for 1 Hz)")
    log.info(f"  Ch1 median rising-edge period: {period_b_us:>10,.1f} µs")
    if period_a_us == period_a_us and period_b_us == period_b_us:
        rate_match_ppm = abs(period_a_us - period_b_us) / period_a_us * 1e6
        log.info(f"  Rate match: {rate_match_ppm:.1f} ppm difference")

    deltas_us = cross_board_delta_us(ra, rb)
    if not deltas_us:
        log.info("  ERROR: no paired rising edges within ±300 ms of each other.")
        log.info("  Boards are likely > 300 ms out of phase or one PD10 is dead.")
        return 3

    deltas_sorted = sorted(deltas_us)
    median_us  = statistics.median(deltas_us)
    mad_us     = mad(deltas_us)
    p99_us     = percentile(deltas_sorted, 99)
    p1_us      = percentile(deltas_sorted, 1)
    spread_us  = deltas_sorted[-1] - deltas_sorted[0]
    abs_max_us = max(abs(d) for d in deltas_us)

    log.info("")
    log.info(f"  Cross-board PD10 rising-edge delta (Ch1 − Ch0):")
    log.info(f"    n       : {len(deltas_us)}")
    log.info(f"    min     : {deltas_sorted[0]:>+12,.1f} µs   "
             f"(= {deltas_sorted[0]/1000:+.3f} ms)")
    log.info(f"    p1      : {p1_us:>+12,.1f} µs")
    log.info(f"    median  : {median_us:>+12,.1f} µs   "
             f"(= {median_us/1000:+.3f} ms)")
    log.info(f"    p99     : {p99_us:>+12,.1f} µs")
    log.info(f"    max     : {deltas_sorted[-1]:>+12,.1f} µs   "
             f"(= {deltas_sorted[-1]/1000:+.3f} ms)")
    log.info(f"    spread  : {spread_us:>12,.1f} µs   "
             f"(= {spread_us/1000:.3f} ms)")
    log.info(f"    MAD     : {mad_us:>12,.1f} µs")
    log.info(f"    |max|   : {abs_max_us:>12,.1f} µs")

    # ----- Per-edge CSV -----
    with open(out_dir / "deltas_us.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_idx", "ch0_t_s", "delta_us"])
        for i, (ta, d) in enumerate(zip(ra, deltas_us)):
            w.writerow([i, f"{ta:.9f}", f"{d:+.4f}"])

    # ----- Verdict -----
    banner("Verdict", log)
    median_ms     = abs(median_us) / 1000.0
    pass_visible  = median_ms < args.threshold_ms
    log.info(f"  |median delta|  : {median_ms:>7.3f} ms")
    log.info(f"  Visual gate     : < {args.threshold_ms:.1f} ms  "
             f"-> {'PASS' if pass_visible else 'FAIL'}")
    if pass_visible:
        log.info(f"  Boards are visually synchronous "
                 f"(human eye doesn't notice < {args.threshold_ms:.0f} ms).")
    else:
        log.info(f"  Boards are visibly out of phase by ~{median_ms:.0f} ms.")
        if median_ms > 250:
            log.info("  > 250 ms means the LEDs land on opposite slots of the")
            log.info("  500 ms half-period rectangle (180° phase mismatch).")
        elif median_ms > 50:
            log.info("  Visible but not 180° — the boards' SW PTP_CLOCKs differ")
            log.info("  by this amount.  See README_PTP §R25 for the calibration")
            log.info("  constant PTP_FOL_ANCHOR_OFFSET_NS that compensates the")
            log.info("  LAN865x RX-pipeline delay; current value may need re-tune")
            log.info("  for this specific board pair.")

    # ----- Summary CSV -----
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "median_us", "mad_us", "p99_us", "max_abs_us",
                    "spread_us", "ch0_period_us", "ch1_period_us",
                    "verdict_visible"])
        w.writerow([len(deltas_us),
                    f"{median_us:+.3f}", f"{mad_us:.3f}",
                    f"{p99_us:+.3f}", f"{abs_max_us:.3f}",
                    f"{spread_us:.3f}",
                    f"{period_a_us:.3f}", f"{period_b_us:.3f}",
                    "PASS" if pass_visible else "FAIL"])
    # ----- Histogram PNG -----
    png = write_histogram(deltas_us, median_us, mad_us, out_dir, log)

    log.info("")
    log.info(f"  Per-edge CSV : {out_dir/'deltas_us.csv'}")
    log.info(f"  Summary CSV  : {out_dir/'summary.csv'}")
    if png is not None:
        log.info(f"  Histogram    : {png}")
    log.info(f"  Log file     : {log_path}")

    # Shut down verbose drainers + close serial ports cleanly.
    if drainer is not None:
        drainer.stop()
        try:
            gm.close()
        except Exception:
            pass
        try:
            fol.close()
        except Exception:
            pass

    return 0 if pass_visible else 1


if __name__ == "__main__":
    sys.exit(main())
