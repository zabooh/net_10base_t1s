# Repo-Aufräum-Plan: `net_10base_t1s`

Stand: 2026-04-23 · Branch: `cyclic-isr`

Dieser Plan ist ein **Vorschlag zur Umstrukturierung** — noch nichts verschoben. Freigabe Phase für Phase.

---

## 1. IST-ZUSTAND

### 1.1 README-Dateien (20 insgesamt)

| Pfad | Titel/Zweck | Größe | Letzter Touch |
|------|-------------|-------|---------------|
| **Root** | | | |
| `./readme.md` | LAN8651 PTP IEEE 1588-2008 — Überblick & Index | ~139 KB | 2026-04-23 |
| `./readme_risks.md` | Risks & Open Questions | ~53 KB | 2026-04-20 |
| `./README_agent_automation.md` | Agent-Driven Automation in VS Code + Claude Code | ~16 KB | 2026-04-20 |
| **apps/** (Microchip-Boilerplate, minimal) | | | |
| `./apps/readme.md` | MCHP Logo + Boilerplate | 287 B | 2024-12-18 |
| `./apps/tcpip_iperf_lan865x/readme.md` | MCHP Logo + Boilerplate | 291 B | 2024-12-04 |
| `./apps/tcpip_iperf_lan867x/readme.md` | MCHP Logo + Boilerplate | 287 B | 2024-12-04 |
| **firmware/** | | | |
| `./apps/tcpip_iperf_lan865x/firmware/README_timestamp.md` | Software PTP Clock — Design | ~12.5 KB | 2026-04-20 |
| `./apps/tcpip_iperf_lan865x/firmware/README_timer_considerations.md` | Timer Considerations — PTP_CLOCK | ~11.4 KB | 2026-04-20 |
| `./apps/tcpip_iperf_lan865x/firmware/README_timestamp_test.md` | Software PTP Clock — CLI-Test | ~14.3 KB | 2026-04-08 |
| `./apps/tcpip_iperf_lan865x/firmware/src/README_modules.md` | Firmware Modules — Function, Description, API | ~28.7 KB | 2026-04-20 |
| **tcpip_iperf_lan865x.X/** | | | |
| `./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_PTP.md` | PTP Implementation — Spec | ~50.2 KB | 2026-04-23 |
| `./.../README_NTP.md` | Software NTP — Application-Layer Time Sync | ~23.6 KB | 2026-04-18 |
| `./.../README_exception_dump.md` | Exception Dump + Watchdog + find_exception.py | ~23.1 KB | 2026-04-23 |
| `./.../README_saleae.md` | Saleae Logic 2 — Setup and Test Scripts | ~18.7 KB | 2026-04-20 |
| `./.../README_standalone_demo.md` | Standalone PTP Synchronisation Demo | ~18.5 KB | 2026-04-23 |
| `./.../README_distributed_adc.md` | Distributed ADC Sampling Bandwidth | ~17.7 KB | 2026-04-23 |
| `./.../README_tfuture.md` | tfuture — Coordinated Firing at Absolute PTP_CLOCK | ~24.8 KB | 2026-04-19 |
| `./.../README_drift_filter.md` | PTP Drift Filter — Adaptive IIR Design | ~14.3 KB | 2026-04-23 |
| `./.../README_saleae_freq_check.md` | saleae_freq_check.py — Freq & Phase | ~13.3 KB | 2026-04-20 |
| `./.../README_smoke_test.md` | Smoke Test — Broad Regression Guard | ~13.0 KB | 2026-04-19 |
| `./.../README_pd10_sync_test.md` | PD10 Cross-Board Synchronicity Test | ~5.6 KB | 2026-04-22 |
| `./.../README_pd10_sync_before_after.md` | PD10 Before/After Sync Test | ~14.8 KB | 2026-04-23 |

**Thematische Gruppierung:**

| Kategorie | Dateien |
|-----------|---------|
| Timing/Clock | `README_timer_considerations.md`, `README_timestamp.md`, `README_timestamp_test.md` |
| PTP-Protokoll | `README_PTP.md`, `README_NTP.md`, `README_drift_filter.md` |
| Hardware | `README_distributed_adc.md`, `README_exception_dump.md` |
| Features | `README_tfuture.md`, `README_standalone_demo.md` |
| Testing | `README_smoke_test.md`, `README_saleae.md`, `README_saleae_freq_check.md`, `README_pd10_sync*.md` |
| Firmware-Module | `src/README_modules.md` |
| Risk/Meta | `readme_risks.md`, `README_agent_automation.md` |
| App-Boilerplate | `apps/readme.md`, `apps/*/readme.md` (bleiben unverändert) |

---

### 1.2 Python-Skripte (47 insgesamt)

| Kategorie | Anzahl | Bemerkung |
|-----------|--------|-----------|
| **Flash / Debug** | 5 | `flash.py`, `mdb_flash.py`, `setup_compiler.py`, `setup_debug.py`, `setup_flasher.py` — in `tcpip_iperf_lan865x.X/` |
| **PTP Sync-Tests** | 12 | `ptp_*.py`, `pd10_*.py` — in `tcpip_iperf_lan865x.X/` |
| **PD10 / Filter** | 3 | `pd10_sync_check.py`, `pd10_filter_freeze_test.py`, `pd10_phase_diag.py` |
| **tfuture-Tests** | 5 | `tfuture_*.py` |
| **Saleae** | 4 | `saleae_capture_blink.py`, `saleae_freq_check.py`, `saleae_poll.py`, `saleae_smoke.py` |
| **Analyse / Plot** | 3 | `drift_filter_analysis.py`, `build_summary.py`, `cyclic_fire_hw_test.py` |
| **Root-Utilities** | 2 | `analyze_dependencies.py`, `build_pptx.py` — bleiben im Root |
| **Hardware / Meta** | 3 | `hw_timer_sync_test.py`, `smoke_test.py`, `standalone_demo_test.py`, `sw_ntp_vs_ptp_test.py`, `meta_cyclic_fire_sweep.py`, `ptp_offset_capture.py` |
| **MCC-Driver-Config** | 4 | `config/module.py`, `driver/lan865x/config/*.py`, `driver/lan867x/config/*.py` — **nicht verschieben** (an Harmony gekoppelt) |
| **Output-Artefakte** | 1+ | `pd10_sync_check_20260423_171342/plot_histogram.py` — **löschen** |

**Duplikate / Varianten:**
- `ptp_sync_before_after_test.py` (42 KB) vs. `ptp_sync_before_after_mux_test.py` (30 KB) — unterschiedliche Modi, separate halten
- `pd10_sync_test.py` (7 KB) / `pd10_sync_before_after_test.py` (32 KB) / `pd10_sync_check.py` (23 KB) — drei Varianten, separat lassen oder später konsolidieren

---

### 1.3 Hardkodierte Pfad-Verweise

| Datei | Verweis | Aktion |
|-------|---------|--------|
| `.vscode/launch.json` (in `tcpip_iperf_lan865x.X/.vscode/`) | Kommentar `python flash.py` | Kommentar aktualisieren |
| `flash.py` | `HEX_DEFAULT = os.path.join(_HERE, r"out\tcpip_iperf_lan865x\default.hex")` | **Pfad anpassen** (kritisch!) |
| `flash.py` | `sys.path.insert(0, _HERE); from mdb_flash import flash` | OK, `mdb_flash.py` wird mitverschoben |
| `cmake/tcpip_iperf_lan865x/default/user.cmake` | Kommentar zu `flash.py`/`build_summary.py` | Kommentar aktualisieren |
| `build_summary.py` | von CMake aufgerufen | **nicht verschieben** |

---

## 2. ZIEL-STRUKTUR

### 2.1 Baum

```
root/
├── README.md                     ← Haupt-Einstieg (bleibt!)
├── readme_risks.md               ← bleibt im Root
├── README_agent_automation.md    ← zu docs/meta/ verschieben?
│
├── docs/                         ← NEU: gebündelte Dokumentation
│   ├── README.md                 ← Navigations-Hub / Inhaltsverzeichnis
│   │
│   ├── timing/
│   │   ├── software_ptp_clock_design.md      (← README_timestamp.md)
│   │   ├── software_ptp_clock_cli_test.md    (← README_timestamp_test.md)
│   │   └── timer_considerations.md           (← README_timer_considerations.md)
│   │
│   ├── ptp/
│   │   ├── implementation.md                 (← README_PTP.md)
│   │   ├── ntp_reference.md                  (← README_NTP.md)
│   │   └── drift_filter.md                   (← README_drift_filter.md)
│   │
│   ├── hardware/
│   │   ├── exception_dump.md                 (← README_exception_dump.md)
│   │   └── distributed_adc.md                (← README_distributed_adc.md)
│   │
│   ├── features/
│   │   ├── tfuture.md                        (← README_tfuture.md)
│   │   └── standalone_demo.md                (← README_standalone_demo.md)
│   │
│   ├── testing/
│   │   ├── smoke_test.md                     (← README_smoke_test.md)
│   │   ├── pd10_sync_tests.md                (← README_pd10_sync_test.md)
│   │   ├── pd10_sync_before_after_tests.md   (← README_pd10_sync_before_after.md)
│   │   ├── saleae_logic_analyzer.md          (← README_saleae.md)
│   │   └── saleae_freq_characterization.md   (← README_saleae_freq_check.md)
│   │
│   ├── firmware/
│   │   └── modules.md                        (← src/README_modules.md)
│   │
│   └── datasheets/               ← OPTIONAL: PDFs aus Root verschieben
│       ├── LAN8650-1_datasheet.pdf
│       ├── SAME54_datasheet.pdf
│       └── SAME54_curiosity_users_guide.pdf
│
├── tools/                        ← NEU: ausführbare Developer-Tools
│   ├── README.md
│   │
│   ├── flash/
│   │   ├── flash.py
│   │   ├── mdb_flash.py
│   │   ├── setup_flasher.py
│   │   ├── setup_compiler.py
│   │   └── setup_debug.py
│   │
│   ├── test-harness/
│   │   ├── smoke_test.py
│   │   ├── standalone_demo_test.py
│   │   ├── meta_cyclic_fire_sweep.py
│   │   └── cyclic_fire_hw_test.py
│   │
│   ├── ptp-analysis/
│   │   ├── sync-tests/
│   │   │   ├── ptp_sync_before_after_test.py
│   │   │   ├── ptp_sync_before_after_mux_test.py
│   │   │   ├── pd10_sync_test.py
│   │   │   ├── pd10_sync_before_after_test.py
│   │   │   └── pd10_sync_check.py
│   │   ├── ptp-delay-tests/
│   │   │   ├── ptp_delay_test.py
│   │   │   ├── ptp_offset_test.py
│   │   │   ├── ptp_offset_capture.py
│   │   │   └── ptp_time_test.py
│   │   ├── ptp-drift-tests/
│   │   │   ├── ptp_drift_compensate_test.py
│   │   │   ├── pd10_filter_freeze_test.py
│   │   │   ├── drift_filter_analysis.py
│   │   │   └── pd10_phase_diag.py
│   │   ├── misc-ptp-tests/
│   │   │   ├── ptp_onoff_test.py
│   │   │   ├── ptp_reproducibility_test.py
│   │   │   ├── ptp_role_swap_test.py
│   │   │   ├── ptp_trace_debug_test.py
│   │   │   ├── sw_ntp_vs_ptp_test.py
│   │   │   └── hw_timer_sync_test.py
│   │   └── tfuture-tests/
│   │       ├── tfuture_anchor_delay_test.py
│   │       ├── tfuture_diagnose_test.py
│   │       ├── tfuture_drift_forced_test.py
│   │       ├── tfuture_drift_forced_fol_test.py
│   │       └── tfuture_sync_test.py
│   │
│   └── saleae-logic-analyzer/
│       ├── saleae_poll.py
│       ├── saleae_capture_blink.py
│       ├── saleae_freq_check.py
│       └── saleae_smoke.py
│
├── apps/                         ← Microchip-Struktur unverändert
├── config/                       ← MCC-Module-Config unverändert
├── driver/                       ← LAN865x/LAN867x Driver unverändert
└── .git, .vscode, .claude, requirements.txt, analyze_dependencies.py, build_pptx.py
```

### 2.2 Begründungen

| Entscheidung | Begründung |
|--------------|-----------|
| **`docs/` statt `readme/`** | `docs/` ist GitHub-/GitHub-Pages-Konvention; Tooling erkennt es automatisch. |
| **Thematische Subfolders** | 15+ Dateien flach wären unübersichtlich; Navigation nach Interessensgebiet. |
| **`tools/` getrennt** | Trennung Firmware-Code (apps, driver, config) vs. Developer-Tools (flash, test, analyse). |
| **`tools/ptp-analysis/` Sub-Sub** | 15+ PTP-Tests — Unterteilung nach Fokus (sync/delay/drift/misc/tfuture). |
| **`apps/`, `driver/`, `config/` unangetastet** | Harmony-3-Konvention; MCC-Regeneration würde Änderungen überschreiben. |
| **`analyze_dependencies.py`, `build_pptx.py` im Root** | Repo-weite Meta-Tools, werden vom Root ausgeführt. |
| **`readme.md` + `readme_risks.md` im Root** | Haupt-Einstieg; GitHub rendert Root-`README.md` automatisch. |

---

## 3. MIGRATIONS-SCHRITTE

Jede Phase ist ein **eigener Commit**. Freigabe Phase für Phase.

### Phase 1 — Zielordner erstellen

```bash
mkdir -p ./docs/timing ./docs/ptp ./docs/hardware ./docs/features ./docs/testing ./docs/firmware
mkdir -p ./tools/flash ./tools/test-harness ./tools/saleae-logic-analyzer
mkdir -p ./tools/ptp-analysis/sync-tests
mkdir -p ./tools/ptp-analysis/ptp-delay-tests
mkdir -p ./tools/ptp-analysis/ptp-drift-tests
mkdir -p ./tools/ptp-analysis/misc-ptp-tests
mkdir -p ./tools/ptp-analysis/tfuture-tests
```

### Phase 2 — READMEs verschieben

```bash
# Timing
git mv ./apps/tcpip_iperf_lan865x/firmware/README_timestamp.md           ./docs/timing/software_ptp_clock_design.md
git mv ./apps/tcpip_iperf_lan865x/firmware/README_timestamp_test.md      ./docs/timing/software_ptp_clock_cli_test.md
git mv ./apps/tcpip_iperf_lan865x/firmware/README_timer_considerations.md ./docs/timing/timer_considerations.md

# PTP
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_PTP.md          ./docs/ptp/implementation.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_NTP.md          ./docs/ptp/ntp_reference.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_drift_filter.md ./docs/ptp/drift_filter.md

# Hardware
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_exception_dump.md   ./docs/hardware/exception_dump.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_distributed_adc.md  ./docs/hardware/distributed_adc.md

# Features
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_tfuture.md          ./docs/features/tfuture.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_standalone_demo.md  ./docs/features/standalone_demo.md

# Testing
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_smoke_test.md              ./docs/testing/smoke_test.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_saleae.md                  ./docs/testing/saleae_logic_analyzer.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_saleae_freq_check.md       ./docs/testing/saleae_freq_characterization.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_pd10_sync_test.md          ./docs/testing/pd10_sync_tests.md
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_pd10_sync_before_after.md  ./docs/testing/pd10_sync_before_after_tests.md

# Firmware
git mv ./apps/tcpip_iperf_lan865x/firmware/src/README_modules.md ./docs/firmware/modules.md
```

### Phase 3 — Index-READMEs schreiben

Erstelle manuell:
- `./docs/README.md` — Navigations-Hub mit Links auf alle verschobenen Dateien, thematisch gruppiert
- `./tools/README.md` — Tools-Übersicht mit Kurzbeschreibung je Skript
- Optional: `./docs/{timing,ptp,hardware,features,testing,firmware}/README.md` — Sub-Indizes

Im Root-`README.md` einen Link auf `./docs/README.md` hinzufügen.

### Phase 4 — Python-Skripte verschieben

```bash
# Flash
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/flash.py           ./tools/flash/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/mdb_flash.py       ./tools/flash/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/setup_flasher.py   ./tools/flash/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/setup_compiler.py  ./tools/flash/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/setup_debug.py     ./tools/flash/

# Test-Harness
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/smoke_test.py              ./tools/test-harness/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/standalone_demo_test.py    ./tools/test-harness/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/meta_cyclic_fire_sweep.py  ./tools/test-harness/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cyclic_fire_hw_test.py     ./tools/test-harness/

# PTP Sync-Tests
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_sync_before_after_test.py      ./tools/ptp-analysis/sync-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_sync_before_after_mux_test.py  ./tools/ptp-analysis/sync-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/pd10_sync_test.py                  ./tools/ptp-analysis/sync-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/pd10_sync_before_after_test.py     ./tools/ptp-analysis/sync-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/pd10_sync_check.py                 ./tools/ptp-analysis/sync-tests/

# PTP Delay/Offset
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_delay_test.py      ./tools/ptp-analysis/ptp-delay-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_offset_test.py     ./tools/ptp-analysis/ptp-delay-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_offset_capture.py  ./tools/ptp-analysis/ptp-delay-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_time_test.py       ./tools/ptp-analysis/ptp-delay-tests/

# PTP Drift
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_drift_compensate_test.py ./tools/ptp-analysis/ptp-drift-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/pd10_filter_freeze_test.py   ./tools/ptp-analysis/ptp-drift-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/drift_filter_analysis.py     ./tools/ptp-analysis/ptp-drift-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/pd10_phase_diag.py            ./tools/ptp-analysis/ptp-drift-tests/

# Misc PTP
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_onoff_test.py           ./tools/ptp-analysis/misc-ptp-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_reproducibility_test.py ./tools/ptp-analysis/misc-ptp-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_role_swap_test.py       ./tools/ptp-analysis/misc-ptp-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ptp_trace_debug_test.py     ./tools/ptp-analysis/misc-ptp-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/sw_ntp_vs_ptp_test.py       ./tools/ptp-analysis/misc-ptp-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/hw_timer_sync_test.py       ./tools/ptp-analysis/misc-ptp-tests/

# tfuture
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/tfuture_anchor_delay_test.py      ./tools/ptp-analysis/tfuture-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/tfuture_diagnose_test.py          ./tools/ptp-analysis/tfuture-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/tfuture_drift_forced_test.py      ./tools/ptp-analysis/tfuture-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/tfuture_drift_forced_fol_test.py  ./tools/ptp-analysis/tfuture-tests/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/tfuture_sync_test.py              ./tools/ptp-analysis/tfuture-tests/

# Saleae
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/saleae_poll.py           ./tools/saleae-logic-analyzer/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/saleae_capture_blink.py  ./tools/saleae-logic-analyzer/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/saleae_freq_check.py     ./tools/saleae-logic-analyzer/
git mv ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/saleae_smoke.py          ./tools/saleae-logic-analyzer/
```

**Nicht verschieben:**
- `analyze_dependencies.py`, `build_pptx.py` (Root, Repo-Meta-Tools)
- `build_summary.py` (wird von CMake aufgerufen)
- `config/module.py`, `driver/lan86*x/config/*.py` (MCC-generierten Code ergänzend)

### Phase 5 — Pfad-Anpassung in `flash.py`

**KRITISCH:** `flash.py` verwendet `_HERE` relativ zum eigenen Pfad. Nach Verschiebung nach `tools/flash/` zeigt der HEX-Pfad ins Leere.

Edit in `./tools/flash/flash.py`:
```python
# Alt:
HEX_DEFAULT = os.path.join(_HERE, r"out\tcpip_iperf_lan865x\default.hex")

# Neu:
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
HEX_DEFAULT = os.path.join(
    _REPO_ROOT,
    r"apps\tcpip_iperf_lan865x\firmware\tcpip_iperf_lan865x.X\out\tcpip_iperf_lan865x\default.hex",
)
```

Analog in `setup_flasher.py` falls dort auf relative Pfade zugegriffen wird (prüfen).

Kommentare aktualisieren:
- `./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/.vscode/launch.json` — Pfad-Hinweis zu `flash.py`
- `./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/tcpip_iperf_lan865x/default/user.cmake` — Kommentar zu `flash.py`

**Test:** `python tools/flash/setup_flasher.py` + `python tools/flash/flash.py` aus Repo-Root ausführen → muss erfolgreich programmieren.

### Phase 6 — Artefakte bereinigen (OPTIONAL)

```bash
# cyclic_fire_hw_* Output-Ordner (~14 MB, 99 Stück)
find ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/ -maxdepth 1 -type d -name "cyclic_fire_hw_*" -exec rm -rf {} +

# Test-Output-Dirs
rm -rf ./pd10_sync_check_20260423_171342

# Leerer/korrupter Ordner mit Apostroph als Name
rm -rf "./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/'"

# Temp-Files
rm -f ./_commit_msg.txt
rm -f ./apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/_test_output.txt

# Datasheets optional nach docs/datasheets/
mkdir -p ./docs/datasheets
git mv ./LAN8650-1-Data-Sheet-60001734.pdf ./docs/datasheets/
git mv ./SAME54_Datasheet.pdf ./docs/datasheets/
git mv "./SAME54_Curiosity_Ultra_Users_Guide_DS70005405A (1).pdf" ./docs/datasheets/SAME54_Curiosity_Ultra_Users_Guide.pdf
```

---

## 4. RISIKEN

| Risiko | Mitigation |
|--------|-----------|
| **`flash.py` HEX-Pfad bricht** | Phase 5 Pfad-Patch vor erstem Flash-Test |
| **`setup_flasher.config` muss neben `setup_flasher.py` leben** | `.config` wird beim nächsten Lauf automatisch regeneriert |
| **VS-Code-Tasks/-Launch referenzieren alte Pfade** | Nur Kommentare betroffen (nicht funktional) — trotzdem aktualisieren |
| **MCC-Regeneration überschreibt** | `apps/`, `driver/`, `config/` werden nicht angefasst |
| **`git mv`-History** | Git erkennt Moves automatisch (`git log --follow`) |
| **Dokumenten-Links untereinander brechen** | Nach Phase 2 in allen verschobenen Dateien Cross-Links prüfen & patchen |

---

## 5. REIHENFOLGE & COMMITS

1. Phase 1 — `chore: create docs/ and tools/ directory skeleton`
2. Phase 2 — `docs: move README*.md into thematic docs/ subfolders`
3. Phase 3 — `docs: add navigation indices (docs/README.md, tools/README.md)`
4. Phase 4 — `tools: relocate Python scripts from tcpip_iperf_lan865x.X/ into tools/`
5. Phase 5 — `tools/flash: fix HEX_DEFAULT path after relocation`
6. Phase 6 — `chore: remove cyclic_fire_hw_* output artifacts and temp files` (optional)

**Nach jeder Phase:**
- `git status` / `git diff --stat`
- Build/Flash-Test wo relevant
- Freigabe einholen bevor nächste Phase läuft

---

## 6. OFFENE FRAGEN

- `README_agent_automation.md` im Root lassen oder nach `docs/meta/` verschieben?
- `readme_risks.md` umbenennen nach `RISKS.md` (Konvention)?
- PDFs (Datasheets, PPTX) im Repo behalten oder extern referenzieren? (Git-History-Größe)
- Doppelte `.pptx` im Root (`PTP_LAN8651.pptx` + `__PTP_LAN8651.pptx`) — welche ist aktuell?
