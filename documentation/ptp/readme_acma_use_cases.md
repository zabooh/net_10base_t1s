# ACMA + PTP + 10BASE-T1S — Anwendungsfälle

Was kann man mit deterministischem TDMA-Bus-Zugriff (ACMA) plus
PTP-synchroner Wall Clock auf 10BASE-T1S realisieren?
Diese Datei sammelt konkrete Anwendungs-Klassen, die mit
Standard-PLCA oder klassischem Ethernet **nicht** realisierbar wären
und Microchip's ACMA-Mechanismus zur kostengünstigen TSN-Light-
Alternative machen.

**Erstellt:** 2026-04-27
**Bezugs-Dokumente:**

- [readme_acma.md](readme_acma.md) — Architektur-Skizze für ACMA + EG0
  + PTP, mit ISR-Re-Arm-Pattern und Bootstrap
- [readme_results.md](readme_results.md) — PTP-Machbarkeits-Analyse
  und Reality-Check
- [readme_upgrade.md](readme_upgrade.md) — AN1847-Style Refactor auf
  dem `mult-sync` Branch
- [../pdf/lan86xx_family.md](../pdf/lan86xx_family.md) — LAN86xx
  Familien-Übersicht inkl. Errata
- [../pdf/lan865x_vs_lan867x_architecture.md](../pdf/lan865x_vs_lan867x_architecture.md)
  — Block-Architektur-Vergleich

---

## Inhaltsverzeichnis

1. [Was ACMA + PTP an Hardware-Garantien liefert](#1-was-acma--ptp-an-hardware-garantien-liefert)
2. [Industrielle Steuerung](#2-industrielle-steuerung)
3. [Sensor-Cluster und Datenerfassung](#3-sensor-cluster-und-datenerfassung)
4. [Audio / Video / Multimedia](#4-audio--video--multimedia)
5. [Automotive](#5-automotive)
6. [Avionik und Sicherheit](#6-avionik-und-sicherheit)
7. [Test- und Mess-Anwendungen](#7-test--und-mess-anwendungen)
8. [Spezielle Anwendungsfälle](#8-spezielle-anwendungsfälle)
9. [Anwendungs-Übersicht (kompakte Tabelle)](#9-anwendungs-übersicht-kompakte-tabelle)
10. [Was ACMA + T1S nicht kann](#10-was-acma--t1s-nicht-kann)
11. [Microchip's Markt-Strategie](#11-microchips-markt-strategie)

---

## 1. Was ACMA + PTP an Hardware-Garantien liefert

ACMA + Event Generator + PTP-Sync gibt einer Anwendung **drei
garantierte Eigenschaften** auf 10BASE-T1S Multidrop:

- **Determinismus** — jeder Knoten kennt seinen Sende-Slot auf
  Nanosekunden genau (siehe [readme_acma.md §4](readme_acma.md))
- **Garantierte Latenz** — Worst-Case = ein Bus-Zyklus, typisch
  1-10 ms abhängig von Slot-Konfiguration
- **Sub-µs synchrone Wall Clock** auf allen Knoten via PTP
  (gemessene 100 ns p-p / 25 ns σ in AN1847)

Die Anwendungen unten sind nach Branchen sortiert. Jede nutzt eine
oder mehrere dieser drei Eigenschaften als zentrale
Architektur-Voraussetzung.

---

## 2. Industrielle Steuerung

### 2.1 Verteilte SPS / Motion Control

**Szenario:** Eine Master-SPS koordiniert mehrere Motor-Drives,
Sensoren und Aktoren auf einem T1S-Bus.

- Alle Drives empfangen ihren Soll-Wert im selben Slot der SPS
- Alle Drives senden ihren Ist-Wert in ihren eigenen Slots zurück
- **Worst-Case-Latenz < 1 ms** statt mehrerer ms wie bei klassischem
  Ethernet
- **Synchrones Sampling** der Position: alle Drives lesen ihren
  Encoder zur selben PTP-Zeit

**Konkurrenz-Technologien:** heute via EtherCAT, PROFINET-IRT oder
Sercos III auf 100 Mbit/s. ACMA + T1S ist die **kostengünstige
Variante** für niedrigere Bandbreite-Anforderungen — z. B.
Kran-Steuerung, Förderband-Aktorik, Verpackungsmaschinen.

### 2.2 Synchrones Multi-Achs-Sampling

**Szenario:** 8 Encoder-Knoten auf einem Bus, alle müssen ihre
Position zur **selben Wall-Clock-Zeit** sampeln.

- Master setzt Trigger-Zeit `T = next_second + 100ms`
- Alle Knoten programmieren EG1 als Single-Shot-Trigger zu T
- EG1 löst lokalen ADC-Sample aus
- Daten werden in jedem eigenen ACMA-Slot zurückgesendet

**Genauigkeit der Synchron-Samples:** < 100 ns, limitiert durch
PTP-Genauigkeit. Anwendungen: Robotik mit gekoppelter Kinematik,
Werkzeugmaschinen mit Mehr-Achs-Interpolation, Form-Mess-Systeme.

### 2.3 Coordinated I/O Triggering

**Szenario:** Eine Maschine muss zum exakt gleichen Zeitpunkt
mehrere Aktoren auslösen — z. B. Schwingungs-Anregung, Stempel-
Pressen, Sprüh-Düsen.

- Master sendet Sync-Frame: "Trigger zur Zeit T"
- Alle Knoten programmieren ihren EG2 auf T
- Alle Aktoren feuern simultan, **sub-µs synchron**

**Vorteil:** keine Sternverkabelung mehr nötig (klassisch über
Trigger-Bus oder PCB-Backplane), ein einzelnes T1S-Kabel reicht.

---

## 3. Sensor-Cluster und Datenerfassung

### 3.1 Verteilte ADC-Arrays

**Szenario:** 8 ADC-Knoten messen synchron Strom/Spannung an
verschiedenen Punkten einer Maschine oder eines Solar-Wechselrichters.

- Synchron-Sample alle 1 ms (1 kHz Sample-Rate)
- ACMA-Slots transportieren die Sample-Werte zum Master
- Master rekonstruiert Multi-Channel-Wellenform mit konstantem
  Phasen-Bezug zwischen Kanälen

**Anwendungen:** Smart-Grid-Power-Monitoring, Vibrations-Diagnose,
Strukturüberwachung (Brücken, Windkraft-Türme), Prozess-Monitoring
in Chemie/Pharma.

### 3.2 Akustische Sensor-Arrays / Beamforming

**Szenario:** Mehrere Mikrofone in räumlich verteilter Anordnung,
Beamforming für Quellenortung oder Spracherkennung.

- Mikrofone sampeln synchron (etwa 16 kHz Audio)
- Phasen-Bezug zwischen Kanälen muss < 1 µs sein für Beamforming
- ACMA garantiert deterministische Datenübertragung
- PTP garantiert Sample-Synchronität

**Anwendungen:** Industrie-Akustik (Maschinenüberwachung),
Sicherheits-Systeme (Schuss-Detektion), intelligente Räume.

### 3.3 LiDAR / ToF-Sensor-Cluster

**Szenario:** Mehrere ToF-Distanz-Sensoren in einem Roboter oder
einer Sicherheits-Anlage müssen synchron messen, um keine
Interferenzen zu produzieren.

- ACMA-Slot pro Sensor → garantiert exklusiver Sende-Zeitschlitz für
  Lichtpuls
- PTP-Sync → keiner sendet während der Messung eines anderen
- Dramatisch reduzierte Cross-Talk-Probleme

---

## 4. Audio / Video / Multimedia

### 4.1 Distributed Audio (AVB-Light)

**Szenario:** Mehrere Lautsprecher oder Mikrofone an einem T1S-Bus,
synchroner Audio-Stream.

- Audio-Daten in ACMA-Slots (typisch 48 kHz × 16-bit Stereo =
  1.5 Mbit/s, passt in 1 ms-Zyklus)
- PTP-synchrone Wiedergabe → keine Phasen-Verschiebung zwischen
  Lautsprechern
- Garantierte konstante Latenz → kein Audio-Buffering-Jitter

**Anwendungen:** Gebäude-Beschallung, Konferenz-Räume, Auto-Audio-
Systeme. Diese Anwendung ist heute typisch via AVB/TSN auf
100 Mbit/s — ACMA-T1S wäre die Low-Cost-Variante.

### 4.2 Synchronized Lighting

**Szenario:** LED-Strips, Bühnenbeleuchtung, Architektur-Beleuchtung
mit präzise synchronisierten Effekten.

- Master sendet Frame-Sequenz mit Trigger-Zeitstempel
- Alle Knoten triggern Effekt-Wechsel zur exakt gleichen
  Wall-Clock-Zeit
- Sub-µs synchrone Effekte über das gesamte Beleuchtungs-Setup

---

## 5. Automotive

### 5.1 Sensor-Backbone

**Szenario:** Verteilte Sensorik im Fahrzeug — Park-Sensoren,
Temperatur-Sensoren, Reifen-Druck (TPMS), elektrische
Verbraucher-Steuerung.

- Jeder Sensor-Knoten in eigenem ACMA-Slot
- Synchrone Sensor-Reads ermöglichen Sensorfusion (z. B. zeitlich
  abgestimmte Park-Sensor-Daten + Lenkwinkel + Geschwindigkeit)
- Ersetzt CAN-FD oder LIN für höhere Bandbreite mit besseren
  Determinismus-Eigenschaften

Microchip's Marketing-Push für ACMA + T1S richtet sich primär an
genau diesen Markt — IEEE 802.3cg + AEC-Q100 Qualifikation der
LAN86xx zielt auf Automotive.

### 5.2 ADAS-Sensor-Synchronisation

**Szenario:** Kameras, Radar, LiDAR, Ultraschall müssen frame-synchron
sein für Sensorfusion.

- T1S nicht für die Sensor-Daten selbst (Bandbreite zu klein), aber
  für **Trigger-Verteilung**
- Alle Sensoren bekommen ihren Capture-Trigger zur selben PTP-Zeit
  über ACMA-koordinierten T1S-Bus
- Datenleitung läuft separat über höher-bandbreitiges Medium

### 5.3 In-Vehicle-Network-Management

**Szenario:** Verteilte ECUs, deren Steuerungs-Frames zeitlich
kontrolliert werden müssen — Battery-Management, Bremsen-Steuerung,
Klimaanlage.

- Ergänzung oder Ersatz für CAN-FD mit garantierter Latenz
- ACMA-Slot pro ECU = garantierter Sende-Zeitschlitz, kein
  Konkurrenz-Verhalten

---

## 6. Avionik und Sicherheit

### 6.1 Redundante Steuerungs-Bus-Systeme

**Szenario:** Flugsteuerungs- oder Sicherheits-kritische Anwendung
mit deterministischen Anforderungen.

- ACMA-Slots garantieren, dass jeder Knoten seinen Status sendet
- Watchdog-Mechanik: bleibt ein Slot leer → System weiß sofort,
  welcher Knoten ausgefallen ist
- Höhere Wahrscheinlichkeit der ISO-26262-/ARP4761-Zertifizierbarkeit

Microchip's ISO-26262-Safety-Package macht ACMA-T1S für funktional-
sichere Anwendungen attraktiv.

### 6.2 Verteilte Fehlertoleranz

**Szenario:** N+1-redundante Knoten teilen sich einen Bus.

- Jeder Knoten in eigenem Slot → keine Stör-Möglichkeit zwischen
  redundanten Pfaden
- Hot-Standby-Wechsel ist deterministisch in einem definierten Slot
- Voting-Logik (2-aus-3) auf der Anwendungs-Ebene mit garantiert
  verfügbaren Daten

---

## 7. Test- und Mess-Anwendungen

### 7.1 Synchrone Mess-Knoten

**Szenario:** Mehrere Messgeräte (Oszilloskope, Logger, Power-Analyser)
auf einem Bus, alle PTP-synchron.

- Trigger-Verteilung über ACMA
- Mess-Daten-Rückführung über ACMA
- Time-Correlation der Messungen ist Hardware-garantiert

### 7.2 Hardware-in-the-Loop (HIL) Testbeds

**Szenario:** Test-System mit mehreren Lasten und Stimuli, die
synchron geschaltet werden.

- ACMA-Slots koordinieren den Lasten-Schalter
- PTP-Sync ermöglicht Mikrosekunden-genaue Test-Sequenzen
- Reproducible Test-Bedingungen über lange Zeiträume

---

## 8. Spezielle Anwendungsfälle

### 8.1 Backbone für IIoT-Sensor-Mesh

**Szenario:** Sensor-Knoten kommunizieren nicht direkt mit Cloud,
sondern aggregieren über Gateway.

- ACMA-koordinierter Bus → keine Bandbreiten-Konflikte zwischen
  Sensoren
- PTP gibt jedem Sample einen exakten Zeitstempel
- Gateway leitet aggregierte Daten an Cloud weiter

### 8.2 Smart-Building / Gebäude-Automatisierung

**Szenario:** Lichtsteuerung, HVAC, Sicherheits-Systeme, Zugangs-
kontrolle in einem Gebäude.

- Niedrige Bandbreite (10 Mbit/s reicht), aber **viele Knoten** pro
  Bus (bis zu 50 mit erweitertem Reach laut AN60001829)
- Determinismus für Türöffner, Aufzugs-Steuerung, Brand-Alarm-Sensoren
- PoDL-Speisung über dasselbe Kabel reduziert Verkabelungs-Kosten

### 8.3 Energiemanagement / Smart-Grid-Edge

**Szenario:** Verteilte Energie-Mess-Punkte in einem Gebäude oder
Industrie-Anlage.

- Synchrones Power-Monitoring über mehrere Punkte
- Phasor-Measurements (PMU) für Stabilitäts-Analyse
- Grid-synchron mit externer 1PPS-Referenz

---

## 9. Anwendungs-Übersicht (kompakte Tabelle)

| Anwendungs-Klasse | Was ACMA liefert | Bandbreite-Bedarf | Kapitel |
|---|---|---|---|
| Motion Control | sub-ms Latenz, sub-µs Sync | mittel | §2.1 |
| Multi-Channel ADC | exakte Sample-Synchronität | mittel-hoch | §3.1 |
| Distributed Audio | Phasen-konstante Wiedergabe | mittel | §4.1 |
| Trigger-Verteilung | sub-µs Trigger-Sync | gering | §2.3, §5.2 |
| Sensorfusion | Time-Correlation in Hardware | gering-mittel | §5.1, §5.2 |
| Sicherheits-Systeme | deterministische Latenz, Watchdog | gering | §6.1 |
| Smart-Building | viele Knoten, niedrige Latenz | gering | §8.2 |
| Avionik / Funktionale Sicherheit | ISO-26262, deterministisch | gering-mittel | §6.1, §6.2 |
| Beamforming-Arrays | sub-µs Phasen-Bezug | mittel | §3.2 |
| LiDAR-Cluster | Cross-Talk-Vermeidung | gering | §3.3 |
| In-Vehicle-Backbone | Replacement für CAN-FD | gering | §5.3 |
| HIL-Testbeds | Reproducibility | gering | §7.2 |

---

## 10. Was ACMA + T1S nicht kann

Klare Grenzen der Technologie:

- **High-Throughput-Anwendungen** — 10 Mbit/s mit ACMA-Overhead
  bedeutet ~5-7 Mbit/s effektiv pro Bus, geteilt auf alle Knoten.
  Für Video-Streaming oder Bulk-Daten-Transfer ist das zu wenig.
- **Drahtlose Synchronisation** — ACMA ist kabel-gebunden
- **Standardkonformität für AVB-Bridges** — siehe
  [readme_802_1as_roadmap.md](readme_802_1as_roadmap.md), keine
  vendor-übergreifende Interop ohne IEEE 802.3da
- **Datenmengen über die Bus-Bandbreite hinaus** — typische
  Sample-Rates für hochauflösende ADCs (z. B. 24-bit @ 100 kSPS pro
  Kanal × 8 Kanäle = 19 Mbit/s) passen nicht in einen 10 Mbit/s-Bus
- **Multi-Vendor-Bus-Setups** — andere T1S-Vendoren (NXP, Onsemi,
  Marvell, ADI) haben kein ACMA-Pendant; gemischte Knoten würden
  ungated weitersenden und ACMA-Slots stören (siehe
  [readme_acma.md §13.2](readme_acma.md#132-was-die-anderen-vendoren-haben-oder-nicht))

---

## 11. Microchip's Markt-Strategie

Mit ACMA als Alleinstellungsmerkmal positioniert Microchip
10BASE-T1S als **kostengünstige TSN-Light-Alternative** für
Anwendungen, die heute typisch teure 100-Mbit-AVB/TSN-Lösungen
brauchen würden.

**Marktlogik:**

- Standardisiertes TDMA für 10BASE-T1S Multidrop existiert nicht
  (IEEE 802.3cg deckt nur PLCA + CSMA/CD, IEEE 802.1Qbv-TAS gibt's
  nur in TSN-Switches)
- Andere T1S-Vendoren bieten kein TDMA-Pendant
- **Microchip ist allein mit ACMA in dieser Nische**
- Wer TDMA auf T1S braucht, kommt **automatisch** zu Microchip

Diese Markt-Position macht ACMA-Wegfall ökonomisch unwahrscheinlich
— Microchip würde damit direkt Marktanteile in einem hochwertigen
Industrie-/Automotive-Segment verlieren.

Das Spektrum der oben gelisteten Anwendungen reicht von
Smart-Building über Industrie 4.0 bis Automotive-Backbone und
funktional-sichere Avionik — eine breite Anwendungs-Palette, die
Microchip's Investition in ACMA wirtschaftlich rechtfertigt und
nahelegt, dass das Feature in nachfolgenden Si-Generationen
beibehalten wird.

---

**Stand 2026-04-27.**  Diese Liste ist nicht erschöpfend — neue
Anwendungen für deterministischen TDMA-Bus-Zugriff entstehen mit
jedem zusätzlichen Industrie-Adoption von 10BASE-T1S. Bei
relevanten neuen Anwendungsfällen die entsprechenden Kapitel
ergänzen.
