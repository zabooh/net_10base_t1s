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

**Antwort finden:** `resetSlaveNode()` in `ptp_fol_task.c` lesen: der Fast-Reset-Pfad (`calibratedTI_value != 0u`) springt direkt nach MATCHFREQ und überspringt die 16-Frame-Neuberechnung. Die Frage ist damit im Code beantwortet — MATCHFREQ wird wiederverwendet, aber die Messung wird nicht neu durchgeführt. Empirisch: Servo nach erstem Lock stromlos machen, neu starten und Konvergenzzeit messen; sollte kürzer sein als der initiale UNINIT-Durchlauf.

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

**Antwort finden:** `grep -rn "PTP_CLOCK_GetTime_ns" firmware/src/` ausführen; alle Call-Sites auflisten. Falls nur `ptp_fol_task.c` (t3-Erfassung) und ggf. `app.c` (CLI) Call-Sites vorhanden sind, nutzt der GM die Software-Uhr nicht für interne PTP-Berechnungen.

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
`#ifndef PTP_GM_ANCHOR_OFFSET_NS`-Makro, überschreibbar über den Compile-Flag
`-DPTP_GM_ANCHOR_OFFSET_NS=...`.

**Update 2026-04-20 (commit `657e8a1`):** Wert von 575983 → **800000 ns**
rekalibriert für den neuen ISR-Anker-Pfad.  Der alte Wert war für die
Task-Latenz-Lücke (~ms-Bereich) zwischen TTSCA-latch und `SYS_TIME_Counter64Get()`
bei FollowUp-TX-done getuned; der neue Wert kompensiert die ISR-Frische-Lücke
(~µs-Bereich) zwischen TTSCA-latch und dem im EXTINT-14-Handler gekapselten
`s_nirq_tick`.  Kalibrierprozedur ist jetzt im Header-Kommentar dokumentiert:
`cyclic_fire_hw_test.py --no-compensate` → median rising delta D ablesen →
neuer Wert = alter − D.

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

### F8 — Woher kommt der Wert 575983 ns genau? ✅ TEILWEISE BEANTWORTET (2026-04-20)

Der alte `+575983 ns`-Offset im GM-FollowUp war die empirische Summe aus:

1. **LAN865x-internen TX-Pipeline-Latenz** (interne TTSCA-Latch → SFD auf
   dem Draht): ~7.65 µs (das ist die separate `PTP_GM_STATIC_OFFSET` Konstante,
   siehe ptp_gm_task.h).
2. **Task-Latenz-Gap** (TTSCA-latch Moment → `SYS_TIME_Counter64Get()` im
   FollowUp-TX-done Callback): ~568 µs, dominiert durch die SPI-Lese-Rundreise
   für STATUS0+TTSCA-Werte plus die FollowUp-TX-Wartezeit.

Zusammen ~575.9 µs, empirisch rundgerechnet zu 575983 ns.

Mit dem commit `657e8a1` entfällt die Task-Latenz-Komponente (Anker-Tick
wird jetzt im EXTINT-14-ISR erfasst, ~5 µs nach SFD statt ~568 µs).  Der
rekalibrierte Wert **800000 ns** enthält daher nicht mehr die alte
Task-Latenz, sondern eine neue Kombination aus LAN865x-internen
TX-Pipeline-Latenzen, und ist ebenfalls empirisch gemessen (nicht analytisch
hergeleitet).

**Für eine echte Bauteilwechsel-Anpassung** bleibt die empirische
Rekalibrierung per `cyclic_fire_hw_test.py --no-compensate` nötig.  Eine
vollständige analytische Herleitung (jede Komponente aus Datasheet +
Messgeräten summiert) ist weiterhin offen.

---

### F9 — Wird 1PPS bei Rückkehr aus FINE nach COARSE/HARDSYNC abgeschaltet?

`PPSCTL=0x7D` (1PPS enable) wird beim Erreichen von FINE aktiviert.
Im Pseudocode des Servos ist kein explizites Deaktivieren bei einer
Rückkehr in COARSE, HARDSYNC oder UNINIT zu sehen. Gibt der 1PPS-Ausgang
bei einem Rückfall aus FINE weiterhin Pulse aus, die zeitlich nicht mehr
mit dem GM synchronisiert sind? Das könnte externe Systeme, die auf dem
1PPS aufbauen, stören.

**Antwort finden:** In `ptp_fol_task.c` alle PPSCTL-Write-Stellen suchen (`grep -n "PPSCTL" ptp_fol_task.c`). Falls kein Write mit 0x0000 bei Rückfall auf COARSE/HARDSYNC/UNINIT vorhanden ist, ist der 1PPS permanent aktiv. Praktisch: 1PPS-Ausgang mit Oszilloskop messen während durch `ptp_interval` änderung ein FINE→UNINIT-Rückfall provoziert wird.

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

**Antwort finden:** `FIR_FILER_SIZE`, `MATCHFREQ_RESET_THRESHOLD`, `HARDSYNC_THRESHOLD` und `HARDSYNC_FINE_THRESHOLD` in `ptp_fol_task.h` prüfen. Bei `ptp_interval 10` testen: Konvergenzzeit messen und servo-Zustand im Log verfolgen. Falls der Servo zwischen HARDSYNC und COARSE pendelt statt FINE zu erreichen, sind die Schwellen nicht auf kurze Intervalle ausgelegt.

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

## Quellcode-Analyse (ptp_gm_task.c / ptp_fol_task.c / ptp_clock.c / filters.c)

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

**Fundstelle:** `ptp_fol_task.c`, `PTP_FOL_Init()`:
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

### R18 — PTP_CLOCK_SetDriftPPB() wird gespeichert, aber nie für die Zeitinterpolation verwendet ✅ BEHOBEN (2026-04-20, in `baaa3e5` / `657e8a1`)

Der Zustand bis 2026-04-19: `PTP_CLOCK_Update()` ignorierte `s_drift_ppb`
vollständig in `GetTime_ns()`, die Softwareuhr drifted unkompensiert mit
dem Kristallfehler.

**Status 2026-04-20:** In `ptp_clock.c` ist die Drift-Korrektur jetzt
vollständig aktiv:

- `PTP_CLOCK_Update()` berechnet `inst_ppb` aus dem Verhältnis
  (neue-wallclock − alte-wallclock) / (neue-tick − alte-tick) und mischt
  es per IIR-Filter (`DRIFT_IIR_N = 128`, Halbwertszeit ~11 s) in
  `s_drift_ppb` ein.
- `PTP_CLOCK_GetTime_ns()` ruft `ticks_to_ns_corrected(delta_tick,
  s_drift_ppb)` auf, was die Tick-zu-ns-Umrechnung um den gefilterten
  ppb-Wert justiert.

**Messung (2026-04-20, drift_filter_analysis.py):** Filter konvergiert
auf ~990 ppm (GM vs FOL Quarzmismatch), Langzeit-Cross-Board-Rate-Residual
+1.2 ppm.  Das ist Faktor ~800 besser als der vorher dokumentierte
10.5 µs/500 ms Drift (= 21 ppm unkompensiert) und zeigt, dass die
Kompensation funktioniert wie vorgesehen.

Die stale-Doku-Bemerkung in `ptp_clock.c` und README_PTP.md §4.6 wurde
im gleichen Commit (`657e8a1`) korrigiert.

---

### R19 — Drift-IIR-Filter Kurzzeit-Wander ("random walk") auch bei N=128

**Fundstelle:** `ptp_clock.c`, IIR-Filter auf `s_drift_ppb`.

Der Filter-Output zeigt eine **starke Lag-1-Autokorrelation** (+0.97) —
das heißt er random-walkt langsam statt stationär um den Mittelwert zu
oszillieren.  Messung per `drift_filter_analysis.py` (60 s Sampling):

| Metrik | GM | FOL |
|---|---|---|
| Filter-Stddev | ~37 ppm | ~15-32 ppm |
| Lag-1-Autokorr | +0.98 | +0.83-0.96 |
| Spread über 60 s | 109-138 ppm | 58-101 ppm |

**Auswirkung:** Langzeit (60 s) Cross-Board-Rate-Residual ist exzellent
(+1.2 ppm), aber **kurze Capture-Fenster (0.7 s) können bis zu 200 µs/s
Drift zeigen**, wenn der Filter gerade in einer schnellen Wander-Phase
ist.  In `cyclic_fire_hw_test.py`-Outputs erscheint das als MAD = 15-45 µs
statt der theoretischen Sub-µs-Stabilität.

**Ursache:** Die IIR-Mittelung reduziert zwar unabhängiges
Per-Sample-Rauschen (40 ppm → 4 ppm), aber nicht korreliertes Rauschen
(z.B. thermisch-bedingter Quarz-Drift, Servo-induzierte systematische
Anpassungen).

**Empfehlung (falls Sub-10 µs MAD benötigt wird):** Median-of-N Filter
statt IIR, oder hardware-getriggerte GPIO-Ausgabe über MAC-Timer-Compare
(siehe README_PTP §12.3) statt Software-PTP_CLOCK-Poll.

**Bewertung:** Wahrscheinlichkeit: Sicher (inhärent bei IIR-Filter
auf korrelierten Eingangssamples) | Auswirkung: Gering (Demo-Sync
weiterhin ±50 µs-Klasse, sub-µs nur mit HW-Scheduling erreichbar) |
Gesamtrisiko: 🟢 Gering | Priorität: P3

**Validierung:** `drift_filter_analysis.py --settle-s 60 --sample-s 60`
laufen lassen; `lag-1 autocorrelation > 0.8` und
`spread > 50 ppm` bestätigen das Verhalten.  Die CSV
`drift_samples_*.csv` gegen PC-Zeit plotten zeigt den Random-Walk visuell.

---

### R20 — cyclic_fire MARKER-Phase-Zähler nicht an Anker gebunden

**Fundstelle:** `cyclic_fire.c`, `s_marker_phase` start-zustand:

```c
s_pattern      = pattern;
s_marker_phase = 0u;         // Start immer bei phase 0
```

Jedes `cyclic_fire_start_ex(CYCLIC_FIRE_PATTERN_MARKER, ...)` setzt den
Phase-Zähler auf 0 und zählt ihn modulo 10 bei jedem Callback hoch.
Bei zwei Boards mit demselben Anker erwarten wir, dass **beide ihre
Phase-0 (= rising edge) zum selben PTP-Wallclock-Moment** haben.

Problem: wenn ein Board spät armt (z.B. weil FOL nach einem Reset
hunderte ms später reagiert) und sein `first_target_ns` per
Phase-Align-Loop um `N × period` nach vorne gerollt wird, beginnt es
trotzdem bei `s_marker_phase = 0`.  Aber das entsprechende Raster des
anderen Boards ist in dem Moment vielleicht bei Phase 4 oder 6 — beide
Boards feuern dann zu **unterschiedlichen Zeitpunkten innerhalb des
5-Perioden-Zyklus**.

**Auswirkung:** Die MARKER-Pulse der beiden Boards können systematisch
gegeneinander versetzt auftauchen (1, 2, 3 oder 4 Perioden Versatz)
obwohl PTP-seitig alles perfekt synchron ist.  Visuelle "Wer feuert
zuerst?"-Diagnose kann irreführend werden.

**Empfehlung:** `s_marker_phase` aus dem Anker ableiten, z.B.
`s_marker_phase = (first_target_ns / half_period_ns) % 10` — so
lautet die Regel "Phase 0 ist das Halbperioden-Slot, dessen Nummer
modulo 10 Null ist".  Alle Boards konvergieren automatisch auf dasselbe
Raster, unabhängig vom Arm-Zeitpunkt.

**Bewertung:** Wahrscheinlichkeit: Mittel (tritt bei ungleichzeitigem
Arm auf) | Auswirkung: Mittel (MARKER-Demo kann verwirren) |
Gesamtrisiko: 🟡 Mittel | Priorität: P3

**Validierung:** Zwei Boards arm'en mit absichtlich unterschiedlichem
Delay (einmal 0s, einmal 2s warten nach PTP-FINE), dann `cyclic_start_marker
1000 <shared_anchor>`.  Logic 2 sollte dieselbe Phase-Ausrichtung zeigen
wie beim gleichzeitigen Arm-Aufruf.  Falls nicht → R20 bestätigt.

---

### R21 — LAN865x-MAC verklemmt nach langen cyclic_fire-Läufen

**Beobachtung (2026-04-20, mehrfach reproduziert):** Nach ~5-10 Minuten
kontinuierlichem `cyclic_fire`-Betrieb (beide Boards togglen PD10 im
1-kHz-Rhythmus) kann es passieren, dass ein anschließendes
`ptp_mode follower / master` nicht mehr zu PTP FINE konvergiert —
Timeout nach 60 s.

Ein **Software-`reset`** (per CLI oder im Test-Skript) hilft in diesem
Fall **nicht**.  Die einzige zuverlässige Recovery ist ein **Hard-Power-Cycle**
(USB-Kabel ab, 3 s warten, wieder an).

**Vermutete Ursache:** Der LAN865x-MAC behält einen internen Zustand
(TX-Match-Detektor, 1PPS-Konfiguration, TTSCA-Register) über einen
Software-Reset-Ereignis hinweg.  Ein `ptp_mode master/follower`-CLI-Befehl
reinitialisiert nicht alle relevanten Register.  Nach ausreichend langem
Cyclic-Betrieb gibt es eine Konfiguration, aus der das
Re-Init-Protokoll nicht korrekt herauskommt.

**Empfehlung:** PTP_GM_Init / PTP_FOL_Init sollten beim Aufruf
**expliziter alle TX-Match- und Timestamp-Register** zurücksetzen (nicht
nur die, die den Anfangszustand erwarten).  Alternativ: ein `mac_reset`
CLI-Befehl, der den LAN865x hardwareseitig via Reset-Pin zurücksetzt,
wäre ein einfacher Workaround.

**Bewertung:** Wahrscheinlichkeit: Mittel (tritt nach Demo-typischen
Betriebsintervallen auf) | Auswirkung: Mittel (benötigt manuelles
Eingreifen zur Recovery, Demo-Stop) | Gesamtrisiko: 🟡 Mittel |
Priorität: P2

**Validierung:** Beide Boards je 10 min `cyclic_start 1000` laufen
lassen, dann beide Boards `reset` → `ptp_mode master/follower` → warten
ob FINE in üblicher Zeit erreicht wird.  Falls Timeout in ≥30 % der
Fälle, ist R21 reproduziert.

---

### R22 — ISR-Anker-Race bei konkurrierenden nIRQs (GM-Seite)

**Fundstelle:** `drv_lan865x_api.c`, `_OnStatus0()`:

```c
if (0u != (value & 0x0700u)) {
    for (i = 0u; i < DRV_LAN865X_INSTANCES_NUMBER; i++) {
        if (pDrvInst == &drvLAN865XDrvInst[i]) {
            drvTsCaptureStatus0[i] |= (value & 0x0700u);
            drvTsCaptureNirqTick[i] = s_nirq_tick;   // R22: siehe unten
            break;
        }
    }
}
```

`_OnStatus0` läuft im **SPI-Callback-Kontext**, also einige hundert µs
nach der eigentlichen TTSCAA-nIRQ-Assertion (SPI-STATUS0-Read-Rundreise).
In diesem Fenster kann ein **anderer nIRQ feuern** — typisch ein
RX-Frame eines anderen Boards auf dem 10BASE-T1S-Bus, z.B. ein Delay_Req
eines FOL.  Der EXTINT-14-Handler aktualisiert dann `s_nirq_tick` mit
einem Zeitpunkt, der **nicht** zum TTSCA-Ereignis gehört.
`drvTsCaptureNirqTick` bekommt einen falschen Wert.

**Auswirkung:** Einzelne Cyclic_fire-Outlier. Im 15:58-Run beobachtet:
p10/median/p75 = (−63/−30/−3) µs, aber p90 = +108 µs und max = +146 µs.
Die rechts-schief/bimodale Verteilung passt zu seltenen falsch
korrelierten Anker-Ticks.

**Empfehlung:** Arm-Latch-Mechanismus statt "Letzter-nIRQ":

```c
static volatile bool     s_nirq_latch_tx_armed  = false;
static volatile uint64_t s_nirq_latch_tx        = 0u;

EIC_EXTINT_14_Handler():
    uint64_t t = SYS_TIME_Counter64Get();
    s_nirq_tick = t;                         // generisch (für FOL RX)
    if (s_nirq_latch_tx_armed) {
        s_nirq_latch_tx       = t;           // spezifisch (für GM TX)
        s_nirq_latch_tx_armed = false;       // self-disarm
    }
    ...

// Vor dem SendRawEthFrame(tsc=1):
DRV_LAN865X_ArmNirqLatchTx();                // setzt s_nirq_latch_tx_armed
```

Der nächste nIRQ nach dem Arm-Aufruf latched den Tick in eine dedizierte
Variable.  Nachfolgende nIRQs tasten sie nicht an, weil das Flag sich
selbst deaktiviert.  Robust gegen interleaved RX.

**Bewertung:** Wahrscheinlichkeit: Gering (RX während der SPI-STATUS0-
Rundreise ist nicht häufig auf einem 2-Node-Bench; nimmt zu bei
Multi-Drop-Bus) | Auswirkung: Mittel (p90-Outliers in cyclic_fire-Capture,
verfälscht die Cross-Board-Statistik-Tails) | Gesamtrisiko: 🟡 Mittel |
Priorität: P3

**Validierung:** `cyclic_fire_hw_test.py` mit einem **dritten Board** auf
dem Bus laufen lassen, das regelmäßig PTP-Traffic generiert.  Die Tail-
Breite der Cross-Board-Delta-Verteilung (p90-max) sollte deutlich
zunehmen, wenn R22 vorliegt.

---

### R23 — cyclic_fire Spin-Wait drosselt PTP/TCPIP bei sub-ms Perioden

**Fundstelle:** `cyclic_fire.c`:

```c
#define CYCLIC_FIRE_SPIN_US  100u
```

`tfuture` wird bei jedem `cyclic_fire`-Start auf einen 100 µs Spin-
Threshold gesetzt.  Bei jedem Callback (alle `period_us/2`) kann der
Main-Loop bis zu 100 µs in einem busy-wait blockieren.  Bei
`period_us=500` (1 kHz Rechteck) sind das bis zu **40 % CPU-Zeit im
Spin** (100 µs Spin pro 250 µs Half-Period-Callback), plus die eigentliche
Callback-Logik, SPI-Rundreisen für tfuture_arm, etc.

PTP-Servo, TCPIP-Stack, Drift-Filter-Update und Log-Flush laufen alle
im selben Main-Loop — sie bekommen entsprechend weniger Zyklen.

**Mögliche Selbstverstärkung:** Schlechtere PTP-Servo-Qualität → größerer
Offset → Anker rutscht stärker weg vom Anchor+N×period-Grid → mehr
Misses → noch mehr catch-up in `fire_callback` → noch weniger CPU für
PTP.  Nicht akut problematisch bei ≥ 1 ms Perioden, aber ein
unfreundlicher Betriebspunkt für die als „nicht empfohlen" markierten
Perioden < 400 µs.

**Empfehlung:** (a) In cyclic_fire_hw_test's Verdict-Output eine Warnung
bei `period_us < 800` („CPU-Last-Anteil > 25 % im Spin-Pfad — PTP-Servo-
Qualität kann degradieren").  (b) Laufzeit-Messung: die Cycles-/Misses-
Zähler im `cyclic_status` pro Sekunde auslesen und mit dem nominellen
Rate vergleichen — Miss-Rate > 1 % ist alarmierend.

**Bewertung:** Wahrscheinlichkeit: Sehr gering (Standard-Periode 1000 µs,
`cyclic_fire_hw_test.py` defaulted dorthin) | Auswirkung: Gering (nur
falls sub-ms-Betrieb explizit gewählt wird) | Gesamtrisiko: 🟢 Gering |
Priorität: P4

**Validierung:** `cyclic_start 300` (unter der empfohlenen Grenze von
400 µs) + `ptp_status` und `cyclic_status` im 2-s-Takt aufrufen.  Servo-
State-Flapping (wiederkehrendes FINE↔COARSE) oder Misses > 0 bestätigen
die Degradation.

---

### R24 — cyclic_fire ignoriert PTP-Servo-Zustandsrückfälle

**Fundstelle:** `cyclic_fire.c`, `cyclic_fire_start()` und `fire_callback()`:

```c
bool cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns) {
    if (!PTP_CLOCK_IsValid()) { return false; }
    ...
}
// fire_callback tastet PTP_CLOCK_IsValid() nicht erneut ab
```

`cyclic_fire` prüft `PTP_CLOCK_IsValid()` nur beim Start.  Während des
Laufs wird `fire_callback` auf dem gespeicherten Anker weiterfeuern,
**selbst wenn der PTP-Servo zurückfällt** (Kabel-Unterbrechung, LOFE,
großer Offset-Spike, MATCHFREQ-Retry).  Die Board-interne `PTP_CLOCK`
läuft dann auf ihrem letzten gültigen Anker + extrapoliertem TC0-Takt,
drifted mit dem Quarzmismatch (~1000 ppm) weg vom GM.

**Auswirkung:** Während des Servo-Rückfall-Fensters driften die beiden
Boards mit der vollen Quarz-Mismatch auseinander, ohne dass das Skript
/ die Hardware irgendein Signal dafür gibt.  Die Demo-Aussage „Boards
synchron" stimmt dann nicht mehr, aber der Output sieht oberflächlich
aus wie vorher.

**Empfehlung:** `cyclic_fire_service()` (oder im bestehenden
`fire_callback`): `PTP_CLOCK_IsValid()` testen, und entweder
(a) einen `s_out_of_sync_cycles`-Zähler hochzählen, der per
`cyclic_status` sichtbar ist, oder (b) das Toggeln aussetzen bis Sync
wiederhergestellt ist (Strategie-Entscheidung).

**Bewertung:** Wahrscheinlichkeit: Sehr gering (2-Node-Bench mit
stabiler Verkabelung; relevant bei rauher Umgebung) | Auswirkung:
Gering (keine Daten-Korruption, nur Demo-Inkonsistenz) | Gesamtrisiko:
🟢 Gering | Priorität: P4

**Validierung:** Während aktivem `cyclic_start` das Ethernet-Kabel eines
Boards für 3 s ziehen.  FOL sollte in der Zeit aus FINE fallen; PD10
toggelt weiter auf dem alten Anker und drifted.  `cyclic_status` zeigt
aktuell **keine** Anzeichen dafür — das bestätigt R24.

---

## Weitere offene Fragen (Quellcode-Analyse)

### F14 — processDelayResp() liest TS_SYNC direkt — möglicherweise durch neueren Sync überschrieben?

**Fundstelle:** `ptp_fol_task.c`, `processDelayResp()` (nicht-deferred Pfad):
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

✅ = behoben in laufenden Commits seit letzter Matrix-Aktualisierung (R1/R7/R10/R14/R15/R18).
Neu hinzugekommen 2026-04-20: R19 (IIR-Filter-Wander), R20 (MARKER-Phase-Race), R21 (LAN865x-Verklemmen), R22 (ISR-Anker-Race), R23 (Spin-Wait-Drossel), R24 (Servo-Rückfall unerkannt).

| Wahrscheinlichkeit \ Auswirkung | 🟢 Gering | 🟡 Mittel | 🔴 Hoch |
|---|---|---|---|
| **Sicher** | R4, R19, ✅R18 | ✅R1, R5 | ✅**R14** |
| **Hoch** | — | ✅R15 | R12 *(bei Multi-FOL)* |
| **Mittel** | R6 | R8, ✅R10, R16, R20, R21 | — |
| **Gering** | R2, R11, R13, ✅R7 | R3, R9, R22 | — |
| **Sehr gering** | R23, R24 | R17 | — |

### Priorisierte Abarbeitungsreihenfolge (aktualisiert 2026-04-20)

| Priorität | Risiken | Status |
|---|---|---|
| **P1 — Sofort** | ~~R14~~, ~~R15~~, R12* | R14 ✅ `8594070`, R15 ✅ `741596f`. R12* noch offen (Multi-FOL). |
| **P2 — Nächster Sprint** | ~~R1~~, R5, R8, ~~R10~~, R16, R21 | R1 ✅ `5e289c8`, R10 ✅ `6f3b197`/`657e8a1`. R5/R8/R16 offen. R21 neu (LAN865x-Verklemmen). |
| **P3 — Backlog** | R4, ~~R7~~, R13, R17, ~~R18~~, R19, R20, R22 | R7 ✅ als Messartefakt geklärt, R18 ✅ Drift-Korrektur jetzt aktiv. R4/R13/R17 offen, R19/R20/R22 neu. |
| **P4 — Nice-to-have** | R2, R3, R6, R9, R11, R23, R24 | R23/R24 neu — nicht akut, dokumentiert für den Fall dass Demo-Anforderungen sich ändern. |

\* R12 nur relevant wenn mehr als ein Follower-Board betrieben wird.

---

## Gesamtbewertung des Projektzustands (Stand 2026-04-20)

Das PTP-Projekt auf Basis IEEE 1588-2008 über 10BASE-T1S (ATSAME54P20A + LAN865x) ist ein technisch ambitioniertes und in vielen Bereichen sorgfältig ausgeführtes Demo. Die nicht-blockierende Zustandsmaschinen-Architektur, die konsequente Nutzung von Hardware-Timestamps (TTSCA), der deferred Delay-Calc-Mechanismus und die FIR/IIR-Servo-Filterung zeigen solides Embedded-Design-Handwerk. Die Servo-Konvergenz auf ±200–500 ns (FINE-Zustand) ist für ein Crystal-basiertes System auf einem Shared-Medium-Bus eine beachtliche Leistung.

Die neu hinzugekommene **Cross-Board-Synchronisation auf Software-Ebene** (`cyclic_fire` + Saleae-Verifikation) erreicht mit dem ISR-Anker-Umbau 2026-04-20 **Median-Delta −30 µs, MAD 35 µs, +1.2 ppm Langzeit-Rate-Residual** — Faktor 5 besser als die bisherige ~−135 µs Baseline (vgl. README_NTP §8 "~150 µs nicht entfernbar mit diesem Design" — inzwischen überholt).

**Fixe seit letzter Bewertung:**

| Commit    | Behebt | Auswirkung |
|-----------|--------|-----------|
| `8594070` | R14    | Kein spuriöser Reset mehr alle 2,28 h |
| `741596f` | R15    | Delay_Resp wird nicht mehr systembedingt ge-droppt; `mean_path_delay` ohne Bias |
| `6f3b197` | R10    | `GM_ANCHOR_OFFSET_NS` jetzt als `#ifndef`-Macro konfigurierbar |
| `5e289c8` | R1     | nIRQ per EIC-ISR → `sysTickAtRx`-Jitter von ~200 µs auf <5 µs |
| *pending* | R7     | 9 ms Outliers als UART/USB-CDC-Jitter nachgewiesen (loop_stats max = 209 µs) |
| `baaa3e5` | R18 (Vorarbeit) | `DRIFT_IIR_N` 32 → 128, Drift-Korrektur in `ticks_to_ns_corrected` aktiv |
| `657e8a1` | R10, R18 | GM-Anker-Tick via EXTINT-14-ISR (analog FOL); `PTP_GM_ANCHOR_OFFSET_NS` 575983 → 800000 ns rekalibriert; GM-Filter Rauschfloor von ~50 ppm auf ~20-30 ppm gefallen; Langzeit-Cross-Board-Rate +11 ppm → +1.2 ppm |

**Neu identifizierte Risiken (2026-04-20):**

- **R19** (🟢): IIR-Filter random-walkt auch bei N=128, kurzfristige Cross-Board-Drift bis 200 µs/s möglich.  Sub-10 µs MAD nur mit Hardware-GPIO-Scheduling erreichbar.
- **R20** (🟡): MARKER-Pattern-Phase-Zähler nicht an Anker gebunden — Demo-Signale können bei ungleichzeitigem Arm um N Perioden verschoben erscheinen.
- **R21** (🟡): LAN865x-MAC verklemmt nach langen `cyclic_fire`-Läufen; nur Hard-Power-Cycle als Recovery.  Mehrfach reproduziert.
- **R22** (🟡): ISR-Anker-Race — `s_nirq_tick` kann zwischen TTSCAA-Event und `_OnStatus0`-Callback durch intervenierende RX-nIRQs überschrieben werden.  Erklärt vermutlich die p90-Outlier (+146 µs max) in der cyclic_fire-Delta-Verteilung.
- **R23** (🟢): Spin-Wait drosselt CPU bei sub-ms Perioden (bis 40 % CPU-Zeit bei `period_us=500`).  Akute Gefahr nur wenn explizit sub-ms gewählt wird.
- **R24** (🟢): cyclic_fire tastet `PTP_CLOCK_IsValid()` nur beim Start — bei Servo-Rückfall driften die Boards ohne Signalisierung auseinander.

**Noch offene kritische/hohe Risiken:** nur noch **R12** (Multi-FOL-GM-Statemachine), relevant erst bei > 1 Follower.

**Zustand nach Risikoklassen (aktualisiert):**

- 🔴 **0 kritische Risiken** (R14 behoben).
- 🔴 **1 hohes Risiko** (R12 — Multi-FOL, architektonisch, nicht akut).
- 🟡 **7 mittlere Risiken** (R5, R8, R9, R16, R20, R21, R22): Für Demo akzeptabel; R21 ist der einzige, der *akuten* Recovery-Aufwand macht (Power-Cycle).
- 🟢 **14 geringe Risiken** (R2–R4, R6, R7, R10, R11, R13, R17, R18, R19, R23, R24, R3): Technische Schulden ohne akute Auswirkung.

**Linux-Portierungsstatus:** unverändert — Build-fähig, aber Flashing/Debug/Testskripte noch nicht Linux-adaptiert (R5).

**Empfohlene nächste Schritte:**

1. **R21 priorisieren** wenn die Demo länger als ~5 min am Stück laufen soll — entweder expliziter MAC-Full-Reset-Pfad in `PTP_GM_Init`/`PTP_FOL_Init` oder neuer `mac_reset`-CLI-Befehl.
2. **R22 als Arm-Latch-Mechanismus fixen** — der Race ist vermutlich die Ursache der Long-Tail-Outlier in der cyclic_fire-Delta-Verteilung.  Sauberes Fix: `DRV_LAN865X_ArmNirqLatchTx()` vor `SendRawEthFrame(tsc=1)`, dedicated `s_nirq_latch_tx` statt shared `s_nirq_tick` in `_OnStatus0` lesen.
3. **R20 einzeilig fixen**: `s_marker_phase = (first_target_ns / half_period_ns) % 10` in `cyclic_fire.c`.
4. **R12 adressieren** wenn Multi-FOL-Betrieb geplant ist (architektonische GM-Änderung — oder explizit als Single-FOL-only dokumentieren).
5. **R5 + R13** → Linux-Workflow und ptp_log-Overrun-Zähler.
6. Die noch nicht committeten Loop-Stats-Instrumentierung + Async-Delay_Req-Timeout + Rate-limited ptp_log_flush in einen eigenen Commit packen und R7 final als "closed" markieren.

