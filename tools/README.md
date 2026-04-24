# Developer Tools

Scripts for flashing firmware, running tests, and analyzing PTP behaviour on the ATSAME54P20A + LAN865x hardware.

For background documentation, see [../documentation/README.md](../documentation/README.md).

## Layout

```
tools/
├── flash/                      # firmware programming & debugger/toolchain setup
├── test-harness/               # top-level regression / smoke / sweep test drivers
├── ptp-analysis/               # PTP measurement & analysis
│   ├── sync-tests/             #   before/after sync & PD10 cross-board sync
│   ├── ptp-delay-tests/        #   round-trip delay, offset, time
│   ├── ptp-drift-tests/        #   drift compensation, filter freeze, phase diag
│   ├── misc-ptp-tests/         #   on/off, reproducibility, role-swap, trace, HW timer
│   └── tfuture-tests/          #   coordinated firing at absolute PTP_CLOCK time
└── saleae-logic-analyzer/      # Saleae Logic 2 capture / polling scripts
```

## Flash & Debugger Setup (at the repo root)

All flash / setup bootstrap scripts live at the repo root (not under `tools/`)
so a fresh clone can flash without any `cd` gymnastics.

| Script | Purpose |
|--------|---------|
| [flash.py](../flash.py) | Program both boards via MPLAB MDB.  **No argument** → programs the checked-in `apps/.../out/tcpip_iperf_lan865x/default.hex` — that file is overwritten in place by `build.bat`, so the next `flash.py` automatically picks up your latest build. |
| [mdb_flash.py](../mdb_flash.py) | Low-level MDB wrapper used by `flash.py` |
| [setup_flasher.py](../setup_flasher.py) | One-time: detect + assign Board 1 / Board 2 debugger serials (writes `setup_flasher.config`) |
| [setup_compiler.py](../setup_compiler.py) | One-time: pick an installed XC32 version (writes `setup_compiler.config`) |
| [setup_debug.py](../setup_debug.py) | One-time: patch SAME54_DFP tool-pack so VS Code `cortex-debug` works |

**Zero-to-flashed quick path:**

```bat
python setup_flasher.py        :: once — detect both EDBG debuggers
python flash.py                :: programs default.hex → both boards
```

**Rebuild + flash:**

```bat
python setup_compiler.py       :: once — pick XC32
build.bat                      :: overwrites default.hex
python flash.py                :: re-flash with the new default.hex
```

See the top-level [readme.md §How To Reproduce](../readme.md#how-to-reproduce)
for the full walkthrough including the post-flash smoke test.

## [test-harness/](test-harness/) — Regression & Smoke

| Script | Purpose |
|--------|---------|
| [smoke_test.py](test-harness/smoke_test.py) | Broad functional regression guard — 58 checks over boot, PTP FINE, every CLI command, tfuture, cyclic_fire (SQUARE/MARKER/FREE), SW-NTP end-to-end.  On exit, automatically resets both boards and re-establishes PTP sync (GM+FOL FINE) so the hardware is left ready for the next step without a manual reset.  Default ports `--gm-port COM8 --fol-port COM10`. |
| [overnight_test.py](test-harness/overnight_test.py) | Long-running stability test: periodic `clk_get` sampling + 5-minute drift regressions + optional Saleae phase checks, stops on any keypress with a summary. |
| [saleae_drift_test.py](test-harness/saleae_drift_test.py) | Short Saleae-based drift check (~30 s end-to-end) — reset → FINE → 1 kHz cyclic_fire on PD10 → short capture → phase-slope regression. |
| [standalone_demo_test.py](test-harness/standalone_demo_test.py) | Standalone PTP sync demo driver |
| [meta_cyclic_fire_sweep.py](test-harness/meta_cyclic_fire_sweep.py) | Automated sweep over cyclic-fire parameters |
| [cyclic_fire_hw_test.py](test-harness/cyclic_fire_hw_test.py) | Cyclic firing hardware test |

## [ptp-analysis/](ptp-analysis/) — PTP Measurement & Analysis

### [sync-tests/](ptp-analysis/sync-tests/)
- [ptp_sync_before_after_test.py](ptp-analysis/sync-tests/ptp_sync_before_after_test.py)
- [ptp_sync_before_after_mux_test.py](ptp-analysis/sync-tests/ptp_sync_before_after_mux_test.py)
- [pd10_sync_test.py](ptp-analysis/sync-tests/pd10_sync_test.py)
- [pd10_sync_before_after_test.py](ptp-analysis/sync-tests/pd10_sync_before_after_test.py)
- [pd10_sync_check.py](ptp-analysis/sync-tests/pd10_sync_check.py)

### [ptp-delay-tests/](ptp-analysis/ptp-delay-tests/)
- [ptp_delay_test.py](ptp-analysis/ptp-delay-tests/ptp_delay_test.py)
- [ptp_offset_test.py](ptp-analysis/ptp-delay-tests/ptp_offset_test.py)
- [ptp_offset_capture.py](ptp-analysis/ptp-delay-tests/ptp_offset_capture.py)
- [ptp_time_test.py](ptp-analysis/ptp-delay-tests/ptp_time_test.py)

### [ptp-drift-tests/](ptp-analysis/ptp-drift-tests/)
- [ptp_drift_compensate_test.py](ptp-analysis/ptp-drift-tests/ptp_drift_compensate_test.py)
- [pd10_filter_freeze_test.py](ptp-analysis/ptp-drift-tests/pd10_filter_freeze_test.py)
- [drift_filter_analysis.py](ptp-analysis/ptp-drift-tests/drift_filter_analysis.py)
- [pd10_phase_diag.py](ptp-analysis/ptp-drift-tests/pd10_phase_diag.py)

### [misc-ptp-tests/](ptp-analysis/misc-ptp-tests/)
- [ptp_onoff_test.py](ptp-analysis/misc-ptp-tests/ptp_onoff_test.py)
- [ptp_reproducibility_test.py](ptp-analysis/misc-ptp-tests/ptp_reproducibility_test.py)
- [ptp_role_swap_test.py](ptp-analysis/misc-ptp-tests/ptp_role_swap_test.py)
- [ptp_trace_debug_test.py](ptp-analysis/misc-ptp-tests/ptp_trace_debug_test.py)
- [sw_ntp_vs_ptp_test.py](ptp-analysis/misc-ptp-tests/sw_ntp_vs_ptp_test.py)
- [hw_timer_sync_test.py](ptp-analysis/misc-ptp-tests/hw_timer_sync_test.py)

### [tfuture-tests/](ptp-analysis/tfuture-tests/)
- [tfuture_anchor_delay_test.py](ptp-analysis/tfuture-tests/tfuture_anchor_delay_test.py)
- [tfuture_diagnose_test.py](ptp-analysis/tfuture-tests/tfuture_diagnose_test.py)
- [tfuture_drift_forced_test.py](ptp-analysis/tfuture-tests/tfuture_drift_forced_test.py)
- [tfuture_drift_forced_fol_test.py](ptp-analysis/tfuture-tests/tfuture_drift_forced_fol_test.py)
- [tfuture_sync_test.py](ptp-analysis/tfuture-tests/tfuture_sync_test.py)

## [saleae-logic-analyzer/](saleae-logic-analyzer/) — Logic Analyzer

| Script | Purpose |
|--------|---------|
| [saleae_poll.py](saleae-logic-analyzer/saleae_poll.py) | Poll the Saleae capture API |
| [saleae_capture_blink.py](saleae-logic-analyzer/saleae_capture_blink.py) | Capture blink-signal traces |
| [saleae_freq_check.py](saleae-logic-analyzer/saleae_freq_check.py) | Frequency & phase of a rectangle signal |
| [saleae_smoke.py](saleae-logic-analyzer/saleae_smoke.py) | Saleae smoke test |

## Scripts NOT in this tree

- **Repo-level meta tools** (run from repo root): `analyze_dependencies.py`, `build_pptx.py`
- **CMake-invoked**: `apps/.../tcpip_iperf_lan865x.X/build_summary.py`
- **MCC-coupled**: `config/module.py`, `driver/lan86*x/config/*.py` — tied to Harmony-generated code, do not relocate.
