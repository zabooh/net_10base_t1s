# Refaktorisierung: `app.c` in 6 Module aufteilen

## Kontext

Das Projekt ist PTP-10BASE-T1S-Firmware auf ATSAME54P20A + LAN865x unter Microchip Harmony 3. Die Datei `apps/tcpip_iperf_lan865x/firmware/src/app.c` (~795 Zeilen) vermischt Anwendungs-State-Machine mit CLI-Commands für fünf verschiedene Subsysteme sowie einem PTP-RX-Packet-Handler. Ziel: `app.c` ausdünnen, CLI-Code und RX-Handler in themenbezogene Module verschieben.

## Zielstruktur

Alle neuen Dateien unter `apps/tcpip_iperf_lan865x/firmware/src/`:

### 1. `lan_regs_cli.c/h`

Extrahieren: State-Machine + Callbacks + `lan_read`/`lan_write` Commands aus `app.c` (L83-154, L658-725).

- State (`app_lan_state_t`, Variablen, Callbacks) wird **modul-intern** (static).
- Die State-Machine-Servicing-Logik aus `APP_Tasks` (L659-725) wird als `LAN_REGS_CLI_Service(uint64_t current_tick, uint64_t ticks_per_ms)` exportiert.
- Öffentlich: `void LAN_REGS_CLI_Register(void)`, `void LAN_REGS_CLI_Service(uint64_t, uint64_t)`.

### 2. `ptp_cli.c/h`

Extrahieren: `ptp_mode`, `ptp_status`, `ptp_time`, `ptp_interval`, `ptp_offset`, `ptp_reset`, `ptp_trace`, `ptp_dst`, `clk_set`, `clk_get`, `ptp_offset_reset`, `ptp_offset_dump` (L156-322 in `app.c`, außer `loop_stats`).

- Öffentlich: `void PTP_CLI_Register(void)`.

### 3. `sw_ntp_cli.c/h`

Extrahieren: IP-Parser `sw_ntp_parse_ip` + alle `sw_ntp_*` Commands (L329-429).

- IP-Parser bleibt `static` im Modul.
- Öffentlich: `void SW_NTP_CLI_Register(void)`.

### 4. `tfuture_cli.c/h`

Extrahieren: Alle `tfuture_*` Commands (L435-496).

- Öffentlich: `void TFUTURE_CLI_Register(void)`.

### 5. `loop_stats_cli.c/h`

Extrahieren: `loop_stats_cmd` aus `app.c` (L302-310).

- Öffentlich: `void LOOP_STATS_CLI_Register(void)`.

### 6. `ptp_rx.c/h`

Extrahieren: `pktEth0Handler` + Stack-Registrierung + Frame-Dispatch.

- `pktEth0Handler` wird modul-intern (static).
- Öffentlich:
  - `bool PTP_RX_Register(TCPIP_NET_HANDLE hNet)` — kapselt `TCPIP_STACK_PacketHandlerRegister`. Gibt `true` zurück bei Erfolg.
  - `void PTP_RX_Poll(void)` — übernimmt den `g_ptp_raw_rx.pending`-Block aus `APP_Tasks` (L754-766), dispatcht je nach `PTP_FOL_GetMode()` an `PTP_FOL_OnFrame` oder `PTP_GM_OnDelayReq`.

## Was in `app.c` bleibt

- `APP_DATA appData`, `APP_Initialize`, `APP_Tasks`
- State-Machine: `APP_STATE_INIT` → `APP_STATE_SERVICE_TASKS` → `APP_STATE_IDLE`
- Erst-Aufruf von `PTP_FOL_Init` + `PTP_FOL_SetMac` (L643-655)
- 1-ms-Tick-Dispatcher: `PTP_GM_Service`, `PTP_FOL_Service`, `sw_ntp_service`, `tfuture_service`
- GM-Reinit-Recovery (L771-778, Tracking `lan865x_prev_ready`)
- `Command_Init()` wird **reiner Aggregator**:

  ```c
  static bool Command_Init(void) {
      LAN_REGS_CLI_Register();
      PTP_CLI_Register();
      SW_NTP_CLI_Register();
      TFUTURE_CLI_Register();
      LOOP_STATS_CLI_Register();
      return true;
  }
  ```

- `APP_Initialize` ruft zusätzlich weiterhin `sw_ntp_init()` und `tfuture_init()`
- In `APP_STATE_SERVICE_TASKS`: `PTP_RX_Register(eth0_net_hd)` statt des direkten `TCPIP_STACK_PacketHandlerRegister`-Aufrufs
- In `APP_STATE_IDLE`: `LAN_REGS_CLI_Service(current_tick, ticks_per_ms)` statt der inline State-Machine, `PTP_RX_Poll()` statt des `g_ptp_raw_rx.pending`-Blocks

## Konventionen

- Jedes neue `.h` bekommt `#ifndef`-Guards im Stil der existierenden Header im selben Ordner (z. B. `_SW_NTP_CLI_H_`).
- Jedes `.c` inkludiert nur, was es wirklich braucht — nicht pauschal alle Includes aus `app.c` übernehmen.
- `static`-Funktionen bleiben `static`; nur `*_Register()` / `*_Service()` / `*_Poll()` werden exportiert.
- Keine neuen Abhängigkeiten, kein Refactoring der darunterliegenden APIs (`PTP_FOL_*`, `PTP_GM_*`, `sw_ntp_*`, `tfuture_*`, `ptp_offset_trace_*`, `PTP_CLOCK_*`).
- Keine Kommentare hinzufügen, die nur erklären, *dass* Code verschoben wurde.

## Scope-Schutz (explizit verboten)

Diese Refaktorisierung ist **reines Verschieben**. Der Agent darf **nicht**:

- APIs, Funktionen oder Variablen umbenennen.
- Format-Strings in `SYS_CONSOLE_PRINT` ändern (auch nicht zur Konsistenz).
- Neue Typen, Enums, `#define`s, Konstanten oder `static_assert`s einführen.
- Magic Numbers (z. B. `0x88F7u`, `APP_LAN_TIMEOUT_MS`) in Konstanten umwandeln.
- Header-Guard-Stil existierender Dateien „vereinheitlichen".
- Vorhandene Bugs „nebenbei" fixen.
- Rückgabewerte (z. B. von `Command_Init()`) anders behandeln als bisher.

Jede dieser Verbesserungen gehört in ein separates Folge-Ticket (siehe `prompts/codebase_cleanup_followups.md`).

## Build-System

MPLAB-X-Projekt: Neue `.c`-Dateien müssen in die Source-Liste. Prüfe:

- `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/tcpip_iperf_lan865x/default/user.cmake` — dort die neuen Dateien ergänzen.
- MPLAB-X `.project`-Konfiguration falls relevant (Dateien werden meist automatisch gepickt, aber verifizieren).

## Git-Workflow

- Neuer Branch: `refactor/app-split` (vom aktuellen `fixes` ausgehend).
- **Ein Commit pro Modul**, in der Reihenfolge: `lan_regs_cli`, `ptp_cli`, `sw_ntp_cli`, `tfuture_cli`, `loop_stats_cli`, `ptp_rx`.
- Commit-Message-Format: `refactor(app): extract <modul> from app.c`.
- Nach **jedem** Commit muss das Projekt compile-clean sein und die CLI-Commands unverändert registriert (in jedem Schritt `lan_cmd_tbl` entsprechend umverdrahten).
- Abschluss-Commit: `refactor(app): command aggregator + user.cmake wiring` — stellt `Command_Init` auf den reinen Aggregator um und ergänzt die `user.cmake`.

## Verifikation

1. Projekt kompiliert nach **jedem** Commit ohne Warnings.
2. Vor/Nach-Diff: Alle 26 Commands in der `lan_cmd_tbl` weiterhin registriert — Liste am Ende durch `help`-Dump auf der Konsole verifizierbar.
3. Bestehende Python-Tests unter `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/` müssen ohne Anpassung durchlaufen.
4. Manuelles HW-Testen nicht erforderlich — der Agent soll nur die Refaktorisierung durchführen und compile-clean liefern.

## Randbedingungen

- **Keine** Verhaltensänderungen — reines Verschieben + Umhüllen.
- `MyEth0HandlerParam` und `lan865x_prev_ready` entsprechend verschieben (ersteres nach `ptp_rx.c`, letzteres bleibt in `app.c`, da es im State-Machine-Kontext der App bleibt).
- Falls unklar, ob eine Variable modul-intern oder in `app.c` gehört: Als Static ins neue Modul, sofern sie außerhalb der extrahierten Funktionen nicht referenziert wird.

## Abschluss-Report

Nach Fertigstellung: Zeilenzahl der neuen `app.c` und Gesamtzeilen der 6 neuen Module im Vergleich zum Original nennen.
