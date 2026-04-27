# PTP auf 10BASE-T1S Multidrop — Analyse, Bewertung und ehrliche Einschätzung

**Erstellt:** 2026-04-27
**Kontext:** Analyse-Dialog zur Frage, ob die aktuelle PTP-Implementierung in
[apps/tcpip_iperf_lan865x/firmware/src/](../../apps/tcpip_iperf_lan865x/firmware/src/)
für 3–8 Knoten auf einem gemeinsamen 10BASE-T1S Mixing Segment mit 1 µs
Synchronisations-Genauigkeit weiterverfolgt werden sollte.

Dieses Dokument fasst die Erkenntnisse aus der Code-Analyse und der
Lektüre der relevanten Microchip-PDFs (siehe [../../pdf/readme_pdf.md](../../pdf/readme_pdf.md))
zusammen.

---

## 1. Die ursprüngliche Frage

> Ist es möglich, in einem 10BASE-T1S-Netz mit 3 bis 8 Knoten eine
> PTP-basierte Zeitsynchronisation der MCU-Firmware zu erreichen, die
> auf 1 µs synchron ist?

Die Antwort entwickelt sich über mehrere Stufen — von einer ersten,
oberflächlichen Einschätzung über die Lektüre von AN1847 und der
Code-Inspektion bis zu einer strategischen Bewertung des Projekts
insgesamt.

---

## 2. Erste Einschätzung (vor Lektüre der PDFs)

Die initiale Einschätzung war: **1 µs ist machbar, aber nicht trivial.**
Die Bedenken waren:

- **PHY-Latenz** — typisch ~300–500 ns, aber konstant → kompensierbar
- **Kabel-Delay** — bis ~125 ns bei max 25 m Kabel
- **PLCA-Slot-Jitter** — bis zu mehrere µs, unklar wie das kompensiert wird
- **Hardware-Timestamping-Auflösung** — 40 ns @ 25 MHz, unkritisch
- **Software-Jitter** — 1–10 µs ohne HW-Timestamps, das wäre der Killer
- **Quarz-Drift** — ±50 ppm, bei 125 ms Sync-Intervall vernachlässigbar

Erwartung: 200–500 ns RMS Knoten-zu-Knoten realistisch, 1 µs erreichbar
mit etwa 2× Reserve. Voraussetzungen: HW-Timestamping zwingend,
PLCA-Slot-Korrektur, Per-Follower Path-Delay.

---

## 3. Korrektur nach Lektüre von AN1847

Nach Durcharbeit der Microchip Application Note **AN60001847 — LAN8650/1
Time Synchronization** (lokal: [../../pdf/LAN8650-1-Time-Synch-AN-60001847.pdf](../../pdf/LAN8650-1-Time-Synch-AN-60001847.pdf))
wurde klar, dass die ursprüngliche Einschätzung **zu konservativ** war.

### 3.1 Microchips eigene Lab-Messung

AN1847 §4 dokumentiert ein konkretes Test-Setup:

- SAM D21 Curiosity Nano + Two-Wire ETH Click (LAN8651)
- 50 cm UTP-Kabel, 2 Knoten (Grandmaster + Follower)
- 8 Sync-Messages pro Sekunde
- **Simpler FIR-Algorithmus, ausschließlich `MAC_TA` (Time Adjust)**, kein PI-Regler, kein Pdelay

Gemessene 1PPS-Differenz Master ↔ Follower:

| Metrik | Wert |
|---|---|
| Maximale Differenz | **100 ns peak-to-peak** |
| Mittelwert | 8 ns |
| Standardabweichung | 25 ns |
| Lock-Zeit | < 20 Sync-Messages (≈ 2,5 s bei 8 Hz) |

Das ist die **gemessene** Baseline für 2 Knoten — nicht eine theoretische
Abschätzung.

### 3.2 PLCA-Slot-Jitter ist Hardware-seitig gelöst

Der entscheidende Punkt, der in der ersten Einschätzung unterschätzt
wurde: das LAN8651 timestempelt **nicht am MAC, sondern am PHY am
Ende des SFD nach dem Elastic Buffer**.

Aus AN1847 §3 (Packet Pattern Matcher):

> "the transmit pattern matcher signals the timestamp unit that the end
> of the SFD has been transmitted by the PHY, so that the time stamp is
> captured at this time, instead of using the time the SFD leaves the
> MAC. This ensures a consistent internal delay when transmitting packets."

Konsequenz: **Die PLCA-Slot-Wartezeit wird gar nicht erst getimestempelt.**
Egal wie lange ein Frame im PLCA-Buffer auf seinen Sende-Slot wartet —
der TX-Timestamp entsteht erst, wenn das SFD physikalisch über die
Leitung geht. RX läuft symmetrisch.

Damit fällt die größte Sorge der ursprünglichen Einschätzung weg.

### 3.3 Aktualisiertes Fehlerbudget für 3–8 Knoten

| Quelle | Beitrag |
|---|---|
| Master ↔ Follower Sync (gemessen, AN1847) | ~100 ns p-p, σ = 25 ns |
| Path-Delay-Asymmetrie zwischen Followern (unkalibriert, 25 m max) | ≤ 125 ns |
| Path-Delay-Asymmetrie nach einmaliger Kalibrierung pro Follower | < 20 ns |
| Quarz-Drift zwischen Syncs (±50 ppm, 125 ms Intervall) | < 10 ns |
| Software-Jitter (HW-Timestamping eliminiert das meiste) | < 50 ns |
| **Worst-Case unkalibriert** | **~250–300 ns** |
| **Worst-Case mit Per-Follower-Pdelay** | **~150–200 ns** |

→ **1 µs hat 3–5× Sicherheitsmarge.** Sogar 300 ns als Spec wäre
erreichbar.

### 3.4 Errata-Fallstrick (ER80001075 §s9)

Aus dem Errata-Dokument [../../pdf/LAN8650-1-Errata-80001075.pdf](../../pdf/LAN8650-1-Errata-80001075.pdf):

> "The Event Generator in periodic mode is locked to the local
> oscillator, **not** the synchronized wall clock, so 1PPS-style outputs
> must use single-shot mode for true synchronization."

**Konkreter Implementierungs-Punkt:** Wer 1PPS als Sync-Pin nutzen will
(z. B. zum gleichzeitigen ADC-Trigger), muss den Generator **single-shot**
fahren und nach jedem Sync-Update neu programmieren. Im periodischen
Modus driftet er mit dem ungeregelten Quarz, nicht mit der Wall Clock.
Wird in AN1847 nicht erwähnt.

---

## 4. Das AN1847-Modell für >2 Knoten

### 4.1 Topologie

```
        Node 0 (Coordinator + Grandmaster)
              │
   ───────────┴────────────────────────────  (Shared 10BASE-T1S Bus)
        │       │       │       │       │
      Node 1  Node 2  Node 3  ...    Node N
       (FOL)   (FOL)   (FOL)         (FOL)
```

### 4.2 Funktionsprinzip

- Node 0 ist gleichzeitig PLCA-Coordinator und Grandmaster
- Sendet periodisch Sync- und Follow_up-Frames als **Multicast** auf
  `01:80:C2:00:00:0E`
- Alle Follower empfangen dasselbe Frame **gleichzeitig** (Shared
  Medium); jeder erfasst seinen eigenen RX-Timestamp am Ende-SFD im PHY
- Follower senden **nichts** zurück (kein Pdelay, kein Announce, kein BMCA)
- Pro Follower: einmalig Path-Delay als Konstante kalibrieren oder als
  Konfigurations-Parameter setzen

### 4.3 Eigenschaften

- **Skaliert linear ohne Mehrverkehr** — ein Sync-Frame synchronisiert
  alle Follower
- **Vermeidet das Pdelay-Broadcast-Problem** komplett (siehe AN1847 §2)
- **Keine PLCA-Bus-Kontention** für Sync — der Master gibt eh den
  Beacon vor und sendet im Slot 0 unmittelbar danach
- **Statische Master-Wahl** — kein BMCA nötig, kein Master-Wechsel-Risiko

---

## 5. Stand der aktuellen Implementierung

Code-Analyse von:

- [ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c) — Grandmaster-Task
- [ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c) — Follower-Task
- [ptp_clock.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c) — Servo
- [ptp_rx.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_rx.c) — Packet-Empfang
- [ptp_drv_ext.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.c) — Driver-Extension
- [ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c) — CLI

### 5.1 Was bereits Multi-Node-fähig ist

**Sync-Adressierung ist Multicast** —
[ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c)
sendet Sync auf `01:80:C2:00:00:0E` bzw. Broadcast. Diese Schicht
skaliert grundsätzlich auf N Knoten.

### 5.2 Was die Implementierung auf 2 Knoten beschränkt

**Ursache liegt fast ausschließlich im Pdelay-Mechanismus** — exakt der
Stelle, vor der AN1847 §2 explizit warnt.

**1. GM Delay_Resp hat nur einen TX-Slot.**
[ptp_gm_task.c:87](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c#L87) —
`gm_delay_resp_tx_busy` ist ein einziger boolean. Senden zwei Follower
gleichzeitig Pdelay_Req, wird der zweite Response **silent gedroppt**
([ptp_gm_task.c:1158-1185](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c#L1158-L1185)).

**2. Follower hat nur einen Pdelay-Pending-Slot.**
[ptp_fol_task.c:84-85](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c#L84-L85) —
`fol_delay_req_pending` und `fol_delay_req_sent_seq_id` sind je ein
Skalar. Pro Follower passt das (er hat ja nur einen Master), wird aber
problematisch falls ein Knoten irgendwann selbst Master werden sollte.

**3. Keine Source-Identity-Validierung beim Sync.**
[ptp_fol_task.c:945](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c#L945) —
`processSync()` prüft nur Sequence-ID-Kontinuität, nicht die
Clock-Identity des Senders. Würde man zwei GMs ans Bus hängen,
wechselt der Follower wild zwischen den Quellen.

**4. Single Path-Delay-State.**
[ptp_fol_task.c:95](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c#L95) —
`fol_mean_path_delay` ist ein Skalar. Pro Follower passt das, wäre aber
beim Master-Wechsel oder Multi-Master-Szenario unzureichend.

**5. CLI ist binär.**
[ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c) —
`ptp_mode [off|master|follower]`. Keine Peer-ID, kein Node-Count, kein
Multi-Master-Konzept.

### 5.3 Die wichtige Erkenntnis

Wenn man auf das **AN1847-Simple-Follower-Modell** wechselt (nur Sync +
Follow_up, kein Pdelay), verschwinden die Punkte 1 und 2 komplett.
Punkt 3 bleibt, ist aber trivial (Master-MAC einmal beim Lock fixieren).
Punkt 4 wird zu einer **statischen Per-Follower-Konstante**. Punkt 5
braucht eine kleine Erweiterung für `ptp path_delay <ns>`.

**Die 2-Knoten-Beschränkung ist im Wesentlichen eine Folge davon, dass
die aktuelle Implementierung versucht, ein 802.1AS-ähnliches Profil
abzubilden — auf einem Medium, für das 802.1AS nicht definiert ist.**

---

## 6. Warum ein vollständiger 802.1AS-Pfad nicht trivial wäre

### 6.1 Die Grundannahme verletzt

802.1AS-2020 modelliert jeden Time-Aware-Port als Endpunkt eines
**dedizierten Punkt-zu-Punkt-Links**. Auf einem Multidrop-Bus heißt
"ein Port" plötzlich "N-1 Nachbarn". Der Standard gibt darauf keine
Antwort. Man muss sich entscheiden:

- **(a) Pseudo-Port pro Peer instanziieren** — Discovery-Problem, wer ist da?
- **(b) Eine State-Machine über N Peers multiplexen** — alle Felder
  werden zu Arrays, alle Timing-Intervalle bekommen eine zusätzliche
  Dimension

Beides ist machbar, aber **keines davon steht im Standard**. Man baut
eine eigene Spec-Erweiterung.

### 6.2 Pdelay auf Shared Medium

- Alle Pdelay-Frames sind Multicast — auf Multidrop sehen alle alles
- GM braucht **per-peer Response-Queue mit Demultiplexing**
- Jeder Follower sieht **die Pdelay_Resp aller anderen Follower** und
  muss filtern
- PLCA-Slot-Wartezeiten verzerren die Round-Trip-Messung — `t4 - t1`
  enthält jetzt nicht mehr nur den Kabel-Delay, sondern auch die
  Wartezeit des Responders auf seinen Slot

### 6.3 BMCA auf Multidrop ist konzeptionell broken

BMCA wurde so designt: *"Auf jedem Port: vergleiche meinen Master-Status
mit dem, was vom Nachbarn auf diesem Port kommt."* Auf Multidrop:

- N Knoten broadcasten N Announce-Messages
- Per-Port-Logik kann nicht mehr "auf diesem Port" sagen — alle
  Announces kommen vom selben physikalischen Port
- `announceReceiptTimeout` wird mehrdeutig
- Race-Conditions beim Master-Wechsel: zwei Knoten könnten
  gleichzeitig zur Schlussfolgerung kommen, sie seien der neue Master
- Im Wesentlichen ein verteiltes Konsens-Problem

### 6.4 Weitere Standard-Anforderungen

- **`neighborRateRatio` und `cumulativeRateRatio`** pro Peer
- **Sync-Forwarding / Residence-Time-Korrektur** (nur falls Bridge nötig)
- **Path-Trace-TLVs**
- **Test-Infrastruktur** existiert nicht für Multidrop-802.1AS

### 6.5 Aufwands-Vergleich

| Aufgabe | Aufwand |
|---|---|
| Pdelay-State pro Peer + Demux ([ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c) und [ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c)) | Wochen |
| Announce + multidrop-fähiges BMCA | Wochen, hoher Design-Anteil |
| `neighborRateRatio` pro Peer | Tage |
| Sync-Forwarding / Bridge-Logik (falls nötig) | Wochen |
| Test-Harness für Multi-Master-Edge-Cases | Wochen |
| Debugging unter PLCA-Load | Open-ended |

Im Vergleich der **AN1847-Simple-Follower-Pfad**:

| Aufgabe | Aufwand |
|---|---|
| Pdelay-Code optional deaktivieren | Stunden |
| Per-Knoten Path-Delay als CLI-Konstante | Stunden |
| Source-MAC nach Lock fixieren in [`processSync()`](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c#L945) | Stunden |
| Test mit 3-8 Knoten am Bus | Tage |

---

## 7. Die strategische Frage: Insel-Lösung?

### 7.1 Wo "Insel" zutrifft

1. **Kein anerkannter Standard.** AN1847 ist eine Application Note,
   kein IEEE-Dokument.
2. **Keine Interop-Brücke zu echten TSN-Netzen.** Eine AVB-Bridge
   würde das Segment als nicht-time-aware einstufen, weil die Follower
   auf kein Pdelay/Announce antworten.
3. **Vendor-Lock-in auf Microchip.** Andere T1S MAC-PHYs (NXP TJA1120,
   Onsemi NCN26010) haben unterschiedliche Timestamp-Hardware.
4. **Keine Upstream-Heimat.** Linux-Driver hat (Stand 2026-04) kein PTP
   für LAN8651, Zephyr auch nicht. Bleibt Fork.
5. **Kein Pfad zu TSN-Features.** CBS, TAS, SRP bauen alle auf
   802.1AS-Time auf.

### 7.2 Wo "Insel" *nicht* zutrifft

1. **Der Standard existiert für diesen Fall schlicht nicht.**
   AN1847 §2 sagt unverblümt: *"current PTP standards do not yet cover
   multidrop / PLCA broadcast Pdelay"*. IEEE 802.3da arbeitet daran,
   aber kein veröffentlichtes Amendment.
2. **Microchip ist faktisch der einzige relevante T1S-Vendor** in 2026.
3. **Geschlossene Systeme brauchen keine Interop.**
4. **Auch "echtes" 802.1AS ist nicht so einheitlich, wie es klingt** —
   linuxptp, gPTP-Stacks von TI/NXP/Microchip haben alle eigene Quirks.

### 7.3 Die eigentliche Frage

Nicht "Insel ja/nein", sondern: **welches Deployment-Szenario?**

| Szenario | Bewertung | Empfehlung |
|---|---|---|
| Geschlossenes Cluster aus 3-8 T1S-Knoten unter einem MCU-Master, kein Anschluss an Fremdnetze | Insel ist OK, weil System eine Insel *ist* | AN1847-Pfad, ship |
| T1S-Segment als Branch eines TSN/AVB-Netzes mit Switches | Insel killt — echtes 802.1AS nötig | Auf Standard warten oder kommerziell lizenzieren |
| Lerncode / Forschungsplattform / Demo | Egal welcher Pfad, Hauptsache Erkenntnis | AN1847 für schnellen Erfolg, optional 802.1AS für Tiefe |
| Forschung am Multidrop-PTP-Problem selbst | Insel-Lösung ist der wissenschaftliche Beitrag | Beide Pfade implementieren, vergleichen, publizieren |

### 7.4 Was der bisherige Code wert ist

**Etwa 80 % des bisherigen Codes ist protokoll-unabhängig:**

- LAN8651 Register-Init (AN60001760-konform) → in jeder PTP-Variante nötig
- TSU-Konfiguration / Pattern Matcher → unverändert
- HW-Timestamp-Capture-Pfad → unverändert
- Sync/Follow_up Frame-Parsing → unverändert
- Wall-Clock-Servo (`MAC_TA`, `MAC_TI`) → unverändert

Die Verzweigung liegt allein in der **High-Level-Protokoll-Logik** — und
genau dort ist der AN1847-Pfad um Größenordnungen kleiner als der
802.1AS-Pfad.

---

## 8. Realistische Optionen

### Option 1 — AN1847 jetzt, fertig

Pragmatisch. Funktioniert für 1 µs / 3-8 Knoten. Vendor-Lock-in
akzeptiert. Klar definierte, kleine Code-Base. Kein Anschluss an
größere TSN-Welt.

### Option 2 — Auf Standardisierung warten

IEEE 802.3da arbeitet an Multidrop-PTP. Wenn das Amendment in 1-2
Jahren kommt, wird Microchip eine Reference-Implementierung publizieren,
und Linux/Zephyr werden sie aufnehmen. Bis dahin: Hardware-Handling und
Servo-Logik fertig haben, Protokoll-Layer modular halten, beim Standard
dann den Layer austauschen.

### Option 3 — Forschungs-Beitrag

AN1847 als Baseline implementieren, tatsächliche Genauigkeit auf 3-8
Knoten messen, Trade-offs sauber dokumentieren, publizieren. Die
IEEE-Working-Group sucht aktiv nach Implementierungsdaten.

### Option 4 — Sun-set

Wenn der eigentliche Wert des Projekts woanders liegt
(TCP/IP-Stack, Iperf-Performance) und PTP nicht zur
Produkt-Differenzierung beiträgt: Aufwand/Nutzen-Rechnung geht
nicht auf, dann nicht weitermachen.

---

## 9. Ehrliche Einschätzung

**Wenn das Ziel ein deploybares Produkt mit 3-8 T1S-Knoten und 1 µs
Sync ist:** AN1847-Pfad ist die richtige Antwort. Der Standard für
diesen Fall existiert nicht — "Insel" wäre auch jede andere heutige
Lösung, nur eben mit 10× mehr Code.

**Wenn das Ziel TSN-Anbindung an existierende AVB-Welt ist:** Projekt
zurückstellen, bis IEEE 802.3da fertig ist. Selber zu bauen wäre
Forschungsarbeit, nicht Engineering.

**Wenn das Ziel nicht klar ist** — und das ist der häufigste Fall in
solchen Repos: die strategische Frage muss gestellt werden, *bevor*
mehr Code geschrieben wird. Den Pfad zu definieren ist wichtiger als
den nächsten Patch.

Die kritische Beobachtung des Projekt-Eigentümers — *"das macht doch
keinen Sinn weiter zu verfolgen, weil sie eine Insel-Lösung darstellt"*
— ist nicht falsch, sie ist nur **abhängig vom Deployment-Szenario**.
Im geschlossenen System ist Insel-Sein kein Bug, sondern Feature-Scope.
Im offenen System ist es ein Show-Stopper.

Die Entscheidung hängt nicht an PTP — sie hängt am Anwendungsfall.

---

## 10. Quellenverzeichnis

### Microchip-Dokumente

- [LAN8650-1-Time-Synch-AN-60001847.pdf](../../pdf/LAN8650-1-Time-Synch-AN-60001847.pdf) — *AN60001847 Time Synchronization*
- [LAN8650-1-Data-Sheet-60001734.pdf](../../pdf/LAN8650-1-Data-Sheet-60001734.pdf) — *DS60001734 Datasheet, §4.5 Synchronization Support, §11 Register Map*
- [LAN8650-1-Configuration-Appnote-60001760.pdf](../../pdf/LAN8650-1-Configuration-Appnote-60001760.pdf) — *AN60001760 Configuration*
- [LAN8650-1-Errata-80001075.pdf](../../pdf/LAN8650-1-Errata-80001075.pdf) — *ER80001075 Errata, insbesondere §s9 zum 1PPS-Generator*
- [LAN86xx-topology-discovery-AN-00006067.pdf](../../pdf/LAN86xx-topology-discovery-AN-00006067.pdf) — *AN00006067 Topology Discovery* (für automatische Per-Knoten-Distanz-Messung)

### Standards (extern)

- IEEE Std 1588-2019 — Precision Time Protocol v2
- IEEE Std 802.1AS-2020 — gPTP für TSN
- IEEE Std 802.3-2022 Clause 147 — 10BASE-T1S
- IEEE Std 802.3-2022 Clause 148 — PLCA

### Projekt-interne Dokumente

- [plca_ptp_asymmetrie.md](plca_ptp_asymmetrie.md) — Architektur-Bild und Annex-H-Roadmap
- [README_cross.md](README_cross.md) — Driver-Patches und Plattform-Querschnitt
- [implementation.md](implementation.md) — Implementierungs-Notizen
- [drift_filter.md](drift_filter.md) — Servo-Filter-Design
- [ntp_reference.md](ntp_reference.md) — NTP-Vergleichsreferenz

### Code-Referenzen

- [apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c) — Grandmaster
- [apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.c) — Follower
- [apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c) — Clock-Servo
- [apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.c) — TSU-Driver-Extension
- [apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.c) — CLI-Konfiguration
