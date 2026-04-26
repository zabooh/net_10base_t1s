# Documentation Index

This directory contains project documentation for the PTP-10BASE-T1S firmware (ATSAME54P20A + LAN865x).

The top-level [readme.md](../readme.md) has the project overview. Risks and open questions live in [RISKS.md](../RISKS.md).

> **Freeze-state sync quality (2026-04-24):** cross-board PD10 drift
> MAD 13.6 µs, slope −0.07 ppm over 10 s, both PASS gates met
> (`|slope| < 5 ppm`, `MAD < 50 µs`). Canonical test:
> [../tools/ptp-analysis/sync-tests/pd10_sync_before_after_test.py](../tools/ptp-analysis/sync-tests/pd10_sync_before_after_test.py).
> Full numbers in [testing/pd10_sync_before_after_tests.md](testing/pd10_sync_before_after_tests.md).

> **Note:** `documentation/` (not `docs/`) — the `docs/` directory holds Microchip Harmony Oxygen-generated web-help and must stay untouched.

## Topics

### [timing/](timing/) — Timing & Clock Subsystem
- [software_ptp_clock_design.md](timing/software_ptp_clock_design.md) — Software PTP clock design & architecture
- [software_ptp_clock_cli_test.md](timing/software_ptp_clock_cli_test.md) — CLI test procedures for the software PTP clock
- [timer_considerations.md](timing/timer_considerations.md) — Hardware timer trade-offs for PTP_CLOCK

### [ptp/](ptp/) — PTP Protocol
- [implementation.md](ptp/implementation.md) — Full PTP / IEEE 1588-2008 implementation spec
- [ntp_reference.md](ptp/ntp_reference.md) — Software NTP as application-layer time sync reference
- [drift_filter.md](ptp/drift_filter.md) — Adaptive IIR drift filter design
- [README_cross.md](ptp/README_cross.md) — Cross-build (CMake + MPLAB X), driver-minimization journey, MCC tooling-bug analysis, and Zephyr/Harmony future-platform options

### [hardware/](hardware/) — Hardware & Diagnostics
- [exception_dump.md](hardware/exception_dump.md) — Exception dump + watchdog + find_exception.py
- [distributed_adc.md](hardware/distributed_adc.md) — Distributed ADC sampling bandwidth characterization

### [features/](features/) — Features
- [tfuture.md](features/tfuture.md) — Coordinated firing at absolute PTP_CLOCK time
- [standalone_demo.md](features/standalone_demo.md) — Standalone PTP synchronisation demo

### [testing/](testing/) — Verification & Test
- [smoke_test.md](testing/smoke_test.md) — Broad functional regression guard
- [pd10_sync_tests.md](testing/pd10_sync_tests.md) — PD10 cross-board synchronicity
- [pd10_sync_before_after_tests.md](testing/pd10_sync_before_after_tests.md) — Detailed before/after sync quality
- [saleae_logic_analyzer.md](testing/saleae_logic_analyzer.md) — Saleae Logic 2 setup & usage
- [saleae_freq_characterization.md](testing/saleae_freq_characterization.md) — `saleae_freq_check.py` freq & phase

### [firmware/](firmware/) — Firmware Modules
- [modules.md](firmware/modules.md) — Function / description / API for firmware modules

### [agent/](agent/) — Agent / Tooling
- [agent_automation.md](agent/agent_automation.md) — Agent-driven automation in VS Code + Claude Code

### [datasheets/](datasheets/) — External Datasheets
- Reference PDFs for LAN8650, SAME54, and the SAME54 Curiosity Ultra board.

## Developer Tools

Scripts for flashing, testing, and analysis live under [../tools/](../tools/). See [../tools/README.md](../tools/README.md) for the catalog.
