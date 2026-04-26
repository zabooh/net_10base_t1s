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

#### Wichtige Klarstellung: das ist ein Microchip-Template-Bug, nicht ein Konflikt mit diesem Fork

Auf den ersten Blick wirkt das so, als ob ein Eingriff dieses Forks mit MCC's
Output kollidiert. **Tut es nicht.** Die nüchterne Beweislage:

| Variante des Treibers | `<stdarg.h>` | `PrintRateLimited()` | Build? |
|---|---|---|---|
| Microchip Upstream HEAD (GitHub, Stand 2023-10-27) | ✅ vorhanden | ✅ vorhanden | ✅ baut |
| `cross` / `cross-minimize` / `cross-driverless` (dieser Fork) | ✅ vorhanden | ✅ vorhanden | ✅ baut |
| **MCC-Regeneration (heutige Tooling-Version)** | ❌ **entfernt** | ✅ vorhanden | ❌ **bricht** |

Wer einen frischen, unmodifizierten Upstream-Clone nimmt und blind
MCC drüberlaufen lässt, bekommt **denselben** `va_start`-Fehler.
Reproduktionsschritt:

```bash
git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
# in MPLAB X öffnen → MCC starten → "Generate" → Build versuchen
# → derselbe Fehler
```

Der Bug steckt im **MCC-Component-Template** für den LAN865x-Treiber
(irgendwo unter `~/.mchp_packs/Microchip/...` oder in der
Harmony-Net-Component-Definition). Microchip hat zwischen 2023-10-27
und heute den `<stdarg.h>`-Eintrag aus dem Template entfernt, ohne die
`PrintRateLimited()`-Funktion mit zu entfernen — ein klassischer
Tooling-Drift-Bug.

`PrintRateLimited()` ist übrigens echter Microchip-Code, von Thorsten
Kummermehr (Microchip) am 2023-10-27 in Commit `1846c05` eingeführt
("Update LAN865x application to latest Harmony3 packages [MH3-86573]").
Es ist eine reine Anti-Flood-Logging-Hilfe (max. 5 Prints / 1 s, danach
`[skipped N]`). Kein PTP-Bezug.

#### Was das praktisch bedeutet

→ **Der `va_start`-Fehler ist trotzdem dein Freund.** Egal ob Ursache
ein Microchip-Template-Bug ist oder die PTP-Patches: er signalisiert
*sofort und laut*, dass der Treiber durch MCC verändert wurde. Wer die
MCC-Vorschläge unbesehen akzeptiert, hat einen nicht baubaren Tree —
und selbst nach Wieder-Hinzufügen von `<stdarg.h>` fehlt weiterhin
die komplette PTP-Hardware-Timestamping-Infrastruktur aus §2.1–2.6.

→ **Microchip-Issue/PR wäre der richtige Weg.** Trivialer 1-Zeilen-Fix
im Template — `#include <stdarg.h>` zurück oder `PrintRateLimited()`
in `#ifdef SYS_CONSOLE_PRINT` einklammern. Nicht dein Problem zu
lösen, aber gut zu wissen, an wen du dich wenden müsstest.

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

### 2.8 Implementierung — Endstand `cross-driverless`

Die Refaktorisierung ist in zwei Stufen umgesetzt worden:

| Branch | Treiber-Diff | Headerdiff | Total |
|---|---|---|---|
| `cross` (Ausgangsstand) | ~237 Zeilen | 62 Zeilen | **~299** |
| `cross-minimize` (Commit `b0d7b8c`) | 58 Zeilen | 3 Zeilen | **61** |
| `cross-driverless` (aktuell) | **11 Zeilen** | **1 Zeile** | **12** |

Insgesamt **25× kleiner** als der Ausgangsstand. Build sauber mit XC32 v5.10
(`-Werror -Wall`).

#### Was eliminiert wurde (5 von 7 Patches verschwinden komplett)

| | Eingriff | Eliminiert via |
|---|---|---|
| A1 | TC6_MEMMAP-Edits (IMASK0, DEEP_SLEEP, TXM-Filter `0x40040..45`) | `PTP_DRV_EXT_Tasks()`-State-Machine — Last-Write-Wins **nach** `IsReady()`. Schreibt 7 Register im App-Code via `DRV_LAN865X_WriteRegister()`. |
| A2 | CONFIG0 FTSE+FTSS-Bits | dito — RMW (mask=0xC0, value=0xC0) auf CONFIG0 in derselben State-Machine |
| A3 | Cases 46/47 PADCTRL+PPSCTL | dito — 2 Writes (PADCTRL RMW value=0x100 mask=0x300, PPSCTL value=0x7D) |
| A4 | `DELAY_UNLOCK_EXT 100→5` | revertiert auf upstream `100u`. Die neue Architektur (TX-Match aktiv, TXMCTL pro-Sync arm'd) macht den Workaround überflüssig — kein TTSCMA-Trigger mehr. |
| A5a | OnStatus0_Hook (TTSCAA save-before-W1C) | komplett entfernt. `ptp_gm_task.c` hatte längst einen SPI-Fallback (`GM_STATE_READ_STATUS0`/`WAIT_STATUS0`); `GetAndClearTsCapture()` gibt jetzt immer `0u` zurück → Fallback wird zum einzigen Pfad. |

#### Was unvermeidbar im Treiber bleiben muss (irreduzible 12 Zeilen)

| | Eingriff | Begründung (im Code als Kommentar fixiert) |
|---|---|---|
| **A5b** | `DRV_LAN865X_OnPtpFrame_Hook` (1 weak decl + 1 hook call = 2 Zeilen) | **Echte API-Lücke.** Der 64-Bit RX-Hardware-Timestamp kommt **ausschließlich** über den `rxTimestamp`-Parameter von `TC6_CB_OnRxEthernetPacket`. Die Upstream-Treiber **propagiert ihn nicht** in `TCPIP_MAC_PACKET`. Bei `TCPIP_STACK_PacketHandlerRegister`-Zeitpunkt ist er unwiederbringlich verloren. Ohne diesen Hook ist `t2_ns` (PTP-Slave Sync-Empfangszeit) immer 0 → Slave-Sync kaputt. |
| **A6** | `DRV_LAN865X_GetTc6Inst()`-Accessor (5 Zeilen Body + 1 Zeilen-Header-Decl) | Das `drvLAN865XDrvInst[]`-Array ist `static` im Treiber. Der private `TC6_t*` ist nur über diesen Accessor zugänglich — und er wird gebraucht für `TC6_SendRawEthernetPacket(g, …, tsc=0x01, …)`, der die TX-Capture-A für PTP-Sync arm'd. Keine andere Microchip-API erlaubt das `tsc`-Flag. |

Beide Patches sind in der Datei mit einem `/* … irreducible — see ptp_drv_ext.c … */`-Kommentar markiert, damit sie bei zukünftigen MCC-Reviews nicht gelöscht werden.

#### Neue Struktur in `ptp_drv_ext.{c,h}`

```
ptp_drv_ext.h        Public API + 2 Hook-Prototypen
ptp_drv_ext.c        EIC-ISR
                     PTP_DRV_EXT_Init()
                     PTP_DRV_EXT_Tasks()              ← NEU: 24-State Reg-Init
                     PTP_DRV_EXT_RegisterInitDone()   ← NEU: Predicate für ptp_*-Tasks
                     OnPtpFrame_Hook strong impl
                     SendRawEthFrame, IsReady,
                     GetAndClearTsCapture (= 0u),
                     GetTsCaptureNirqTick (= s_nirq_tick)
```

#### Wiring

- [`app.c`](apps/tcpip_iperf_lan865x/firmware/src/app.c): `APP_Initialize()` ruft
  `PTP_DRV_EXT_Init()`. `APP_Tasks()` ruft `PTP_DRV_EXT_Tasks(0u)` periodisch
  (no-op bis `IsReady()` ⇒ Reg-Init State-Machine läuft einmal durch).
- `ptp_gm_task.c` und `ptp_fol_task.c` gaten optional auf
  `PTP_DRV_EXT_RegisterInitDone()` bevor sie die ersten Frames absetzen.

#### Konsequenz für MCC-Workflow

| Szenario | vor `cross-driverless` | jetzt |
|---|---|---|
| MCC-Lauf, Treiber-Override **abgelehnt** | 61 Zeilen recovern | **12 Zeilen recovern** (`git checkout HEAD -- 2 files`) |
| MCC-Lauf, Treiber-Override **akzeptiert** (Versehen) | Compile- + Link-Fehler sofort | dasselbe — die 2 verbleibenden Patches lösen ebenfalls Build-Bruch aus |
| Driver-Drift im täglichen Arbeiten | 61 Zeilen pflegen | **12 Zeilen** pflegen |

#### Caveats (vor Hardware-Sign-off prüfen)

1. **`DELAY_UNLOCK_EXT 5→100`-Revert** ist nicht hardware-verifiziert. Bei
   Wiederauftreten von TTSCMA-Events einfach als Einzeiler-Patch zurück.
2. **`OnStatus0_Hook`-Entfernung** — `gm_task`-SPI-Fallback ist langsamer als
   der In-Driver-Hook (extra SPI-Roundtrip). Toleriert via `gm_wait_ticks`,
   aber Sync-Genauigkeit auf Hardware noch nicht in dieser Config bestätigt.
3. **Race-Window beim Boot:** die ersten ~2 ms nach `IsReady()` läuft der
   Chip mit Upstream-Default-Config (TXMCTL=0x02, FTSE aus, IMASK0=0x100).
   Erste PTP-Frames in dieser Phase wären miskonfiguriert; PTP_GM/FOL gaten
   aber sowieso auf Driver-Readiness, sollte in der Praxis kein Issue sein.
4. **Hardware-Sign-off steht aus** — Build clean, aber kein PoR-to-Sync-Lauf
   in dieser Config. Vor Merge in `master` zwingend testen:
   - GM sendet Sync mit gültigem TX-Timestamp
   - Slave empfängt Sync mit `g_ptp_raw_rx.rxTimestamp != 0` und `sysTickAtRx != 0`
   - PTP-Offset stabilisiert sich auf < 1 µs

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

---

## 6) Reproduktions-Plan: MCC's `<stdarg.h>`-Removal-Bug

§2 dokumentiert, dass MCC's Regeneration von `drv_lan865x_api.c`
einen Build-Fehler erzeugt (`va_start` ohne `<stdarg.h>`-Include). Die
Behauptung: Der Bug liegt in **Microchips LAN865x-MCC-Component-Template**,
nicht in irgendeinem Code dieses Forks.

Dieser Abschnitt definiert ein **reproduzierbares Test-Protokoll**, mit
dem die Behauptung **unabhängig von diesem Repository** nachgewiesen
werden kann — etwa als Beleg für einen Microchip-Bug-Report oder zur
Validierung dieses README.

### Voraussetzungen

- Test-Verzeichnis abseits von `c:/work/ptp/check4/...`
- Installiert: MPLAB X IDE, XC32, MCC-Plugin, `git` (Versionen werden
  in Schritt 7 erfasst)
- Internet-Zugang für `git clone` und Harmony-Package-Download
- `~/.mchp_packs/`-Cache **bewahren** — das ist die forensisch
  interessante Stelle (nicht löschen vor Schritt 8)

### Schritt 1: Test-Workspace anlegen

```bash
mkdir c:\work\ptp\bugtest && cd c:\work\ptp\bugtest
```

### Schritt 2: Frischen Upstream-Clone ziehen

```bash
git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
cd net_10base_t1s
git log -1 --oneline   # SHA notieren — Beleg-Stand
```

### Schritt 3: Upstream-Konsistenz-Check (Vorbedingung)

Der Upstream selbst muss konsistent sein, sonst ist der Test wertlos:

```bash
grep '#include <stdarg.h>' \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c
# erwartet: #include <stdarg.h>

grep -c 'PrintRateLimited' \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c
# erwartet: ≥ 3 Treffer (Macro, Decl, Def)
```

Beide Bedingungen müssen erfüllt sein → Upstream-Datei ist intern
konsistent, der Bug existiert *noch nicht*.

### Schritt 4: MCC laufen lassen (ohne weitere Änderungen)

1. MPLAB X starten
2. Projekt öffnen: `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/`
3. MCC-Tab öffnen ("Load existing configuration" wenn nötig)
4. **Keine Konfiguration ändern**
5. **"Generate"** klicken
6. Im Merge-Dialog **alle vorgeschlagenen Änderungen akzeptieren**
   (insbesondere die für `drv_lan865x_api.c`)

### Schritt 5: Diff dokumentieren — Beweissicherung

```bash
git diff apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c \
  > mcc_regen_diff.patch
```

Erwartete Diff-Zeile (mindestens):

```diff
-#include <stdarg.h>
+
```

Patch-File mit Datum und MCC-Version benennen (z. B.
`mcc_regen_diff_2026-04-25_mcc564.patch`).

### Schritt 6: Build versuchen, Fehler erfassen

```bash
build.bat rebuild > build_failure.log 2>&1
```

Erwarteter Inhalt der Log-Datei:

```
.../drv_lan865x_api.c: In function 'PrintRateLimited':
1532:9: error: implicit declaration of function 'va_start'
        [-Werror=implicit-function-declaration]
1534:9: error: implicit declaration of function 'va_end'
cc1.exe: all warnings being treated as errors
BUILD FAILED.
```

Log aufbewahren.

### Schritt 7: Tooling-Fingerprint festhalten

Auszufüllen für den Test-Lauf — als Information für den Bug-Report:

| Eigenschaft | Wert |
|---|---|
| MPLAB X Version | `____________` (z. B. 6.25 / 6.30) |
| XC32 Version | `____________` (z. B. 4.60 / 5.00 / 5.10) |
| MCC Plugin Version | `____________` (z. B. 5.6.4) |
| Harmony Net Package Version | `____________` (z. B. v3.14.5 / v3.15.0) |
| Harmony Core Package Version | `____________` (z. B. v3.16.0) |
| CSP Package Version | `____________` (z. B. v3.25.1) |
| Microchip net_10base_t1s Repo SHA | `____________` |
| Datum des Test-Laufs | `____________` |

### Schritt 8: Forensik im MCC-Cache

Wo genau wurde der `<stdarg.h>`-Eintrag aus dem Template entfernt?

```bash
# Suchen wo der MCC-LAN865x-Driver-Template lebt:
ls "$USERPROFILE/.mchp_packs/Microchip/" 2>/dev/null

# Files mit PrintRateLimited:
grep -rln 'PrintRateLimited' "$USERPROFILE/.mchp_packs/" 2>/dev/null

# Files mit stdarg im selben Component-Verzeichnis:
grep -rln 'stdarg' "$USERPROFILE/.mchp_packs/" 2>/dev/null

# Ftl-Template-Files (das ist wahrscheinlich der Übeltäter):
find "$USERPROFILE/.mchp_packs/" -name 'drv_lan865x_api*' 2>/dev/null
```

Erwartung: Mindestens eine Template-/Quelldatei (`.ftl`, `.c`,
`.c.ftl`) im Harmony-Net-Package, die `PrintRateLimited()` enthält
**aber nicht** das `<stdarg.h>`-Include. Pfad, Datei und
Component-Manifest-Version festhalten — das ist der konkrete Ort des
Microchip-Bugs.

### Schritt 9: Ergebnis-Tabelle ausfüllen

| Eigenschaft | Wert |
|---|---|
| Upstream-HEAD enthält `<stdarg.h>` + `PrintRateLimited` | ✅ |
| Upstream-HEAD baut sauber (ohne MCC-Run) | ✅ |
| MCC-regen entfernt `<stdarg.h>` | _____ (erwartet: ✅) |
| MCC-regen behält `PrintRateLimited()` | _____ (erwartet: ✅) |
| Build mit `-Werror -Wall` schlägt fehl mit `va_start`-Error | _____ (erwartet: ✅) |
| Test ohne irgendeinen Fork-Eingriff durchgeführt | ✅ (Pristine-Upstream-Clone) |
| **Schlussfolgerung** | Bug liegt im MCC-Template, nicht in diesem Fork |

### Schritt 10: (Optional) Microchip-Bug-Report einreichen

Aus den Beweisen aus Schritten 5/6/7/8 einen Issue auf
[github.com/Microchip-MPLAB-Harmony/net_10base_t1s/issues](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s/issues)
oder über `support.microchip.com` einreichen.

**Vorschlag für Issue-Titel:**

> `drv_lan865x_api.c::PrintRateLimited()` uses `va_start`/`va_end` but
> MCC-regenerated version drops `#include <stdarg.h>` → build fails
> with `-Werror`

**Issue-Body-Skelett:**

```
## Reproduction
1. git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
2. Open in MPLAB X, run MCC "Generate" without changing config.
3. Accept all proposed merges.
4. Build: fails with the error below.

## Environment
[Tabelle aus Schritt 7 einkleben]

## Error output
[build_failure.log einkleben]

## Diff produced by MCC
[mcc_regen_diff.patch einkleben]

## Root cause (suspected)
The MCC component template at <Pfad aus Schritt 8> defines
`PrintRateLimited()` (which uses `va_start`/`va_end`) but does not
emit the matching `#include <stdarg.h>` into the regenerated source
file.

## Suggested fix
Either: (a) restore `#include <stdarg.h>` in the LAN865x
driver template, or (b) wrap the `PrintRateLimited()` definition
in `#ifdef SYS_CONSOLE_PRINT` so the variadic-macro dependency
disappears when console printing is disabled.
```

### Was dieser Plan beweist (und was nicht)

✅ **Bewiesen:** der Bug ist in MCC's Tooling, nicht in diesem Fork —
weil er auch im pristine Upstream-Clone reproduzierbar ist.

❌ **Nicht bewiesen (außerhalb des Scopes):** ob die *PTP-Patches*
dieses Forks andere Probleme mit MCC haben. Die §2.1–2.6/2.8
beschriebenen Patches überleben einen MCC-Lauf eigenständig
(`ptp_drv_ext.{c,h}` werden gar nicht von MCC angefasst); die 12
verbleibenden Inline-Zeilen würden bei Accept verloren gehen, sind
aber separat in §2.8 dokumentiert.

---

## 7) Praxis-Bericht: MCC-Lauf auf `cross-driverless` mit manuellem Merge (2026-04-26)

Real-World-Test der Minimierungs-Strategie: MCC wurde auf dem
`cross-driverless`-Stand laufen gelassen, der Merge-Dialog für
`drv_lan865x_api.c` wurde **manuell** bearbeitet — nicht blind
akzeptiert, nicht blind abgelehnt. Ergebnis: **Build erfolgreich**, alle
PTP-Hooks erhalten.

### 7.1 Beweisgang — was MCC angeboten hat und was übernommen wurde

MCC bot die typische Microchip-Template-Output-Variante an:

- `<stdarg.h>` entfernen (der bekannte MCC-Template-Bug, siehe §6)
- TC6_MEMMAP-Tabelle umsortieren mit teils geänderten Register-Werten
- Diverse YAML-/Manifest-Reorderings (kosmetisch)

Im Manual-Merge-Dialog wurde **MCC's Output für die Memory-Map akzeptiert,
aber die kritischen PTP-Patches verteidigt:**

| Patch | Quelle nach Merge | Status |
|---|---|---|
| `<stdarg.h>` Include (Z. 41) | manuell zurück eingefügt | ✅ erhalten |
| `OnPtpFrame_Hook` weak default (Z. 51) | aus cross-driverless behalten | ✅ erhalten |
| `OnPtpFrame_Hook` Aufruf in RX-Callback (Z. 1383) | aus cross-driverless behalten | ✅ erhalten |
| `GetTc6Inst()`-Accessor (Z. 2446) | aus cross-driverless behalten | ✅ erhalten |
| `<stdarg.h>` Include (Z. 41) | manuell wieder eingefügt | ✅ Build-Bruch vermieden |

→ Die in §2.8 als "irreduzibel" dokumentierten 12 Zeilen sind **alle drin**.
PTP-Plumbing intakt.

### 7.2 ⚠ Nebenwirkung: TC6_MEMMAP-Tabelle hat jetzt Duplikate

Beim manuellen Merge wurden **MCC's neue Init-Einträge zusätzlich
übernommen, ohne die alten zu entfernen** — Resultat: doppelte (teilweise
dreifache) Schreibzugriffe auf dieselben Register beim Boot.

| Register | Alte Position | Neue Position | Effektiver Wert (last-write-wins) |
|---|---|---|---|
| `0x000400E9` | Z. 1704 | Z. 1721 | `0x9E50` (idempotent) |
| `0x000400F5` | Z. 1705 | Z. 1722 | `0x1CF8` (idempotent) |
| `0x000400F4` | Z. 1706 | Z. 1723 | `0xC020` (idempotent) |
| **`0x000400F8`** | Z. 1707 (`0xB900`) | Z. 1724 (`0x9B00`) | **`0x9B00`** ⚠ Wert-Konflikt |
| `0x000400F9` | Z. 1708 | Z. 1725 | `0x4E53` (idempotent) |
| **`0x00040081`** DEEP_SLEEP_CTRL_1 | Z. 1709 + 1711 (`0x80` × 2) | Z. 1740 (`0xE0`) | **`0xE0`** ⚠ Wert-Konflikt, 3-fach geschrieben |

→ Der Chip wird durch den Merge mit **Microchips neuen B1-Fix-Werten**
(`0x9B00`, `0xE0`) initialisiert — vermutlich Erratum-Patches für die
Rev-B1-Hardware. Die alten Werte (`0xB900`, `0x80`) waren PTP-validiert,
die neuen sind es noch nicht.

### 7.3 Funktionale Bewertung

| Aspekt | Bewertung |
|---|---|
| Build-Status | ✅ erfolgreich (XC32 v5.10) |
| Code-Korrektheit | ✅ funktional OK (idempotent oder last-write-wins) |
| Boot-Zeit | minimal länger (5–7 zusätzliche SPI-Writes durch Duplikate) |
| Code-Hygiene | ⚠ Suboptimal — die alten Einträge sind toter Code |
| PTP-Funktion | ❓ unklar bis Hardware-Test (alte Werte waren validiert, neue noch nicht) |

### 7.4 IMASK0 bleibt bei `0x100` — kein Problem für diese Architektur

MCC's Tabelle behält den Upstream-Wert `0x00000100` für IMASK0 (Bit 8
TTSCAA **maskiert**). Das ist in der `cross-driverless`-Architektur **kein
Problem**, weil:

- `ptp_gm_task.c::GM_STATE_READ_STATUS0/WAIT_STATUS0` pollt STATUS0 selbst
  via SPI (`DRV_LAN865X_ReadModifyWriteRegister`).
- Der Driver-`_OnStatus0`-Callback wird zwar nicht durch IMASK0-Interrupt
  getriggert, aber die App-seitige Polling-Schleife liest die TTSCAA-Bits
  trotzdem zuverlässig.
- Diese Architektur-Wahl ist genau der Grund, warum die `OnStatus0_Hook`
  in §2.8 entfernt werden konnte (A5a eliminiert).

→ Microchips Default (`IMASK0=0x100`) und unsere PTP-Anforderung sind
**kompatibel** — keine Anpassung nötig.

### 7.5 Diff-Größen — gegen Upstream und gegen letzten Commit

| Vergleich | Echte Zeilen Diff |
|---|---|
| Gegen committed `cross-driverless` HEAD | 13 |
| Gegen Microchip-Upstream-HEAD (`586ffc1`) | 22 (12 essential + 10 Merge-Duplikate) |

Die +10 vom messy Merge stammen ausschließlich aus den 5 idempotenten
Duplikat-Schreibzugriffen + dem zusätzlichen DEEP_SLEEP-Eintrag.

### 7.6 Empfehlung: Bereinigung nach Hardware-Sign-off

**Schritt 1 — Hardware-Test**: PoR → PTP-Sync → Offset stabil < 1 µs?
Die effektiven Register-Werte (`0x9B00` / `0xE0`) müssen sich beweisen.

**Schritt 2 (falls PTP OK)**: TC6_MEMMAP-Tabelle aufräumen, indem die
**alten Einträge** (Zeilen 1704–1709 + 1711) gelöscht werden:

```diff
@@ static const MemoryMap_t TC6_MEMMAP[] = { ... }
-        {  .address=0x000400E9, .value=0x00009E50, ... },   // alte Position
-        {  .address=0x000400F5, .value=0x00001CF8, ... },
-        {  .address=0x000400F4, .value=0x0000C020, ... },
-        {  .address=0x000400F8, .value=0x0000B900, ... },   // alter B1-Wert
-        {  .address=0x000400F9, .value=0x00004E53, ... },
-        {  .address=0x00040081, .value=0x00000080, ... }, /* DEEP_SLEEP_CTRL_1 */
         {  .address=0x00040091, .value=0x00009660, ... },
-        {  .address=0x00040081, .value=0x00000080, ... },   // Merge-Duplikat
         {  .address=0x00010077, .value=0x00000028, ... },
         ...
```

Damit reduziert sich der Diff gegen Upstream auf die nominalen 12 Zeilen.

**Schritt 3 (falls PTP nach Hardware-Test nicht OK)**: alte Werte
manuell wiederherstellen, indem Microchips neue Werte in der MCC-
Output-Position auf die alten zurückgesetzt werden:

```c
{  .address=0x000400F8,  .value=0x0000B900, ... },   // PTP-validierter B1-Wert
{  .address=0x00040081,  .value=0x00000080, ... },   // PTP-validierter DEEP_SLEEP-Wert
```

### 7.7 Lessons Learned

1. **Manueller Merge mit Köpfchen funktioniert.** Bei nur 12 irreduziblen
   Treiber-Zeilen ist der Merge-Dialog überschaubar genug, um Patch-für-
   Patch entscheiden zu können.
2. **`<stdarg.h>` muss aktiv verteidigt werden.** Microchips Template-Bug
   schlägt bei jedem MCC-Lauf zu — siehe §6 Reproduktions-Plan.
3. **Last-write-wins rettet vor Funktionsfehlern bei messy Merges.** Die
   Duplikate sind hässlich, aber funktional unkritisch.
4. **Microchips B1-Erratum-Werte sind jetzt automatisch übernommen.**
   Wenn das Hardware-OK ist, ist `cross-driverless` damit auch trunk-näher
   geworden (Microchip-Wert für `0x000400F8`/`0x00040081`).
5. **Der Reproduktions-Plan aus §6 ist 1:1 bestätigt** — am 2026-04-26
   in einem realen Workflow, ohne dass speziell danach gesucht wurde.
