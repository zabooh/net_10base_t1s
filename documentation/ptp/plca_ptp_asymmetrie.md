# PLCA-Slot-Asymmetrie und PTP-Kompensation auf 10BASE-T1S

## Übersicht

Dieses Dokument erklärt, warum Standard-PTP (IEEE 1588) auf 10BASE-T1S Multidrop-Bussen mit PLCA nicht direkt funktioniert und welche zusätzlichen Mechanismen IEEE 802.1AS-2020 Annex H definiert.

---

## 1. Der relevante Standard

**IEEE 802.1AS-2020, Annex H**

- **IEEE 802.1AS** = gPTP (generalized PTP) — Profil von IEEE 1588 für zeitkritische Bridged Networks
- **Annex H** wurde 2020 hinzugefügt, um T1S mit PLCA zu adressieren
- Definiert Anpassungen an Pdelay-Mechanismus und Sync-Logik für Multidrop-Bus-Topologien

**Verwandte Standards:**

| Standard | Inhalt |
|----------|--------|
| IEEE 802.3cg-2019 | 10BASE-T1S PHY-Layer + PLCA |
| IEEE 1588-2008/2019 | Ursprünglicher PTPv2-Standard |
| IEEE 802.1AS-2020 | gPTP mit Annex H für T1S |
| OPEN Alliance TC10/TC14 | Automotive-spezifische Erweiterungen |

---

## 2. Das Grundproblem

### Standard-PTP-Annahmen

Standard-PTP geht von **Punkt-zu-Punkt-Verbindungen** mit folgenden Eigenschaften aus:

- Full-Duplex (separate TX/RX-Lanes)
- Symmetrische Latenz in beide Richtungen
- Konstante, deterministische Übertragungszeit
- Switch/Bridge entscheidet über Medium-Zugriff

### Was T1S anders macht

| Eigenschaft | Standard-Ethernet | 10BASE-T1S |
|-------------|------------------|------------|
| Topologie | Punkt-zu-Punkt | Multidrop-Bus |
| Duplex | Full-Duplex | Half-Duplex |
| Medium-Zugriff | Switch / CSMA/CD | PLCA Token-Passing |
| Knoten pro Segment | 2 | bis zu 8 |

PLCA = **Physical Layer Collision Avoidance**: Token-Passing-Mechanismus im PHY, der Slot-basierten Zugriff regelt.

---

## 3. Wo der Timestamp genommen wird

### Standard-PTP-Definition

Der Timestamp wird beim **Start of Frame Delimiter (SFD)** genommen — am Medium, nicht in Software.

### Auf klassischem Ethernet

```
Sender:    Application → MAC → PHY → SFD auf Draht → Timestamp t1
Empfänger: SFD auf Draht → Timestamp t2 → PHY → MAC → Application
```

Klar definierter Punkt, keine Ambiguität.

### Auf T1S mit PLCA

```
Sender:    MAC fertig → warten auf PLCA-Slot → SFD auf Draht
                       ↑
                       Hier kann beliebige Wartezeit liegen
```

**Kritische Frage:** Wann wird der TX-Timestamp genommen?

- **Option A:** Wenn die MAC den Frame fertig hat (vor PLCA-Wartezeit)
- **Option B:** Wenn der SFD tatsächlich auf dem Draht erscheint

**Antwort des Standards (Annex H):** Option B — Timestamp am echten SFD-Moment auf dem Medium.

### Warum das so wichtig ist

```
MAC sagt "fertig" bei t = 100 ms
PLCA-Wartezeit: 5 ms (Slot kommt erst bei 105 ms)
SFD auf Draht:  t = 105 ms

Falsch (Option A): Timestamp = 100 ms
Richtig (Option B): Timestamp = 105 ms

Fehler bei Option A: 5 ms — komplett zerstört PTP-Genauigkeit
```

PTP zielt auf Sub-Mikrosekunden-Genauigkeit. Ein 5-ms-Fehler ist 5000× zu groß.

### Lösung im LAN8651

Der LAN8651 implementiert das **richtig in Hardware**:

- TSU (Timestamp Unit) sitzt direkt am PHY-Medium-Interface
- Schnappt den Timestamp genau beim SFD auf dem Draht
- PLCA-Wartezeit ist automatisch im Timestamp enthalten

Die `MAC_TSH/TSL`-Register liefern diese korrekten Timestamps.

---

## 4. Die zusätzliche Asymmetrie bei mehr als 2 Knoten

Selbst mit korrektem SFD-Timestamping entstehen **bei 3+ Knoten** zusätzliche Probleme.

### Bei 2 Knoten: symmetrisch

```
Knoten A (PLCA ID 0)  ←→  Knoten B (PLCA ID 1)
```

Im PLCA-Zyklus wechseln sich beide ab. Wartezeiten sind im Mittel symmetrisch verteilt. Pdelay-Standard-Annahme `delay = round_trip / 2` funktioniert.

### Bei 3+ Knoten: asymmetrisch

```
Knoten A (ID 0)  ←→  Knoten B (ID 1)  ←→  Knoten C (ID 2)
```

PLCA-Zyklus:
- Slot 0: A darf senden
- Slot 1: B darf senden
- Slot 2: C darf senden
- Wiederholung

#### Beispiel: A misst Pdelay zu C

**Schritt 1:** A sendet `Pdelay_Req` an C
- A wartet im Mittel halben Zyklus auf Slot 0
- A sendet, C empfängt

**Schritt 2:** C antwortet mit `Pdelay_Resp`
- C muss von Slot 2 zu Slot 0 warten — fast einen ganzen Zyklus
- C sendet

**Resultat:**

```
A → C:  ~0,5 Slots Wartezeit + Übertragungszeit
C → A:  ~2 Slots Wartezeit + Übertragungszeit
```

Standard-gPTP rechnet `path_delay = round_trip / 2`. Diese Annahme ist falsch, weil die Richtungen unterschiedliche Wartezeiten haben.

### Die Asymmetrie skaliert

**Bei 8 Knoten (T1S-Maximum):**

```
PLCA-Zyklus-Dauer:                30-50 µs (typisch)
Maximale Slot-Wartezeit:           ~7 × Slot-Zeit
Asymmetrie zwischen ID 0 und 7:   bis zu 50 µs
```

PTP-Ziel: < 1 µs Genauigkeit. 50 µs Asymmetrie wäre 50× zu groß.

**Ohne Annex-H-Kompensation ist PTP auf Multidrop-T1S unbrauchbar.**

---

## 5. Naive Kompensation reicht nicht

### Die einfache Formel

Wenn jeder Knoten seine eigene NodeID und die Max-NodeID kennt, könnte man theoretisch die erwartete Slot-Wartezeit berechnen:

```
slot_wartezeit(target, current) = 
    ((target - current + max_id + 1) MOD (max_id + 1)) × slot_dauer
```

**Beispiel mit 8 Knoten, slot_dauer = 20 µs:**

A (ID=0) sendet an C (ID=2):
- Wartezeit für A: ~0 (Slot ist gleich da)

C antwortet an A:
- Wartezeit für C: (0 - 2 + 8) mod 8 = 6 Slots = 120 µs

Kompensation:
```
echter_path_delay = (round_trip - bekannte_PLCA_wartezeiten) / 2
```

### Warum die einfache Formel nicht ausreicht

Die statische Berechnung ist nur eine **Obergrenze**, nicht der echte Wert.

#### Problem 1: Slot-Skipping

PLCA überspringt leere Slots:

```
Konfiguriert: A-B-C-D-E-F-G-H = 8 Slots × 20µs = 160µs Zyklus

Wenn B+D+F nichts senden:
Effektiv: A-C-E-G-H = 5 Slots × 20µs = 100µs Zyklus
```

Aus Sicht von G hat sich die effektive Slot-Position verschoben.

#### Problem 2: Burst-Modus

PLCA erlaubt mehrere Frames pro Slot (`max_burst_count`). Variable Auslastung verändert Zyklus-Dauer.

#### Problem 3: Variable Bus-Auslastung

Mal sind alle Knoten aktiv, mal nur zwei. Die theoretische Berechnung passt dann nicht exakt.

#### Problem 4: Beacon-Timing

PLCA-Beacon vom Coordinator und Cycle-Beginn können sich unterscheiden. Bei Beacon-Verlust kann sich die Phase verschieben.

---

## 6. Was Annex H tatsächlich definiert

Annex H ist **realistischer** als die einfache Formel und nutzt mehrere Mechanismen kombiniert:

### 6.1 PLCA Beacon Timestamp

- Beacon-Zeitpunkt wird als zusätzliche Referenz genutzt
- Pdelay wird auf den Beacon bezogen, nicht auf den absoluten SFD
- Reduziert Abhängigkeit von Slot-Skipping

### 6.2 Cycle Time Tracking

- Mechanismen zur Schätzung der **aktuellen** Zyklus-Dauer
- Berücksichtigt aktive Knoten und Burst-Counts in Echtzeit

### 6.3 Konservative Filterung

- Pdelay-Werte mit hoher Varianz werden verworfen
- Statistische Filterung über mehrere Messungen
- Konvergiert auf den stabilen Anteil der Latenz

### 6.4 Per-Hop Approach

- Pdelay wird zwischen direkten Nachbarn gemessen
- Statt End-to-End, was bei Multidrop unklar definiert ist
- PLCA-Effekte sind pro Hop besser kontrollierbar

### 6.5 NodeID-abhängige Bias-Kompensation

- Berücksichtigt PLCA-NodeID-Position
- Schätzt erwartete Slot-Wartezeit basierend auf Beobachtung

---

## 7. Was Hardware-PHY zur Verfügung stellen muss

Für eine echte Annex-H-Implementierung braucht man Laufzeit-Information vom PHY.

### LAN8651-Register (Beispiel)

| Register-Typ | Information |
|--------------|-------------|
| PLCA-Status | Aktueller Slot, NodeID, Burst-Count |
| Beacon-Detection | Zeitstempel der letzten Beacon-Erkennung |
| PLCA-Statistics | Gezählte Slots, Empty-Slots, Burst-Events |
| TSU-Register | SFD-Timestamps (TX/RX) |

### Was im aktuellen Projekt genutzt wird

Im 2-Knoten-Setup werden die Standard-TSU-Register genutzt:
- `MAC_TSH` / `MAC_TSL` für SFD-Timestamps
- TTSCAA-Bit für TX-Capture-Confirmation

Die PLCA-Status-Register werden nicht ausgelesen, weil bei 2 Knoten keine Annex-H-Kompensation nötig ist.

---

## 8. Praktische Implementierungs-Strategien

### Variante 1: Statische Worst-Case-Annahme

Verwende die einfache Formel:
```
echter_delay = round_trip / 2 - statische_PLCA_kompensation
```

**Anwendbar wenn:**
- Topologie und Verkehrsmuster sind stabil
- Anforderung ist "gut genug" (10-50 µs Genauigkeit)
- Implementierungs-Aufwand muss minimal bleiben

**Nicht anwendbar wenn:**
- Sub-µs-Genauigkeit gefordert
- Bus-Auslastung schwankt stark
- Knoten kommen und gehen

### Variante 2: Statistische Mittelung

Über viele Pdelay-Messungen mitteln. Slot-Skipping mittelt sich heraus, wenn der Verkehr stationär ist.

**Vorteile:** Einfach zu implementieren, funktioniert ohne PLCA-Status-Register

**Nachteile:** Lange Konvergenzzeit (Sekunden bis Minuten), schlechte Reaktion auf Topologie-Änderungen

### Variante 3: Vollständige Annex-H-Implementierung

PLCA-Status pro Frame auslesen, Beacon-Timestamps nutzen, Cycle-Tracking implementieren.

**Vorteile:** Echte Sub-µs-Genauigkeit auf Multidrop möglich

**Nachteile:** Hoher Implementierungs-Aufwand, abhängig von Hardware-Support

---

## 9. Anwendung auf das aktuelle Projekt

### Aktueller Stand: 2 Knoten

Im aktuellen `net_10base_t1s`-Projekt sind nur 2 Knoten beteiligt. Das bedeutet:

- Asymmetrie ist im Mittel null
- Standard `delay = round_trip / 2` funktioniert
- Keine Annex-H-Kompensation nötig
- Implementierung folgt IEEE 1588-2008 (PTPv2)

Erreichte Genauigkeit: < 50 ns mean offset, < 200 ns worst case (gemessen mit `ptp_offset_capture.py`).

### Bei Erweiterung auf 3+ Knoten

Für eine Multidrop-Konfiguration mit 3+ Knoten wären folgende Ergänzungen nötig:

#### Mindest-Implementierung

1. **PLCA-Status-Lesen** aus LAN8651-Registern
   - Eigene NodeID
   - Max-NodeID
   - Slot-Zeit (`to_timer`)

2. **Erweiterung der `processFollowUp()`-Funktion**
   ```c
   pdelay_komp = berechne_plca_wartezeit(meine_id, gm_id, max_id, slot_dauer);
   echter_delay = (round_trip - pdelay_komp) / 2;
   ```

3. **Best Master Clock Algorithm (BMCA)**
   Bei mehr als 2 Knoten muss entschieden werden, wer Grandmaster ist.

#### Vollständige Annex-H-Implementierung

4. **Beacon-Tracking**
   Beacon-Timestamps aus PLCA-Status auswerten

5. **Cycle-Duration-Tracking**
   Aktuelle Zyklus-Dauer kontinuierlich messen

6. **Burst-Count-Berücksichtigung**
   Variable Slot-Belegung in Kompensation einrechnen

7. **Konservative Filterung**
   Pdelay-Messungen mit hoher Varianz verwerfen

---

## 10. Zusammenfassung

### Die zwei Hauptprobleme bei T1S+PTP

**Problem 1: Timestamp-Position**
- Lösung: Hardware-Timestamp am SFD (im LAN8651 korrekt umgesetzt)
- Bereits gelöst, keine Software-Anpassung nötig

**Problem 2: PLCA-Slot-Asymmetrie**
- Tritt erst bei 3+ Knoten auf
- Kann nicht allein durch Hardware gelöst werden
- Erfordert Annex-H-Algorithmik in Software

### Was reicht für 2 Knoten

- Standard PTPv2 (IEEE 1588-2008)
- Hardware-Timestamping am SFD
- Keine Slot-Asymmetrie-Kompensation

### Was bei 3+ Knoten zusätzlich nötig ist

- IEEE 802.1AS-2020 Annex H Mechanismen
- Laufzeit-Beobachtung der PLCA-Dynamik
- Mindestens: NodeID-basierte Kompensation
- Optimal: Beacon-Tracking + Cycle-Tracking + Filterung

### Statische Information reicht nicht

NodeID und Max-NodeID liefern nur eine Worst-Case-Schätzung. Echte Kompensation erfordert Laufzeit-Beobachtung von:

- Tatsächlicher Slot-Belegung
- Empty-Slot-Skipping
- Burst-Count-Variationen
- Beacon-Phasen-Verschiebung

---

## 11. Genauigkeits-Vergleich

| Konfiguration | Erreichbare Genauigkeit |
|---------------|------------------------|
| 2 Knoten, Standard-PTPv2 + HW-Timestamping | < 1 µs |
| 8 Knoten, ohne Kompensation | 50-150 µs |
| 8 Knoten, statische ID-Kompensation | 5-20 µs |
| 8 Knoten, vollständige Annex-H-Implementierung | < 1 µs |

---

## Referenzen

- IEEE 802.1AS-2020 (gPTP mit Annex H)
- IEEE 802.3cg-2019 (10BASE-T1S + PLCA)
- IEEE 1588-2008/2019 (PTPv2)
- Microchip AN1847 (PTP über 10BASE-T1S)
- LAN8651 Datasheet (TSU + PLCA-Status-Register)

---

**Erstellt:** 2026-04-26
