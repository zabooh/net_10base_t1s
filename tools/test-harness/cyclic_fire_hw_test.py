#!/usr/bin/env python3
"""cyclic_fire HW-verification test — Saleae Logic 2 automation
================================================================

Runs the cyclic_fire module on both boards with a shared PTP-wallclock
anchor and uses a Saleae Logic 8 (or compatible) to measure the
**actual** phase alignment between GM-PD10 and FOL-PD10 rectangles —
bypassing any firmware self-reporting.

Wiring (defaults — override with --gm-port / --fol-port if different):
    GM  = COM10 → Saleae Ch0  ← PD10 of master board   (EXT1 pin 5)
    FOL = COM8  → Saleae Ch1  ← PD10 of follower board
    Saleae GND  ← common ground with both boards

Prerequisites:
    1. `pip install logic2-automation`
    2. Logic 2 desktop app running with a Logic 8 connected.
    3. Scripting socket server enabled in
       Options → Preferences → Developer → "Enable scripting socket server".

Usage:
    python cyclic_fire_hw_test.py                     # full run: reset + FINE + capture (1 kHz default, square wave)
    python cyclic_fire_hw_test.py --no-reset          # skip boot, PTP already FINE
    python cyclic_fire_hw_test.py --period-us 2000    # 500 Hz rectangle (full period)
    python cyclic_fire_hw_test.py --sample-rate 25000000 --duration-s 5.0
    python cyclic_fire_hw_test.py --compensate-offset # EXPERIMENTAL: see flag help
    python cyclic_fire_hw_test.py --marker            # isolated rising-edge pulses (1 high + 4 low periods)
                                                       # makes "who fires first?" visually unambiguous

Note on compensation: --compensate-offset tries to read both boards' PTP
wallclocks via clk_get and offset FOL's anchor accordingly.  In practice
the USB-CDC serial round-trip jitter is at the millisecond level while the
real cross-board offset is at the µs level, so the measurement is dominated
by noise and the resulting compensation makes the cross-board phase WORSE,
not better.  Therefore default is OFF — the uncompensated capture is the
honest measure of PTP sync quality.  Re-enabling makes sense only as an
experiment / for diagnostic purposes.
"""

import argparse
import csv
import datetime
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

try:
    from saleae import automation
except ImportError:
    print("ERROR: logic2-automation not installed.  Run: pip install logic2-automation")
    sys.exit(1)

from ptp_drift_compensate_test import (  # noqa: E402
    Logger, open_port, send_command, wait_for_pattern,
    reset_and_wait_for_boot, sleep_with_countdown,
    RE_IP_SET, RE_FINE, RE_MATCHFREQ, RE_HARD_SYNC, RE_COARSE,
    DEFAULT_GM_IP, DEFAULT_FOL_IP, DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
)

RE_CLK_GET             = re.compile(r"clk_get:\s+(\d+)\s+ns\s+drift=([+-]?\d+)ppb")
RE_CYC_START_OK        = re.compile(r"cyclic_start OK")
RE_CYC_START_MARKER_OK = re.compile(r"cyclic_start_marker OK")

# MARKER pattern: rising edge happens once per MARKER_CYCLE_PERIODS.  Between
# the rising and the next rising, the signal goes HIGH for one period, then
# stays LOW for (MARKER_CYCLE_PERIODS - 1) periods.  This must match the
# firmware-side constants in cyclic_fire.c (10 half-period callbacks / 5 full
# periods per cycle, signal HIGH on callback #0, LOW on callback #2).
MARKER_CYCLE_PERIODS = 5

# Wiring assumption (this test specifically): GM = COM10 → Saleae Ch0,
# FOL = COM8 → Saleae Ch1.  Override via --gm-port / --fol-port if your
# physical setup differs.  These overrides take precedence over the
# generic defaults imported from ptp_drift_compensate_test.
DEFAULT_GM_PORT  = "COM10"
DEFAULT_FOL_PORT = "COM8"

# Defaults tuned for a 1 kHz rectangle and Logic 8 at full rate on 2 channels.
DEFAULT_SAMPLE_RATE_HZ = 50_000_000    # 50 MHz → 20 ns edge resolution
DEFAULT_DURATION_S     = 3.0
DEFAULT_PERIOD_US      = 1000          # full rectangle period → 1 kHz on PD10
DEFAULT_ANCHOR_LEAD_MS = 2000          # firing starts this far after clk_get;
                                       # large enough that FOL still sees the
                                       # anchor as future even if its wallclock
                                       # is hundreds of ms behind GM at arm time
                                       # (which can happen right after FINE
                                       # before the drift filter is settled)

# Verdict gates.  Product requirement: cross-board edge delta must be within
# a ±100 µs window so any time-slot boundary ≥100 µs can be reliably serviced
# on both boards.  Median + MAD together approximate a worst-case bound
# (|median| + MAD covers ~75 % of samples for a unimodal distribution,
# |median| + 3·MAD covers the long-tail p99 region).  Gate chosen so the
# 100 µs budget is split between a steady-state bias (GATE_PHASE_ABS_US)
# and per-sample jitter (GATE_PHASE_MAD_US): 50 µs each leaves margin for
# a few-sigma outlier while still keeping the p99 inside the window.
GATE_PHASE_ABS_US  = 50.0              # |median(FOL-GM)| edge delta
GATE_PHASE_MAD_US  = 50.0              # robust stdev of deltas


# ---------------------------------------------------------------------------
# Saleae capture
# ---------------------------------------------------------------------------

def start_saleae_capture(sample_rate_hz: int, duration_s: float, log: Logger):
    """Connect to Logic 2 and start a timed capture.  Returns (manager, capture)."""
    log.info(f"  Saleae: connecting to Logic 2 ...")
    mgr = automation.Manager.connect()                             # localhost:10430
    log.info(f"  Saleae: starting {duration_s:.1f} s capture "
             f"({sample_rate_hz/1_000_000:.0f} MS/s, Ch0+Ch1) ...")
    dev = automation.LogicDeviceConfiguration(
        enabled_digital_channels=[0, 1],
        digital_sample_rate=sample_rate_hz,
        # Logic 8 (non-Pro) has a fixed 1.65 V threshold — do not pass
        # digital_threshold_volts here (device would reject it).  The 3.3 V
        # signal from PD10 is well above the threshold.
    )
    cap_cfg = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=duration_s)
    )
    capture = mgr.start_capture(device_configuration=dev,
                                capture_configuration=cap_cfg)
    return mgr, capture


def export_capture_csv(capture, out_dir: Path, log: Logger) -> Path:
    """Export raw digital samples to CSV.  Returns path to the produced file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    abs_dir = out_dir.resolve()
    log.info(f"  Saleae: exporting raw data CSV to {abs_dir} ...")
    capture.export_raw_data_csv(directory=str(abs_dir),
                                digital_channels=[0, 1])
    # Logic 2 writes a file called "digital.csv" in that directory.
    csv_path = abs_dir / "digital.csv"
    if not csv_path.exists():
        # Newer versions may vary the name — pick any CSV in the dir.
        candidates = list(out_dir.glob("*.csv"))
        if not candidates:
            raise FileNotFoundError(f"No CSV found in {out_dir}")
        csv_path = candidates[0]
    log.info(f"  Saleae: CSV written ({csv_path.stat().st_size/1024:.1f} KiB)")
    return csv_path


# ---------------------------------------------------------------------------
# CSV parsing — extract rising & falling edge timestamps per channel
# ---------------------------------------------------------------------------

def parse_edges(csv_path: Path) -> Tuple[Dict[int, List[float]], Dict[int, List[float]]]:
    """Parse a Saleae raw-data CSV.  Handles both the sample-per-row form
    and the transition-only form.  Returns (rising[ch], falling[ch])."""
    rising:  Dict[int, List[float]] = {0: [], 1: []}
    falling: Dict[int, List[float]] = {0: [], 1: []}
    prev = {0: None, 1: None}
    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        # Map channel index → column index by looking for "0" / "1" in the header.
        ch_cols: Dict[int, int] = {}
        for i, name in enumerate(header):
            low = name.strip().lower()
            if low.endswith(" 0") or low == "channel 0" or low.endswith("d0"):
                ch_cols[0] = i
            elif low.endswith(" 1") or low == "channel 1" or low.endswith("d1"):
                ch_cols[1] = i
        if 0 not in ch_cols or 1 not in ch_cols:
            # fallback: assume column 1 = ch0, column 2 = ch1
            ch_cols = {0: 1, 1: 2}
        for row in reader:
            if len(row) < 2:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue
            for ch, col in ch_cols.items():
                if col >= len(row):
                    continue
                try:
                    val = int(float(row[col]))
                except ValueError:
                    continue
                if prev[ch] is None:
                    prev[ch] = val
                    continue
                if val != prev[ch]:
                    if val == 1:
                        rising[ch].append(t)
                    else:
                        falling[ch].append(t)
                    prev[ch] = val
    return rising, falling


# ---------------------------------------------------------------------------
# Phase + period analysis
# ---------------------------------------------------------------------------

def compute_phase_pairs(edges_gm: List[float],
                        edges_fol: List[float],
                        t_lo: float = float("-inf"),
                        t_hi: float = float("inf")
                        ) -> List[Tuple[float, float, float]]:
    """For each GM edge in [t_lo, t_hi], find the closest FOL edge and
    return (t_gm_s, t_fol_s, delta_s) triples.  Two-pointer sweep, O(n).
    The window discards edges outside the time range where both channels
    are active — otherwise the nearest-edge match for lonely GM edges
    pollutes the statistics."""
    pairs: List[Tuple[float, float, float]] = []
    if not edges_gm or not edges_fol:
        return pairs
    j = 0
    for tg in edges_gm:
        if tg < t_lo or tg > t_hi:
            continue
        while (j + 1 < len(edges_fol)
               and abs(edges_fol[j + 1] - tg) < abs(edges_fol[j] - tg)):
            j += 1
        pairs.append((tg, edges_fol[j], edges_fol[j] - tg))
    return pairs


def compute_phase_deltas(edges_gm: List[float],
                         edges_fol: List[float],
                         t_lo: float = float("-inf"),
                         t_hi: float = float("inf")) -> List[float]:
    """Convenience wrapper around compute_phase_pairs that returns only
    the delta values."""
    return [d for _, _, d in compute_phase_pairs(edges_gm, edges_fol, t_lo, t_hi)]


def percentile(sorted_vals: List[float], p: float) -> float:
    """Simple percentile on already-sorted input.  p in [0, 100]."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def overlap_window(edges_gm: List[float],
                   edges_fol: List[float],
                   guard_s: float = 0.1) -> Tuple[float, float]:
    """Return (t_lo, t_hi): the time range where both channels are toggling.
    Adds `guard_s` of margin at each end to avoid the very first / last
    edge being matched against a partial cycle of the other channel."""
    if not edges_gm or not edges_fol:
        return (0.0, 0.0)
    t_lo = max(edges_gm[0],  edges_fol[0])  + guard_s
    t_hi = min(edges_gm[-1], edges_fol[-1]) - guard_s
    return (t_lo, t_hi)


# ---------------------------------------------------------------------------
# Cross-board path-delay measurement via clk_get bracketing
# ---------------------------------------------------------------------------

def _read_clk_get(ser, log: Logger) -> Tuple[int, int, float]:
    """Send clk_get, return (wallclock_ns, drift_ppb, midpoint_pc_time_s).
    The midpoint of (send, receive) PC time is our best estimate of when
    the firmware actually sampled its wallclock."""
    t_start = time.monotonic()
    resp = send_command(ser, "clk_get", 2.0, log)
    t_end = time.monotonic()
    m = RE_CLK_GET.search(resp)
    if not m:
        raise RuntimeError(f"clk_get failed: {resp[:120]!r}")
    return (int(m.group(1)), int(m.group(2)), 0.5 * (t_start + t_end))


def measure_fol_vs_gm_offset_ns(ser_gm, ser_fol, log: Logger,
                                samples: int = 5) -> Tuple[int, List[int]]:
    """Measure (FOL_wallclock - GM_wallclock) at the same real-time moment.

    Strategy: bracket each FOL clk_get between two GM clk_gets, then
    linearly interpolate GM's wallclock to the moment of the FOL read.
    The offset is the median over multiple samples (rejects outliers
    from serial-timing jitter or a transient PTP servo update).
    Returns (median_offset_ns, [per-sample offsets]) — the per-sample
    list reveals drift/jitter in the FOL-vs-GM PTP_CLOCK relationship
    that single-shot compensation cannot follow.

    Sign convention: positive = FOL is AHEAD of GM in wallclock terms.
    To make FOL fire at the same real-time moment as GM, send FOL an
    anchor that is HIGHER by this offset (FOL must wait until its
    clock reaches the larger value, which happens at the same real
    time as GM reaching the smaller value).
    """
    offsets: List[int] = []
    drifts_gm: List[int] = []
    drifts_fol: List[int] = []
    for _ in range(samples):
        v_gm1, d_gm1, t_gm1 = _read_clk_get(ser_gm, log)
        v_fol, d_fol, t_fol = _read_clk_get(ser_fol, log)
        v_gm2, d_gm2, t_gm2 = _read_clk_get(ser_gm, log)
        if t_gm2 <= t_gm1:
            continue
        v_gm_at_t_fol = v_gm1 + (v_gm2 - v_gm1) * (t_fol - t_gm1) / (t_gm2 - t_gm1)
        offsets.append(int(v_fol - v_gm_at_t_fol))
        drifts_gm.append(d_gm1)
        drifts_fol.append(d_fol)
    if not offsets:
        raise RuntimeError("offset measurement: no usable samples")
    median_offset = sorted(offsets)[len(offsets) // 2]
    log.info(f"  GM  drift_ppb (samples): {drifts_gm}")
    log.info(f"  FOL drift_ppb (samples): {drifts_fol}")
    return (median_offset, offsets)


def period_stats(edges: List[float]) -> Tuple[float, float, int]:
    """Returns (median_period_s, mad_period_s, count)."""
    if len(edges) < 2:
        return (0.0, 0.0, 0)
    gaps = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    m = statistics.median(gaps)
    mad = statistics.median(abs(g - m) for g in gaps)
    return (m, 1.4826 * mad, len(gaps))


def robust(vs: List[float]) -> Tuple[float, float]:
    if not vs:
        return (0.0, 0.0)
    m = statistics.median(vs)
    mad = statistics.median(abs(v - m) for v in vs)
    return (m, 1.4826 * mad)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def setup_ptp(args, ser_gm, ser_fol, log: Logger) -> bool:
    """Reset both boards, confirm boot via build-banner, configure IPs,
    enable PTP, wait for FINE.  Aborts immediately if either board fails
    to emit '[APP] Build:' within 8 s of the reset command — the typical
    cause is a wedged LAN865x state that needs a hard power-cycle (R21)."""
    log.info("\n--- Reset + IP + PTP to FINE ---")
    try:
        gm_build  = reset_and_wait_for_boot(ser_gm,  "GM ", timeout=8.0, log=log)
        fol_build = reset_and_wait_for_boot(ser_fol, "FOL", timeout=8.0, log=log)
    except RuntimeError as exc:
        log.info(f"  ERROR: {exc}")
        log.info(f"  Aborting test — hard power-cycle both boards "
                 f"(USB unplug → 3 s wait → plug) and retry.")
        return False
    if gm_build != fol_build:
        log.info(f"  WARN: build mismatch — GM={gm_build} FOL={fol_build}  "
                 f"(test continues, but both boards should be flashed with the same firmware)")
    send_command(ser_gm,  f"setip eth0 {args.gm_ip} {args.netmask}", 3.0, log)
    send_command(ser_fol, f"setip eth0 {args.fol_ip} {args.netmask}", 3.0, log)
    send_command(ser_fol, "ptp_mode follower", 3.0, log)
    time.sleep(0.3)
    ser_fol.reset_input_buffer()
    send_command(ser_gm,  "ptp_mode master", 3.0, log)
    matched, elapsed, _ = wait_for_pattern(
        ser_fol, RE_FINE, args.conv_timeout, log,
        extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                        "HARD_SYNC": RE_HARD_SYNC,
                        "COARSE":    RE_COARSE})
    if not matched:
        log.info(f"  PTP FINE not reached in {elapsed:.1f} s — aborting")
        return False
    log.info(f"  PTP FINE reached after {elapsed:.1f} s")
    sleep_with_countdown(args.settle_s,
                         label="settling before capture (drift-filter convergence)",
                         log=log)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gm-port",        default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port",       default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",          default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",         default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",        default=DEFAULT_NETMASK)
    p.add_argument("--conv-timeout",   default=DEFAULT_CONV_TIMEOUT, type=float)
    p.add_argument("--settle-s",       default=15.0, type=float,
                   help="seconds to wait after PTP FINE before arming "
                        "cyclic_fire — gives the drift-IIR filter "
                        "(half-life ~11 s with N=128) time to partially "
                        "converge.  Empirically 15 s is enough for the "
                        "cross-board MAD to stabilise (~88 %% of full "
                        "convergence, indistinguishable from 60 s in the "
                        "Saleae output).  Set higher only when explicitly "
                        "characterising the filter.")
    p.add_argument("--no-reset",       action="store_true",
                   help="skip boot+FINE; assume PTP already running")
    p.add_argument("--period-us",      default=DEFAULT_PERIOD_US, type=int,
                   help="cyclic_fire rectangle period (µs). 1000 = 1 kHz")
    p.add_argument("--marker",         action="store_true",
                   help="use the MARKER pattern (1-period high + "
                        f"{MARKER_CYCLE_PERIODS-1}-period low, rising edge "
                        f"isolated every {MARKER_CYCLE_PERIODS} × period_us) "
                        "instead of the default square wave.  Makes the "
                        "'which board's edge is first?' question visually "
                        "unambiguous on a scope.  Analysis still measures "
                        "rising-edge delta, just on fewer samples.")
    p.add_argument("--sample-rate",    default=DEFAULT_SAMPLE_RATE_HZ, type=int,
                   help="Saleae digital sample rate (Hz)")
    p.add_argument("--duration-s",     default=DEFAULT_DURATION_S, type=float,
                   help="Saleae capture duration")
    p.add_argument("--anchor-lead-ms", default=DEFAULT_ANCHOR_LEAD_MS, type=int,
                   help="anchor = gm_clk + this many ms")
    p.add_argument("--compensate-offset", dest="compensate_offset",
                   action="store_true", default=False,
                   help="EXPERIMENTAL — try to compensate the FOL-vs-GM "
                        "wallclock offset by clk_get bracketing.  Currently "
                        "broken by USB-CDC serial round-trip jitter (~ms "
                        "noise on the µs-level offset), so the comp value "
                        "is wrong and makes things WORSE than no comp.  "
                        "Default OFF.  See log per-sample spread to judge.")
    p.add_argument("--no-compensate", dest="compensate_offset",
                   action="store_false",
                   help="(default) leave both anchors equal — yields the "
                        "uncompensated cross-board phase, which is the "
                        "honest measure of PTP sync quality (constant ~"
                        "path-delay offset + jitter MAD)")
    p.add_argument("--offset-samples", default=5, type=int,
                   help="number of clk_get triplets to median over for "
                        "the cross-board offset measurement")
    p.add_argument("--out-dir",        default=None,
                   help="directory for CSV / capture output (default: ./cyclic_fire_hw_<ts>)")
    p.add_argument("--log-file",       default=None)
    p.add_argument("--verbose",        action="store_true")
    args = p.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"cyclic_fire_hw_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = Logger(log_file=args.log_file or str(out_dir / f"run_{ts}.log"),
                 verbose=args.verbose)

    log.info("=" * 70)
    log.info("  cyclic_fire Hardware Verification — Saleae Logic 2")
    log.info("=" * 70)
    log.info(f"  GM port        : {args.gm_port}   FOL port: {args.fol_port}")
    log.info(f"  period_us      : {args.period_us}")
    log.info(f"  sample rate    : {args.sample_rate/1_000_000:.0f} MS/s  "
             f"(resolution {1e9/args.sample_rate:.0f} ns)")
    log.info(f"  duration       : {args.duration_s:.1f} s")
    log.info(f"  anchor lead    : {args.anchor_lead_ms} ms")
    log.info(f"  path-delay comp: {'ON ('+str(args.offset_samples)+' samples)' if args.compensate_offset else 'OFF'}")
    log.info(f"  pattern        : {'MARKER (1-high + '+str(MARKER_CYCLE_PERIODS-1)+'-low)' if args.marker else 'SQUARE (50/50)'}")
    log.info(f"  output dir     : {out_dir}")

    try:
        ser_gm  = open_port(args.gm_port)
        ser_fol = open_port(args.fol_port)
    except serial.SerialException as exc:
        log.info(f"ERROR: cannot open port: {exc}")
        return 1

    rc = 0
    try:
        if not args.no_reset:
            if not setup_ptp(args, ser_gm, ser_fol, log):
                return 1

        # Measure the residual cross-board wallclock offset BEFORE starting
        # the Saleae capture (so the capture window isn't consumed by the
        # ~750 ms of clk_get round-trips).
        offset_ns = 0
        if args.compensate_offset:
            log.info("")
            log.info("--- Cross-board offset measurement (clk_get bracketing) ---")
            try:
                offset_ns, samples = measure_fol_vs_gm_offset_ns(
                    ser_gm, ser_fol, log, samples=args.offset_samples)
            except RuntimeError as exc:
                log.info(f"  WARN: offset measurement failed ({exc}); "
                         f"falling back to offset=0")
                samples = []
            for i, s in enumerate(samples):
                log.info(f"    sample {i+1}: {s:+8d} ns ({s/1000:+8.2f} µs)")
            log.info(f"  median FOL_wc - GM_wc : {offset_ns:+d} ns "
                     f"({offset_ns/1000:+.2f} µs)")
            if samples:
                spread = max(samples) - min(samples)
                log.info(f"  per-sample spread     : {spread} ns "
                         f"({spread/1000:.2f} µs)  — large spread = "
                         f"offset is drifting; single-shot comp is limited")
        else:
            log.info("")
            log.info("--- Path-delay compensation DISABLED (--no-compensate) ---")

        # Start Saleae capture BEFORE issuing cyclic_start so we don't miss
        # the very first edges.  The capture begins immediately and runs
        # for duration_s in the background.
        log.info("")
        mgr, capture = start_saleae_capture(args.sample_rate,
                                            args.duration_s, log)

        # Small settle so the capture is definitely running.
        time.sleep(0.1)

        # Pick the GM anchor far enough in the future that both boards
        # see it as future after serial command round-trip.  FOL gets the
        # same anchor PLUS the measured offset, so when FOL's wallclock
        # reaches anchor_fol it is the same real-time moment that GM's
        # wallclock reaches anchor_gm.
        resp = send_command(ser_gm, "clk_get", 2.0, log)
        m    = RE_CLK_GET.search(resp)
        if not m:
            log.info(f"ERROR: could not read GM clk_get: {resp[:120]!r}")
            capture.close()
            return 1
        anchor_gm  = int(m.group(1)) + args.anchor_lead_ms * 1_000_000
        anchor_fol = anchor_gm + offset_ns
        log.info(f"  GM  anchor     : {anchor_gm} ns  "
                 f"(+{args.anchor_lead_ms} ms from GM clk_get)")
        log.info(f"  FOL anchor     : {anchor_fol} ns  "
                 f"(= GM anchor {offset_ns:+d} ns)")

        # Arm cyclic on both boards with their respective compensated anchors.
        # Use the MARKER pattern if requested — same serial protocol, just
        # different CLI command name and different success-line regex.
        cmd_name  = "cyclic_start_marker" if args.marker else "cyclic_start"
        ok_regex  = RE_CYC_START_MARKER_OK if args.marker else RE_CYC_START_OK
        gm_ok  = bool(ok_regex.search(
            send_command(ser_gm,  f"{cmd_name} {args.period_us} {anchor_gm}", 2.0, log)))
        fol_ok = bool(ok_regex.search(
            send_command(ser_fol, f"{cmd_name} {args.period_us} {anchor_fol}", 2.0, log)))
        if not (gm_ok and fol_ok):
            log.info(f"ERROR: cyclic_start failed  GM={gm_ok} FOL={fol_ok}")
            send_command(ser_gm,  "cyclic_stop", 2.0, log)
            send_command(ser_fol, "cyclic_stop", 2.0, log)
            capture.close()
            return 1

        # Wait for capture to finish (duration_s is handled internally by the
        # Saleae timed-capture mode).  We sleep with a visible countdown on
        # the host, then call capture.wait() as a belt-and-braces no-op that
        # returns immediately once the timed capture is done.
        sleep_with_countdown(args.duration_s,
                             label="capturing",
                             log=log)
        capture.wait()

        # Intentionally NOT sending cyclic_stop here — both boards keep
        # toggling so the user can inspect the live signals in Logic 2.
        # Run `cyclic_stop` manually on each board (or `reset`) to halt.

        # Export + save capture.
        csv_path = export_capture_csv(capture, out_dir, log)
        sal_path = (out_dir / f"capture_{ts}.sal").resolve()
        try:
            capture.save_capture(filepath=str(sal_path))
            log.info(f"  Saleae: capture saved to {sal_path}")
        except Exception as exc:
            log.info(f"  (save_capture failed, skipping: {exc})")
        capture.close()

        # Parse edges and analyse.
        log.info("")
        log.info("--- Edge analysis ---")
        rising, falling = parse_edges(csv_path)
        log.info(f"  GM  Ch0 rising edges: {len(rising[0])}  "
                 f"falling: {len(falling[0])}")
        log.info(f"  FOL Ch1 rising edges: {len(rising[1])}  "
                 f"falling: {len(falling[1])}")
        if rising[0] and rising[1]:
            log.info(f"  GM  Ch0 time range : {rising[0][0]:.6f} s … "
                     f"{rising[0][-1]:.6f} s  (span {rising[0][-1]-rising[0][0]:.3f} s)")
            log.info(f"  FOL Ch1 time range : {rising[1][0]:.6f} s … "
                     f"{rising[1][-1]:.6f} s  (span {rising[1][-1]-rising[1][0]:.3f} s)")

        med_period_gm, mad_period_gm, n_gm = period_stats(rising[0])
        med_period_fol, mad_period_fol, n_fol = period_stats(rising[1])
        # Period between consecutive rising edges: for SQUARE = period_us,
        # for MARKER = MARKER_CYCLE_PERIODS × period_us (one rising edge per
        # pattern cycle).
        rising_multiplier   = MARKER_CYCLE_PERIODS if args.marker else 1
        nominal_period_s    = args.period_us * 1e-6 * rising_multiplier
        pattern_label       = "marker cycle" if args.marker else "rectangle period"
        log.info("")
        log.info(f"  GM  {pattern_label}: median={med_period_gm*1e6:>8.3f} µs  "
                 f"MAD={mad_period_gm*1e6:>6.3f} µs  (n={n_gm}, nominal {nominal_period_s*1e6:.0f} µs)")
        log.info(f"  FOL {pattern_label}: median={med_period_fol*1e6:>8.3f} µs  "
                 f"MAD={mad_period_fol*1e6:>6.3f} µs  (n={n_fol})")

        # Cross-board edge delta restricted to the time window where BOTH
        # channels are toggling — outside that window the nearest-edge
        # matcher produces meaningless huge deltas (matches lonely GM
        # edges to the first/last FOL edge from far away).
        t_lo, t_hi = overlap_window(rising[0], rising[1], guard_s=0.1)
        log.info("")
        log.info(f"  Overlap window     : {t_lo:.6f} s … {t_hi:.6f} s  "
                 f"(span {max(0.0, t_hi-t_lo):.3f} s)")

        rising_pairs   = compute_phase_pairs(rising[0],  rising[1],  t_lo, t_hi)
        falling_pairs  = compute_phase_pairs(falling[0], falling[1], t_lo, t_hi)
        rising_deltas  = [d for _, _, d in rising_pairs]
        falling_deltas = [d for _, _, d in falling_pairs]
        r_med, r_mad   = robust(rising_deltas)
        f_med, f_mad   = robust(falling_deltas)

        log.info("")
        log.info("  Cross-board edge delta (FOL - GM), nearest-edge match within overlap:")
        log.info(f"    rising  n={len(rising_deltas):>4}  "
                 f"median={r_med*1e6:+10.3f} µs  MAD={r_mad*1e6:>7.3f} µs")
        log.info(f"    falling n={len(falling_deltas):>4}  "
                 f"median={f_med*1e6:+10.3f} µs  MAD={f_mad*1e6:>7.3f} µs")

        # ----------------------------------------------------------------
        # Delta verification — let the user cross-check the script's
        # measurement against Logic 2 by reading specific edge timestamps.
        # ----------------------------------------------------------------
        if rising_pairs:
            sorted_rising = sorted(rising_deltas)
            log.info("")
            log.info("  Rising-delta distribution (across overlap):")
            log.info(f"    min     = {sorted_rising[0]*1e6:+8.3f} µs")
            log.info(f"    p10     = {percentile(sorted_rising, 10)*1e6:+8.3f} µs")
            log.info(f"    p25     = {percentile(sorted_rising, 25)*1e6:+8.3f} µs")
            log.info(f"    median  = {percentile(sorted_rising, 50)*1e6:+8.3f} µs")
            log.info(f"    p75     = {percentile(sorted_rising, 75)*1e6:+8.3f} µs")
            log.info(f"    p90     = {percentile(sorted_rising, 90)*1e6:+8.3f} µs")
            log.info(f"    max     = {sorted_rising[-1]*1e6:+8.3f} µs")
            log.info(f"    spread  = {(sorted_rising[-1]-sorted_rising[0])*1e6:.3f} µs")

            log.info("")
            log.info("  Verification stichproben — open capture_*.sal in Logic 2,")
            log.info("  navigate to the GM-edge time below, place cursors on the")
            log.info("  Ch0-rising edge and the closest Ch1-rising edge, and verify")
            log.info("  the delta matches.  Format: [#idx] t_gm | t_fol | delta")
            n_pairs = len(rising_pairs)
            sample_indices = sorted(set([0,
                                         n_pairs // 4,
                                         n_pairs // 2,
                                         (3 * n_pairs) // 4,
                                         n_pairs - 1]))
            for idx in sample_indices:
                tg, tf, d = rising_pairs[idx]
                log.info(f"    [#{idx:>4}]  t_gm={tg:.7f} s  "
                         f"t_fol={tf:.7f} s  delta={d*1e6:+9.3f} µs")

            # Drift assessment: is the delta drifting linearly across the
            # capture window?  Compare median(first 10%) vs median(last 10%).
            n_first = max(1, n_pairs // 10)
            first_block = sorted([d for _, _, d in rising_pairs[:n_first]])
            last_block  = sorted([d for _, _, d in rising_pairs[-n_first:]])
            d_first_med = percentile(first_block, 50)
            d_last_med  = percentile(last_block, 50)
            t_first_mid = rising_pairs[n_first // 2][0]
            t_last_mid  = rising_pairs[-(n_first // 2 + 1)][0]
            dt = t_last_mid - t_first_mid
            drift_us_per_s = (d_last_med - d_first_med) * 1e6 / dt if dt > 0 else 0.0
            log.info("")
            log.info(f"  Delta drift across capture window:")
            log.info(f"    first 10% median ({n_first} pairs): {d_first_med*1e6:+8.3f} µs")
            log.info(f"    last  10% median ({n_first} pairs): {d_last_med*1e6:+8.3f} µs")
            log.info(f"    drift rate                        : "
                     f"{drift_us_per_s:+8.3f} µs/s  "
                     f"({drift_us_per_s:+.0f} ppm rate-mismatch)")

            # Dump full per-edge data to CSV for offline plotting / scrutiny.
            deltas_csv = (out_dir / f"deltas_rising_{ts}.csv").resolve()
            with open(deltas_csv, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["idx", "t_gm_s", "t_fol_s", "delta_s"])
                for i, (tg, tf, d) in enumerate(rising_pairs):
                    w.writerow([i, f"{tg:.9f}", f"{tf:.9f}", f"{d:.9f}"])
            log.info(f"  Per-edge rising deltas → {deltas_csv}")

        # Verdicts
        ok_phase = abs(r_med) * 1e6 < GATE_PHASE_ABS_US
        ok_jitter = r_mad * 1e6 < GATE_PHASE_MAD_US
        log.info("")
        log.info("--- Verdict ---")
        log.info(f"  |median rising delta| < {GATE_PHASE_ABS_US:.0f} µs : "
                 f"{'PASS' if ok_phase else 'FAIL'}  "
                 f"(={abs(r_med)*1e6:.2f} µs)")
        log.info(f"  rising delta MAD      < {GATE_PHASE_MAD_US:.0f} µs : "
                 f"{'PASS' if ok_jitter else 'FAIL'}  (={r_mad*1e6:.2f} µs)")
        log.info("")
        if args.marker:
            log.info(f"  → configured marker cycle     : {args.period_us * MARKER_CYCLE_PERIODS} µs "
                     f"({1e6/nominal_period_s:.1f} Hz rising-edge rate, "
                     f"{args.period_us} µs rectangle, {MARKER_CYCLE_PERIODS} periods/cycle)")
        else:
            log.info(f"  → configured rectangle period : {args.period_us} µs "
                     f"({1e6/nominal_period_s:.0f} Hz nominal)")
        log.info(f"  → measured GM {'marker cycle' if args.marker else 'rectangle period'}: "
                 f"{med_period_gm*1e6:.1f} µs  "
                 f"(factor vs nominal: {med_period_gm/nominal_period_s:.3f}×)")
        log.info("")
        log.info(f"  Output files in: {out_dir}")
        log.info("")
        log.info("  Note: cyclic_fire is still running on both boards so you can")
        log.info("  inspect the live signals in Logic 2.  Send 'cyclic_stop' on")
        log.info("  each console (or 'reset') to halt the toggling.")

        rc = 0 if (ok_phase and ok_jitter) else 1

    finally:
        for ser in (ser_gm, ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except: pass
        log.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
