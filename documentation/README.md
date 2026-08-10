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
- [plca_ptp_asymmetrie.md](ptp/plca_ptp_asymmetrie.md) — PLCA slot asymmetry and PTP compensation on 10BASE-T1S (Annex H background)
- [README_cross.md](ptp/README_cross.md) — Cross-build (CMake + MPLAB X), driver-minimization journey, MCC tooling-bug analysis, and Zephyr/Harmony future-platform options
- [readme_results.md](ptp/readme_results.md) — Feasibility analysis for 1 µs sync on 3–8 nodes: AN1847 review, 2-node limitations of the current implementation, why full 802.1AS multidrop is non-trivial, island-vs-deployment-scenario assessment, and a comparison of the project's added value over AN1847
- [readme_upgrade.md](ptp/readme_upgrade.md) — Concrete upgrade notes for the `mult-sync` branch: how this AN1847-style implementation differs from Microchip's reference (auto-mode, static path delay, GM-MAC fixation, multi-state servo, build flag `PTP_AN1847_STYLE`)
- [readme_acma.md](ptp/readme_acma.md) — Architecture sketch for **ACMA + Event-Generator-0 + PTP**: deterministic TDMA slot access on the multidrop bus, errata-aware ISR-driven re-arm pattern (s9 workaround), bootstrap from PLCA → ACMA, code skeleton, test plan, implementation roadmap
- [readme_acma_use_cases.md](ptp/readme_acma_use_cases.md) — Applications enabled by ACMA + PTP + 10BASE-T1S: 12+ use case classes across industrial control, sensor clusters, audio/video, automotive, avionics/safety, test/measurement, smart-building. Compact summary table mapping applications to required guarantees and bandwidth needs, plus what ACMA cannot do and Microchip's market positioning
- [readme_ptp_vs_acma_decision.md](ptp/readme_ptp_vs_acma_decision.md) — Decision guide (English): when do you need PTP, ACMA, or both? Decomposes "time-synchronous measurement and control with 1 µs resolution" into independent requirements, gives the four possible PLCA/ACMA/PTP configurations, four readings of "1 µs resolution", and a decision matrix by application type
- [readme_802_1as_roadmap.md](ptp/readme_802_1as_roadmap.md) — Strategy paper: gap from AN1847+ACMA to full 802.1AS-2020 conformance. Lists the seven missing building blocks (Pdelay multi-peer, BMCA, Announce, TLVs, sync-forwarding, state-machine framework, conformance tests), three concrete options (Island / Hybrid / Full), implementation-phase plan, and what survives the jump from current code

### [pdf/](pdf/) — Vendor PDF Reference
- [readme_pdf.md](pdf/readme_pdf.md) — Indexed cross-reference of the Microchip PDFs backing the PTP / Annex H analysis (datasheet, AN1847 time-synch, AN1760 configuration, ER1075 errata, AN6067 topology discovery, plus Linux/Zephyr driver application notes); includes §8 Topic Quick Lookup table mapping ~115 keywords to PDF + section + page
- [lan86xx_family.md](pdf/lan86xx_family.md) — Standalone reference table for the entire Microchip LAN86xx silicon family (LAN8650/1 integrated MAC-PHYs, LAN8670/1/2 standalone PHYs): main features, silicon revisions, complete feature matrix, electrical specs, document cross-references, application-driven selection guide, full errata overview with cross-family comparison
- [lan865x_vs_lan867x_architecture.md](pdf/lan865x_vs_lan867x_architecture.md) — Detailed block-architecture comparison: what lives in the MAC of LAN8650/1 (classic MAC functions plus the entire TSU per DS60001734 §4.5.1), where those functions migrate in the MAC-less LAN8670/1/2 family, with mermaid block diagrams for both architectures and an explanation of why LAN8670/1/2 has 5 RMII-specific errata that LAN8650/1 does not have

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
