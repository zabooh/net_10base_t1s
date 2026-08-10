# LAN8651 — Registerzugriff und IEEE-Test-Modi in diesem Arbeitsverzeichnis

> Erstellt 2026-08-10 durch Quellcode-Analyse unter `c:\work\ptp\`.
> Diese Datei beschreibt, **wie man in diesem Projekt auf LAN8650/1-Register zugreift** und
> **welche Transmitter-Test-Modi zur messtechnischen Bus-Überprüfung vorhanden sind** —
> inklusive der Angabe, in welchem Zweig was liegt und was nur dokumentiert, aber nicht
> implementiert ist.

---

## TL;DR

| Frage | Antwort |
|---|---|
| **Registerzugriff zur Laufzeit?** | Ja — `lan_read` / `lan_write` über die serielle Konsole, **in diesem Repository** |
| **IEEE-Transmitter-Test-Modi?** | Ja — dokumentiert und per GUI bedienbar, aber im separaten Repo [t1s_100baset_bridge](https://github.com/zabooh/t1s_100baset_bridge/tree/vscode-migration), **nicht** hier |
| **Braucht `check4` dafür Firmware-Änderungen?** | Nein — `lan_write` genügt, die Test-Modi sind reine Register-Writes |
| **Kabel-/Signalqualitäts-Diagnose?** | Ja — SQI- und CFD-Skriptsammlung, ebenfalls nur in `AN1847` |
| **Bekanntes Doku-Problem** | Die PLCA-Adresse `0x0200004A` in `README_TEST_MODES.md` ist zweifelhaft → [§6](#6-offene-punkte-und-fallstricke) |

---

## 1. Registerzugriff über die CLI (`check4`)

### 1.1 Ort

| Datei | Zweck |
|---|---|
| [check4/…/src/lan_regs_cli.c](apps/tcpip_iperf_lan865x/firmware/src/lan_regs_cli.c) | Implementierung der Kommandos + Zustandsmaschine |
| [check4/…/src/lan_regs_cli.h](apps/tcpip_iperf_lan865x/firmware/src/lan_regs_cli.h) | Schnittstelle (`LAN_REGS_CLI_Register`, `LAN_REGS_CLI_Service`) |
| [check4/…/src/app.c:87](apps/tcpip_iperf_lan865x/firmware/src/app.c#L87) | Registrierung der Kommandogruppe `LAN865X` |
| [check4/…/src/app.c:259](apps/tcpip_iperf_lan865x/firmware/src/app.c#L259) | Takten der Zustandsmaschine aus der Main-Loop |

### 1.2 Kommandos

```
lan_read  <addr_hex>
lan_write <addr_hex> <value_hex>
```

Antwortformat:

```
LAN865X Read OK: Addr=0x00010077 Value=0x00000028
LAN865X Write OK: Addr=0x000308FB Value=0x00002000
```

Fehlerfälle: `LAN865X Read timeout for addr=…`, `LAN865X Read failed for addr=…`,
`ERROR: Previous LAN operation still in progress`.

### 1.3 Adress-Kodierung

Die Adresse ist **32 Bit: obere 16 Bit = MMS (Memory Map Selector), untere 16 Bit = Registeroffset**.
Nachweis: [tc6.c:883 ff.](driver/lan865x/src/dynamic/tc6/tc6.c#L883) —
`SET_VAL(HDR_C_MMS, (addr >> 16), tx_buf)`.

| Block | MMS | Beispiel |
|---|---|---|
| OA Standard (OA_CONFIG0, OA_STATUS0/1, OA_IMASK0/1) | 0 | `lan_read 0x00000000` |
| MAC (Wall Clock MAC_TSH/L/N, MAC_TI, MAC_TA, TX-Timestamps) | 1 | `lan_read 0x00010077` → MAC_TI |
| PHY PCS | 2 | |
| **PHY PMA/PMD — hier liegen die Test-Modi** | **3** | `lan_read 0x000308FB` |
| PHY Vendor-Specific (PLCA, ACMA, CBS, SQI, Cable Fault Diag) | 4 | `lan_read 0x0004CA01` |
| Miscellaneous (Event Capture/Generator, 1PPS, PADCTRL, DEVID) | 10 (0x0A) | `lan_read 0x000A0094` → DEVID |

### 1.4 Verhalten und Grenzen

- **Asynchron.** Die CLI setzt nur einen Zustand; die eigentliche Transaktion läuft in
  `LAN_REGS_CLI_Service` über `DRV_LAN865X_ReadRegister()` / `DRV_LAN865X_WriteRegister()`,
  das Ergebnis kommt per Callback.
- **`protected = true`** — alle Zugriffe laufen mit OA-TC6-Protection-Bytes.
- **Nur eine Operation gleichzeitig.**
- **Timeout 200 ms** (`APP_LAN_TIMEOUT_MS`).
- Parsing über `strtoul(…, NULL, 0)` — `0x`-Präfix wird erkannt, Dezimaleingabe funktioniert auch.
- Der Treiber bietet zusätzlich `DRV_LAN865X_ReadModifyWriteRegister()`; das ist in der CLI
  **nicht** exponiert. Read-Modify-Write muss host-seitig aus `lan_read` + `lan_write`
  zusammengesetzt werden.

---

## 2. IEEE-Transmitter-Test-Modi (`AN1847`)

Primärquelle im Repo:
[AN1847/…/README_TEST_MODES.md](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/README_TEST_MODES.md)
(Stand 10. März 2026), abgeleitet aus dem LAN8650/1-Datenblatt §11 und IEEE Std 802.3-2022 §147.5.2.

### 2.1 T1STSTCTL — Test Mode Control

| Eigenschaft | Wert |
|---|---|
| MMS / Offset | 3 / `0x08FB` |
| Vollständige Adresse | `0x000308FB` |
| Bitfeld | 15:13 (`TSTCTL[2:0]`) |
| Access / Reset | R/W / `0x0000` |

| TSTCTL | Modus | Prüfzweck | Messmittel |
|---|---|---|---|
| `000` | Normal Operation | — | — |
| `001` | Test Mode 1 | Transmitter Output Voltage, Timing Jitter | Oszilloskop |
| `010` | Test Mode 2 | Transmitter Output Droop (Langzeit-Signalstabilität) | Oszilloskop |
| `011` | Test Mode 3 | Transmitter PSD Mask (Störaussendung, EMV) | Spektrumanalysator |
| `100` | Test Mode 4 | Transmitter High Impedance | Kabeldiagnose, Multidrop-Messung |
| `101`–`111` | Reserved | — | — |

### 2.2 T1SPMACTL — PMA Control

| Eigenschaft | Wert |
|---|---|
| MMS / Offset | 3 / `0x08F9` |
| Vollständige Adresse | `0x000308F9` |

| Bit | Name | Funktion | Hinweis |
|---|---|---|---|
| 15 | `RST` | PMA Reset (self-clearing) | nicht zusammen mit anderen Bits setzen |
| 14 | `TXD` | Transmit Disable | für normalen Betrieb 0 |
| 11 | `LPE` | Low Power Enable | entspricht Power-Down in BASIC_CONTROL |
| 10 | `MDE` | Multidrop Enable | laut Doku ohne Effekt auf Device-Operation |
| 0 | `LBE` | PMA Loopback Enable | siehe Einschränkungen unten |

**PMA-Loopback-Datenpfad:** MAC → PCS Scrambler/Descrambler → 4B/5B Encoder/Decoder →
PMA Differential Manchester Encoder/Decoder → zurück zum MAC.

**Einschränkungen des Loopback:**
- PLCA muss deaktiviert **oder** als Coordinator (Local ID = 0) konfiguriert sein.
- Keine externe Kommunikation während aktivem Loopback.
- Nach Deaktivierung Reset erforderlich, um regulären Betrieb wiederherzustellen.

### 2.3 Weitere relevante Register

| Register | Name | MMS | Offset | Adresse | Funktion |
|---|---|---|---|---|---|
| T1STSTCTL | Test Mode Control | 3 | `0x08FB` | `0x000308FB` | IEEE Test-Modi 1–4 |
| T1SPMACTL | PMA Control | 3 | `0x08F9` | `0x000308F9` | Loopback, TXD, LPE, RST |
| T1SPMASTS | PMA Status | 3 | `0x08FA` | `0x000308FA` | PMA Status (read-only) |

---

## 3. Bedienung

### 3.1 Direkt am Terminal (funktioniert auch in `check4`)

```
# Status lesen
lan_read  0x000308FB          # aktiver Test-Modus
lan_read  0x000308F9          # PMA-Control
lan_read  0x000308FA          # PMA-Status

# IEEE-Test-Modi aktivieren
lan_write 0x000308FB 0x2000   # Test Mode 1 — Voltage & Jitter
lan_write 0x000308FB 0x4000   # Test Mode 2 — Output Droop
lan_write 0x000308FB 0x6000   # Test Mode 3 — PSD Mask
lan_write 0x000308FB 0x8000   # Test Mode 4 — High Impedance

# PMA-Loopback
lan_write 0x000308F9 0x0001   # LBE = 1
lan_write 0x000308F9 0x0000   # LBE = 0  (danach Reset)

# Immer am Ende: zurück auf Normalbetrieb
lan_write 0x000308FB 0x0000
lan_write 0x000308F9 0x0000
```

Der Wert ergibt sich aus `(mode & 0x7) << 13`.

### 3.2 Über die GUI (`AN1847`)

[AN1847/…/gui/lan8651_bitfield_gui.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/gui/lan8651_bitfield_gui.py)
— Tab **„🧪 Test Modes"**, angelegt in
[`_create_test_modes_tab()` ab Zeile 1127](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/gui/lan8651_bitfield_gui.py#L1127).

Enthaltene Bedienelemente:

| Element | Funktion | Implementierung |
|---|---|---|
| Dropdown + „▶ Activate Selected" | schreibt `(mode & 0x7) << 13` nach `0x000308FB` | [Zeile 1193](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/gui/lan8651_bitfield_gui.py#L1193) |
| „⏹ Normal (000)" | schreibt `0x0000` nach `0x000308FB` | `set_test_mode_normal()` |
| „📖 Read Status" | liest `0x000308FB` und dekodiert den Modus | `read_test_mode_status()` |
| PMA Loopback ein/aus | Read-Modify-Write auf Bit 0 von `0x000308F9` | `set_pma_loopback()` |
| PMA TX Disable ein/aus | Read-Modify-Write auf Bit 14 von `0x000308F9` | `set_pma_txd()` |
| „📖 Read PMA Control" | liest `0x000308F9` und dekodiert die Bits | `read_pma_control_status()` |

**Wichtig zum Verständnis der Architektur:** Die GUI enthält **keinen** eigenen SPI-Treiber.
Sie öffnet einen COM-Port mit `pyserial` (Standard COM8 bzw. COM9, 115200 Baud) und schickt
genau die Textkommandos aus [§1.2](#12-kommandos):

```python
command = f"lan_write 0x{address:08X} 0x{value:08X}"   # Zeile 1267
command = f"lan_read 0x{address:08X}"                  # Zeile 1293
```

Damit ist die GUI **an jede Firmware anschließbar, die `lan_read`/`lan_write` anbietet** —
also auch an den PTP-Stand in `check4`.

---

## 4. Ergänzende Diagnose-Werkzeuge (`AN1847`)

Diese bewerten nicht den Sender, sondern Empfangsqualität und Kabel:

| Datei | Zweck |
|---|---|
| [lan8651_cable_diagnostic.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/lan8651_cable_diagnostic.py) | Kabeldiagnose |
| [lan8651_cfd_test.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/lan8651_cfd_test.py) | Cable Fault Diagnostics |
| [lan8651_1760_sqi_diagnostics.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/lan8651_1760_sqi_diagnostics.py) | SQI-Auswertung nach App-Note |
| [sqi_decoder.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/sqi_decoder.py) / [sqi_correct_registers.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/sqi_correct_registers.py) | Dekodierung, Registeradressen |
| [lan8651_complete_register_scan.py](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/lan8651_complete_register_scan.py) | Registerabbild vor/nach einem Test |
| [README_AN1740_SQI_DIAGNOSTICS.md](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/README_AN1740_SQI_DIAGNOSTICS.md) | Hintergrund |
| [PROMPT_lan8651_sqi_diagnostics.md](https://github.com/zabooh/t1s_100baset_bridge/blob/vscode-migration/firmware/T1S_100BaseT_Bridge.X/PROMPT_lan8651_sqi_diagnostics.md) | Spezifikation: SQI 0–7, TDR-Längenschätzung, Open/Short/Miswiring |

---

## 5. Was in `check4` (PTP-Stand) fehlt

- **Kein Test-Mode-Code, keine Test-Mode-CLI.** Nur die generische Register-CLI aus §1.
  Das ist funktional ausreichend — die Register-Writes aus §3.1 wirken dort unverändert.
- **SQI wird konfiguriert, aber nie ausgelesen.** Die Init-Map schreibt
  `0x000400B0 = 0x00000103` („SQI CONFIGURATION") und behandelt `0x000400AD` per
  Read-Modify-Write, entsprechend Tabelle 2 der Configuration-App-Note
  (siehe [drv_lan865x_api.c](apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c)).
  Es gibt keinen Pfad, der einen SQI-Wert an die Konsole meldet.
- **Kein Register-Dump-Kommando.** Ein Registerabbild muss host-seitig aus vielen
  `lan_read`-Aufrufen zusammengesetzt werden — so arbeiten die Skripte in `AN1847`.

---

## 6. Offene Punkte und Fallstricke

### 6.1 Zweifelhafte PLCA-Adresse in der Test-Mode-Doku

`README_TEST_MODES.md` empfiehlt vor dem Loopback-Test:

```
lan_write 0x0200004A 0x0000   # PLCA_CTRL_STS deaktiviert
```

Das entspricht **MMS 2 / Offset 0x004A**. Laut der Zusammenfassung der
LAN8650/1-Configuration-App-Note in
[check4/…/documentation/pdf/readme_pdf.md](documentation/pdf/readme_pdf.md)
liegt `PLCA_CTRL0` beim LAN8651 dagegen auf **MMS 4 / 0xCA01** (`0x0004CA01`) und
`PLCA_CTRL1` auf `0xCA02`.

Dass im selben Ordner drei Skripte namens `test_plca_address_comparison.py`,
`test_plca_address_variants.py` und `test_firmware_plca_addresses.py` existieren,
deutet darauf hin, dass die PLCA-Adressierung dort tatsächlich unklar war.

Diese drei sind absichtlich **nicht verlinkt**: sie liegen ausschließlich lokal in
`AN1847/t1s_100baset_bridge/firmware/T1S_100BaseT_Bridge.X/` und werden dort von
`.gitignore:77` (`test_*.py`) erfasst — sie stehen in keinem Commit und damit auch
nicht auf GitHub. Wer die Frage abschließend klären will, braucht diese Dateien von
der Arbeitsplatte; sie sind über das Repository nicht beschaffbar.

**→ Vor einem Loopback-Test die PLCA-Adresse am Gerät verifizieren** (`lan_read 0x0004CA01`
gegen `lan_read 0x0200004A` vergleichen und mit dem erwarteten Reset-/Konfigurationswert
abgleichen), nicht blind aus der Doku übernehmen.

### 6.2 Status der Dokumentation

`README_TEST_MODES.md` schließt mit „bereit für Implementation" und listet unter
„Empfohlene neue Test-Tools" `test_modes_control.py` und `loopback_diagnostic.py` als
*zu entwickeln*. Diese beiden Dateien existieren nicht. Umgesetzt wurde stattdessen der
GUI-Tab aus §3.2 — die Doku ist an dieser Stelle also veraltet, nicht die Implementierung.

### 6.3 Betriebliche Warnungen (aus der Doku, unverändert übernommen)

- Test-Modi erzeugen **Störaussendungen** — nur in kontrollierter, isolierter Umgebung nutzen,
  EMV-Vorschriften beachten.
- **Test Mode 4 (High-Z)** und **TXD** nehmen den Knoten vom Bus; auf einem Multidrop-Segment
  ist das genau erwünscht, wenn man den Bus *ohne* diesen Sender messen will — es stört aber
  laufende Kommunikation.
- **PMA-Loopback** unterbricht die externe Kommunikation vollständig.
- Nach jeder Testsequenz auf `TSTCTL = 000` und `T1SPMACTL = 0x0000` zurückstellen; nach
  Loopback zusätzlich Reset.

---

## 7. Referenzen

| Quelle | Ort |
|---|---|
| LAN8650/1 Datenblatt, §11 Register Descriptions | [check4/…/documentation/pdf/LAN8650-1-Data-Sheet-60001734.pdf](documentation/pdf/LAN8650-1-Data-Sheet-60001734.pdf) |
| LAN8650/1 Configuration App-Note (Registersequenzen, PLCA, SQI) | [check4/…/documentation/pdf/LAN8650-1-Configuration-Appnote-60001760.pdf](documentation/pdf/LAN8650-1-Configuration-Appnote-60001760.pdf) |
| Erratum s1 (DEVID statt OA_PHYID lesen) | [check4/…/documentation/pdf/lan86xx_family.md](documentation/pdf/lan86xx_family.md) |
| MMS-Blockaufteilung MAC / PHY / Misc | [check4/…/documentation/pdf/lan865x_vs_lan867x_architecture.md](documentation/pdf/lan865x_vs_lan867x_architecture.md) |
| IEEE Std 802.3-2022, Clause 147.5.2 | extern |
| OPEN Alliance 10BASE-T1x MAC-PHY Serial Interface (TC6) | extern |
| Verwendungsbeispiel `lan_read` für Quarz-Abweichung | [check4/…/documentation/features/tfuture.md:487](documentation/features/tfuture.md#L487) |
