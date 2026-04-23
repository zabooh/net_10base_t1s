#!/usr/bin/env python3
"""meta_cyclic_fire_sweep.py — characterise where cyclic_fire works
=====================================================================

Drives `cyclic_fire_hw_test.py` repeatedly across a range of period_us
values (and SQUARE / MARKER patterns), then aggregates the run logs into
a CSV + text summary highlighting:

  - which periods give clean cross-board sync (low MAD, low spread)
  - which periods exhibit R23-style CPU starvation (sub-ms regime)
  - which periods reveal R20-style MARKER-phase races
  - run-to-run variance (if --reps > 1)

Designed for overnight execution: one full sweep with default settings
takes ~25-30 minutes; with --reps 3 closer to 75 minutes.

Phase 1 — Sweep:
    For each (period_us, mode, rep) combination, invoke
    cyclic_fire_hw_test.py as a subprocess.  Each invocation produces
    its own cyclic_fire_hw_<ts>/run_<ts>.log alongside this script.
    A run that fails (e.g., PTP FINE timeout, R21 wedge) is logged but
    the sweep continues to the next configuration.

Phase 2 — Aggregate:
    Parse every produced run_*.log, extract the headline metrics
    (median delta, MAD, spread, drift rate, sample count, verdict
    PASS/FAIL), and emit a per-(period, mode) summary table plus a
    CSV for offline plotting.

Usage:
    # Full default sweep, suitable for overnight:
    python meta_cyclic_fire_sweep.py

    # Custom period set:
    python meta_cyclic_fire_sweep.py --periods 1000 2000 5000

    # Square only, three reps each:
    python meta_cyclic_fire_sweep.py --modes square --reps 3

    # Skip Phase 1, only re-aggregate logs from a previous sweep:
    python meta_cyclic_fire_sweep.py --phase2-only ./
"""

import argparse
import csv
import datetime
import re
import subprocess
import sys
import time
from pathlib import Path

# Default period sweep — covers the four interesting regimes:
#   - sub-ms (≤800 µs): R23 zone, expect MAD blow-up + cross-board jitter
#   - normal (1-2 ms):  sweet spot expected
#   - medium (3-10 ms): low CPU load, near-µs floor
#   - long (≥20 ms):    very low rate, characterise long-tail outliers
DEFAULT_PERIODS_US = [
    300, 500, 700, 800,
    1000, 1200, 1500, 2000, 3000,
    5000, 7500, 10000,
    20000, 50000, 100000,
]

DEFAULT_MODES = ["square", "marker"]
DEFAULT_REPS_PER_CONFIG = 1
DEFAULT_DURATION_S = 10.0
DEFAULT_SETTLE_S   = 15.0
PER_RUN_TIMEOUT_S  = 300.0   # subprocess hard-kill after this
ABORT_ON_N_CONSECUTIVE_FAILS = 5

# --quick: one representative period per regime, MARKER only, shorter settle +
# capture.  Used for "does the fix hold?" spot-checks — ~2 min total vs the
# ~25 min full sweep.
QUICK_PERIODS_US   = [500, 1000, 5000, 20000]
QUICK_MODES        = ["marker"]
QUICK_DURATION_S   = 5.0
QUICK_SETTLE_S     = 8.0


# ---------------------------------------------------------------------------
# Log-parsing patterns — must match cyclic_fire_hw_test.py output
# ---------------------------------------------------------------------------

RE_PERIOD       = re.compile(r"^\s*period_us\s*:\s*(\d+)")
RE_PATTERN      = re.compile(r"^\s*pattern\s*:\s*(\S+)")
RE_FINE_TIME    = re.compile(r"PTP FINE reached after\s+([\d.]+)\s*s")
RE_GM_BUILD     = re.compile(r"GM\s+booted.*Build:\s+(.+)$")
RE_FOL_BUILD    = re.compile(r"FOL\s+booted.*Build:\s+(.+)$")
RE_CROSS_RISE   = re.compile(r"rising\s+n=\s*(\d+)\s+median=\s*([+-]?[\d.]+)\s*µs\s+MAD=\s*([\d.]+)\s*µs")
RE_DRIFT_RATE   = re.compile(r"drift rate\s+:\s*([+-]?[\d.]+)\s*µs/s")
RE_SPREAD       = re.compile(r"spread\s+=\s*([\d.]+)\s*µs")
RE_OVERLAP      = re.compile(r"Overlap window.*span\s+([\d.]+)\s*s")
RE_VERDICT_PHA  = re.compile(r"\|median rising delta\|.*:\s*(PASS|FAIL)\s+\(=([\d.]+)")
RE_VERDICT_MAD  = re.compile(r"rising delta MAD\s+<.*:\s*(PASS|FAIL)\s+\(=([\d.]+)")
RE_GM_PERIOD    = re.compile(r"GM\s+(?:rectangle period|marker cycle)\s*:\s*median=\s*([\d.]+)\s*µs\s+MAD=\s*([\d.]+)\s*µs\s+\(n=(\d+)")
RE_FOL_PERIOD   = re.compile(r"FOL\s+(?:rectangle period|marker cycle)\s*:\s*median=\s*([\d.]+)\s*µs\s+MAD=\s*([\d.]+)\s*µs\s+\(n=(\d+)")


# ---------------------------------------------------------------------------
# Phase 1 — sweep
# ---------------------------------------------------------------------------

def run_one_test(script_path: Path, period_us: int, mode: str,
                 duration_s: float, settle_s: float,
                 search_root: Path, log) -> tuple:
    """Invoke cyclic_fire_hw_test.py once.  Returns (output_dir_path, exit_code).
    output_dir_path is None if no new dir was created (subprocess crashed
    before reaching its setup phase)."""
    cmd = [sys.executable, str(script_path),
           "--period-us",  str(period_us),
           "--duration-s", str(duration_s),
           "--settle-s",   str(settle_s)]
    if mode == "marker":
        cmd.append("--marker")

    # Snapshot existing dirs so we can identify the new one after the run.
    pre_dirs = set(search_root.glob("cyclic_fire_hw_*"))
    t_start  = time.monotonic()
    try:
        result = subprocess.run(cmd, cwd=script_path.parent,
                                capture_output=True, text=True,
                                timeout=PER_RUN_TIMEOUT_S)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        log.info(f"    !! subprocess hard-kill after {PER_RUN_TIMEOUT_S} s")
        rc = -1

    elapsed = time.monotonic() - t_start
    new_dirs = sorted(set(search_root.glob("cyclic_fire_hw_*")) - pre_dirs,
                      key=lambda p: p.stat().st_mtime)
    out_dir  = new_dirs[-1] if new_dirs else None
    log.info(f"    elapsed={elapsed:.1f} s  exit={rc}  dir={out_dir.name if out_dir else 'NONE'}")
    return (out_dir, rc)


# ---------------------------------------------------------------------------
# Phase 2 — parse + aggregate
# ---------------------------------------------------------------------------

def parse_log(log_path: Path) -> dict:
    """Pull every interesting metric out of one cyclic_fire_hw_*/run_*.log.
    Missing fields just don't appear in the dict — caller handles None."""
    metrics = {"__log": str(log_path)}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        for re_obj, keys in (
            (RE_PERIOD,      ("period_us",)),
            (RE_PATTERN,     ("pattern",)),
            (RE_FINE_TIME,   ("fine_s",)),
            (RE_GM_BUILD,    ("gm_build",)),
            (RE_FOL_BUILD,   ("fol_build",)),
            (RE_DRIFT_RATE,  ("drift_us_per_s",)),
            (RE_SPREAD,      ("spread_us",)),
            (RE_OVERLAP,     ("overlap_s",)),
        ):
            m = re_obj.search(line)
            if m:
                v = m.group(1)
                try:    v = int(v)
                except ValueError:
                    try: v = float(v)
                    except ValueError: pass
                metrics[keys[0]] = v

        m = RE_CROSS_RISE.search(line)
        if m:
            metrics["cross_n"]          = int(m.group(1))
            metrics["cross_median_us"]  = float(m.group(2))
            metrics["cross_mad_us"]     = float(m.group(3))

        m = RE_GM_PERIOD.search(line)
        if m:
            metrics["gm_cycle_us"]     = float(m.group(1))
            metrics["gm_cycle_mad_us"] = float(m.group(2))
            metrics["gm_cycle_n"]      = int(m.group(3))

        m = RE_FOL_PERIOD.search(line)
        if m:
            metrics["fol_cycle_us"]     = float(m.group(1))
            metrics["fol_cycle_mad_us"] = float(m.group(2))
            metrics["fol_cycle_n"]      = int(m.group(3))

        m = RE_VERDICT_PHA.search(line)
        if m:
            metrics["verdict_phase"]    = m.group(1)
            metrics["verdict_phase_us"] = float(m.group(2))

        m = RE_VERDICT_MAD.search(line)
        if m:
            metrics["verdict_mad"]      = m.group(1)
            metrics["verdict_mad_us"]   = float(m.group(2))

    return metrics


def fmt_or(v, fmt: str, fallback: str = "    --") -> str:
    if v is None:
        return fallback
    try:
        return fmt.format(v)
    except (ValueError, TypeError):
        return fallback


def summarize(all_runs: list, out_dir: Path, log) -> None:
    """Emit per-mode tables, CSV, and verdict roll-up."""
    log.info("\n" + "=" * 78)
    log.info("  PHASE 2 — SUMMARY")
    log.info("=" * 78)

    by_mode = {}
    for r in all_runs:
        by_mode.setdefault(r.get("pattern", "?"), []).append(r)

    for mode in sorted(by_mode.keys()):
        runs = sorted(by_mode[mode], key=lambda r: (r.get("period_us") or 0))
        log.info(f"\n--- Mode: {mode}  ({len(runs)} runs) ---")
        log.info(f"  {'period':>8}  {'overlap':>8}  {'n':>5}  {'median':>11}  "
                 f"{'MAD':>9}  {'spread':>9}  {'drift':>11}  verdicts")
        log.info(f"  {'-'*8}  {'-'*8}  {'-'*5}  {'-'*11}  {'-'*9}  "
                 f"{'-'*9}  {'-'*11}  --------")
        for r in runs:
            log.info(
                f"  {fmt_or(r.get('period_us'),     '{:>8}'):>8}  "
                f"{fmt_or(r.get('overlap_s'),      '{:>6.1f} s'):>8}  "
                f"{fmt_or(r.get('cross_n'),        '{:>5}'):>5}  "
                f"{fmt_or(r.get('cross_median_us'),'{:>+8.1f} µs'):>11}  "
                f"{fmt_or(r.get('cross_mad_us'),   '{:>5.1f} µs'):>9}  "
                f"{fmt_or(r.get('spread_us'),      '{:>5.0f} µs'):>9}  "
                f"{fmt_or(r.get('drift_us_per_s'), '{:>+5.2f} µs/s'):>11}  "
                f"phase:{r.get('verdict_phase','?')} "
                f"mad:{r.get('verdict_mad','?')}"
            )

    # Sweet-spot detection: tightest MAD (after stripping FAIL phase rows)
    log.info(f"\n--- Sweet spot — top 5 lowest MAD (gates passed) ---")
    candidates = [r for r in all_runs
                  if r.get("verdict_phase") == "PASS"
                  and r.get("cross_mad_us") is not None]
    candidates.sort(key=lambda r: r["cross_mad_us"])
    for r in candidates[:5]:
        log.info(f"  period={r['period_us']:>6} mode={r['pattern']:>6}  "
                 f"MAD={r['cross_mad_us']:>5.1f} µs  "
                 f"median={r['cross_median_us']:>+7.1f} µs  "
                 f"spread={r.get('spread_us', 0):>5.0f} µs")

    # Failure roll-up — anything with a FAIL on either gate
    log.info(f"\n--- Failure roll-up — runs with at least one FAIL gate ---")
    fails = [r for r in all_runs
             if "FAIL" in (r.get("verdict_phase",""), r.get("verdict_mad",""))]
    if not fails:
        log.info("  (none)")
    for r in fails:
        log.info(
            f"  period={r.get('period_us',0):>6} mode={r.get('pattern','?'):>6}  "
            f"phase={r.get('verdict_phase','?')}({r.get('verdict_phase_us',0):>6.1f}µs)  "
            f"mad={r.get('verdict_mad','?')}({r.get('verdict_mad_us',0):>5.1f}µs)  "
            f"dir={Path(r.get('__log','')).parent.name}"
        )

    # Build banner sanity — any mismatch warrants investigation
    builds = set()
    for r in all_runs:
        gm = r.get("gm_build")
        fol = r.get("fol_build")
        if gm and fol and gm != fol:
            log.info(f"\n  WARN build mismatch in {Path(r.get('__log','')).parent.name}: "
                     f"GM={gm!r} FOL={fol!r}")
        if gm: builds.add(gm)
        if fol: builds.add(fol)
    if builds:
        log.info(f"\n  Firmware builds observed: {sorted(builds)}")

    # CSV export — one row per run, all metrics flattened
    csv_path = out_dir / "summary.csv"
    fieldnames = [
        "period_us", "pattern",
        "cross_median_us", "cross_mad_us", "spread_us", "drift_us_per_s",
        "cross_n", "overlap_s",
        "gm_cycle_us", "gm_cycle_mad_us",
        "fol_cycle_us", "fol_cycle_mad_us",
        "fine_s", "verdict_phase", "verdict_phase_us",
        "verdict_mad", "verdict_mad_us",
        "gm_build", "fol_build", "__log",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in sorted(all_runs, key=lambda r: (r.get("pattern",""), r.get("period_us") or 0)):
            w.writerow(r)
    log.info(f"\n  CSV: {csv_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

class TeeLog:
    """Tiny logger that writes both to stdout and a file."""
    def __init__(self, path: Path):
        self.fh = open(path, "w", encoding="utf-8")
    def info(self, msg: str = ""):
        print(msg)
        self.fh.write(msg + "\n")
        self.fh.flush()
    def close(self):
        self.fh.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--periods",  nargs="+", type=int, default=None)
    p.add_argument("--modes",    nargs="+", choices=["square", "marker"],
                   default=None)
    p.add_argument("--reps",     type=int, default=DEFAULT_REPS_PER_CONFIG)
    p.add_argument("--duration-s", type=float, default=None)
    p.add_argument("--settle-s",   type=float, default=None)
    p.add_argument("--quick",    action="store_true",
                   help="short sweep for spot-checks (~2 min): "
                        "4 representative periods × MARKER × 5 s capture × 8 s settle")
    p.add_argument("--script",   type=Path,
                   default=Path(__file__).parent / "cyclic_fire_hw_test.py")
    p.add_argument("--phase2-only", type=Path, default=None,
                   help="skip Phase 1; aggregate from this directory's "
                        "cyclic_fire_hw_* subdirs (use '.' for current dir)")
    args = p.parse_args()

    # Fill in defaults, honouring --quick if set.
    if args.periods    is None: args.periods    = QUICK_PERIODS_US if args.quick else DEFAULT_PERIODS_US
    if args.modes      is None: args.modes      = QUICK_MODES      if args.quick else DEFAULT_MODES
    if args.duration_s is None: args.duration_s = QUICK_DURATION_S if args.quick else DEFAULT_DURATION_S
    if args.settle_s   is None: args.settle_s   = QUICK_SETTLE_S   if args.quick else DEFAULT_SETTLE_S

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(f"meta_sweep_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = TeeLog(out_dir / "meta_run.log")

    log.info("=" * 78)
    log.info("  Meta cyclic_fire sweep")
    log.info("=" * 78)
    log.info(f"  Periods  : {args.periods}")
    log.info(f"  Modes    : {args.modes}")
    log.info(f"  Reps     : {args.reps}")
    log.info(f"  Settle   : {args.settle_s} s   Capture: {args.duration_s} s")
    log.info(f"  Script   : {args.script}")
    log.info(f"  Output   : {out_dir.resolve()}")

    # ---- Phase 1: sweep --------------------------------------------------
    all_run_dirs = []
    if args.phase2_only:
        log.info(f"\n--- Phase 1: SKIPPED (--phase2-only={args.phase2_only}) ---")
        all_run_dirs = sorted(args.phase2_only.glob("cyclic_fire_hw_*"))
        log.info(f"  found {len(all_run_dirs)} existing run directories")
    else:
        n_total = len(args.periods) * len(args.modes) * args.reps
        est_min = n_total * (args.settle_s + args.duration_s + 8) / 60.0
        log.info(f"\n--- Phase 1: sweep ({n_total} runs, ~{est_min:.0f} min estimated) ---")
        if not args.script.exists():
            log.info(f"  ERROR: cyclic_fire_hw_test.py not found at {args.script}")
            log.close()
            return 1

        t_phase1   = time.monotonic()
        n_done     = 0
        consec_fail = 0
        for period in args.periods:
            for mode in args.modes:
                for rep in range(args.reps):
                    n_done += 1
                    log.info(f"\n[{n_done}/{n_total}] period_us={period} mode={mode} "
                             f"rep={rep+1}/{args.reps}")
                    out, rc = run_one_test(args.script, period, mode,
                                           args.duration_s, args.settle_s,
                                           args.script.parent, log)
                    if out is not None:
                        all_run_dirs.append(out)
                    if rc != 0:
                        consec_fail += 1
                        log.info(f"    NON-ZERO EXIT (rc={rc}); consecutive fails={consec_fail}")
                        if consec_fail >= ABORT_ON_N_CONSECUTIVE_FAILS:
                            log.info(f"\n  ABORTING SWEEP — {ABORT_ON_N_CONSECUTIVE_FAILS} "
                                     f"consecutive failures.  Likely R21 wedge — boards "
                                     f"need a hard power-cycle.")
                            break
                    else:
                        consec_fail = 0
                else:
                    continue
                break
            else:
                continue
            break
        log.info(f"\n  Phase 1 done in {(time.monotonic()-t_phase1)/60:.1f} min, "
                 f"{len(all_run_dirs)} run dirs collected")

    # ---- Phase 2: aggregate ----------------------------------------------
    log.info(f"\n--- Phase 2: parse + aggregate ({len(all_run_dirs)} dirs) ---")
    all_runs = []
    for d in all_run_dirs:
        log_files = list(d.glob("run_*.log"))
        if not log_files:
            log.info(f"  WARN: no run_*.log in {d.name}")
            continue
        m = parse_log(log_files[0])
        all_runs.append(m)
    log.info(f"  parsed {len(all_runs)} log files")

    summarize(all_runs, out_dir, log)
    log.close()
    print(f"\nDone. Output in: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
