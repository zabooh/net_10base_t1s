# Upgrade-Notiz — wie sich diese Implementierung von AN1847 unterscheidet

**Erstellt:** 2026-04-27
**Branch:** `mult-sync`
**Bezugs-Dokumente:**

- [LAN8650-1-Time-Synch-AN-60001847.pdf](../../pdf/LAN8650-1-Time-Synch-AN-60001847.pdf)
  — Microchips originale Application Note zur Zeitsynchronisation auf
  10BASE-T1S mit dem LAN8650/1
- [readme_results.md](readme_results.md) — Machbarkeits-Analyse für
  3–8 Knoten mit 1 µs Sync-Genauigkeit, mit Reality-Check zur
  praktischen Relevanz dieser Implementierung gegenüber AN1847
  (insbesondere §10–§12)

---

## Inhaltsverzeichnis

1. [Worum es geht](#1-worum-es-geht)
2. [TL;DR — Vergleich auf einen Blick](#2-tldr--vergleich-auf-einen-blick)
3. [Was wir mit AN1847 gemeinsam haben](#3-was-wir-mit-an1847-gemeinsam-haben)
4. [Wo wir AN1847 erweitern](#4-wo-wir-an1847-erweitern)
5. [Wo wir AN1847 absichtlich nicht folgen](#5-wo-wir-an1847-absichtlich-nicht-folgen)
6. [Build-Flag `PTP_AN1847_STYLE`](#6-build-flag-ptp_an1847_style)
7. [CLI-Befehle, die für den AN1847-Modus relevant sind](#7-cli-befehle-die-für-den-an1847-modus-relevant-sind)
8. [Auto-Mode-Auswahl beim Boot](#8-auto-mode-auswahl-beim-boot)
9. [Geänderte Quelldateien im `mult-sync`-Refactor](#9-geänderte-quelldateien-im-mult-sync-refactor)
10. [Wo es weiter geht](#10-wo-es-weiter-geht)

---

## 1. Worum es geht

Die Microchip Application Note **AN1847 — Time Synchronization using
the LAN8650/1** beschreibt eine **demo-grade Implementierung** eines
Zeit-synchronen 10BASE-T1S-Multidrop-Segments. Die Demo zeigt, dass
LAN8651 die nötigen Hardware-Features hat (94-Bit Wall Clock,
Pattern-Matcher-Timestamping am End-of-SFD, Event-Generators), und sie
demonstriert eine 100 ns peak-to-peak Genauigkeit (σ = 25 ns) auf 50 cm
UTP zwischen zwei Knoten — mit einem **simplen FIR-Filter** und
ausschließlich `MAC_TA`-Adjustments.

Dieses Repo enthält auf dem `mult-sync`-Branch eine
**Engineering-Implementation desselben Modells** auf SAM E54 + LAN8651,
mit deutlich erweiterter Servo-Logik, Source-MAC-Fixation,
PLCA-Node-ID-basierter Auto-Mode-Wahl und einem Compile-Flag, das den
Pdelay-Pfad gegen den AN1847-Pfad austauscht. Das Ziel: 3–8 Knoten am
selben Bus, davon einer als statischer Master, sub-µs Sync-Genauigkeit
(siehe [readme_results.md §11–§12](readme_results.md#11-2-ptp-knoten-in-einem-8-knoten-bus)).

---

## 2. TL;DR — Vergleich auf einen Blick

| Aspekt | AN1847 (Microchip-Demo) | `mult-sync`-Branch (diese Implementierung) |
|---|---|---|
| **Protokoll** | Sync + Follow_up | Sync + Follow_up (identisch) |
| **Path-Delay** | ignoriert (50 cm Demo-Setup) | **statisch konfigurierbar** via CLI `ptp_path_delay <ns>` |
| **Master-Wahl** | hardcodiert auf einem Knoten | **automatisch** aus `DRV_LAN865X_PLCA_NODE_ID_IDX0` (Node 0 = Master) |
| **Source-MAC-Validierung** | keine | **GM-MAC-Lock** beim ersten Sync, danach Drop fremder Sender |
| **Servo** | FIR-Filter, nur `MAC_TA` | 4-State-Servo (UNINIT → MATCHFREQ → HARDSYNC → COARSE → FINE), `MAC_TA` + `MAC_TI` + adaptiver IIR-Drift-Filter |
| **Frequenz-Korrektur** | nicht ausgeführt | `MAC_TI` + `MAC_TISUBN` (Increment-Tuning) |
| **Pdelay-Roundtrip** | nicht vorgesehen | **deaktiviert** in AN1847-Mode (kompiliert via `#if !PTP_AN1847_STYLE` weiterhin als Legacy) |
| **CLI / Runtime-Config** | nein (hardcodiert) | ja: `ptp_mode`, `ptp_path_delay`, `ptp_status`, `ptp_trace`, `clk_set`, `ptp_offset_dump`, ~15 weitere |
| **Logging** | printf-Stubs | strukturiertes Log + Offset-Trace-Ringbuffer |
| **TCP/IP-Stack** | keiner (raw eth) | volle Microchip Harmony Integration mit iperf-fähig |
| **Demo-Hardware** | SAM D21 + Two-Wire ETH Click | SAM E54 + LAN865X (PD10-Test-Rig) |
| **Skalierbarkeit** | 2 Knoten | **N Knoten** (1 Master, N-1 Follower) auf AN1847-Pfad |
| **Reference-Genauigkeit** | 100 ns p-p, σ = 25 ns @ 50 cm | dieselbe Hardware-Baseline + zusätzlicher Servo-Headroom |

---

## 3. Was wir mit AN1847 gemeinsam haben

**Hardware-Pfad ist identisch:**

- LAN8651 als MAC-PHY mit aktiviertem TSU
- 94-Bit Wall Clock (`MAC_TSH` + `MAC_TSL` + `MAC_TSN`)
- `MAC_TI` = `0x28` (40 ns Increment für 25 MHz Quarz)
- TX-Pattern-Matcher gefiltert auf EtherType `0x88F7`
- Timestamps am **End-of-SFD im PHY** (nach Elastic Buffer)
- Anchor-Update-Mechanik: Wall Clock direkt setzen +
  Increment-Register-Tuning für laufende Synchronisation

**Protokoll-Wahl ist identisch:**

- IEEE 1588 PTPv2 Layer-2 Multicast (`01:80:C2:00:00:0E`)
- Sync mit `twoStepFlag` gesetzt
- Follow_up trägt `preciseOriginTimestamp` (= GM-TX-Zeit am SFD)
- Kein Pdelay, kein Announce, kein BMCA in AN1847-Mode

**Topologie-Annahme ist identisch:**

- Ein Grandmaster, N Follower auf einem geteilten T1S-Mixing-Segment
- Master ist zugleich PLCA-Coordinator (Slot 0)
- Follower senden im aktiven AN1847-Mode keine PTP-Frames

---

## 4. Wo wir AN1847 erweitern

### 4.1 Statisches, konfigurierbares Path-Delay statt Ignorieren

AN1847's Demo nimmt an, dass das Path-Delay vernachlässigbar ist
(50 cm × 5 ns/m ≈ 2,5 ns). Bei 5–25 m Kabel im realen Mixing-Segment
sind das 25–125 ns — bei sub-100-ns-Zielen relevant. Diese
Implementierung erlaubt das per CLI:

```text
ptp_path_delay 350     # setzt Master→Follower one-way Path-Delay auf 350 ns
ptp_path_delay         # zeigt den aktuellen Wert
```

Der Wert wird in [ptp_fol_task.c::processFollowUp()](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c)
direkt vom Roh-Offset abgezogen:
`offset_corrected = (t2 - t1) - fol_static_path_delay_ns`.

### 4.2 Auto-Mode-Auswahl aus PLCA-Node-ID

Statt dass jeder Knoten manuell mit `ptp_mode master|follower`
konfiguriert werden muss, liest die Implementierung beim Boot
`DRV_LAN865X_PLCA_NODE_ID_IDX0` (aus
[configuration.h](../../apps/tcpip_iperf_lan865x/firmware/src/config/default/configuration.h))
und entscheidet:

- Node 0 → `PTP_FOL_SetMode(PTP_MASTER)` + `PTP_GM_Init()`
- Node ≥ 1 → `PTP_FOL_SetMode(PTP_SLAVE)`

Die Konvention wird so über die gesamte Bus-Topologie konsistent: Node 0
ist immer Coordinator + Grandmaster. Manuelle Override per CLI bleibt
möglich.

Implementiert in [PTP_FOL_AutoSelectMode()](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c),
aufgerufen aus [app.c::APP_STATE_IDLE](../../apps/tcpip_iperf_lan865x/firmware/src/app.c)
nach `PTP_FOL_Init()`.

### 4.3 Source-MAC-Fixation als BMCA-Ersatz

802.1AS-BMCA ist auf Multidrop nicht definiert. Diese Implementierung
nutzt eine **simple Source-Identity-Validierung** in
[handlePtp()](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c):

1. Beim ersten empfangenen Sync wird die Quell-MAC in
   `fol_locked_gm_mac[]` kopiert und `fol_gm_mac_locked = true`
2. Spätere Syncs/Follow_ups von einer anderen Quell-MAC werden silent
   gedroppt
3. Bei `PTP_FOL_Reset()` / Sequence-ID-Reset wird der Lock gelöst, ein
   neuer Master kann übernommen werden

Damit ist der Bus robust gegen versehentliche Doppel-Master, ohne dass
ein voller BMCA-Algorithmus nötig wäre.

### 4.4 Mehrstufiger Servo statt FIR-only

AN1847's Demo verwendet *einen einzigen* FIR-Filter und schreibt nur
`MAC_TA` (Phase Adjust). Diese Implementierung fährt eine
4-Zustands-Maschine aus
[ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c) und
[ptp_clock.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c):

```text
UNINIT     →  Direkt-Set der Wall Clock + initiale Quarz-Kalibrierung
MATCHFREQ  →  Increment-Register tunen (MAC_TI / MAC_TISUBN)
HARDSYNC   →  Größere Phase-Sprünge via MAC_TA
COARSE     →  Mittel-große MAC_TA / MAC_TI Tweaks
FINE       →  Kleine Increment-Updates, sub-µs Stabilität
```

Plus adaptive IIR-Drift-Filterung (siehe
[drift_filter.md](drift_filter.md)) und Rate-Ratio-Tracking aus
aufeinanderfolgenden Sync-Timestamps. Das Resultat: schnellerer
First-Lock, geringere Phase-Jitter im FINE-Zustand, robustere
Handhabung von Quarz-Drift bei längeren Sync-Intervallen.

### 4.5 Strukturiertes Logging und Offset-Tracing

AN1847's Demo gibt schlichte printf-Logs aus.
Diese Implementierung hat:

- [ptp_log.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_log.c)
  — strukturierte Log-Pipeline mit Severity
- [ptp_offset_trace.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_offset_trace.c)
  — Ringbuffer für Offset-Verlauf, dumpbar via CLI (`ptp_offset_dump`)
- [ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c)
  — `ptp_trace [on|off]` schaltet detailliertes Per-Frame-Logging

### 4.6 Volle TCP/IP-Stack-Integration

Während die AN1847-Referenz auf raw Ethernet bleibt, läuft diese
Implementierung über **Microchip Harmony v3 TCP/IP Stack**, was
zusätzlich erlaubt:

- iperf2/iperf3 als Last-Generator parallel zu PTP
- DHCP / DNS / Telnet / HTTP-Server auf demselben Knoten
- Anwendungs-Logik (z. B. tfuture, distributed ADC sampling) auf der
  PTP-synchronisierten Wall Clock

### 4.7 Migrations-Pfad zurück zu Pdelay-802.1AS-Style

Über das Compile-Flag
[`PTP_AN1847_STYLE`](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.h)
lässt sich die alte Pdelay-basierte 802.1AS-Variante reaktivieren —
siehe [§6](#6-build-flag-ptp_an1847_style). Damit kann derselbe
Code-Tree weiterhin als Forschungs-Baseline für 2-Knoten-Pdelay-
Vergleichsmessungen dienen.

---

## 5. Wo wir AN1847 absichtlich nicht folgen

### 5.1 Keine Pdelay-Roundtrip-Messung

AN1847 erwähnt Pdelay als optionale Erweiterung (§2.3 "Modifications
Needed to Support Multidrop and PLCA"). Wir implementieren das
**absichtlich nicht** in AN1847-Mode, weil der Single-`gm_delay_resp_tx_busy`-Slot
in [ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c#L87)
auf 2 PTP-Teilnehmer beschränkt — siehe
[readme_results.md §5.2](readme_results.md#52-was-die-implementierung-auf-2-knoten-beschränkt).
Die Pdelay-Code-Pfade sind via `#if !PTP_AN1847_STYLE` weiterhin
kompilierbar, im Default-Build aber inaktiv.

### 5.2 Kein BMCA

Aus denselben Gründen wie in
[readme_results.md §6.3](readme_results.md#63-bmca-auf-multidrop-ist-konzeptionell-broken)
beschrieben (BMCA ist auf Multidrop konzeptionell broken). Stattdessen:
statische Master-Wahl per PLCA-Node-ID + Source-MAC-Fixation.

### 5.3 Keine Announce-Frames

Folgt aus der Abwesenheit von BMCA — Announce ist primär für die
Master-Auswahl-Phase relevant. In einem Setup mit fixem Master ist
Announce überflüssig.

### 5.4 Keine Sync-Forwarding / Residence-Time-Korrektur

Bridge-Funktionalität nach 802.1AS ist nicht implementiert. Jedes
Mixing-Segment hat genau einen Master; Inter-Segment-Bridging muss auf
Anwendungs-Ebene oder durch externe Bridges erfolgen.

---

## 6. Build-Flag `PTP_AN1847_STYLE`

Definiert in
[ptp_fol_task.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.h):

```c
#ifndef PTP_AN1847_STYLE
#define PTP_AN1847_STYLE 1
#endif
```

| Wert | Verhalten |
|---|---|
| `1` (default auf `mult-sync`) | AN1847-Mode: Sync + Follow_up only, statisches Path-Delay, GM-MAC-Lock, Pdelay-Code dormant |
| `0` (legacy `cross-driverless` Verhalten) | Volle Pdelay-Roundtrip-Messung, dynamisches `mean_path_delay`, 2-PTP-Knoten-Beschränkung wie auf `cross-driverless` |

Über Compile-Definition setzen, etwa per CMake:

```text
add_compile_definitions(PTP_AN1847_STYLE=0)
```

Die so betroffenen Code-Stellen:

- [ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c) —
  Path-Delay-Substitution in `processFollowUp`, Pdelay-TX/RX-Routing in
  `handlePtp`, GM-MAC-Lock, `PTP_FOL_AutoSelectMode`,
  `PTP_FOL_GetMeanPathDelay`/`Get/SetStaticPathDelay`
- [ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c) —
  `PTP_GM_OnDelayReq` early-return, `gm_delay_resp_buf` und
  `gm_delay_resp_tx_cb` weggekapselt
- [ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c) —
  `ptp_status` zeigt statisches Path-Delay
- [app.c](../../apps/tcpip_iperf_lan865x/firmware/src/app.c) —
  Auto-Mode-Aufruf am Boot

---

## 7. CLI-Befehle, die für den AN1847-Modus relevant sind

| Befehl | Wirkung |
|---|---|
| `ptp_path_delay [<ns>]` | **NEU** — statisches Path-Delay setzen oder anzeigen |
| `ptp_mode [off\|master\|follower]` | Modus manuell setzen (überschreibt Auto-Mode aus PLCA-Node-ID) |
| `ptp_status` | Zeigt Modus, Offset, Path-Delay (mit Vermerk "AN1847 mode") |
| `ptp_offset` | Aktueller Follower-Offset in ns |
| `ptp_offset_dump` | Ringbuffer der letzten Offsets dumpen |
| `ptp_reset` | Servo + GM-MAC-Lock zurücksetzen |
| `ptp_trace [on\|off]` | Per-Frame-Trace-Logs |
| `ptp_interval <ms>` | GM Sync-Intervall ändern (default 125 ms) |
| `ptp_dst [multicast\|broadcast]` | GM Destination-MAC umschalten |
| `clk_get` / `clk_set <ns>` | Software Wall Clock lesen / setzen |

Beispiel-Sitzung auf einem Follower (Node 1, 5 m Kabel):

```text
ptp_path_delay 25
ptp_path_delay set to 25 ns
ptp_status
PTP mode   : follower
Offset ns  : -3
Abs off ns : 3
Path delay : 25 ns (static, AN1847 mode)
```

---

## 8. Auto-Mode-Auswahl beim Boot

Aus [app.c::APP_STATE_IDLE](../../apps/tcpip_iperf_lan865x/firmware/src/app.c)
nach erfolgreichem `PTP_FOL_Init()`:

```c
#if PTP_AN1847_STYLE
PTP_FOL_AutoSelectMode((uint8_t)DRV_LAN865X_PLCA_NODE_ID_IDX0);
#endif
```

`DRV_LAN865X_PLCA_NODE_ID_IDX0` wird in
[config/default/configuration.h](../../apps/tcpip_iperf_lan865x/firmware/src/config/default/configuration.h)
definiert (typisch durch MCC) und steuert sowohl die PLCA-Konfiguration
als auch die PTP-Rolle. Für ein Mixing-Segment mit 8 Knoten:

| Board | `DRV_LAN865X_PLCA_NODE_ID_IDX0` | PTP-Rolle |
|---|---|---|
| Board 0 | 0 | Master (sendet Sync + Follow_up) |
| Board 1 | 1 | Follower |
| Board 2 | 2 | Follower |
| … | … | … |
| Board 7 | 7 | Follower |

Bei einem späteren CLI-Override mit `ptp_mode master|follower` bleibt
die manuelle Wahl bis zum nächsten Reset bestehen.

---

## 9. Geänderte Quelldateien im `mult-sync`-Refactor

Gegenüber dem `cross-driverless`-Branch (kompletter Pdelay-Stack) fünf
betroffene Dateien:

| Datei | Änderungen |
|---|---|
| [apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.h) | `PTP_AN1847_STYLE`-Macro, neue Public-API (`PTP_FOL_SetStaticPathDelay`, `PTP_FOL_GetStaticPathDelay`, `PTP_FOL_AutoSelectMode`) |
| [apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c) | Static-Path-Delay-Variable, GM-MAC-Lock, Source-MAC-Fixation in `handlePtp`, Pdelay-Pfade weggekapselt, neue Public-API-Implementierungen |
| [apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c) | `PTP_GM_OnDelayReq` early-return, `gm_delay_resp_buf` und `gm_delay_resp_tx_cb` weggekapselt |
| [apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c) | `ptp_path_delay` CLI-Befehl, `ptp_status` zeigt statischen Wert |
| [apps/tcpip_iperf_lan865x/firmware/src/app.c](../../apps/tcpip_iperf_lan865x/firmware/src/app.c) | Auto-Mode-Aufruf am Boot |

Build-Status auf `mult-sync`-HEAD (XC32 v5.10):

```text
Flash: 163,788 bytes ( 159.9 KiB)  15.6%
RAM  :  43,487 bytes (  42.5 KiB)  16.6%
```

---

## 10. Wo es weiter geht

- **Hardware-Test** auf 3+ Boards mit unterschiedlichen
  `DRV_LAN865X_PLCA_NODE_ID_IDX0`-Werten — Sync-Verhalten verifizieren
- **Cross-Board-Genauigkeitsmessung** über 1PPS-Vergleich, vergleichbar
  mit bisherigen Tests in
  [testing/pd10_sync_before_after_tests.md](../testing/pd10_sync_before_after_tests.md)
- **Path-Delay-Kalibrierung** pro Knoten dokumentieren — empfohlene
  Vorgehensweise: 1 m Kabel als Referenz mit `ptp_path_delay 5`,
  längere Strecken proportional
- **Annex-H-Roadmap** (siehe
  [plca_ptp_asymmetrie.md §12](plca_ptp_asymmetrie.md))
  — wie sich AN1847-Mode mit der geplanten Topology-Discovery-
  Erweiterung verbindet, falls IEEE 802.3da publiziert wird
- **Zurück-Migration** auf `PTP_AN1847_STYLE=0` für 2-Knoten-Pdelay-
  Vergleichsmessungen, falls Forschungsdaten gewünscht (siehe
  [readme_results.md §12.5 Option C](readme_results.md#125-drei-handlungsoptionen))

---

**Stand 2026-04-27, Branch `mult-sync` Commit-HEAD nach AN1847-Refactor.**
