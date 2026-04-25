# README — Cross-Build (CMake/Makefile + MPLAB X)

Dieses Dokument beschreibt den Stand des `cross`-Branches: das Projekt
[`apps/tcpip_iperf_lan865x`](apps/tcpip_iperf_lan865x/) wird nun **parallel** über
zwei Build-Wege gebaut:

1. **CMake/Makefile** (Hauptweg, aktiv genutzt) — über
   [firmware/Makefile](apps/tcpip_iperf_lan865x/firmware/Makefile) und
   [firmware/cmake/](apps/tcpip_iperf_lan865x/firmware/cmake/)
2. **MPLAB X IDE** — über
   [firmware/tcpip_iperf_lan865x.X/](apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/),
   gesteuert durch
   [nbproject/configurations.xml](apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/nbproject/configurations.xml)

Ziel des Branches: Embedded-Entwicklern eine **vorzeigbare PTP-Implementierung
für den LAN8651** zur Verfügung zu stellen, die auch in MPLAB X gebaut und
debuggt werden kann (das Harmony-/MCC-Drumherum dient als Demonstrator).

---

## 1) Was wurde an `configurations.xml` geändert

Das MPLAB X-Projekt war stark veraltet — der CMake/Makefile-Build hatte 23 neue
`.c` und 23 neue `.h` Dateien, die in MPLAB X nicht eingetragen waren.

### Source-Files hinzugefügt (`<logicalFolder name="SourceFiles">`)

`button_led.c`, `cyclic_fire.c`, `cyclic_fire_cli.c`, `cyclic_fire_isr.c`,
`demo_cli.c`, `exception_handler.c`, `iperf_control.c`, `lan_regs_cli.c`,
`loop_stats.c`, `loop_stats_cli.c`, `pd10_blink.c`, `pd10_blink_cli.c`,
`ptp_cli.c`, `ptp_offset_trace.c`, `ptp_rx.c`, `standalone_demo.c`, `sw_ntp.c`,
`sw_ntp_cli.c`, `sw_ntp_offset_trace.c`, `test_exception_cli.c`, `tfuture.c`,
`tfuture_cli.c`, `watchdog.c`

### Header-Files hinzugefügt (`<logicalFolder name="HeaderFiles">`)

`app_log.h`, `button_led.h`, `cyclic_fire{,_cli,_isr}.h`, `demo_cli.h`,
`iperf_control.h`, `lan_regs_cli.h`, `loop_stats{,_cli}.h`,
`pd10_blink{,_cli}.h`, `ptp_cli.h`, `ptp_offset_trace.h`, `ptp_rx.h`,
`standalone_demo.h`, `sw_ntp{,_cli,_offset_trace}.h`, `test_exception_cli.h`,
`tfuture{,_cli}.h`, `watchdog.h`

### C-Compiler (C32)

| Property | Vorher | Nachher |
|---|---|---|
| `extra-include-directories` | `../src;…` | `..;../src;…` (firmware-root als Pfad) |
| `preprocessor-macros` | `HAVE_CONFIG_H;WOLFSSL_IGNORE_FILE_WARN` | `__DEBUG;HAVE_CONFIG_H;WOLFSSL_IGNORE_FILE_WARN;XPRJ_default=default` |

### C++-Compiler (C32CPP)

| Property | Vorher | Nachher |
|---|---|---|
| `extra-include-directories` | `../src;…` | `..;../src;…` |
| `preprocessor-macros` | `""` | `__DEBUG;XPRJ_default=default` |

→ Damit deckt sich der MPLAB X-Build vollständig mit den
   Compile-Optionen aus [firmware/cmake/.../rule.cmake](apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/tcpip_iperf_lan865x/rule.cmake).

Commit dieser Änderung: `db64350 build(mplabx): sync configurations.xml with current sources`

---

## 2) ⚠ KRITISCH: `drv_lan865x_api.c` enthält die PTP-HW-Timestamping-Infrastruktur

Der LAN865x-Treiber in diesem Fork weicht **erheblich** vom Upstream ab
(416 geänderte Zeilen in `drv_lan865x_api.c`, ~62 zusätzliche Zeilen in
`drv_lan865x.h`). Diese Änderungen sind **keine Kosmetik**, sondern die
fundamentale Hardware-Timestamping-Schicht, ohne die PTP nicht funktioniert.

Wenn MCC innerhalb von MPLAB X erneut über das Projekt läuft, versucht er,
genau diese Datei zu überschreiben — **die Regeneration MUSS abgelehnt werden**
(im MCC-Merge-Dialog auf **Reject** klicken).

Datei:
[`apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`](apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c)

### Was im Treiber für PTP zwingend ergänzt wurde

#### 2.1 EXTINT-14 nIRQ-ISR mit TC0-Tick-Latch

```c
static volatile bool     s_nirq_pending = false;
static volatile uint64_t s_nirq_tick    = 0u;
// ISR captures a TC0 tick at the earliest possible moment of nIRQ assertion
s_nirq_tick    = SYS_TIME_Counter64Get();
s_nirq_pending = true;
```

Liefert **ISR-Präzision (~5 µs Jitter)** statt Task-Level-Read (~100 µs Jitter
+ mehrere ms Latenz vom eigentlichen t1-Event). Anker-Tick für
`PTP_CLOCK_Update`.

#### 2.2 TTSCAA save-before-W1C — fixt Race Condition

```c
static volatile uint32_t drvTsCaptureStatus0[DRV_LAN865X_INSTANCES_NUMBER];
static volatile uint64_t drvTsCaptureNirqTick[DRV_LAN865X_INSTANCES_NUMBER];
// Save TTSCAA/B/C bits (8-10) BEFORE W1C clear
drvTsCaptureStatus0[i] |= (value & 0x0700u);
drvTsCaptureNirqTick[i] = s_nirq_tick;
```

Ohne diese Sicherung verliert die GM-State-Machine die TTSCAA-Bits, weil der
Treiber-Status-Handler sie per Write-1-Clear löscht, bevor sie gelesen werden
können.

#### 2.3 FTSE-Bit (Frame Timestamp Enable)

```c
regVal |= 0x80u; /* FTSE: required for TTSCAA TX capture */
```

Ohne dieses Bit feuert die Hardware **gar keinen** TX-Timestamp-Capture beim
Sync-Versand.

#### 2.4 IMASK0 für TTSCAA freigeschaltet

```c
{ .address=0x0000000C, .value=0x00000000, ... }
/* IMASK0: bit 8 (TTSCAA) unmaskiert → _OnStatus0 fires on timestamp capture */
```

Upstream-Wert ist `0x00000100` — Bit 8 ist **maskiert**, kein Interrupt bei
Timestamp-Capture.

#### 2.5 `DRV_LAN865X_SendRawEthFrame()` mit `tsc`-Flag

```c
bool DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf, uint16_t len,
                                 uint8_t tsc, DRV_LAN865X_RawTxCallback_t cb,
                                 void *pTag);
// tsc=0x01 für Sync (Timestamp Capture A), tsc=0x00 für FollowUp
```

Die Standard-Sende-API führt über die TCP/IP-Stack-Queue und kennt kein
`tsc`-Flag — also keine HW-Timestamps für PTP-Sync-Messages.

#### 2.6 Weitere PTP-Helper im Public API

| Funktion | Zweck |
|---|---|
| `DRV_LAN865X_IsReady()` | Readiness-Probe vor erstem PTP-Frame |
| `DRV_LAN865X_GetAndClearTsCapture()` | Atomic read-and-clear der TTSCAA/B/C-Bits |
| `DRV_LAN865X_GetTsCaptureNirqTick()` | Latched TC0-Tick aus 2.1 abfragen |
| `DELAY_UNLOCK_EXT` reduziert | Kommentar im Code: *"100ms caused TTSCMA"* (Timestamp-Capture-Miss) |

Plus `g_ptp_raw_rx.sysTickAtRx` für RX-Path-Anchoring.

### Konsequenz ohne diese Änderungen

| Was fehlen würde | Auswirkung auf PTP |
|---|---|
| TX-Timestamps für Sync | Sync-Anker fehlt, kein Master-Slave-Sync möglich |
| ISR-präziser RX-Tick | Zeitstempel-Jitter ~100 µs + ms-Latenz |
| TTSCAA save-before-W1C | Timestamps gehen durch Race-Condition verloren |
| TTSCAA-Interrupt | Status0-Handler feuert nie |
| FTSE-Bit | HW erzeugt gar keine TX-Timestamps |

→ **PTP wäre nicht funktionsfähig.**

### Was MCC zusätzlich noch ändern würde (Init-Sequenz)

```diff
-#include <stdarg.h>
```

`<stdarg.h>` wird entfernt.

In der Memory-Map `TC6_MEMMAP[]` (LAN865x-Initialisierungssequenz):

| Register | Wert (aktuell, verifiziert) | Wert (MCC-neu) |
|---|---|---|
| `0x000400F8` | `0x0000B900` | `0x00009B00` |
| `0x00040081` (DEEP_SLEEP_CTRL_1) | `0x00000080` | `0x000000E0` |

8 Register-Writes werden umsortiert und `DEEP_SLEEP_CTRL_1` ans Ende der
Tabelle hinter `IMASK0` verschoben.

### Empfohlener Workflow nach einem MCC-Lauf

1. Im MCC-Merge-Dialog für `drv_lan865x_api.c` auf **Reject** klicken.
2. Analog für die FreeRTOS-Variante:
   `apps/tcpip_iperf_lan865x/firmware/src/config/FreeRTOS/driver/lan865x/src/dynamic/drv_lan865x_api.c`.
3. Restliche von MCC vorgeschlagene Änderungen können akzeptiert werden — sie
   sind kosmetisch (Timestamps, YAML-Reihenfolge, doppelte Dependency-Einträge).
4. Anschließend `git diff` prüfen und nicht relevante MCC-Metadaten verwerfen
   (`git restore <datei>`).

Letzter verifizierter Stand der Datei: Commit
`deb2773 fix(ptp_fol): compensate 10 ms LAN865x RX-nIRQ delay in PTP_CLOCK anchor`.

Vollständige Commit-Historie der Datei (jüngste zuerst):

```
deb2773 fix(ptp_fol): compensate 10 ms LAN865x RX-nIRQ delay in PTP_CLOCK anchor
657e8a1 feat(ptp): ISR-captured GM anchor tick + docs overhaul
5e289c8 fix(R1): replace nIRQ pin polling with EIC EXTINT14 change-notification ISR
e74eb8c firmware timer sync added but working accurate enough. need to be improved
85e41c6 PTP Works
```

### Empirischer Beweis: Build bricht sofort, wenn MCC-Vorschlag akzeptiert wird

In einem parallelen Test-Repo (`check3`) wurden die MCC-Vorschläge für
`drv_lan865x_api.c` **akzeptiert**. Folge: der allererste Build-Versuch
schlägt mit folgendem Fehler fehl:

```
../src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c:
  In function 'PrintRateLimited':
1532:9: error: implicit declaration of function 'va_start'
        [-Werror=implicit-function-declaration]
1534:9: error: implicit declaration of function 'va_end'
cc1.exe: all warnings being treated as errors
make[2]: *** [build/.../drv_lan865x_api.o] Error 1
BUILD FAILED
```

Ursache: Die MCC-Regeneration entfernt am Dateianfang `#include <stdarg.h>`,
während `PrintRateLimited()` (Zeile 1532) weiterhin `va_start()` und `va_end()`
verwendet. Mit `-Werror -Wall` (Standard-Build-Flags des Projekts) wird die
implicit-declaration zur Fehler.

→ **Selbst die rein kosmetisch wirkende `<stdarg.h>`-Entfernung kompiliert
nicht durch.** Wer die MCC-Vorschläge unbesehen akzeptiert, hat sofort einen
nicht baubaren Tree — und selbst nach Wieder-Hinzufügen von `<stdarg.h>` fehlt
weiterhin die komplette PTP-Hardware-Timestamping-Infrastruktur aus §2.1–2.6.

### Recovery, falls die MCC-Änderung versehentlich akzeptiert wurde

Wenn die Datei bereits überschrieben ist und `git restore` nicht hilft (z. B.
weil schon committet), kann der korrekte Stand aus diesem `cross`-Branch
übernommen werden:

```bash
# Aus dem cross-Branch dieses Repos
git checkout cross -- \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/drv_lan865x.h
```

Anschließend `mcc-config.mc4` prüfen — der dort gespeicherte Hash für
`drv_lan865x_api.c` wird beim nächsten MCC-Lauf den Override-Dialog erneut
auslösen.

### Strategisches Dilemma — Trunk-Anbindung vs. PTP-Funktion

Die in §2 beschriebene "Reject"-Strategie hat einen erheblichen Preis:

> **Wer den MCC-Merge des aktuellen offiziellen LAN865x-Treibers ablehnt,
> hängt das PTP-Projekt vom Harmony-Trunk ab.**

Konkret bedeutet das:

- **Keine Bugfixes** aus neueren Treiber-Releases (z. B. korrigierte
  Init-Sequenzen für LAN865x B1, geänderte Register-Werte wie
  `0x000400F8: 0xB900 → 0x9B00` oder `DEEP_SLEEP_CTRL_1: 0x80 → 0xE0`,
  die möglicherweise Microchip-bestätigte Hardware-Anpassungen sind).
- **Keine Feature-Updates** — wenn Harmony Net v3.15+ neue Treiber-APIs,
  bessere TC6-Integration oder Erratum-Workarounds bringt, sind die für
  dieses Projekt nicht zugänglich.
- **Anhäufung des Diff-Schuldenbergs** — mit jedem akzeptierten oder
  abgelehnten MCC-Lauf wächst die Distanz zwischen lokalem Treiber und
  Trunk weiter. Spätere Re-Synchronisation wird immer schwieriger.
- **Kein gemeinsamer Codebase** mit anderen Harmony-LAN865x-Anwendern —
  ein Embedded-Entwickler, der dieses Projekt übernimmt, kann seine
  Treiber-Kenntnisse aus anderen Projekten nicht eins-zu-eins anwenden.

### Mitigations-Strategien (Trade-offs)

Keine dieser Optionen ist perfekt — sie sind in der Reihenfolge ihres
Realisierungsaufwands aufgeführt.

**A) Status Quo: "Reject" und manueller Cherry-Pick**
- Bei jedem MCC-Lauf den Override für `drv_lan865x_api.c` ablehnen.
- Periodisch (z. B. alle 6 Monate) einen Side-by-Side-Diff der MCC-Vorschläge
  prüfen und einzelne sinnvolle Änderungen (z. B. neue Register-Werte) per
  Hand übernehmen.
- ✅ Einfach, kein Tooling.
- ❌ Skaliert nicht, fehleranfällig, Drift kumuliert.

**B) PTP-Patches als separates Patch-Set**
- Treiber wird als unmodifiziert vom Trunk gehalten.
- PTP-Hooks (§2.1–2.6) werden als `git`-Patches oder als Wrapper-Datei
  (`drv_lan865x_ptp_ext.c`) ausgelagert.
- Vor jedem Build wird das Patch-Set angewendet.
- ✅ Trunk-Updates sind übernehmbar, PTP-Diff bleibt isoliert dokumentiert.
- ❌ Setzt voraus, dass die PTP-Hooks sauber separierbar sind. In der Praxis
  greifen sie tief in `_OnStatus0()`, `_InitMemMap()` und die ISR-Logik
  ein → Patches brechen bei größeren Trunk-Refactorings.

**C) Treiber-Variante via Harmony-Template-Mechanismus**
- Eigene `drv_lan865x_ptp` Komponente in Harmony anlegen, die vom
  Standard-`drv_lan865x` erbt/abzweigt und die PTP-Erweiterungen mitbringt.
- ✅ Sauber im MCC-Modell, koexistiert mit dem Trunk-Treiber.
- ❌ Hoher Engineering-Aufwand (Harmony-Component-Definition, YAML-Schemas,
  FTL-Templates), MCC-internes Wissen erforderlich. Microchip-Support
  hilft hier kaum.

**D) Upstream-PR an Microchip-Harmony**
- Die PTP-Hooks (`SendRawEthFrame`, `GetTsCaptureNirqTick`, ISR mit
  TC0-Latch, FTSE-Bit, IMASK0-Demaskierung, TTSCAA save-before-W1C) als
  Pull-Request an
  [github.com/Microchip-MPLAB-Harmony/net_10base_t1s](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s)
  einreichen.
- ✅ Beste Lösung langfristig — der Trunk *enthält* die PTP-Erweiterung,
  damit ist auch dieses Projekt wieder Trunk-kompatibel.
- ❌ Akzeptanz-Lotterie. Microchip muss die Änderungen reviewen,
  testen und mergen. Kann Monate dauern oder abgelehnt werden, wenn
  PTP nicht zur offiziellen Roadmap passt.

**E) Eigener Soft-Fork des Treibers**
- Den Treiber-Pfad umbenennen (z. B. `driver/lan865x_ptp/`) und MCC die
  Komponente nicht mehr regenerieren lassen (durch eigene
  Component-Definition oder durch Entfernen aus dem MCC-Projekt).
- ✅ Komplette Kontrolle, keine MCC-Override-Dialoge mehr.
- ❌ Verlust der MCC-Konfigurationsoberfläche für diesen Treiber. Wenn
  z. B. SPI-Pins oder Driver-Index geändert werden müssen, muss das
  manuell im Code passieren statt grafisch im MCC.

### Empfehlung

Für dieses Projekt — das laut Beschreibung primär als
**PTP-Implementierungs-Demonstrator** dient und nicht als generischer
Harmony-Beispiel-Anwender — ist die richtige Reihenfolge:

1. **Kurzfristig (Status Quo, A)**: Reject-Workflow beibehalten, in diesem
   README dokumentieren (siehe oben).
2. **Mittelfristig (D)**: Upstream-PR an Microchip vorbereiten. Selbst wenn
   nicht akzeptiert, hat die Diskussion mit Microchip Wert (z. B. um
   herauszufinden, warum sie die Init-Sequenz geändert haben).
3. **Falls (D) fehlschlägt (B oder E)**: Patch-Set oder Soft-Fork.
   Welche der beiden Varianten geeignet ist, hängt davon ab, wie tief
   die PTP-Hooks im Treiber verankert sind — bei den jetzigen Eingriffen
   in `_OnStatus0()` und `_InitMemMap()` ist (E) wahrscheinlich
   wartbarer als (B).

### 2.7 Minimierung des Treiber-Diffs (Spezifikation für ein Refactoring)

Eine technische Audit-Analyse (siehe `cross`-Branch-Doku) zeigt, dass der
heutige Treiber-Diff von ~478 Zeilen auf **~35 Zeilen Inline-Diff plus ~320
Zeilen in zwei neuen, treiber-externen Dateien** reduziert werden kann.

| | Aktuell | Erreichbar |
|---|---|---|
| `drv_lan865x_api.c` Diff | ~416 Zeilen | **~35 Zeilen** |
| `drv_lan865x.h` Diff | ~62 Zeilen | **0 Zeilen** |
| **Treiber-Diff gesamt** | **~478** | **~35** |
| Neue Dateien | – | `ptp_drv_ext.{c,h}` (~320 Z.) |

#### Was zwingend im Treiber bleiben muss (Kategorie A — ~30 Zeilen)

Diese Änderungen sitzen in `static const`-Tabellen, lokalen
Stack-Variablen oder mitten in der Treiber-Init-State-Machine — sie
sind nicht extrahierbar:

| # | Stelle im Treiber | Diff-Größe |
|---|---|---|
| A1 | `TC6_MEMMAP[]`-Edits in `_InitMemMap()`: IMASK0 `0x100→0x000` (TTSCAA freigeschaltet), DEEP_SLEEP_CTRL_1 `0x80→0xE0`, TXM-Filter (`0x40040..0x40045`) | 7 Array-Zeilen |
| A2 | `regVal \|= 0x80u; regVal \|= 0x40u;` (FTSE + FTSS) in `_InitUserSettings()` case 8 | 2 Zeilen |
| A3 | Neue Init-States cases 46/47 (PADCTRL, PPSCTL) inkl. `done`-Flag-Verschiebung | 14 Zeilen |
| A4 | Service-Loop (line ≈ 410): `if (s_nirq_pending \|\| !SYS_PORT_PinRead(...))` — additiv, Upstream-Pin-Polling bleibt erhalten | 1 Zeile |
| A5 | `_OnStatus0()`: Aufruf `DRV_LAN865X_OnStatus0_Hook(idx, status0)` plus `__attribute__((weak))` Default-Implementierung | 3 Zeilen |
| A6 | `TC6_CB_OnRxEthernetPacket()`: Aufruf `DRV_LAN865X_OnPtpFrame_Hook(buf, len, rxTs, success)` plus weak Default | 3 Zeilen |
| A7 | Neue Public-API `DRV_LAN865X_GetTc6Inst(idx)` als 5-Zeiler-Accessor | 5 Zeilen |
| **Summe** | | **~35 Zeilen** |

#### Was ausgelagert werden kann (Kategorie B — ~320 Zeilen → neue Dateien)

In zwei neuen Dateien `apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.{c,h}`:

- **EXTINT-14 ISR + `_InitNIrqEIC()`** (~50 Zeilen) — `EIC_EXTINT_14_Handler`
  ist ein Linker-weak Symbol in der Harmony-Startup-Datei und kann von
  außen definiert werden, kein Treiber-Eingriff nötig
- **`s_nirq_pending` / `s_nirq_tick` Statics + Getter** (~25 Zeilen)
- **`drvTsCaptureStatus0[]` / `drvTsCaptureNirqTick[]` + Save-Logik**
  (~25 Zeilen) — gefüttert über den Weak-Hook A5
- **`DRV_LAN865X_SendRawEthFrame()` / `IsReady()` / `GetAndClearTsCapture()` /
  `GetTsCaptureNirqTick()`** als reine Wrapper (~50 Zeilen) — nutzen den
  neuen `GetTc6Inst()`-Accessor (A7)
- **PTP-RX-Sniff** (`g_ptp_raw_rx`, EtherType-Check 0x88F7) (~50 Zeilen)
  über den Weak-Hook A6
- **62 Zeilen Public-Header-Deklarationen** wandern komplett aus
  `drv_lan865x.h` heraus in `ptp_drv_ext.h`. PTP-Code (`ptp_gm_task.c`,
  `ptp_fol_task.c`, `ptp_rx.c`, `ptp_clock.c`) `#include`d die neue Header.

#### Was ganz weg kann (Kategorie C — ~30 Zeilen Cosmetic)

- `DELAY_UNLOCK_EXT 100→5` — Workaround, evtl. Build-Flag oder revert nach
  Smoke-Test (1 Hz Sync × 1 min, zero TTSCMA events)
- `PRINT_LIMIT`-Reorderings (`LAN865x_%d`-Prefix in jeder case-Zeile) —
  reine Diff-Lärm-Reduktion
- `case 28: continue;` und Bit-8/9/10-Print-Suppression in `_OnStatus0` —
  Debug-Kosmetik

#### Vorgeschlagene Ziel-Struktur

```
apps/tcpip_iperf_lan865x/firmware/src/
├── ptp_drv_ext.c          NEU, ~250 Zeilen
└── ptp_drv_ext.h          NEU, ~70 Zeilen
```

Inhalt von `ptp_drv_ext.c`:

- File-static `s_nirq_pending`, `s_nirq_tick`
- `EIC_EXTINT_14_Handler` (Linker-weak override)
- `_InitNIrqEIC()` + `PTP_DRV_EXT_Init()` (aufgerufen aus `APP_Initialize`)
- `drvTsCaptureStatus0[]`, `drvTsCaptureNirqTick[]`
- Strong-Implementierung `DRV_LAN865X_OnStatus0_Hook(idx, status0)` —
  speichert die TTSCAA-Bits und latched `s_nirq_tick`
- Strong-Implementierung `DRV_LAN865X_OnPtpFrame_Hook(buf, len, rxTs, success)` —
  EtherType-Test, Kopie nach `g_ptp_raw_rx`, `sysTickAtRx`-Stempel
- `DRV_LAN865X_SendRawEthFrame`, `IsReady`, `GetAndClearTsCapture`,
  `GetTsCaptureNirqTick` (alle nutzen `GetTc6Inst()`-Accessor)
- `g_ptp_rx_ts`, `g_ptp_raw_rx` Definitionen

Inhalt von `ptp_drv_ext.h`:

- `DRV_LAN865X_RawTxCallback_t` typedef
- Prototypen für die vier Public-API-Funktionen
- `extern` Deklarationen der zwei Globals
- Hook-Prototypen für die zwei Weak-Callbacks (zum Verlinken)

#### Wichtige Risiken & Verifikationen vor Refactoring

1. **Linker-Resolution für Weak-Symbols**: prüfen mit `xc32-nm` und
   Map-File, dass die starke App-Definition vor der Treiber-Default-Variante
   gelinkt wird. Sicherer Pfad: Treiber definiert `DRV_LAN865X_OnStatus0_Hook`
   als `__weak`, nicht die App.
2. **MCC regeneriert auch die ~35 Zeilen**: Selbst nach Minimierung muss bei
   jedem MCC-Lauf der Override für `drv_lan865x_api.c` abgelehnt werden — der
   Vorteil ist nur, dass ein **Patch-Set** aus 35 Zeilen viel besser als
   400+ Zeilen wartbar / re-applicierbar ist (z. B. via `git apply`).
3. **`_OnStatus0` Hook-Reihenfolge**: Hook-Aufruf muss **vor** dem
   `TC6_WriteRegister` W1C-Clear stehen. Mit Kommentar im Code fixieren.
4. **EIC EXTINT-14 Ownership**: falls MCC zukünftig EIC-CONFIG[1] für
   andere Peripherie generiert, race-anfällig. EXTINT-14 ist exklusiv
   für nIRQ → in Pin-Manager dokumentieren.
5. **`g_ptp_raw_rx` ABI**: `volatile`-Qualifier und Field-Order zwischen
   `ptp_ts_ipc.h` und `ptp_drv_ext.c` müssen identisch bleiben (`ptp_rx.c`
   liest ohne Locking).
6. **`DELAY_UNLOCK_EXT`-Revert**: braucht Smoke-Test bevor in `cross`
   gemerget wird.

#### Vorgehensweise (in der Reihenfolge)

1. Branch `cross-minimize` von `cross` abzweigen.
2. `ptp_drv_ext.c`/`.h` anlegen, Code aus `drv_lan865x_api.c` rausschneiden.
3. Im Treiber die 7 minimalen Hooks (A1–A7) einbauen.
4. Build. PTP-Smoke-Test (Sync läuft, Offset stabil).
5. `git diff cross..cross-minimize -- '...drv_lan865x_api.c' '...drv_lan865x.h'`
   sollte nun ~35 Zeilen zeigen.
6. Diese 35 Zeilen als `essential.patch` speichern, Workflow dokumentieren:
   "MCC ablehnen → Patch re-apply".

---

## 3) Was ein MCC-Lauf typischerweise sonst noch anfasst

Reine Metadaten — können meist ohne Risiko verworfen werden:

- **`configurations.xml`** — Reihenfolge der `.yml`-Component-Einträge,
  `languageToolchainVersion` und `platformTool` werden auf die lokale MPLAB X /
  XC32-Installation angepasst (kann zwischen 4.60, 5.00, 5.10 schwanken).
  ⚠ Die in diesem Branch hinzugefügten 46 `<itemPath>`-Einträge werden
  von MCC **nicht** angefasst — Build bleibt funktional.
- **3× Manifest-YAMLs** (`harmony-manifest-success.yml`, `mcc-manifest-*.yml`)
  — nur Timestamps + Compiler-Version.
- **6× Layer-YAMLs** unter `tcpip_iperf_lan865x_default/components/.../...yml` —
  doppelte `dependency:`-Einträge werden bereinigt (echte Verbesserung).
- **`mcc-config.mc4`** — Hash-Eintrag für `drv_lan865x_api.c` wird
  aktualisiert. ⚠ Folge: Beim nächsten MCC-Lauf wird die Datei *erneut* zur
  Regeneration vorgeschlagen, da der Datei-Hash auf der Disk vom
  gespeicherten Hash abweicht.

---

## 4) Vergleich mit dem Upstream-Original

Vergleichsbasis: [github.com/Microchip-MPLAB-Harmony/net_10base_t1s](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s)
(im Test geklont nach `c:/work/ptp/org/net_10base_t1s/`).

### Eigene Erweiterungen in diesem Fork (im `firmware/src/`)

46 zusätzliche Dateien (PTP-Stack, Demos, CLIs):

- **PTP-Kern**: `ptp_clock.{c,h}`, `ptp_gm_task.{c,h}`, `ptp_fol_task.{c,h}`,
  `ptp_log.{c,h}`, `ptp_offset_trace.{c,h}`, `ptp_rx.{c,h}`, `ptp_ts_ipc.h`,
  `ptp_cli.{c,h}`, `filters.{c,h}`
- **NTP-Vergleich**: `sw_ntp{,_cli,_offset_trace}.{c,h}`
- **Demos & Tools**: `cyclic_fire{,_cli,_isr}.{c,h}`, `pd10_blink{,_cli}.{c,h}`,
  `button_led.{c,h}`, `loop_stats{,_cli}.{c,h}`, `iperf_control.{c,h}`,
  `lan_regs_cli.{c,h}`, `standalone_demo.{c,h}`, `demo_cli.{c,h}`,
  `tfuture{,_cli}.{c,h}`, `watchdog.{c,h}`,
  `test_exception_cli.{c,h}`, `exception_handler.c`, `app_log.h`

### Modifizierte Upstream-Dateien

- `app.{c,h}` — PTP-Integration
- `config/default/configuration.h`, `initialization.c`, `tasks.c`
- `config/default/library/tcpip/src/iperf.c`
- `config/default/peripheral/port/plib_port.c`
- `config/default/system/command/sys_command.h` (auch im FreeRTOS-Pfad)
- `config/default/driver/lan865x/drv_lan865x.h`
- `config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c` ← siehe §2
- `config/FreeRTOS/driver/lan865x/src/dynamic/drv_lan865x_api.c` ← analog

### Eigene Tooling-Adds (in `tcpip_iperf_lan865x.X/`)

- [`cmake/`](apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/) —
  CMake-Build-System (entspricht 1:1 dem MPLAB X-Build)
- `.vscode/`, `.gitignore`, `check_serial_tk.pyw`

---

## 5) Build-Verifikation

| Build-Weg | Status |
|---|---|
| CMake/Makefile (`firmware/Makefile`) | ✅ funktioniert |
| MPLAB X IDE (`tcpip_iperf_lan865x.X`) | ✅ funktioniert (mit Stand `db64350`) |

Beide Builds sind kompile-äquivalent: gleiche Source-Dateien, gleiche
Include-Pfade, gleiche Preprocessor-Defines.
