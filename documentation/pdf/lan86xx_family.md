# LAN86xx Familie — Übersicht

Microchip's 10BASE-T1S Silicon-Familie umfasst zwei Klassen — integrierte
MAC-PHYs und standalone PHYs — mit insgesamt sieben aktuellen Bausteinen.
Diese Datei dient als Schnell-Referenz, um zu entscheiden welcher Chip
für welchen Anwendungsfall passt, welche Features wo verfügbar sind und
welches Dokument zu welchem Chip gehört.

**Erstellt:** 2026-04-27
**Bezugs-Dokumente:** Siehe [readme_pdf.md](readme_pdf.md) für vollständige
PDF-Referenzen mit Section- und Page-Hinweisen.

---

## Inhaltsverzeichnis

1. [Hauptmerkmale](#1-hauptmerkmale)
2. [Silicon-Revisionen (Stand 2025)](#2-silicon-revisionen-stand-2025)
3. [Feature-Matrix](#3-feature-matrix)
4. [Elektrische Spezifikationen](#4-elektrische-spezifikationen)
5. [Errata-Übersicht](#5-errata-übersicht)
   - 5.1 [LAN8650/1 — ER80001075](#51-lan86501--er80001075-9-items)
   - 5.2 [LAN8670/1/2 — ER80000962](#52-lan867012--er80000962-12-items)
   - 5.3 [Cross-Family-Vergleich](#53-cross-family-vergleich-analoge-items)
   - 5.4 [Items mit ungelöster Wirkung in 2026](#54-items-mit-ungelöster-wirkung-in-2026)
   - 5.5 [Gesamt-Matrix — Errata × Chip-Varianten](#55-gesamt-matrix--errata--chip-varianten)
6. [Dokumentations-Querverweise](#6-dokumentations-querverweise)
7. [Familien-übergreifende Dokumente](#7-familien-übergreifende-dokumente)
8. [Auswahl-Hilfe nach Anwendungsfall](#8-auswahl-hilfe-nach-anwendungsfall)
9. [Häufige Verwechslungen](#9-häufige-verwechslungen)
10. [Quellen](#10-quellen)

---

## 1. Hauptmerkmale

| Chip | Klasse | Package | Host-Interface | Integrierte LDO | AEC-Q100 | Pin-Kompatibel mit |
|---|---|---|---|---|---|---|
| **LAN8650** | MAC-PHY (integriert) | 32-VQFN | SPI (OA-TC6) | nein (1.8 V extern) | ja | LAN8651 (32-VQFN) |
| **LAN8651** | MAC-PHY (integriert) | 32-VQFN | SPI (OA-TC6) | **ja (1.8 V intern)** | ja | LAN8650 |
| **LAN8670** | Standalone PHY | 32-VQFN | MII / SC-MII / RMII | n/a | ja | – |
| **LAN8671** | Standalone PHY | 24-VQFN | MII / SC-MII / RMII | n/a | ja | – |
| **LAN8672** | Standalone PHY | 36-VQFN | MII / SC-MII (kein RMII) | n/a | ja | – |

---

## 2. Silicon-Revisionen (Stand 2025)

| Chip | Aktuelle Si-Rev | Frühere Si-Revs | Errata-Dokument |
|---|---|---|---|
| LAN8650 | B1 (0x0010) | B0 (0x0001) | ER80001075 |
| LAN8651 | B1 (0x0010) | B0 (0x0001) | ER80001075 |
| LAN8670 | D0 (0x0110) | C2 (0x0101), C1 (0x0100), B1 (0x0010) | ER80000962 |
| LAN8671 | D0 (0x0110) | C2 (0x0101), C1 (0x0100), B1 (0x0010) | ER80000962 |
| LAN8672 | C2 (0x0101) | B1 (0x0010) | ER80000962 |

---

## 3. Feature-Matrix

| Feature | LAN8650 | LAN8651 | LAN8670 | LAN8671 | LAN8672 |
|---|:---:|:---:|:---:|:---:|:---:|
| Integrierter MAC | ✅ | ✅ | ❌ | ❌ | ❌ |
| Integrierter 10BASE-T1S PHY | ✅ | ✅ | ✅ | ✅ | ✅ |
| Integrierte 1.8 V LDO | ❌ | ✅ | ❌ | ❌ | ❌ |
| OA-TC6 SPI Host-Interface | ✅ | ✅ | ❌ | ❌ | ❌ |
| MII Host-Interface | ❌ | ❌ | ✅ | ✅ | ✅ |
| RMII Host-Interface | ❌ | ❌ | ✅ | ✅ | ❌ |
| SC-MII Host-Interface | ❌ | ❌ | ✅ | ✅ | ✅ |
| SMI Management Interface | ❌ | ❌ | ✅ | ✅ | ✅ |
| PLCA (IEEE 802.3 Clause 148) | ✅ | ✅ | ✅ | ✅ | ✅ |
| ACMA (TDMA-fähig) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Credit-Based Traffic Shaping | ✅ | ✅ | ✅ | ✅ | ✅ |
| TSU / Wall Clock (94-bit) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Packet Timestamping (Pattern Matcher) | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4× Event Capture | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4× Event Generators | ✅ | ✅ | ✅ | ✅ | ✅ |
| 1PPS Output (DIOA4) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Phase Adjuster | ✅ | ✅ | ✅ | ✅ | ✅ |
| Topology Discovery (Rev D0+) | ❌ | ❌ | ✅ | ✅ | ❌ |
| SQI (Signal Quality Indicator) | ✅ | ✅ | ✅ | ✅ | ✅ |
| DCQ SQI (Dynamic Channel Quality, Rev D0+) | ❌ | ❌ | ✅ | ✅ | ❌ |
| Cable Fault Diagnostics | ✅ | ✅ | ✅ | ✅ | ✅ |
| Harness Defect Detection (Rev D0+) | ❌ | ❌ | ✅ | ✅ | ❌ |
| Sleep Mode + Wake (Rev D0 erweitert) | ✅ | ✅ | ✅ | ✅ | ✅ |
| INH Pin Support | ✅ | ✅ | ✅ | ✅ | ✅ |
| WAKE_IN / WAKE_OUT Pins | ✅ | ✅ | ✅ | ✅ | ✅ |
| ISO 26262 Safety Package | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 4. Elektrische Spezifikationen

| Parameter | LAN8650/1 | LAN8670/1/2 |
|---|---|---|
| Versorgung digital (VDDP) | 3.3 V | 3.3 V |
| Versorgung analog (VDDA) | 3.3 V | 3.3 V |
| Versorgung kontinuierlich (VDDAU) | 3.3 V | 3.3 V |
| Interner LDO-Output (LAN8651/keiner) | 1.8 V | n/a |
| Referenz-Clock (XTI) | 25 MHz | 25 MHz |
| Temperaturbereich | -40 bis +125 °C | -40 bis +125 °C |
| MDI Datenrate | 10 Mbit/s | 10 Mbit/s |
| Max. Bus-Länge (IEEE-Baseline) | 25 m | 25 m |
| Max. Knoten-Anzahl (IEEE-Baseline) | 8 | 8 |
| Mit AN60001829 erweitert | ~50 Knoten / 50 m | ~50 Knoten / 50 m |

---

## 5. Errata-Übersicht

Beide Familien haben eigene Errata-Dokumente mit unterschiedlichen
Item-Nummerierungen. LAN8650/1 hat **9 Items** (s1-s9), LAN8670/1/2
hat **12 Items** (s1-s12). Einige Items sind funktional analog, aber
die Nummerierung stimmt **nicht** überein.

### 5.1 LAN8650/1 — ER80001075 (9 Items)

Silicon-Revisionen B0 (0x0001) und B1 (0x0010).

| Item | Thema | Betrifft | Workaround |
|---|---|---|---|
| **s1** | OA_PHYID identifiziert nur den PHY-Block, nicht das MAC-PHY-Produkt | B0, B1 | DEVID bei MMS 10 / 0x0094 lesen statt OA_PHYID |
| **s2** | Useless RX data block following early CS_N de-assertion | B0 only | Resolved B1 — keine Aktion nötig auf B1 |
| **s3** | SPI receive Ethernet frame transfer halt | B0 only | Resolved B1 — keine Aktion nötig auf B1 |
| **s4** | TX halts on excessive collisions | B0, B1 | PLCA verwenden (keine Kollisionen); oder in CSMA/CD nur 1 Frame pro Chunk senden |
| **s5** | Multi-Coordinator BEACON setzt UNEXPB; Knoten geht in Recovery-State | B0, B1 | Software muss UNEXPB monitoren und PHY als Follower rekonfigurieren |
| **s6** | SLPCAL field of SLPCTL0 muss immer als 0 geschrieben werden | B0, B1 | Bei jedem SLPCTL0-Write (auch RMW) SLPCAL-Bits explizit als 0 maskieren |
| **s7** | Coordinator stoppt beacons nicht bei Sleep-Entry | B0, B1 | NODE_ID=0 nie auf Inactivity-Sleep konfigurieren; PLCA_CTRL0.EN=0 vor Sleep-Entry |
| **s8** | TO_TMR ≥ 29 (default 32) für Rauschimmunität | B0, B1 | Default 32 belassen; in EMI-Stress-Umgebung evtl. erhöhen |
| **s9** | Event Generator periodic-mode driftet relativ zur sync. Wall Clock | B0, B1 | Single-Shot-Mode (REP=0) mit Software-Re-Arm pro Pulse — siehe [readme_acma.md §7.6](../ptp/readme_acma.md) |

### 5.2 LAN8670/1/2 — ER80000962 (12 Items)

Silicon-Revisionen B1, C1, C2, D0.

| Item | Thema | Betrifft | Workaround |
|---|---|---|---|
| **s1** | RMII Identification | B1 only | Resolved C1 — keine Aktion nötig ab C1 |
| **s2** | Package Type Identification (LAN8671) | B1 only | Resolved C1 |
| **s3** | RMII CSMA/CD operation in mixed PLCA segments | B1, C1, C2 | Resolved D0; vor D0: RMII nur wenn alle Knoten CSMA/CD; LAN8672 hat kein RMII |
| **s4** | Incorrect reset indication on IRQ_N | B1 only | Resolved C1 |
| **s5** | Multi-Coordinator PLCA action (analog LAN8650/1 s5) | alle Revs | UNEXPB monitoren, PHY als Follower rekonfigurieren |
| **s6** | Transmission of collision fragments with PLCA + RMII | **alle Revs inkl. D0** | RMW MMD 0x1F / 0x008F = 0x00E0 mit Maske 0x07F0; reduziert PLCA delay-line buffer |
| **s7** | Incorrect Carrier Sense in RMII nach logischer Kollision | B1, C1 | Resolved C2 |
| **s8** | Revert to CSMA/CD when PLCA Beacons missing | B1 only | Resolved C1 |
| **s9** | Packet pattern matcher matches all message types | B1 only | Resolved C1 |
| **s10** | SLPCAL field of SLPCTL0 (analog LAN8650/1 s6) | C1, C2 | Resolved D0; vor D0: bei SLPCTL0-Writes SLPCAL als 0 maskieren |
| **s11** | Coordinator beacon stoppt nicht bei Sleep (analog LAN8650/1 s7) | C1, C2, D0 | Wie bei LAN8650/1: NODE_ID=0 nie auf Inactivity-Sleep; PLCA_CTRL0.EN=0 vor Sleep |
| **s12** | Noise immunity vs carrier sense latency trade-off (analog LAN8650/1 s8) | C1, C2 | Default TO_TMR belassen |

### 5.3 Cross-Family-Vergleich (analoge Items)

Mehrere Errata-Items haben funktionales Pendant in der jeweils
anderen Familie — die Item-Nummern stimmen aber nicht überein:

| Beschreibung | LAN8650/1 (ER80001075) | LAN8670/1/2 (ER80000962) | Status |
|---|:---:|:---:|---|
| Multi-Coordinator BEACON / UNEXPB | s5 | s5 | identisch — selbe Item-Nummer |
| SLPCAL muss als 0 geschrieben werden | s6 | s10 | analog — andere Item-Nummer |
| Coordinator beacon stoppt nicht bei Sleep | s7 | s11 | analog — andere Item-Nummer |
| TO_TMR ≥ 29 für Rauschimmunität | s8 | s12 | analog — andere Item-Nummer |

Items **ohne Pendant** (chipspezifisch):

- LAN8650/1 **s1** (OA_PHYID-Lesen) — LAN8670/1/2 hat anderes ID-Schema (PHY_ID1/ID2-Register)
- LAN8650/1 **s4** (TX-Halt bei Excessive Collisions) — LAN8670/1/2 erwähnt das nicht (vermutlich anderes MAC-Verhalten oder bereits in Si-Rev gelöst)
- LAN8650/1 **s9** (EG periodic-mode drift) — **bemerkenswert**: dieses Item ist **nicht** im LAN8670/1/2 Errata enthalten. Möglich dass die EG-Implementierung dort anders ist oder das Verhalten anderswo beschrieben wird (in DS60001573 § Time Synchronization). **Vor Verwendung des EG periodic-mode auf LAN8670/1/2 verifizieren.**
- LAN8670/1/2 **s1, s2, s3, s4, s7, s8, s9** — alle RMII- und Identification-bezogen, beim integrierten LAN8650/1 nicht relevant (kein RMII-Interface)
- LAN8670/1/2 **s6** (Collision fragments mit PLCA+RMII) — RMII-spezifisch, nicht auf LAN8650/1 anwendbar

### 5.4 Items mit ungelöster Wirkung in 2026

Diese Items betreffen **die aktuell produzierten Si-Revs** und bleiben
für jeden Designer dauerhaft relevant:

| Familie | Items | Zusammenfassung |
|---|---|---|
| LAN8650/1 (B1) | s1, s4, s5, s6, s7, s8, s9 | Beacon-Sleep-Konflikt, EG-Periodic-Drift, Multi-Coordinator-Recovery, plus die RMW-Masking-Regeln |
| LAN8670/1/2 (D0) | s5, s6, s11 | Multi-Coordinator (s5), Collision Fragments mit RMII (s6), Beacon-Sleep-Konflikt (s11) |
| LAN8672 (C2) | s5, s6, s10, s11, s12 | C2 ist *nicht* die neueste Rev — D0 fixt s10 weiter |

**Praktische Bedeutung für PTP / ACMA:**

- **LAN8650/1 s9** ist der zentrale Punkt für die ACMA-Architektur in
  [readme_acma.md](../ptp/readme_acma.md) — zwingt Single-Shot-EG mit
  ISR-Re-Arm.
- **LAN8650/1 s7 + LAN8670/1/2 s11** dictieren das PLCA→ACMA-
  Übergangs-Protokoll (PLCA_CTRL0.EN=0 vor jedem Mode-Change, der mit
  Sleep oder Beacon-Stop einhergeht).
- **LAN8650/1 s5 + LAN8670/1/2 s5** sind relevant für jedes
  Multi-Master-Setup — Software muss UNEXPB konstant monitoren.
- **LAN8650/1 s8 + LAN8670/1/2 s12** binden alle Knoten am Bus an
  identischen TO_TMR-Wert — wichtig wenn unterschiedliche Si-Revs
  oder Familien gemischt werden.

### 5.5 Gesamt-Matrix — Errata × Chip-Varianten

Die folgende Matrix vereint alle Errata-Items beider Familien in
einer einzigen Tabelle. Die Zeilen sind Errata-Themen (mit den
jeweiligen Item-Nummern beider Familien wo zutreffend), die Spalten
sind die einzelnen Chip-Variante × Si-Rev-Kombinationen.

**Lesart der Zellen:**

- **<span style="color:#c00;font-weight:bold">✗</span>** — Errata ist in dieser Si-Rev **aktiv** (Workaround nötig)
- **<span style="color:#0a0;font-weight:bold">✓</span>** — Errata wurde in dieser Si-Rev **behoben** (silicon-fixed)
- **—** — nicht zutreffend (Errata gehört zu anderer Familie, oder
  Si-Rev existiert für diesen Chip nicht, oder Item wurde in dieser
  Rev nicht gelistet)

| Errata-Thema | 8650/1<br/>B0 | 8650/1<br/>B1 | 8670/1<br/>B1 | 8670/1<br/>C1 | 8670/1<br/>C2 | 8670/1<br/>D0 | 8672<br/>B1 | 8672<br/>C2 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **PHYID-Register identifiziert nur PHY-Block** *(8650/1 s1)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | — | — | — | — | — |
| **RX-Datenblock-Bug nach early CS_N** *(8650/1 s2)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — | — | — | — | — |
| **SPI RX-Frame-Transfer-Halt** *(8650/1 s3)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — | — | — | — | — |
| **TX-Halt bei excessive collisions** *(8650/1 s4)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | — | — | — | — | — |
| **RMII-Mode-Identifikation** *(8670/1 s1)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — |
| **Package-Type-Identifikation** *(LAN8671 only, 8670/1 s2)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — |
| **RMII CSMA/CD in mixed PLCA segments** *(8670/1 s3)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — |
| **Falsche Reset-Indikation auf IRQ_N** *(8670/1/2 s4)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> |
| **Multi-Coordinator BEACON / UNEXPB** *(8650/1 s5 / 8670/1/2 s5)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> |
| **Collision-Fragmente mit PLCA + RMII** *(8670/1 s6)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | — |
| **Carrier Sense in RMII nach Kollision** *(8670/1 s7)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | — |
| **Revert zu CSMA/CD bei fehlenden BEACONs** *(8670/1/2 s8)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> |
| **Pattern Matcher matcht alle Message-Types** *(8670/1/2 s9)* | — | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#0a0;font-weight:bold">✓</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> |
| **SLPCAL-Feld muss als 0 geschrieben werden** *(8650/1 s6 / 8670/1/2 s10)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#0a0;font-weight:bold">✓</span> | — | <span style="color:#c00;font-weight:bold">✗</span> |
| **Coordinator stoppt Beacons nicht bei Sleep-Entry** *(8650/1 s7 / 8670/1/2 s11)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | <span style="color:#c00;font-weight:bold">✗</span> |
| **TO_TMR ≥ 29 / Rauschimmunität-Trade-off** *(8650/1 s8 / 8670/1/2 s12)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | — | <span style="color:#c00;font-weight:bold">✗</span> |
| **Event Generator Periodic-Mode-Drift vs Wall Clock** *(8650/1 s9)* | <span style="color:#c00;font-weight:bold">✗</span> | <span style="color:#c00;font-weight:bold">✗</span> | — | — | — | — | — | — |

**Was die Matrix sichtbar macht:**

- **Vertikale Lese-Richtung:** für eine bestimmte Chip × Si-Rev sieht
  man auf einen Blick, welche Errata-Items in genau dieser Variante
  aktiv sind. Beispiel: LAN8650/1 B1 hat 7 aktive Items (s1, s4, s5,
  s6, s7, s8, s9 — alle <span style="color:#c00;font-weight:bold">✗</span> in der B1-Spalte).

- **Horizontale Lese-Richtung:** für ein gegebenes Errata-Thema sieht
  man, welche Chip-Varianten betroffen oder bereits gefixt sind.
  Beispiel: das Multi-Coordinator-PLCA-Verhalten (s5 in beiden
  Familien) ist in **allen** Chip-Varianten und **allen** Si-Revs
  aktiv — keine einzige Variante hat es behoben.

- **Familien-übergreifende Themen** (Multi-Coordinator, SLPCAL,
  Sleep-Beacon, TO_TMR) sind mit beiden Item-Nummern beschriftet.
  Damit lässt sich die Asymmetrie der Item-Nummerierung zwischen
  den Familien direkt nachvollziehen.

- **Aktuell verkaufte Si-Revs** (LAN8650/1 B1, LAN8670/1 D0,
  LAN8672 C2) konzentrieren das praktisch relevante Risiko:
  - LAN8650/1 B1 hat 7 aktive Items — der Workaround-Aufwand für
    Designer ist erheblich
  - LAN8670/1 D0 hat nur 4 aktive Items (s3 wurde gefixt, viele
    RMII-Items sind silicon-resolved)
  - LAN8672 C2 hat 4 aktive Items, aber andere als LAN8670/1 D0
    (kein Topology Discovery, kein RMII)

- **Das EG-Periodic-Mode-Drift-Item (LAN8650/1 s9)** ist die einzige
  Errata-Zeile, die in **keiner** anderen Chip-Variante auftaucht —
  weder als <span style="color:#c00;font-weight:bold">✗</span> noch als <span style="color:#0a0;font-weight:bold">✓</span>. Das stützt die Vermutung, dass entweder
  die EG-Implementierung in der LAN8670/1/2-Familie fundamental
  anders aufgebaut ist, oder das Verhalten dort früh silicon-fixed
  wurde, bevor es jemals in einem Errata-Dokument auftauchte.

---

## 6. Dokumentations-Querverweise

| Chip | Datasheet | Configuration AN | Errata | Hardware Checklist |
|---|---|---|---|---|
| LAN8650/1 | DS60001734 | AN60001760 | ER80001075 | (in DS60001734 §8) |
| LAN8670/1/2 | DS60001573 | AN60001699 | ER80000962 | UG60001745 |

Vollständige Datei-Referenzen mit Section- und Seiten-Hinweisen
in [readme_pdf.md](readme_pdf.md).

---

## 7. Familien-übergreifende Dokumente

| Dokument | Gilt für |
|---|---|
| AN60001847 (Time Synchronization) | LAN8650/1 (TSU dokumentiert; LAN8670/1/2 hat dieselbe Hardware aber DS60001573 §4.12 ist die Referenz) |
| AN00006067 (Topology Discovery) | LAN8670/1/2 D0+ (LAN8650/1 hat **kein** Topology Discovery) |
| AN60001718 (BIN Reference Design) | alle LAN86xx |
| AN00006174 (Layout Guide) | alle LAN86xx |
| AN60001829 (Intrinsic Noise) | alle LAN86xx |
| AN60001848 (PoDL) | LAN8650/1, LAN8670/1 (LAN8672 nicht erwähnt) |
| AN00005990 (Linux Driver) | LAN865x (also LAN8650 + LAN8651) |
| AN00006170 (Zephyr Driver) | LAN8651 |
| AN00006120 (EVB-RMII Linux) | LAN8670 EVB |
| AN00006247 (EVB-RMII Zephyr) | LAN8670 EVB |

---

## 8. Auswahl-Hilfe nach Anwendungsfall

| Anwendungsfall | Empfehlung | Grund |
|---|---|---|
| MCU mit eigenem MAC + standard Ethernet-Treiber | **LAN8670** (32-VQFN) oder **LAN8671** (24-VQFN, kleiner) | MII/RMII direkt am MCU |
| MCU ohne MAC, möglichst kompakt | **LAN8650** oder **LAN8651** | Integrierter MAC, SPI-Host-Interface, weniger Pins |
| Niedrigere Versorgungs-Komplexität (nur 3.3 V) | **LAN8651** | Internes 1.8 V LDO erspart externen Regulator |
| RMII-Host (z.B. Raspberry Pi mit Standard-PHY-Treiber) | **LAN8670** oder **LAN8671** | LAN8672 unterstützt **kein** RMII |
| Topology Discovery / Per-Knoten-Distanz-Messung | **LAN8670/1 D0+** | Hardware-Feature nicht im LAN8650/1 |
| TDMA via ACMA + PTP-Sync | **LAN8650/1** oder **LAN8670/1/2** | ACMA in beiden Familien identisch |
| Funktional-sichere Anwendung (ISO 26262) | **alle** unterstützen es | Safety Package via Microchip Support |
| Produktion 2026: aktuellste Si-Rev | LAN8650/1 **B1**, LAN8670/1 **D0**, LAN8672 **C2** | Errata-Items minimiert |

---

## 9. Häufige Verwechslungen

- **LAN865x ist ein Sammelbegriff** für LAN8650 und LAN8651 (integrierte
  MAC-PHYs); **LAN867x** für LAN8670, LAN8671, LAN8672 (standalone
  PHYs); **LAN86xx** schließt beide Familien ein.
- **LAN8670 und LAN8651** haben unterschiedliche Pinouts und
  unterschiedliche Konfigurations-Sequenzen, obwohl der PHY-Kern
  identisch ist.
- **LAN8672 unterstützt kein RMII** — wichtig wenn ein Design RMII
  voraussetzt.
- **Topology Discovery existiert nur** im LAN8670/1 ab Si-Rev D0;
  LAN8650/1 und LAN8672 haben es **nicht**.
- **Die Errata-Items sind familien-spezifisch** und nicht direkt
  übertragbar — z. B. LAN8650/1 erratum s9 (EG periodic-mode drift)
  ist im LAN8670/1/2 errata-Dokument **nicht enthalten** (vermutlich
  weil dort anders implementiert oder via Si-Rev gefixt).

---

## 10. Quellen

Diese Tabelle basiert auf den folgenden Microchip-Dokumenten:

- DS60001734F — LAN8650/1 Datasheet (2025)
- DS60001573K — LAN8670/1/2 Datasheet (2025)
- ER80001075F — LAN8650/1 Errata (2025)
- ER80000962G — LAN8670/1/2 Errata (2025)
- AN60001760G — LAN8650/1 Configuration AN (2024)
- AN60001699G — LAN8670/1/2 Configuration AN (2025)
- UG60001745D — LAN8670/1/2 HW Checklist (2024)

Lokale Kopien der oben genannten Dokumente liegen in
[documentation/pdf/](.) und sind in [readme_pdf.md](readme_pdf.md)
indexiert.

---

**Stand 2026-04-27.**  Wenn neue Si-Revs oder zusätzliche Familien-
Mitglieder erscheinen, diese Tabelle entsprechend pflegen.
