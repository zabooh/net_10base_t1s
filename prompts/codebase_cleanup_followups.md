# Codebase-Cleanup Follow-Ups

Kleine Aufräumarbeiten, die nach der `app.c`-Refaktorisierung (siehe [refactor_app_c_into_modules.md](refactor_app_c_into_modules.md)) sinnvoll wären. Jeder Punkt ist als **eigenständiges Ticket** gedacht — nicht zusammen mit der Refaktorisierung mischen und nicht untereinander bündeln.

## Ticket 1: Magic Numbers → Named Constants

**Scope**: Nur `app.c` / `ptp_rx.c` (nach Refactoring).

- `0x88F7u` (PTP EtherType) → `PTP_ETHERTYPE` in einem passenden Header (z. B. `ptp_rx.h` oder `ptp_ts_ipc.h`).
- `APP_LAN_TIMEOUT_MS` (200u) — ist schon benannt, aber in `lan_regs_cli.c` prüfen, ob Wert konfigurierbar gemacht werden soll (z. B. als `#define` im Header statt `.c`).

**Abgrenzung**: Keine weiteren Magic Numbers jagen — nur diese beiden.

## Ticket 2: Format-String-Audit

**Scope**: `apps/tcpip_iperf_lan865x/firmware/src/*.c`

Systematisch alle `SYS_CONSOLE_PRINT`-Aufrufe durchgehen und prüfen:

- `%ld` muss `(long)` bekommen, `%lu` → `(unsigned long)`
- `%lld` muss `(long long)`, `%llu` → `(unsigned long long)`
- `int64_t` / `uint64_t` konsistent mit `%lld` / `%llu` + Cast formatieren
- `%d` / `%u` nur für `int` / `unsigned`

Beispiel-Inkonsistenz in `app.c`:

```c
SYS_CONSOLE_PRINT("Offset ns  : %ld\r\n", (long)offset);              // int64_t als %ld — auf 32-Bit: OK, verliert Range
SYS_CONSOLE_PRINT("Last offset ns : %lld\r\n", (long long)last);      // int64_t als %lld — korrekt
```

**Abgrenzung**: Nur Format-Strings korrigieren, kein Refactoring der Ausgaben.

## Ticket 3: `Command_Init()`-Rückgabewert

**Scope**: `app.c`

`Command_Init()` gibt `bool` zurück, aber der Rückgabewert wird in `APP_Initialize` ignoriert.

- Entweder: Rückgabewert prüfen und bei `false` einen Error-Print absetzen.
- Oder: Signatur auf `void` ändern.

Empfehlung: Rückgabewert prüfen — ein gescheitertes `SYS_CMD_ADDGRP` ist ein echter Fehler.

## Ticket 4: Header-Guard-Stil vereinheitlichen

**Scope**: `apps/tcpip_iperf_lan865x/firmware/src/*.h`

Derzeit gemischt: `_FOO_H_` (mit Unterstrichen) und `FOO_H` (ohne). Auf **eine** Variante normalisieren. C-Standard reserviert führende Unterstriche + Großbuchstabe für Implementierung — streng genommen sollte **`FOO_H`** gewählt werden.

**Abgrenzung**: Nur Guards anfassen, keine anderen Header-Änderungen.

## Ticket 5: `ticks_per_ms`-Berechnung aus Mainloop

**Scope**: `app.c`

In [app.c:635-641](apps/tcpip_iperf_lan865x/firmware/src/app.c#L635-L641) wird `ticks_per_ms` über `static` + Lazy-Init im IDLE-State berechnet. Besser:

- Einmal in `APP_Initialize` berechnen und als Modul-Global (`static`) halten.
- Entfernt den Branch `if (ticks_per_ms == 0u)` aus der 1-ms-Hot-Path-Prüfung.

## Ticket 6: Naming-Konvention vereinheitlichen

**Scope**: gesamtes `apps/tcpip_iperf_lan865x/firmware/src/`

Aktuell gemischt:

- `PTP_FOL_*`, `PTP_GM_*`, `PTP_CLOCK_*` — ALL CAPS, Unterstriche
- `sw_ntp_*`, `tfuture_*` — snake_case
- `loop_stats_*` — snake_case

**Empfehlung**: Alle öffentlichen APIs auf ein Schema bringen. Da die PTP-Module älter und tiefer verankert sind (mehr Aufrufstellen), ist snake_case → ALL_CAPS wahrscheinlich der kleinere Schmerz.

**Warnung**: Das ist ein **großer** Eingriff mit vielen Call-Site-Änderungen. Nur angehen, wenn Zeit und klare Motivation vorhanden sind. Sonst: Status quo akzeptieren.

## Ticket 7: `g_ptp_raw_rx` kapseln

**Scope**: `ptp_ts_ipc.h`, `ptp_rx.c` (nach Refactoring)

`g_ptp_raw_rx` ist eine globale Struktur, die zwischen Treiber-Callback (`TC6_CB_OnRxEthernetPacket`) und Mainloop (`PTP_RX_Poll`) geteilt wird. Derzeit direkt als `extern` exportiert.

Besser: Zugriff über `PTP_RX_TryGet(out_frame_t *)` / `PTP_RX_Mark_Consumed()`-API kapseln. Spart das direkte `pending`-Flag-Gerangel und macht die Reihenfolge (clear first, then dispatch) explizit.

**Abgrenzung**: Nur API-Kapselung, keine Änderung der Datenstruktur oder des Treiber-Callbacks.
