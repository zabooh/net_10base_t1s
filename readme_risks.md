# Risks & Open Questions

## Risiken

### R1 — Keine nIRQ-ISR-Ankopplung für Software-Wallclock (±100–300 µs Fehler) ✅ BEHOBEN (commit `5e289c8`)

~~`PTP_CLOCK_GetTime_ns()` erfasst den Anchor-Tick nach der SPI-Übertragung~~ —
behoben durch commit `5e289c8` ("fix(R1): replace nIRQ pin polling with EIC
EXTINT14 change-notification ISR").

`EIC_EXTINT_14_Handler()` in `drv_lan865x_api.c` erfasst jetzt `s_nirq_tick =
SYS_TIME_Counter64Get()` beim fallenden nIRQ-Edge (ISR-Latenz 3-5 CPU-Zyklen),
**bevor** die SPI-Transaktion startet. `TC6_CB_OnRxEthernetPacket()` verwendet
diesen vor-erfassten Tick für `sysTickAtRx` statt am Ende des SPI-Transfers zu
lesen. `PORT_PINCFG[14]` wurde auf `0x7` gesetzt (PMUXEN + function A), damit
PC14 gleichzeitig EIC-Input und GPIO-Lesung ist.

**Validierung:** Der `loop_stats`-Mechanismus (commit pending — siehe README §5.7)
zeigt max TOTAL = 209 µs Main-Loop-Zeit über 5.3 Mio Iterationen. Der sysTickAtRx-
Jitter fällt damit auf <5 µs (ISR-Latenz + wenige CPU-Zyklen bis zum Counter-Read).
Die Messung in der UART-CLI bleibt durch USB-CDC-Jitter auf ~100 µs limitiert
(siehe R7).

---

### R2 — Single-Slot RX-Buffer ohne Overrun-Erkennung

`g_ptp_raw_rx` ist ein globaler Single-Slot-Buffer. Wenn ein zweites Sync-Frame
ankommt, bevor der App-Task das erste konsumiert hat (kurzes Sync-Interval,
hohe CPU-Last), wird der erste Frame stillschweigend überschrieben. Es gibt
keinen Overrun-Zähler und keine Warnung.

**Empfehlung:** Overrun-Zähler `g_ptp_rx_overrun` hinzufügen und über
`ptp_status` ausgeben, oder auf einen kleinen Ringbuffer (2–4 Slots) umsteigen.

**Bewertung:** Wahrscheinlichkeit: Gering | Auswirkung: Gering | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung:** `ptp_interval 10` setzen (10 ms Sync-Periode), gleichzeitig iperf-Durchsatztest betreiben. `ptp_status` auf erhöhte SeqID-Mismatch-Rate prüfen. Overrun tritt nur bei CPU-Auslastung > 70% über mehrere Zyklen auf.

---

### R3 — volatile uint32_t |= ist kein atomarer Read-Modify-Write

`drvTsCaptureStatus0[i] |= (value & 0x0700u)` in `_OnStatus0()` (ISR-Kontext)
und `GetAndClearTsCapture()` (Task-Kontext) nutzen nur `volatile`, aber kein
Atomic. Auf ARM Cortex-M4 ist ein `volatile uint32_t |=` kein atomarer
Read-Modify-Write-Befehl — bei Unterbrechung zwischen Lesen und Schreiben kann
ein TTSCAA-Bit verloren gehen (verlorener TX-Timestamp).

**Empfehlung:** Kritischen Abschnitt mit `__disable_irq()` / `__enable_irq()`
oder LDREX/STREX absichern.

**Bewertung:** Wahrscheinlichkeit: Sehr gering (Race-Fenster < 20 CPU-Zyklen) | Auswirkung: Mittel (verlorener TX-Timestamp → SW-Fallback für diesen Zyklus) | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung:** XC32-Disassembly (`.dis`-Datei im Build-Verzeichnis) für `_OnStatus0` öffnen; nach `LDR → ORR → STR`-Sequenz ohne LDREX/STREX suchen. Alternativ: `ptp_trace` aktivieren und `t3 HW capture timeout`-Zeilen über 100 Zyklen zählen — Rate > 0,1 % wäre ein Indiz.

---

### R4 — Linux-Unterstützung nicht in der README dokumentiert

Die README beschreibt Prerequisites und Build-Schritte ausschließlich für
Windows (`C:\Program Files\Microchip\xc32\`, `build.bat`, PowerShell). Die
Linux-Anpassungen (`build.sh`, `setup_compiler.py` Cross-Platform,
`toolchain.cmake` Linux-Pfade) sind nachträglich hinzugefügt worden, aber in
der README nicht aktualisiert. Ein neuer Entwickler auf Linux würde dem
Windows-Workflow folgen und scheitern.

**Empfehlung:** Abschnitt §6.2 und die Prerequisites-Tabelle um Linux-Variante
ergänzen.

**Bewertung:** Wahrscheinlichkeit: Sicher (trifft jeden neuen Linux-Entwickler) | Auswirkung: Gering (kein Datenverlust, nur Zeitverlust) | Gesamtrisiko: 🟢 Gering | Priorität: P3

**Validierung:** Neuen Entwickler mit frischem Linux-System die README-Schritte ohne Vorabwissen nachvollziehen lassen; Blockierstellen protokollieren (Usability-Review).

---

### R5 — setup_debug.py und flash.py noch nicht für Linux angepasst

`setup_debug.py` und `flash.py` wurden bisher nicht auf Linux-Kompatibilität
geprüft. Flashing und VS Code-Debugging unter Linux sind vermutlich noch nicht
funktionsfähig.

**Empfehlung:** Beide Skripte wie `setup_compiler.py` auf Cross-Platform
erweitern (Pfade, Tool-Namen, ggf. MDB-/pyocd-Alternativen).

**Bewertung:** Wahrscheinlichkeit: Sicher (Linux-Setup) | Auswirkung: Mittel (Linux-Flashing und Debugging blockiert) | Gesamtrisiko: 🟡 Mittel | Priorität: P2

**Validierung:** `python3 flash.py` auf Linux aufrufen; erwarteter Fehler: Windows-Pfade oder fehlende `MPLAB_IPE`-Referenz. Protokollieren, welche Zeile zuerst fehlschlägt → gezielte Korrektur.

---

### R6 — XC32-Versionsunterschied: v4.30 lokal vs. v4.60 im Projekt-Original

Das `toolchain.cmake` war ursprünglich auf XC32 v4.60 konfiguriert; lokal ist
nur v4.30 installiert. Obwohl der Build fehlerfrei durchläuft, können sich
zwischen den Versionen Optimierungen, ABI-Details oder Bibliotheksvarianten
unterscheiden — das erzeugte Binary ist nicht identisch mit dem
Windows-Referenz-Build.

**Empfehlung:** Entweder XC32 v4.60 nachinstallieren oder die getestete
Mindestversion explizit auf v4.30 senken und in der README dokumentieren.

**Bewertung:** Wahrscheinlichkeit: Gering | Auswirkung: Gering | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung:** `.hex`-File mit v4.30 und (nach Installation) v4.60 bauen, per `diff` vergleichen. Abweichungen in timing-sensitiven Routinen (Interrupt-Handler, Delay-Loops) Im `.dis`-Disassembly untersuchen.

---

### R7 — ±9 ms Ausreißer in PTP-Messung ✅ ERKLÄRT (Messartefakt, kein PTP-Bug)

Ursache eindeutig als **UART/USB-CDC-Transport-Jitter** identifiziert, **kein**
PTP-Software- oder Hardware-Glitch. Zwei unabhängige Beweise:

1. **`loop_stats` Instrumentierung** (neu in `loop_stats.c`, commit pending):
   misst max/avg Zeit für jedes Subsystem im Harmony-Super-Loop. In 120 s Test
   mit `ptp_trace on` und aufgetretenen 9 ms Outliers:
   - `SYS_CMD_Tasks`:  max 102 µs
   - `TCPIP_STACK_Task`: max 64 µs
   - `ptp_log_flush`:   max 85 µs
   - `APP_Tasks`:       max 166 µs
   - **TOTAL Main-Loop**: max 209 µs über 5.3 Mio Iterationen

   Das Main-Loop blockiert **niemals** länger als 0.21 ms — ein 9 ms Stall im
   Firmware ist damit ausgeschlossen.

2. **`ptp_clock.c` Architektur**: `PTP_CLOCK_GetTime_ns()` liest Hardware-TC0
   (60 MHz) direkt beim `clk_get_cmd`-Aufruf. Der Anchor wird bei jedem Sync
   (125 ms) via EIC-ISR erfasst. Zwischen Syncs lineare Extrapolation. Es gibt
   keinen Codepfad, in dem der Rückgabewert um 9 ms verschoben sein könnte.

**Interpretation der 9 ms:** Wenn Python `clk_get` parallel an beide Boards
sendet, kommen die Bytes durch den EDBG-USB-Bridge-Chip. Bei TX-Kongestion
(viel `ptp_trace`-Output auf dem FOL) werden die RX-Bytes vom PC zum FOL
verzögert weitergeleitet. FOL-Firmware verarbeitet `clk_get` 9 ms später als
GM — liest TC0 zu T+9 ms und meldet wallclock_at_T+9ms. Python sieht
`diff = FOL - GM = +9 ms`. Die PTP-Sync ist korrekt, nur die Messmethode
über die CLI hat 9 ms Jitter.

**Empirische Daten:**
- Mit `ptp_trace on`: ~1 Outlier pro 30 s (viele UART-Bytes → EDBG-Stau)
- Ohne `ptp_trace`: ~1 Outlier pro 4 min (seltene Print-Meldungen wie `PTP FINE`)

**Bewertung:** Wahrscheinlichkeit: Sicher (bei CLI-Messung) | Auswirkung: Gering (nur Messartefakt, PTP selbst korrekt) | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung für absolute Gewissheit:** Beide 1PPS-Ausgänge ≥ 60 s mit
Zweikanal-Oszilloskop aufzeichnen. Werden KEINE 9 ms Spikes auf dem Scope
gesehen (was aus der obigen Analyse folgt), ist bestätigt dass es reine
UART/USB-Messartefakte sind.

---

## Offene Fragen

### F1 — Macht MATCHFREQ nach dem ersten Lock noch Sinn?

Die TISUBN-Korrektur wird einmalig berechnet und danach nicht mehr aktualisiert.
Nach dem ersten erfolgreichen Lock durchläuft der Servo bei jedem Neustart
erneut MATCHFREQ, obwohl der Korrekturfaktor schon bekannt ist. Führt das nur
zu unnötig verzögertem Wiedereinrasten, oder gibt es einen inhaltlichen Grund,
TISUBN jedes Mal neu zu schätzen?

**Antwort finden:** `resetSlaveNode()` in `PTP_FOL_task.c` lesen: der Fast-Reset-Pfad (`calibratedTI_value != 0u`) springt direkt nach MATCHFREQ und überspringt die 16-Frame-Neuberechnung. Die Frage ist damit im Code beantwortet — MATCHFREQ wird wiederverwendet, aber die Messung wird nicht neu durchgeführt. Empirisch: Servo nach erstem Lock stromlos machen, neu starten und Konvergenzzeit messen; sollte kürzer sein als der initiale UNINIT-Durchlauf.

---

### F2 — Was passiert mit TISUBN beim Role-Swap (GM ↔ FOL)?

Der Bugfix für den −3.13 ms stuck-Offset nach Role-Swap ist erwähnt, aber nicht
vollständig erläutert. Wird TISUBN beim Rollenwechsel zurückgesetzt? Wenn der
neue GM eine andere Kristallfrequenz hat, wäre der alte TISUBN-Wert im Follower
falsch — springt der Servo dann sofort in MATCHFREQ zurück oder bleibt er in
FINE mit einem systematischen Drift?
**Antwort finden:** In `ptp_gm_task.c::PTP_GM_Init()` prüfen ob `PTP_FOL_GetCalibratedClockInc()` aufgerufen wird (bereits der Fall) — der GM übernimmt den FOL-Kalibrierungswert. Für den umgekehrten Weg (FOL nach GM-Wechsel): `resetSlaveNode()` untersuchen, ob `calibratedTI_value` bei einem Role-Swap zurückgesetzt oder beibehalten wird. Praktisch: Role-Swap mit `ptp_mode gm` / `ptp_mode fol` ausführen und Servo-Zustand im Log beobachten.
---

### F3 — TXMPATL-Pattern matcht nur Sync (messageType 0x00) — ist das dokumentiert?

`TXMPATL = 0xF700` matcht EtherType `0x88F7` + messageType `0x00` (Sync,
transportSpecific=0). FollowUp hat messageType `0x08` und erhält daher keinen
TX-Timestamp. Das ist für den Algorithmus korrekt (TX-Timestamp wird nur für
Sync benötigt), aber es ist nirgendwo explizit festgehalten, dass FollowUp
bewusst ausgeschlossen ist.

**Antwort finden:** LAN865x Datasheet (DS60001763) Abschnitt TX-Match-Detector lesen: TXMPATL + TXMPATH + TXMMSKH/L-Register-Beschreibung zeigt exakt welche Bits verglichen werden. Alternativ: Wireshark-Capture mit `ptp_trace` aktiv auswerten — wenn kein FollowUp-Timestamp im Trace erscheint, bestätigt das das Verhalten.

---

### F4 — Wofür nutzt der GM PTP_CLOCK_GetTime_ns()?

`PTP_CLOCK_Update()` wird auch auf dem GM aufgerufen (TX-Timestamp nach jedem
Sync-Frame). Hat der GM Codepfade, die `PTP_CLOCK_GetTime_ns()` intern
verwenden, oder dient die Software-Uhr auf dem GM ausschließlich der
Observability über den `clk_get` CLI-Befehl?

**Antwort finden:** `grep -rn "PTP_CLOCK_GetTime_ns" firmware/src/` ausführen; alle Call-Sites auflisten. Falls nur `PTP_FOL_task.c` (t3-Erfassung) und ggf. `app.c` (CLI) Call-Sites vorhanden sind, nutzt der GM die Software-Uhr nicht für interne PTP-Berechnungen.

---

### F5 — Ist WolfSSL aktiv genutzt oder ein ungenutzter Harmony-Überrest?

WolfSSL (viele `wolfcrypt` `.c`-Dateien) ist im Build und belegt einen
erheblichen Teil des Flash-Speichers. Für ein PTP-Demo über 10BASE-T1S ist
TLS/Krypto typischerweise nicht erforderlich. Falls WolfSSL nur ein Überrest
der MCC-Harmony-Konfiguration ist, könnte es deaktiviert werden — das reduziert
die Build-Zeit, den Flash-Verbrauch und eliminiert potenzielle Lizenzpflichten
(WolfSSL ist dual-licensed, GPL-2.0 oder kommerziell).

**Antwort finden:** `xc32-nm firmware.elf | grep -c 'wc_\|wolfSSL_'` ausführen; Anzahl der referenzierten Symbole zeigt ob WolfSSL gelinkt ist. Zusätzlich in MCC Harmony Configurator unter `wolfSSL` prüfen ob `Enable Library` aktiv ist und ob eine `TCPIP_STACK_USE_SSL`-Abhängigkeit besteht.

---

### F6 — Wie verhält sich der Follower bei LAN865x LOFE?

Der GM-seitige LOFE-Recovery-Pfad ist in `app.c` implementiert (`PTP_GM_Init()`
wird nach Wiederherstellung aufgerufen). Für den Follower gibt es keinen
expliziten Wiederanlauf-Pfad. Verliert der Follower nach einem LOFE seinen
FINE-Zustand und muss manuell mit `ptp_reset` zurückgesetzt werden, oder läuft
der Servo automatisch wieder ein, sobald neue Sync-Frames ankommen?

**Antwort finden:** `grep -n "LOFE\|APP_TCPIP\|PTP_FOL_SetMode\|PTP_FOL_Reset" firmware/src/app.c` — prüfen ob für den FOL-Pfad nach LOFE ein automatisches `PTP_FOL_SetMode(PTP_SLAVE)` oder `PTP_FOL_Reset()` erfolgt. Praktisch: LOFE auf FOL-Board auslösen (Kabel ziehen) und beobachten ob `PTP FINE` ohne manuelles `ptp_reset` wieder erscheint.

---

### F7 — Sind die Python-Testskripte auf Linux-Port-Namen vorbereitet?

Die Tests verwenden `serial.Serial(portname)` mit Port-Namen wie `COM8` /
`COM10`. Auf Linux lauten die entsprechenden Namen `ttyUSB0` / `ttyACM0`. Gibt
es eine Konfigurationsdatei für die Port-Zuordnung (analog zu
`setup_compiler.config`), oder sind die Windows-Port-Namen hardcodiert? Falls
letztes, würden alle Testskripte auf Linux ohne Anpassung fehlschlagen.

**Antwort finden:** `grep -rn "COM[0-9]\|serial\.Serial" *.py` im Test-Skript-Verzeichnis ausführen. Falls hardcodierte Windows-Port-Namen gefunden werden, ist die Antwort eindeutig. Alternativ: Skripte auf Linux starten und den genauen Fehler (`serial.SerialException`) protokollieren.

---

## Weitere Risiken (aus README_PTP.md)

### R8 — t3-Software-Fallback: Fehlerbehafteter Timestamp bei TTSCA-Ausfall

Vor dem Senden des Delay_Req wird `fol_t3_ns = PTP_CLOCK_GetTime_ns()` als
Software-Fallback gesetzt. Da PLCA den physischen TX um mehrere Millisekunden
verzögern kann, ist dieser Wert bei einem TTSCA-Ausfall systematisch zu früh
— der t3-Fehler geht direkt in `mean_path_delay` ein. Der Fallback ist im
Code vorhanden, aber es ist unklar wie oft TTSCA tatsächlich ausfällt und ob
der Fallback im Produktivbetrieb je aktiv war.

**Risiko:** Ein stiller TTSCA-Ausfall führt zu einem dauerhaft um mehrere ms
verfälschten Path Delay, ohne dass die Servo-Qualität direkt degradiert
(FINE bleibt erreichbar, aber mit systematischem Offset-Bias).

**Empfehlung:** `fol_t3_hw_valid`-Statistik in `ptp_status` ausgeben; bei
mehr als N% Fallback-Verwendung eine Warnung loggen.

**Bewertung:** Wahrscheinlichkeit: Mittel (gm_tx_busy-Race R15 erhöht Rate) | Auswirkung: Mittel (systematischer Path-Delay-Bias, FINE bleibt erreichbar) | Gesamtrisiko: 🟡 Mittel | Priorität: P2

**Validierung:** `ptp_trace` aktivieren; über 200 Sync-Zyklen `T3_HW`- vs. `t3_sw`-Zeilen zählen. Fallback-Rate > 5% ist alarm-würdig. Zusätzlich: `mean_path_delay`-Wert zwischen HW- und SW-t3-Pfad vergleichen — Differenz > 500 µs bestätigt systematischen Bias.

---

### R9 — Race Condition zwischen TTSCA-Capture und Delay_Resp

Wenn der GM sehr schnell antwortet und das Delay_Resp ankommt, bevor die
TTSCA-Hardware t3_hw geliefert hat, wird die Berechnung aufgeschoben
(`defer calc while TTSCA active`). Was passiert, wenn vor Abschluss dieser
deferred Berechnung der nächste Sync-Zyklus beginnt und neue t1/t2-Werte
die IPC-Strukturen überschreiben? Die aufgeschobene Delay-Calc würde dann
mit veralteten t1/t2 aber frischem t3_hw arbeiten — ein stilles Daten-
konsistenz-Problem.

**Empfehlung:** Deferred Delay-Calc mit einem separaten Snapshot von t1, t2
und t4 arbeiten lassen, der beim Eintreffen von Delay_Resp gekopiert wird.

**Bewertung:** Wahrscheinlichkeit: Gering (TTSCA-Laufzeit << 125 ms Sync-Interval) | Auswirkung: Mittel (stille Datenkonsistenz-Verletzung) | Gesamtrisiko: 🟢 Gering | Priorität: P3

**Validierung:** `ptp_trace` aktivieren; prüfen ob t1 im DELAY_CALC-Log mit dem Origin-Timestamp des passenden Sync-Frames übereinstimmt. Ein t1 > t3 wäre physikalisch unmöglich und würde den Bug beweisen.

---

### R10 — GM_ANCHOR_OFFSET_NS hardcodiert ✅ TEILBEHOBEN (commit `6f3b197`)

Teilbehebung durch commit `6f3b197` ("fix(R10): move GM_ANCHOR_OFFSET_NS to
header as configurable #ifndef macro"). Der Wert ist jetzt im Header als
`#ifndef GM_ANCHOR_OFFSET_NS`-Makro, überschreibbar über den Compile-Flag
`-DGM_ANCHOR_OFFSET_NS=...`.

**Noch offen:** Die automatische Kalibrierungsroutine existiert weiterhin nicht.
Eine empirische Messung pro Board-Kombination bleibt nötig. Für eine neue
Hardware-Revision oder XC32-Version muss der Wert manuell neu ermittelt werden.

**Restrisiko-Bewertung:** Wahrscheinlichkeit: Mittel (bei Portierung) | Auswirkung: Mittel (systematischer Servo-Fehler ohne Kalibrierung) | Gesamtrisiko: 🟡 Mittel (bei Portierung) | Priorität: P3

**Kalibrierung (unverändert):** `GM_ANCHOR_OFFSET_NS=0` compilieren, `ptp_time_test` ausführen. Der mittlere gemessene Offset entspricht dem tatsächlich benötigten Kalibrierungswert.

---

### R11 — MAC-Randomisierung invalidiert laufende Delay_Resp-Unicasts

Die MAC-Adresse wird per Hardware-TRNG zufällig gewählt (`initialization.c`).
Der GM sendet Delay_Resp als Unicast an die Source-MAC des empfangenen
Delay_Req. Wenn der FOL nach einem Reset eine neue zufällige MAC bekommt
(z.B. nach LOFE-Recovery), kann für kurze Zeit ein Delay_Resp an die alte
MAC gehen — diesem Frame antwortet niemand, der FOL wartet auf Timeout und
`mean_path_delay` wird nicht aktualisiert.

**Risiko:** Kurzfristig verlängerter Konvergenz-Delay nach Reset/LOFE des FOL.
Normalerweise harmlos, aber bei häufigen Resets akkumulierend.

**Bewertung:** Wahrscheinlichkeit: Gering | Auswirkung: Gering | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung:** LOFE simulieren (Kabel 3× in < 5 s ziehen/stecken); in Wireshark prüfen ob Delay_Resp nach dem letzten Reset noch an die alte Source-MAC gerichtet ist. Wiedereinlaufzeit des Servos messen — sollte < 2 × normale Konvergenzzeit betragen.

---

### R12 — Multi-Follower: GM-State-Machine ist für Single-FOL ausgelegt

Die GM-Pseudocode-Zustandsmaschine hat nur einen einzigen Pending-Slot für
Delay_Req-Verarbeitung. Wenn mehrere Follower gleichzeitig Delay_Req senden
(erlaubt auf einem Multi-Drop-Bus), kann maximal einer pro Sync-Zyklus
beantwortet werden. Weitere Delay_Req könnten unbeantwortet bleiben oder den
GM-State korrumpieren.

Die README erwähnt Multi-Follower als unterstützten Use-Case, aber der Code ist
dafür nicht explizit ausgelegt.

**Empfehlung:** Klären, ob Multi-Follower tatsächlich getestet wurde, und die
GM-Zustandsmaschine ggf. mit einer Delay_Req-Queue erweitern.

**Bewertung:** Wahrscheinlichkeit: Hoch (wenn Multi-FOL gemäß README eingesetzt wird) | Auswirkung: Hoch (GM-State-Korruption → PTP-Ausfall für alle Follower) | Gesamtrisiko: 🔴 Hoch (bei Multi-FOL-Einsatz) | Priorität: P1 (bei Multi-FOL)

**Validierung:** Zwei FOL-Boards gleichzeitig betreiben; in Wireshark Delay_Req- und Delay_Resp-Frames beider Boards zählen. Falls ein Board dauerhaft keine Delay_Resp erhält, ist der Single-FOL-Bug bestätigt.

---

### R13 — ptp_log-Ringbuffer: Kein dokumentierter Overrun-Schutz

`ptp_log.c` serialisiert GM/FOL-Ausgaben über einen deferred Ringbuffer, der
in `SYS_Tasks()` geleert wird. Bei aktivem `ptp_trace` und schnellen
Zustandswechseln (z.B. wiederholt UNINIT→MATCHFREQ) können mehr
Log-Einträge produziert werden als `ptp_log_flush()` im selben Zyklus
abarbeitet. Ob der Buffer dann überläuft oder ältere Einträge verdrängt, ist
aus der Dokumentation nicht ersichtlich.

**Risiko:** Fehlende Trace-Zeilen, die für Diagnose kritisch sind, ohne
sichtbare Fehlermeldung.

**Bewertung:** Wahrscheinlichkeit: Gering | Auswirkung: Gering (nur Diagnose-Impact, kein funktionaler Schaden) | Gesamtrisiko: 🟢 Gering | Priorität: P4

**Validierung:** `ptp_trace` aktivieren, `ptp_interval 10` setzen. `ptp_log_head` und `ptp_log_tail` per Debugger beobachten. Falls `head` `tail` einholt (Differenz = 0 bei laufenden Logs), ist der Buffer voll und Nachrichten werden verworfen.

---

## Weitere offene Fragen (aus README_PTP.md)

### F8 — Woher kommt der Wert 575983 ns genau?

Der `+575983 ns`-Offset im GM-FollowUp wird als "empirisch bestimmt"
beschrieben. Setzt er sich aus messbaren Komponenten zusammen (LAN865x
TX-Pipeline-Latenz + SPI-Transfer-Latenz + PLCA-Overhead)? Ist er bei
LAN8650 und LAN8651 identisch? Ohne Herleitung kann der Wert bei einem
Bauteilwechsel nicht angepasst werden.

**Antwort finden:** `GM_ANCHOR_OFFSET_NS` auf 0 setzen, neu bauen, `ptp_time_test` ausführen. Der beobachtete mittlere Offset direkt aus der Messung entspricht dem tatsächlich benötigten Wert. Komponenten-Aufschlüsselung: LAN865x TX-Pipeline-Latenz (Datenblatt) + SPI-Transferzeit (Logic Analyzer) + PLCA-Overhead (Wireshark) addieren und mit dem empirischen Wert vergleichen.

---

### F9 — Wird 1PPS bei Rückkehr aus FINE nach COARSE/HARDSYNC abgeschaltet?

`PPSCTL=0x7D` (1PPS enable) wird beim Erreichen von FINE aktiviert.
Im Pseudocode des Servos ist kein explizites Deaktivieren bei einer
Rückkehr in COARSE, HARDSYNC oder UNINIT zu sehen. Gibt der 1PPS-Ausgang
bei einem Rückfall aus FINE weiterhin Pulse aus, die zeitlich nicht mehr
mit dem GM synchronisiert sind? Das könnte externe Systeme, die auf dem
1PPS aufbauen, stören.

**Antwort finden:** In `PTP_FOL_task.c` alle PPSCTL-Write-Stellen suchen (`grep -n "PPSCTL" PTP_FOL_task.c`). Falls kein Write mit 0x0000 bei Rückfall auf COARSE/HARDSYNC/UNINIT vorhanden ist, ist der 1PPS permanent aktiv. Praktisch: 1PPS-Ausgang mit Oszilloskop messen während durch `ptp_interval` änderung ein FINE→UNINIT-Rückfall provoziert wird.

---

### F10 — Hard-Sync schreibt MAC_TSL/MAC_TN während PLCA aktiv ist — Race?

Im MATCHFREQ-State schreibt der Servo `MAC_TSL` (Sekunden) und `MAC_TN`
(Nanosekunden) direkt mit der Ziel-Zeit. Das sind zwei separate SPI-Writes.
Wenn zwischen diesen beiden Writes ein PLCA-Frame empfangen wird, könnte der
LAN865x-TSU kurzzeitig einen inkonsistenten Zustand haben (neues Sekunden-
Register, altes Nanosekunden-Register). Gibt es einen Mechanismus im LAN865x,
der TSL/TN atomar setzt, oder ist ein Shadowing-Register vorhanden?

**Antwort finden:** LAN865x Datasheet Abschnitt „Timestamp Unit“ lesen: prüfen ob ein Double-Buffer oder Shadow-Register für den TSL/TN-Write-Pfad beschrieben ist. Alternativ: Logic Analyzer auf SPI-Bus; Zeitlücke zwischen TSL-Write und TN-Write messen (typisch 1–2 SPI-Frames = ~50 µs) und mit PLCA-Frame-Rate vergleichen — bei 10BASE-T1S mit 8 Nodes kommt alle ~15 µs ein Frame durch.

---

### F11 — Jak verhält sich der Servo bei sehr kurzem ptp_interval (< 30 ms)?

`ptp_interval <ms>` erlaubt die Konfiguration des GM Sync-Intervals.
Das UNINIT-State sammelt 16 Samples für die TISUBN-Schätzung — bei 125 ms
dauert das 2 s. Bei sehr kurzem Interval (z.B. 10 ms) sind es nur 160 ms.
Sind FIR-Filter-Länge und Servo-Schwellenwerte auf kurze Intervalle
ausgelegt, oder gibt es Stabilitätsprobleme?

**Antwort finden:** `FIR_FILER_SIZE`, `MATCHFREQ_RESET_THRESHOLD`, `HARDSYNC_THRESHOLD` und `HARDSYNC_FINE_THRESHOLD` in `PTP_FOL_task.h` prüfen. Bei `ptp_interval 10` testen: Konvergenzzeit messen und servo-Zustand im Log verfolgen. Falls der Servo zwischen HARDSYNC und COARSE pendelt statt FINE zu erreichen, sind die Schwellen nicht auf kurze Intervalle ausgelegt.

---

### F12 — Ist Scenario B (Linux als GM) mit ptp4l vollständig getestet?

Die README beschreibt Scenario B als "recommended": Linux läuft als GM,
SAME54 als FOL. In der Praxis sind die TXMPATL-Pattern-Anforderungen für
den FOL-seitigen Delay_Req (`messageType=0x01`, transportSpecific=0) zu
prüfen — ptp4l muss das `tsmt`-Byte korrekt setzen. Wurde Scenario B
tatsächlich mit einer echten Linux-Installation verifiziert, oder ist es
bisher nur theoretisch beschrieben?

**Antwort finden:** Release Notes und `docs/` nach „Scenario B“ / „ptp4l“-Testprotokollen durchsuchen (`grep -ri "ptp4l\|scenario.b\|linux.*gm" docs/`). Falls keine Testdokumentation vorhanden, muss Scenario B empirisch mit `ptp4l -i eth0 -m --masterOnly 1` auf einem Linux-Host und dem SAME54 als FOL verifiziert werden.

---

### F13 — Sequence-ID-Verifikation auf FOL: Wie groß ist das Toleranzfenster?

`if |seqId - expected| > 10: resetSlaveNode()` — das Fenster von 10 erlaubt
bis zu 10 verlorene Sync-Frames ohne Reset. Bei 125 ms Sync-Interval
entspricht das 1,25 s Ausfall. Ist dieses Fenster bewusst gewählt? Bei einem
kurzen `ptp_interval` (z.B. 10 ms) würde ein 10-Frame-Verlust nur 100 ms
tolerieren — bei einem langen Interval (z.B. 1 s) dagegen 10 s.
Wäre ein zeitbasiertes Timeout (statt Frame-Count) robuster?

**Antwort finden:** In `processSync()` nach der Konstante `10` suchen und prüfen ob eine benannte Konstante oder ein Kommentar den Wert begründet. Praktisch: Sync-Pakete für 1,3 s (= 11 Frames bei 125 ms) unterdrücken (Kabel für exakte Zeit ziehen) und prüfen ob `GM_RESET` im Log erscheint.

---

## Quellcode-Analyse (ptp_gm_task.c / PTP_FOL_task.c / ptp_clock.c / filters.c)

### R14 — SeqID-Wrap-Bug: Spurioser FOL-Reset alle ~2,28 Stunden ✅ BEHOBEN (commit `8594070`)

Behoben durch commit `8594070` ("fix(R14): replace % UINT16_MAX with & 0xFFFF
in sequence-ID wrap"). Alle `% (int)UINT16_MAX`-Ausdrücke in
`processFollowUp()` und verwandten Stellen ersetzt durch `& 0xFFFF`, was
identisch zu `% 65536` ist und korrekt modulo 2¹⁶ rechnet.

Der spurios alle 2,28 h auftretende `resetSlaveNode()`-Aufruf tritt damit nicht
mehr auf.

**Validierung:** `ptp_interval 1` (1 ms Sync), 66 s warten — kein `GM_RESET` in
der Konsole.

---

### R15 — Delay_Resp-Silent-Drop wenn TX-Pfad belegt ✅ BEHOBEN (commit `741596f`)

Behoben durch commit `741596f` ("fix(R15): separate TX-busy flag for Delay_Resp
from Sync/FollowUp path"). `gm_tx_busy` wurde in zwei Flags aufgespalten — eines
für den Sync/FollowUp-Pfad, eines für Delay_Resp. Dadurch kann ein Delay_Req,
der während des ~6 ms FollowUp-TX-Fensters ankommt, jetzt direkt mit einem
Delay_Resp beantwortet werden, ohne auf den Sync/FollowUp-Pfad warten zu müssen.

**Validierung:** `ptp_trace` aktivieren; in der aktuellen Firmware sollten
`GM_DELAY_RESP_SKIPPED_TX_BUSY`-Zeilen nicht mehr auftreten (oder nur noch bei
tatsächlicher Delay_Resp-TX-Kollision, nicht mehr systembedingt).

---

### R16 — Async Deinit/Init-Race: Überschreiben von gm_seq_step

**Fundstelle:** `ptp_gm_task.c`:
- `PTP_GM_Deinit()` setzt `gm_seq_step = 0` und `GM_STATE_DEINIT_WRITE`
- `PTP_GM_Init()` setzt ebenfalls `gm_seq_step = 0u` und
  `GM_STATE_RMW_CONFIG0_READ`

Wenn `PTP_GM_Init()` aufgerufen wird, bevor der Deinit-Ablauf (bis zu
8 × Callback-Rundreise ≈ 40 ms) abgeschlossen ist, überschreibt Init
sowohl `gm_seq_step` als auch den State. Die laufenden Deinit-Writes
benutzen dann die falschen Array-Indizes aus `gm_deinit_addrs[]` — der
TX-Match-Detektor wird möglicherweise nicht korrekt disarmt.

**Szenario:** `app.c` führt bei LOFE-Recovery einen schnellen Role-Swap aus
(Deinit → SetMode → Init). Wenn LOFE mehrmals in kurzer Folge auftritt,
erhöht sich die Wahrscheinlichkeit, dass Init mitten in Deinit feuert.

**Empfehlung:** `PTP_GM_Deinit()` ein `gm_deinit_pending`-Flag setzen
lassen; `PTP_GM_Init()` blockiert oder verzögert, bis Deinit abgeschlossen
ist (`gm_state == GM_STATE_IDLE`).

**Bewertung:** Wahrscheinlichkeit: Mittel (tritt bei LOFE-Recovery-Sequenz auf) | Auswirkung: Mittel (TX-Match-Detektor ggf. nicht korrekt disarmt → Phantom-Timestamps) | Gesamtrisiko: 🟡 Mittel | Priorität: P2

**Validierung:** LOFE 5× in < 2 s simulieren (rasches Kabel-Ziehen). Nach jeder Recovery `ptp_reg_dump` aufrufen und TXMCTL-Wert prüfen. Wert 0x0000 nach Deinit + Wert 0x0002 nach Init = korrekt. Abweichungen zeigen den Race.

---

### R17 — PTP_FOL_Init() schreibt Initialisierungsregister fire-and-forget

**Fundstelle:** `PTP_FOL_task.c`, `PTP_FOL_Init()`:
```c
DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMCTL,  0x00000000u, true, NULL, NULL);
DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMLOC,  30u,         true, NULL, NULL);
// insgesamt 7 Writes ohne Callback
```
Diese Writes sind nicht Callback-bestätigt. Falls der SPI-DMA zum
Aufrufzeitpunkt noch mit anderen Operationen belegt ist, können einige
Writes stillschweigend verworfen werden. Insbesondere `TXMCTL = 0x00000000`
(TX-Match-Detektor disarmen) ist kritisch — fehlt dieser Write, könnte der
Detektor noch auf ein altes Muster armt bleiben und einen Sync-TX-Timestamp
als Delay_Req-Timestamp fehlinterpretieren.

`resetSlaveNode()` setzt `fol_ttsca_state = FOL_TTSCA_IDLE` vor dem
Aufruf von `PTP_FOL_Init()` — minimale Absicherung, aber keine SPI-Queue-Drain.

**Empfehlung:** Die Init-Writes sequentiell Callback-bestätigt ausführen
(analog `gm_init_vals` + `GM_STATE_INIT_WRITE`/`WAIT_INIT_WRITE`).

**Bewertung:** Wahrscheinlichkeit: Sehr gering (SPI-Queue üblicherweise leer bei Init) | Auswirkung: Mittel (falscher TXMCTL-Zustand → Delay_Req-Timestamp einer anderen Rolle hän-genbleibt) | Gesamtrisiko: 🟢 Gering | Priorität: P3

**Validierung:** Direkt nach `ptp_reset` (FOL) per Debugger `fol_ttsca_state` und `FOL_OA_TXMCTL`-Registerwert auslesen. Wert != 0 direkt nach Init ist ein Indiz für einen verlorenen Fire-and-forget-Write.

---

### R18 — PTP_CLOCK_SetDriftPPB() wird gespeichert, aber nie für die Zeitinterpolation verwendet

**Fundstelle:** `ptp_clock.c`:
```c
void PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick) {
    /* Drift correction disabled: ... */
    s_anchor_wc_ns = wallclock_ns;
    s_anchor_tick  = sys_tick;
}

uint64_t PTP_CLOCK_GetTime_ns(void) {
    uint64_t delta_ns = ticks_to_ns(delta_tick);
    return s_anchor_wc_ns + delta_ns;   // s_drift_ppb nicht verwendet
}
```
`PTP_FOL_task.c` ruft `PTP_CLOCK_SetDriftPPB((int32_t)((rateRatioFIR - 1.0) * 1e9))`
auf, aber `ptp_clock.c` ignoriert `s_drift_ppb` vollständig in
`GetTime_ns()`. Die Frequenzkompensation, die der FOL-Servo berechnet, wird
nicht auf die Softwareuhr angewendet. Ohne Kompensation akkumuliert die
Softwareuhr bei einem 21 ppm-Kristallfehler über 500 ms einen Fehler von ca.
10,5 µs — akzeptabel für das Demo, aber undokumentiert.

**Wirkung:** `clk_get` CLI-Ausgabe und GM-Anker-Offset-Berechnung nutzen
eine unkalibrierte Referenz; die eigentliche PTP-Servo-Qualität ist nicht
betroffen (der Servo arbeitet ausschließlich mit LAN865x-Hardware-Timestamps).

**Bewertung:** Wahrscheinlichkeit: Sicher (by Design deaktiviert) | Auswirkung: Gering (nur Software-Uhr-Drift, PTP-Hardware-Genauigkeit unbeeinträchtigt) | Gesamtrisiko: 🟢 Gering | Priorität: P3

**Validierung:** `clk_get` im 1-Sekunden-Takt 60× aufrufen; Differenz zwischen den Ausgaben mit `date +%N` (Linux-Referenz oder GPS/PPS) vergleichen. Drift > 1 µs/s bestätigt den fehlenden Kompensationsterm.

---

## Weitere offene Fragen (Quellcode-Analyse)

### F14 — processDelayResp() liest TS_SYNC direkt — möglicherweise durch neueren Sync überschrieben?

**Fundstelle:** `PTP_FOL_task.c`, `processDelayResp()` (nicht-deferred Pfad):
```c
int64_t t1_ns = (int64_t)tsToInternal(&TS_SYNC.origin);
int64_t t2_ns = (int64_t)tsToInternal(&TS_SYNC.receipt);
```
Wenn zwischen dem Versenden des Delay_Req und dem Eintreffen der Delay_Resp
ein neuer Sync+FollowUp-Zyklus verarbeitet wurde, enthält `TS_SYNC.origin`
bereits t1 der neuen Periode — aber t3/t4 stammen noch vom alten Zyklus.
Das `complete_delay_calc()` würde dann zeitlich inkonsistente Werte verwenden.

Der deferred Pfad speichert t1/t2 korrekt (`fol_deferred_t1`, `fol_deferred_t2`).
Der direkte Pfad tut dies nicht. Wie wahrscheinlich ist es, dass zwischen
Delay_Req-Versand und Delay_Resp-Empfang ein ganzer neuer Sync-Zyklus (125 ms)
abläuft?

**Antwort finden:** `ptp_trace` aktivieren; `DELAY_REQ_SENT`- und `DELAY_RESP_RECEIVED`-Timestamps vergleichen. Typische RTT auf 10BASE-T1S mit 2 Nodes ist < 10 ms — deutlich unter dem 125 ms Sync-Interval. Falls RTT < Sync-Interval, tritt das Problem in der Praxis nicht auf. Bei Multi-Hop-Szenarien oder sehr hoher Buslast neu bewerten.

---

### F15 — PTP_GM_Init(): Kein Fehlerlog wenn TCP/IP-Stack noch nicht bereit

**Fundstelle:** `ptp_gm_task.c`, `PTP_GM_Init()`:
```c
TCPIP_NET_HANDLE netH = TCPIP_STACK_IndexToNet(0);
if (netH != NULL) {
    const uint8_t *pMac = TCPIP_STACK_NetAddressMac(netH);
    if (pMac != NULL) {
        memcpy(gm_src_mac, pMac, 6);
    }
}
```
Falls `netH == NULL` oder `pMac == NULL` (z.B. bei frühem Init-Aufruf),
bleibt `gm_src_mac` = `{0,0,0,0,0,0}`. Alle folgenden Sync-, FollowUp- und
Delay_Resp-Frames haben dann Quell-MAC `00:00:00:00:00:00` — was in Wireshark
direkt auffällt, aber im Betrieb ohne Warnung passiert. Gibt es einen
Startup-Guard, der sicherstellt, dass `PTP_GM_Init()` erst nach vollständiger
TCP/IP-Stack-Initialisierung aufgerufen wird?

**Antwort finden:** In `app.c` den Zustandsautomaten lesen: prüfen in welchem `APP_STATE_*` `PTP_GM_Init()` aufgerufen wird und ob dieser Zustand erst nach `TCPIP_STACK_Status() == SYS_STATUS_READY` erreicht wird. `grep -n "PTP_GM_Init\|TCPIP_STACK_Status" firmware/src/app.c` liefert die relevanten Zeilen.

---

### F16 — PTP_GM_MAX_TN_VAL: Carry-Check mit > statt >= 1000000000?

**Fundstelle:** `ptp_gm_task.c`, `GM_STATE_SEND_FOLLOWUP`:
```c
uint32_t nsec = gm_ts_nsec + PTP_GM_STATIC_OFFSET;
if (nsec > PTP_GM_MAX_TN_VAL) {
    nsec -= PTP_GM_MAX_TN_VAL;
    sec++;
}
```
Wenn `PTP_GM_MAX_TN_VAL == 999999999` (maximaler gültiger ns-Wert), dann
löst die Bedingung `nsec > 999999999` genau bei 1000000000 aus — korrekt.
Wenn `PTP_GM_MAX_TN_VAL == 1000000000`, würde Wert genau 1000000000
(= exakt 1 Sekunde) nicht erkannt und als gültiger ns-Wert übertragen — was
ungültig wäre. Wie ist der Wert exakt definiert?
**Antwort finden:** `grep -rn "PTP_GM_MAX_TN_VAL\|PTP_GM_STATIC_OFFSET" firmware/src/` ausführen. Der Wert in `ptp_gm_task.h` bestimmt ob der Carry-Check korrekt ist. Bei `PTP_GM_MAX_TN_VAL == 999999999` ist `> 999999999` ≡ `>= 1000000000` — korrekt. Bei `== 1000000000` wäre ein Off-by-one vorhanden.
---

### F17 — `last_pos` in firLowPassFilter(): Compiler-Warnung bei aktivem -Wmaybe-uninitialized?

**Fundstelle:** `filters.c`, `firLowPassFilter()`:
```c
uint32_t last_pos;
// ...
for(uint32_t i=0; i<state->filled; i++) {
    if(i>0) { temp = ... state->buffer[last_pos] ...; }  // used before init at i=0
    last_pos = pos;
}
```
Die Variable `last_pos` ist beim ersten Schleifendurchlauf (i=0) formal
uninitializiert — die Benutzung wird nur durch `if(i>0)` verhindert. GCC
mit `-Wmaybe-uninitialized` / XC32 mit `-Wall` kann hier eine Warnung
erzeugen, die echte Uninitialisierungsfehler in anderen Dateien verbirgt.
Wurde der Build mit diesen Flags geprüft?

**Antwort finden:** Build einmalig mit `-Wall -Wextra` neu starten: `cmake -DCMAKE_C_FLAGS="-Wall -Wextra" ...` und Build-Log auf Zeilen mit `filters.c` prüfen. Gleichzeitig alle anderen Warnungen sichten — oft werden dabei latente Fehler in anderen Dateien sichtbar.
---

## Risikomatrix

Bewertungsschema: **Wahrscheinlichkeit** × **Auswirkung** → Gesamtrisiko und Bearbeitungspriorität.

✅ = behoben in laufenden Commits seit letzter Matrix-Aktualisierung (R1/R10/R14/R15/R7).

| Wahrscheinlichkeit \ Auswirkung | 🟢 Gering | 🟡 Mittel | 🔴 Hoch |
|---|---|---|---|
| **Sicher** | R4, R18 | ✅R1, R5 | ✅**R14** |
| **Hoch** | — | ✅R15 | R12 *(bei Multi-FOL)* |
| **Mittel** | R6 | R8, ✅R10, R16 | — |
| **Gering** | R2, R11, R13, ✅R7 | R3, R9 | — |
| **Sehr gering** | — | R17 | — |

### Priorisierte Abarbeitungsreihenfolge (aktualisiert)

| Priorität | Risiken | Status |
|---|---|---|
| **P1 — Sofort** | ~~R14~~, ~~R15~~, R12* | R14 ✅ `8594070`, R15 ✅ `741596f`. R12* noch offen (Multi-FOL). |
| **P2 — Nächster Sprint** | ~~R1~~, R5, R8, ~~R10~~, R16 | R1 ✅ `5e289c8`, R10 ✅ `6f3b197`. R5/R8/R16 offen. |
| **P3 — Backlog** | R4, ~~R7~~, R13, R17, R18 | R7 ✅ als Messartefakt geklärt. R4/R13/R17/R18 offen. |
| **P4 — Nice-to-have** | R2, R3, R6, R9, R11 | — unverändert |

\* R12 nur relevant wenn mehr als ein Follower-Board betrieben wird.

---

## Gesamtbewertung des Projektzustands (Stand 2026-04-18)

Das PTP-Projekt auf Basis IEEE 1588-2008 über 10BASE-T1S (ATSAME54P20A + LAN865x) ist ein technisch ambitioniertes und in vielen Bereichen sorgfältig ausgeführtes Demo. Die nicht-blockierende Zustandsmaschinen-Architektur, die konsequente Nutzung von Hardware-Timestamps (TTSCA), der deferred Delay-Calc-Mechanismus und die FIR/IIR-Servo-Filterung zeigen solides Embedded-Design-Handwerk. Die Servo-Konvergenz auf ±200–500 ns (FINE-Zustand) ist für ein Crystal-basiertes System auf einem Shared-Medium-Bus eine beachtliche Leistung.

**Fixe seit letzter Bewertung:**

| Commit    | Behebt | Auswirkung |
|-----------|--------|-----------|
| `8594070` | R14    | Kein spuriöser Reset mehr alle 2,28 h |
| `741596f` | R15    | Delay_Resp wird nicht mehr systembedingt ge-droppt; `mean_path_delay` ohne Bias |
| `6f3b197` | R10    | `GM_ANCHOR_OFFSET_NS` jetzt als `#ifndef`-Macro konfigurierbar |
| `5e289c8` | R1     | nIRQ per EIC-ISR → `sysTickAtRx`-Jitter von ~200 µs auf <5 µs |
| *pending* | R7     | 9 ms Outliers als UART/USB-CDC-Jitter nachgewiesen (loop_stats max = 209 µs) |

**Noch offene kritische/hohe Risiken:** nur noch **R12** (Multi-FOL-GM-Statemachine), relevant erst bei > 1 Follower.

**Zustand nach Risikoklassen (aktualisiert):**

- 🔴 **0 kritische Risiken** (R14 behoben).
- 🔴 **1 hohes Risiko** (R12 — Multi-FOL, architektonisch, nicht akut).
- 🟡 **4 mittlere Risiken** (R5, R8, R16, R9): Für Demo akzeptabel; für Produktion behebbar.
- 🟢 **10 geringe Risiken** (R2–R4, R6, R7, R11, R13, R17, R18, R3): Technische Schulden ohne akute Auswirkung.

**Linux-Portierungsstatus:** unverändert — Build-fähig, aber Flashing/Debug/Testskripte noch nicht Linux-adaptiert (R5).

**Empfohlene nächste Schritte:**

1. R12 adressieren wenn Multi-FOL-Betrieb geplant ist (architektonische GM-Änderung — oder explizit als Single-FOL-only dokumentieren).
2. R5 + R13 → Linux-Workflow und ptp_log-Overrun-Zähler.
3. Die noch nicht committeten Loop-Stats-Instrumentierung + Async-Delay_Req-Timeout + Rate-limited ptp_log_flush in einen eigenen Commit packen und R7 final als "closed" markieren.

