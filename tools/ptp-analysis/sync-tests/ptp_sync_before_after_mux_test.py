#!/usr/bin/env python3
"""PTP Sync Before/After Test (mux-based)
=========================================

Two-phase measurement of the clk_get-based software clock on two boards.

  Phase 0 -- FREE RUNNING (no PTP):
    Boards are reset. Clocks are zeroed simultaneously (parallel clk_set 0
    via SerialMux rendezvous) and left to free-run for --free-run-s seconds.
    The linear regression slope of diff(t) shows the raw crystal frequency
    difference (typically 1-10 ppm).

  Phase 1 -- PTP ACTIVE (compensated):
    IPs configured, ping verified, PTP started (GM / Follower roles),
    Follower reaches FINE state. After settle, clocks are re-zeroed and
    collection runs for --ptp-s seconds. Regression slope should collapse
    toward 0 ppb -- the IIR-based TISUBN correction has slewed the FOL
    tick rate to match GM.

  Final Comparison:
    Before/after table: slope ppb/ppm, residual stdev, drift_fol, reduction %.

This test reuses the robust mux-based infrastructure from
ptp_drift_compensate_test.py:
  - SerialMux     : background reader thread separating clk_get from trace
  - Parallel      : thread-rendezvous clk_set 0 (sub-ms skew)
  - Filter        : prompt/echo noise stripped from per-sample output
  - loop_stats    : queried at end of each phase to confirm firmware health

ptp_trace is DISABLED by default (it amplifies UART-induced outliers).
Use --trace to enable firmware trace output during the PTP phase.

PASS criteria (PTP phase):
    |slope_ptp| < --slope-threshold-ppm   (default 2.0 ppm)
    residual stdev < --residual-threshold-us (default 500 us)

Usage:
    python ptp_sync_before_after_mux_test.py --gm-port COM8 --fol-port COM10
    python ptp_sync_before_after_mux_test.py --gm-port COM8 --fol-port COM10 \\
        --free-run-s 60 --ptp-s 120

Requirements:
    pip install pyserial
"""

import argparse
import datetime
import sys
import threading
import time
from typing import Optional, Tuple

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)

# Re-use the mux infrastructure and helpers from the drift test.
from ptp_drift_compensate_test import (  # noqa: E402
    Logger,
    SerialMux,
    TraceCollector,
    open_port,
    send_command,
    wait_for_pattern,
    collect_clk_get_samples,
    evaluate_samples,
    print_regression_report,
    RE_IP_SET,
    RE_BUILD,
    RE_PING_REPLY,
    RE_PING_DONE,
    RE_FOL_START,
    RE_GM_START,
    RE_MATCHFREQ,
    RE_HARD_SYNC,
    RE_COARSE,
    RE_FINE,
    DEFAULT_GM_PORT,
    DEFAULT_FOL_PORT,
    DEFAULT_GM_IP,
    DEFAULT_FOL_IP,
    DEFAULT_NETMASK,
    DEFAULT_CONV_TIMEOUT,
    DEFAULT_PAUSE_MS,
    DEFAULT_SETTLE_S,
    DEFAULT_SLOPE_THRESHOLD_PPM,
    DEFAULT_RESIDUAL_THRESHOLD_US,
    DEFAULT_OUTLIER_US,
)

# ---------------------------------------------------------------------------
# Test-specific defaults
# ---------------------------------------------------------------------------

DEFAULT_FREE_RUN_S = 60.0
DEFAULT_PTP_S      = 60.0


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class PTPSyncBeforeAfterMuxTest:

    def __init__(self, gm_port: str, fol_port: str,
                 gm_ip: str, fol_ip: str, netmask: str,
                 free_run_s: float, ptp_s: float,
                 pause_ms: int, settle_s: float,
                 slope_threshold_ppm: float,
                 residual_threshold_us: float,
                 outlier_us: float,
                 conv_timeout: float, no_swap: bool, no_clk_set: bool,
                 no_reset: bool, no_trace: bool,
                 log: Logger):
        self.gm_port              = gm_port
        self.fol_port             = fol_port
        self.gm_ip                = gm_ip
        self.fol_ip               = fol_ip
        self.netmask              = netmask
        self.free_run_s           = free_run_s
        self.ptp_s                = ptp_s
        self.pause_ms             = pause_ms
        self.settle_s             = settle_s
        self.slope_threshold_ppm  = slope_threshold_ppm
        self.residual_threshold_us = residual_threshold_us
        self.outlier_us           = outlier_us
        self.conv_timeout         = conv_timeout
        self.no_swap              = no_swap
        self.no_clk_set           = no_clk_set
        self.no_reset             = no_reset
        self.no_trace             = no_trace
        self.log                  = log

        self.ser_gm:  Optional[serial.Serial]  = None
        self.ser_fol: Optional[serial.Serial]  = None
        self.mux_gm:  Optional[SerialMux]      = None
        self.mux_fol: Optional[SerialMux]      = None
        self.trace:   Optional[TraceCollector] = None
        self.results: list = []
        self.gm_build:  str = "unknown"
        self.fol_build: str = "unknown"

        self._conv_thread: Optional[threading.Thread] = None
        self._conv_result: Optional[tuple]            = None
        self._free_stats: Optional[dict]              = None
        self._ptp_stats:  Optional[dict]              = None

    # ------------------------------------------------------------------
    def connect(self):
        for label, port, attr in [("GM",  self.gm_port,  "ser_gm"),
                                   ("FOL", self.fol_port, "ser_fol")]:
            self.log.info(f"Opening {label} ({port})...")
            try:
                setattr(self, attr, open_port(port))
                self.log.info(f"  {label} open: {port}")
            except serial.SerialException as exc:
                self.log.info(f"ERROR: cannot open {port}: {exc}")
                sys.exit(1)

    def disconnect(self):
        self._stop_mux()
        for ser in (self.ser_gm, self.ser_fol):
            if ser and ser.is_open:
                try: ser.close()
                except Exception: pass

    def _start_mux(self):
        self.mux_gm  = SerialMux(self.ser_gm,  "GM",  self.log)
        self.mux_fol = SerialMux(self.ser_fol, "FOL", self.log)
        self.trace   = TraceCollector(self.log)
        self.trace.add_mux(self.mux_gm)
        self.trace.add_mux(self.mux_fol)
        self.mux_gm.start()
        self.mux_fol.start()
        self.log.info("  [MUX] Background readers started (GM + FOL)")

    def _stop_mux(self):
        if self.mux_gm:
            self.mux_gm.stop()
            self.mux_gm = None
        if self.mux_fol:
            self.mux_fol.stop()
            self.mux_fol = None
        self.trace = None

    def _record(self, name: str, passed: bool, detail: str = ""):
        self.results.append((name, passed, detail))

    # ------------------------------------------------------------------
    def step_reset(self):
        self.log.info("\n--- Step 0: Reset ---")
        for label, ser in [("GM",  self.ser_gm),
                            ("FOL", self.ser_fol)]:
            self.log.info(f"  [{label}] reset")
            send_command(ser, "reset", self.conv_timeout, self.log)
        self.log.info("  Waiting 8 s for boot...")
        time.sleep(8)
        for label, ser, attr in [("GM",  self.ser_gm,  "gm_build"),
                                  ("FOL", self.ser_fol, "fol_build")]:
            extra = b""
            while ser.in_waiting:
                extra += ser.read(ser.in_waiting)
                time.sleep(0.05)
            combined = extra.decode("ascii", errors="replace")
            m = RE_BUILD.search(combined)
            build_ts = m.group(1).strip() if m else "unknown"
            setattr(self, attr, build_ts)
            self.log.info(f"  [{label}] firmware build: {build_ts}")
        self._record("Step 0: Reset", True,
                     f"GM={self.gm_build}  FOL={self.fol_build}")

    # ------------------------------------------------------------------
    def _parallel_zero_via_mux(self) -> bool:
        """clk_set 0 on BOTH boards simultaneously via mux rendezvous."""
        res: dict = {}
        ready_gm  = threading.Event()
        ready_fol = threading.Event()
        go        = threading.Event()

        def _set_via_mux(mux, key, ready):
            ready.set()
            go.wait()
            t, ok = mux.send_and_wait("clk_set 0", "clk_set ok")
            res[key] = (t, ok)

        ta = threading.Thread(target=_set_via_mux,
                              args=(self.mux_gm,  "gm",  ready_gm))
        tb = threading.Thread(target=_set_via_mux,
                              args=(self.mux_fol, "fol", ready_fol))
        ta.start(); tb.start()
        ready_gm.wait(); ready_fol.wait()
        go.set()
        ta.join(timeout=3.0); tb.join(timeout=3.0)
        t_gm,  ok_gm  = res.get("gm",  (0, False))
        t_fol, ok_fol = res.get("fol", (0, False))
        skew_us = (t_fol - t_gm) / 1000
        self.log.info(f"  [GM ] clk_set 0: {'OK' if ok_gm  else 'FAIL'}")
        self.log.info(f"  [FOL] clk_set 0: {'OK' if ok_fol else 'FAIL'}")
        self.log.info(f"  Thread send skew: {skew_us:.1f} us")
        return ok_gm and ok_fol

    def _query_loop_stats(self, phase_label: str):
        if not (self.mux_gm and self.mux_fol and self.trace):
            return
        self.log.info(f"\n  --- loop_stats ({phase_label}) ---")
        for label, mux in [("GM", self.mux_gm), ("FOL", self.mux_fol)]:
            mux.send_and_wait("loop_stats", "TOTAL", timeout_s=2.0)
            time.sleep(0.1)
            lines = self.trace.drain_all() if self.trace else []
            for _, _, txt in lines:
                if "loop_stats" in txt or any(k in txt for k in
                    ("SYS_CMD", "TCPIP", "LOG_FLUSH", "APP", "TOTAL", "subsystem")):
                    self.log.info(f"    [{label}] {txt}")

    # ------------------------------------------------------------------
    def phase_free_run(self) -> bool:
        """Phase 0: Free-running, no PTP active. Mux runs just for safe clk_get."""
        log = self.log
        log.info("\n" + "=" * 68)
        log.info(f"  PHASE 0: FREE RUNNING (no PTP)  ({self.free_run_s:.0f} s)")
        log.info("=" * 68)
        log.info("  Both clocks free-run at their raw TC0 crystal rate.")
        log.info("  Expected:  slope = crystal frequency difference (1-10 ppm)")
        log.info("             drift_ppb = 0 (PTP IIR not active)")

        self._start_mux()

        if not self.no_clk_set:
            log.info("  Zeroing both clocks (parallel via mux) ...")
            ok = self._parallel_zero_via_mux()
            if not ok:
                log.info("  WARNING: clk_set 0 failed on one or both boards")
            log.info("  Settling 2 s after clk_set ...")
            time.sleep(2.0)

        # Reset loop_stats for this phase only
        self.mux_gm.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
        self.mux_fol.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
        self.trace.drain_all()

        smpls, elps, dgm, dfol = collect_clk_get_samples(
            self.mux_gm, self.mux_fol, self.trace,
            self.free_run_s, self.pause_ms,
            self.no_swap, "FREE", self.outlier_us, log)

        self._query_loop_stats("after Phase 0")

        stats = evaluate_samples(smpls, elps, dgm, dfol, "FREE", log)
        self._stop_mux()

        if stats is None:
            self._record("Phase 0: Free-run (no PTP)", False, "too few samples")
            return False

        print_regression_report(stats, "PHASE 0 -- FREE RUNNING (no PTP)", log)
        self._free_stats = stats

        log.info("")
        log.info("  Interpretation:")
        slope_ppb = stats["slope_ppb"]
        slope_ppm = stats["slope_ppm"]
        direction = "SLOWER" if slope_ppb < 0 else "FASTER"
        log.info(f"    FOL ({self.fol_port}) crystal runs "
                 f"{abs(slope_ppm):.4f} ppm {direction} than GM ({self.gm_port}).")
        log.info(f"    Without PTP: ~{abs(slope_ppb):.0f} ns divergence per second"
                 f"  ->  ~{abs(slope_ppb)*3600/1e6:.1f} ms after 1 hour.")

        self._record(
            "Phase 0: Free-run (no PTP)", True,
            f"slope={slope_ppb:+.0f}ppb({slope_ppm:+.4f}ppm) "
            f"res_stdev={stats['res_stdev_ns']/1000:.0f}us "
            f"drift_fol={stats['mean_drift_fol']:+.0f}ppb")
        return True

    # ------------------------------------------------------------------
    def step_ip(self) -> bool:
        self.log.info("\n--- PTP Setup Step 1: IP Configuration ---")
        passed = True
        for label, ser, ip in [("GM",  self.ser_gm,  self.gm_ip),
                                ("FOL", self.ser_fol, self.fol_ip)]:
            resp = send_command(ser, f"setip eth0 {ip} {self.netmask}",
                                self.conv_timeout, self.log)
            ok = bool(RE_IP_SET.search(resp))
            self.log.info(f"  [{label}] {ip}: {'OK' if ok else 'FAIL'}")
            if not ok: passed = False
        self._record("PTP Setup: IP Configuration", passed)
        return passed

    # ------------------------------------------------------------------
    def step_ping(self) -> bool:
        self.log.info("\n--- PTP Setup Step 2: Ping Connectivity ---")
        passed = True
        for src_label, src_ser, dst_ip in [
            ("GM  -> FOL", self.ser_gm,  self.fol_ip),
            ("FOL -> GM",  self.ser_fol, self.gm_ip),
        ]:
            src_ser.reset_input_buffer()
            src_ser.write(f"ping {dst_ip}\r\n".encode("ascii"))
            matched, elapsed, ms = wait_for_pattern(
                src_ser, RE_PING_DONE, timeout=15.0, log=self.log,
                extra_patterns={"reply": RE_PING_REPLY}, live_log=True)
            ok = matched or ms.get("reply") is not None
            self.log.info(f"  [{src_label}]: {'OK' if ok else 'FAIL'} ({elapsed:.1f}s)")
            if not ok: passed = False
        self._record("PTP Setup: Ping", passed)
        return passed

    # ------------------------------------------------------------------
    def _start_conv_thread(self):
        self._conv_result = None
        self._conv_thread = threading.Thread(
            target=self._conv_worker, daemon=True)
        self._conv_thread.start()

    def _conv_worker(self):
        try:
            self._conv_result = wait_for_pattern(
                self.ser_fol, RE_FINE, self.conv_timeout, self.log,
                extra_patterns={"MATCHFREQ": RE_MATCHFREQ,
                                "HARD_SYNC": RE_HARD_SYNC,
                                "COARSE":    RE_COARSE},
                live_log=True)
        except Exception as exc:
            self.log.info(f"  [CONV-THREAD ERROR] {exc}")
            self._conv_result = (False, self.conv_timeout, {})

    def _collect_conv(self) -> Tuple[bool, float, dict]:
        if self._conv_thread:
            self._conv_thread.join(timeout=self.conv_timeout + 2.0)
        return self._conv_result if self._conv_result else (False, self.conv_timeout, {})

    # ------------------------------------------------------------------
    def step_start_ptp(self) -> bool:
        self.log.info("\n--- PTP Setup Step 3: Start PTP ---")
        passed = True

        self.log.info("  [FOL] ptp_mode follower")
        resp = send_command(self.ser_fol, "ptp_mode follower",
                            self.conv_timeout, self.log)
        if RE_FOL_START.search(resp):
            self.log.info("  [FOL] confirmed")
        else:
            self.log.info(f"  [FOL] no confirmation: {resp.strip()!r}")
            passed = False

        time.sleep(0.5)
        self.ser_fol.reset_input_buffer()
        self._start_conv_thread()

        self.log.info("  [GM ] ptp_mode master")
        self.ser_gm.reset_input_buffer()
        self.ser_gm.write(b"ptp_mode master\r\n")
        gm_ok, _, _ = wait_for_pattern(self.ser_gm, RE_GM_START,
                                       timeout=self.conv_timeout, log=self.log)
        self.log.info(f"  [GM ] {'confirmed' if gm_ok else 'NOT confirmed'}")
        if not gm_ok: passed = False

        self.log.info(f"  Waiting for FOL FINE (timeout={self.conv_timeout:.0f}s)...")
        matched, elapsed, milestones = self._collect_conv()
        ms_str = ", ".join(f"{k}@{v:.1f}s" for k, v in milestones.items())
        if matched:
            self.log.info(f"  FOL FINE in {elapsed:.1f}s  ({ms_str})")
        else:
            self.log.info(f"  FOL FINE NOT reached in {self.conv_timeout:.0f}s  "
                          f"({ms_str or 'none'})")
            passed = False

        fine_detail = (f"FINE@{elapsed:.1f}s {ms_str}" if matched
                       else "FINE NOT reached")
        self._record("PTP Setup: FINE Convergence", passed, fine_detail)
        return passed

    # ------------------------------------------------------------------
    def phase_ptp_active(self) -> bool:
        """Phase 1: PTP active, compensated measurement."""
        log = self.log
        log.info("\n" + "=" * 68)
        log.info(f"  PHASE 1: PTP ACTIVE (compensated)  ({self.ptp_s:.0f} s)")
        log.info("=" * 68)
        log.info("  PTP re-anchors FOL clock to GM every ~125 ms (Sync).")
        log.info("  TC0 tick-rate (TISUBN) is trimmed to compensate crystal offset.")
        log.info("  Expected:  slope ~= 0 ppb  (IIR filter active)")
        log.info("             drift_ppb = residual after TISUBN correction")

        # Optionally enable firmware trace BEFORE starting mux
        if not self.no_trace:
            log.info("\n  Enabling ptp_trace on both boards ...")
            for label, ser in [("GM", self.ser_gm), ("FOL", self.ser_fol)]:
                ser.reset_input_buffer()
                ser.write(b"ptp_trace on\r\n")
                time.sleep(0.2)
                _ = ser.read(ser.in_waiting).decode("ascii", errors="replace")
                log.info(f"  [{label}] ptp_trace on sent")

        self._start_mux()

        if not self.no_clk_set:
            log.info(f"\n  Settling {self.settle_s:.0f} s after FINE before zeroing clocks...")
            time.sleep(self.settle_s)
            log.info("  Zeroing both clocks (parallel via mux) ...")
            ok = self._parallel_zero_via_mux()
            if not ok:
                log.info("  WARNING: clk_set 0 failed on one or both boards")
        elif self.settle_s > 0:
            log.info(f"\n  Settling {self.settle_s:.0f} s after FINE ...")
            time.sleep(self.settle_s)

        # Reset loop_stats for the PTP phase
        self.mux_gm.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
        self.mux_fol.send_and_wait("loop_stats reset", "loop_stats: reset", timeout_s=1.0)
        self.trace.drain_all()

        smpls, elps, dgm, dfol = collect_clk_get_samples(
            self.mux_gm, self.mux_fol, self.trace,
            self.ptp_s, self.pause_ms,
            self.no_swap, "PTP", self.outlier_us, log)

        self._query_loop_stats("after Phase 1")

        stats = evaluate_samples(smpls, elps, dgm, dfol, "PTP", log)

        # Disable firmware trace if we enabled it
        if not self.no_trace:
            log.info("\n  Disabling ptp_trace ...")
            for label, ser in [("GM", self.ser_gm), ("FOL", self.ser_fol)]:
                try:
                    ser.write(b"ptp_trace off\r\n")
                except Exception:
                    pass

        self._stop_mux()

        if stats is None:
            self._record("Phase 1: PTP active (compensated)", False, "too few samples")
            return False

        print_regression_report(stats, "PHASE 1 -- PTP ACTIVE (compensated)", log)
        self._ptp_stats = stats

        threshold_ns    = self.slope_threshold_ppm * 1000.0
        res_threshold_ns = self.residual_threshold_us * 1000.0
        slope_passed    = abs(stats["slope_ppb"]) < threshold_ns
        residual_passed = stats["res_stdev_ns"]   < res_threshold_ns
        passed          = slope_passed and residual_passed

        log.info("")
        tag  = "PASS" if slope_passed else "FAIL"
        log.info(f"  {tag}  |slope| = {abs(stats['slope_ppm']):.4f} ppm"
                 f"  (threshold {self.slope_threshold_ppm} ppm)")
        tag2 = "PASS" if residual_passed else "FAIL"
        log.info(f"  {tag2}  residual stdev = {stats['res_stdev_ns']/1000:.3f} us"
                 f"  (threshold {self.residual_threshold_us} us)")

        detail = (f"slope={stats['slope_ppb']:+.0f}ppb({stats['slope_ppm']:+.4f}ppm) "
                  f"res_stdev={stats['res_stdev_ns']/1000:.0f}us "
                  f"drift_fol={stats['mean_drift_fol']:+.0f}ppb")
        self._record("Phase 1: PTP active (compensated)", passed, detail)
        return passed

    # ------------------------------------------------------------------
    def _print_comparison(self):
        if self._free_stats is None or self._ptp_stats is None:
            return

        fr  = self._free_stats
        ptp = self._ptp_stats
        log = self.log

        log.info("\n" + "=" * 68)
        log.info("  BEFORE / AFTER COMPARISON -- Effect of PTP Synchronisation")
        log.info("=" * 68)

        reduction = 0.0
        if abs(fr["slope_ppb"]) > 0:
            reduction = (1.0 - abs(ptp["slope_ppb"]) / abs(fr["slope_ppb"])) * 100.0

        log.info(f"  {'Metric':<30}  {'Free-run (no PTP)':>20}  {'PTP active':>16}")
        log.info(f"  {'-'*30}  {'-'*20}  {'-'*16}")
        log.info(f"  {'Slope (ppb)':<30}  "
                 f"{fr['slope_ppb']:>+18.0f}    "
                 f"{ptp['slope_ppb']:>+14.0f}")
        log.info(f"  {'Slope (ppm)':<30}  "
                 f"{fr['slope_ppm']:>+18.4f}    "
                 f"{ptp['slope_ppm']:>+14.4f}")
        log.info(f"  {'Residual stdev (us)':<30}  "
                 f"{fr['res_stdev_ns']/1000:>18.3f}    "
                 f"{ptp['res_stdev_ns']/1000:>14.3f}")
        log.info(f"  {'Drift FOL mean (ppb)':<30}  "
                 f"{fr['mean_drift_fol']:>+18.0f}    "
                 f"{ptp['mean_drift_fol']:>+14.0f}")
        log.info(f"  {'Drift FOL stdev (ppb)':<30}  "
                 f"{fr['stdev_drift_fol']:>18.0f}    "
                 f"{ptp['stdev_drift_fol']:>14.0f}")
        log.info(f"  {'Time span (s)':<30}  "
                 f"{fr['t_span']:>18.1f}    "
                 f"{ptp['t_span']:>14.1f}")
        log.info("")
        log.info(f"  Slope reduction by PTP : {reduction:.1f} %")
        log.info("")
        log.info("  Interpretation:")
        log.info(f"    Without PTP: FOL drifted at "
                 f"{fr['slope_ppb']:+.0f} ppb ({fr['slope_ppm']:+.4f} ppm).")
        log.info( "    PTP hard-anchors FOL to GM every ~125 ms (each Sync) AND trims the")
        log.info( "    TC0 tick-rate via TISUBN to compensate crystal-frequency offset.")
        if reduction > 0:
            log.info(f"    Residual slope reduced by {reduction:.1f} % after PTP active.")
            log.info(f"    Max drift between two Syncs: ~{abs(fr['slope_ppb'])*0.125/1000:.0f} us"
                     f"  ({abs(fr['slope_ppb']):.0f} ppb x 125 ms)")
        log.info(f"    Residual stdev {ptp['res_stdev_ns']/1000:.3f} us is dominated by"
                 f" UART/USB-CDC measurement jitter (see loop_stats).")
        log.info("=" * 68)

        passed = (reduction > 50.0 or
                  abs(ptp["slope_ppb"]) < self.slope_threshold_ppm * 1000.0)
        self._record(
            "Comparison: Drift reduction by PTP", passed,
            f"free={fr['slope_ppb']:+.0f}ppb "
            f"ptp={ptp['slope_ppb']:+.0f}ppb "
            f"reduction={reduction:.1f}%")

    # ------------------------------------------------------------------
    def run(self) -> int:
        start_time = datetime.datetime.now()
        log = self.log

        log.info("=" * 68)
        log.info("  PTP Sync Before/After Test (mux-based)")
        log.info("=" * 68)
        log.info(f"Date                    : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"Board GM  port          : {self.gm_port}  IP {self.gm_ip}")
        log.info(f"Board FOL port          : {self.fol_port}  IP {self.fol_ip}")
        log.info(f"Free-run duration       : {self.free_run_s:.0f} s")
        log.info(f"PTP-active duration     : {self.ptp_s:.0f} s")
        log.info(f"Pause between samples   : {self.pause_ms} ms")
        log.info(f"Settle after FINE       : {self.settle_s:.0f} s")
        log.info(f"Slope threshold (PTP)   : {self.slope_threshold_ppm} ppm")
        log.info(f"Residual threshold (PTP): {self.residual_threshold_us} us")
        log.info(f"Outlier flag            : > {self.outlier_us:.0f} us")
        log.info(f"PTP conv timeout        : {self.conv_timeout:.0f} s")
        log.info(f"Swap-symmetry           : {'disabled' if self.no_swap else 'enabled'}")
        log.info(f"clk_set 0 before phases : {'skipped' if self.no_clk_set else 'yes'}")
        log.info(f"Reset boards            : {'skipped' if self.no_reset else 'yes'}")
        log.info(f"ptp_trace during PTP    : {'OFF' if self.no_trace else 'ON'}")
        log.info("")

        self.connect()
        try:
            if not self.no_reset:
                self.step_reset()

            # ---- Phase 0: Free-running, no PTP ----
            self.phase_free_run()

            # ---- PTP Setup ----
            log.info("\n" + "=" * 68)
            log.info("  PTP SETUP")
            log.info("=" * 68)
            if not self.step_ip():        return self._report(start_time)
            if not self.step_ping():      return self._report(start_time)
            if not self.step_start_ptp(): return self._report(start_time)

            # ---- Phase 1: PTP active ----
            self.phase_ptp_active()

            # ---- Final comparison ----
            self._print_comparison()

        except KeyboardInterrupt:
            log.info("\nInterrupted by user.")
        except Exception as exc:
            import traceback
            log.info(f"\nFATAL: {type(exc).__name__}: {exc}")
            log.info(traceback.format_exc())
        finally:
            self.disconnect()

        return self._report(start_time)

    # ------------------------------------------------------------------
    def _report(self, start_time: datetime.datetime) -> int:
        log     = self.log
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info("\n" + "=" * 68)
        log.info("  PTP Sync Before/After Test -- Final Report")
        log.info("=" * 68)
        passed_count = 0
        for name, passed, detail in self.results:
            tag = "PASS" if passed else "FAIL"
            log.info(f"[{tag}] {name}")
            for part in detail.split("  "):
                p = part.strip()
                if p: log.info(f"       {p}")
            if passed: passed_count += 1
        total   = len(self.results)
        overall = "PASS" if passed_count == total else "FAIL"
        log.info("")
        log.info(f"Duration : {elapsed:.1f} s")
        log.info(f"Overall  : {overall} ({passed_count}/{total})")
        if log.log_file:
            log.info(f"Log      : {log.log_file}")
        return 0 if passed_count == total else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="PTP Sync Before/After Test (mux-based)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    p.add_argument("--gm-port",  default=DEFAULT_GM_PORT)
    p.add_argument("--fol-port", default=DEFAULT_FOL_PORT)
    p.add_argument("--gm-ip",    default=DEFAULT_GM_IP)
    p.add_argument("--fol-ip",   default=DEFAULT_FOL_IP)
    p.add_argument("--netmask",  default=DEFAULT_NETMASK)

    p.add_argument("--free-run-s", default=DEFAULT_FREE_RUN_S, type=float,
                   help=f"Free-run collection duration in s (default: {DEFAULT_FREE_RUN_S})")
    p.add_argument("--ptp-s",      default=DEFAULT_PTP_S, type=float,
                   help=f"PTP-active collection duration in s (default: {DEFAULT_PTP_S})")
    p.add_argument("--pause-ms",   default=DEFAULT_PAUSE_MS, type=int,
                   help=f"Pause between clk_get pairs in ms (default: {DEFAULT_PAUSE_MS})")
    p.add_argument("--settle",     default=DEFAULT_SETTLE_S, type=float,
                   help=f"Settle after PTP FINE in s (default: {DEFAULT_SETTLE_S})")
    p.add_argument("--conv-timeout", default=DEFAULT_CONV_TIMEOUT, type=float)

    p.add_argument("--slope-threshold-ppm",    default=DEFAULT_SLOPE_THRESHOLD_PPM,   type=float)
    p.add_argument("--residual-threshold-us",  default=DEFAULT_RESIDUAL_THRESHOLD_US, type=float)
    p.add_argument("--outlier-us",             default=DEFAULT_OUTLIER_US,            type=float)

    p.add_argument("--no-swap",    action="store_true")
    p.add_argument("--no-clk-set", action="store_true")
    p.add_argument("--no-reset",   action="store_true")
    p.add_argument("--trace",      action="store_true",
                   help="enable ptp_trace during PTP phase (default: OFF)")

    p.add_argument("--log-file", default=None)
    p.add_argument("--verbose",  action="store_true")

    args = p.parse_args()

    log_file = args.log_file
    if log_file is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"ptp_sync_before_after_mux_test_{ts}.log"

    log  = Logger(log_file=log_file, verbose=args.verbose)
    test = PTPSyncBeforeAfterMuxTest(
        gm_port               = args.gm_port,
        fol_port              = args.fol_port,
        gm_ip                 = args.gm_ip,
        fol_ip                = args.fol_ip,
        netmask               = args.netmask,
        free_run_s            = args.free_run_s,
        ptp_s                 = args.ptp_s,
        pause_ms              = args.pause_ms,
        settle_s              = args.settle,
        slope_threshold_ppm   = args.slope_threshold_ppm,
        residual_threshold_us = args.residual_threshold_us,
        outlier_us            = args.outlier_us,
        conv_timeout          = args.conv_timeout,
        no_swap               = args.no_swap,
        no_clk_set            = args.no_clk_set,
        no_reset              = args.no_reset,
        no_trace              = not args.trace,
        log                   = log,
    )
    try:
        return test.run()
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
