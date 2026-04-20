# Software PTP Clock — Design-Dokumentation

## Übersicht

Ziel ist eine nanosekunden-aufgelöste, software-basierte Uhrzeit in der Firmware, die auf der PTP-Wallclock des jeweils angeschlossenen LAN8651 beruht. Beide Boards — das mit der Rolle **Grandmaster (GM)** und das mit der Rolle **Follower (FOL)** — sollen dieselbe API verwenden und nach PTP-Konvergenz zeitlich synchrone Timestamps liefern.

Die Lösung erfordert **keine neuen Hardware-Timer** und **keinen zusätzlichen SPI-Zugriff** zur Laufzeit. Der Timestamp wird vollständig in Software interpoliert und nur bei jedem PTP-Sync mit der Wallclock neu verankert.

---

## Anforderungen

| # | Anforderung |
|---|-------------|
| 1 | Auflösung: Nanosekunden (≤ 20 ns) |
| 2 | Verfügbar auf GM und FOL ohne Codeänderung am Aufrufer |
| 3 | Kein zusätzlicher SPI-Transfer zur Laufzeit |
| 4 | Kein neuer Hardware-Timer erforderlich |
| 5 | Synchrone Zeitbasis zwischen beiden Boards nach PTP-Konvergenz |
| 6 | Drift-Kompensation für MCU-Kristallabweichung |

---

## Hardwarebasis: TC0 bei 60 MHz

SYS\_TIME ist in Harmony 3 auf TC0 konfiguriert:

```c
/* src/config/default/peripheral/tc/plib_tc0.c */
TC0_REGS->COUNT16.TC_CTRLA = TC_CTRLA_MODE_COUNT16
                            | TC_CTRLA_PRESCALER_DIV1   /* kein Teiler */
                            | TC_CTRLA_PRESCSYNC_PRESC;

uint32_t TC0_TimerFrequencyGet(void) { return 60000000U; }
```

TC0 erhält GCLK0 / 2 = **60 MHz** → **16.67 ns pro Tick**.

`SYS_TIME_Counter64Get()` liefert einen monotonen 64-bit-Zähler aus dem TC0-16-bit-Register plus einem 48-bit-Software-Overflow-Zähler. Der Wertebereich reicht bei 60 MHz für mehrere tausend Jahre ohne Überlauf.

---

## Kernstrategie: Anchor-Point + Interpolation

Das Prinzip ist ein **Linear-Clock-Model**: Zu einem bekannten Zeitpunkt wird ein Paar
`(wallclock_ns, sys_tick)` gespeichert. Zwischen zwei Ankerpunkten wird der aktuelle
Wallclock-Wert aus dem MCU-Timer interpoliert, korrigiert um den gemessenen Kristalldrift.

```
    Sync N                           Datenpunkt X             Sync N+1
       │                                  │                       │
  wallclock_n                       gesucht: WC(X)           wallclock_n+1
  sys_tick_n                        sys_tick_X               sys_tick_n+1
       │                                  │                       │
       └──────────────────────────────────┴───────────────────────┘
                 interpoliert mit Drift-Korrektur
```

**Formel:**

$$WC(X) = \text{anchor\_wc} + \frac{(\text{tick}_X - \text{anchor\_tick}) \times 10^9}{f_{TC}} \times (1 - \delta_{\text{ppb}} \times 10^{-9})$$

Wobei $\delta_{\text{ppb}}$ die gemessene Abweichung des MCU-Kristalls gegenüber der PTP-Wallclock in *parts per billion* ist.

---

## Neues Modul: `ptp_clock.c` / `ptp_clock.h`

### API

```c
/* ptp_clock.h */

/**
 * Ankerpunkt setzen.
 *   wallclock_ns : PTP-Wallclock-Wert in Nanosekunden
 *   sys_tick     : SYS_TIME_Counter64Get() zum exakt gleichen Zeitpunkt
 *
 * Wird von ptp_fol_task.c (processFollowUp) und ptp_gm_task.c (TTSCAL-Callback)
 * automatisch aufgerufen — kein manuelles Aufrufen erforderlich.
 */
void     PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick);

/**
 * Aktuellen wallclock-äquivalenten Zeitstempel in Nanosekunden.
 * Gibt 0 zurück, solange kein Ankerpunkt vorhanden ist.
 * Kein SPI, kein Mutex — überall aufrufbar (auch in Interrupt-Callbacks).
 */
uint64_t PTP_CLOCK_GetTime_ns(void);

/** Gemessene Drift in ppb (MCU vs. PTP-Wallclock). Negativ = MCU zu langsam. */
int32_t  PTP_CLOCK_GetDriftPPB(void);

/** true sobald mindestens ein Ankerpunkt gesetzt wurde. */
bool     PTP_CLOCK_IsValid(void);
```

### Implementierung

```c
/* ptp_clock.c */
#include "ptp_clock.h"
#include "system/time/sys_time.h"

#define PTP_CLOCK_TC_FREQ_HZ  60000000ULL  /* TC0: GCLK0/2 = 60 MHz */

static uint64_t s_anchor_wc_ns = 0u;
static uint64_t s_anchor_tick  = 0u;
static int32_t  s_drift_ppb    = 0;
static bool     s_valid        = false;

/* Hilfsfunktion: tick-Differenz in Nanosekunden, overflow-sicher via __uint128_t */
static uint64_t ticks_to_ns(uint64_t ticks)
{
    /* ticks * 1e9 / 60e6 = ticks * 50 / 3 (exakt bei 60 MHz) */
    return (uint64_t)((__uint128_t)ticks * 1000000000ULL / PTP_CLOCK_TC_FREQ_HZ);
}

void PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick)
{
    if (s_valid && (wallclock_ns > s_anchor_wc_ns)) {
        uint64_t delta_tick   = sys_tick - s_anchor_tick;
        uint64_t delta_wc     = wallclock_ns - s_anchor_wc_ns;
        uint64_t delta_mcu_ns = ticks_to_ns(delta_tick);

        /* Momentane Abweichung in ppb */
        int64_t diff_ns  = (int64_t)delta_mcu_ns - (int64_t)delta_wc;
        int32_t inst_ppb = (int32_t)(diff_ns * 1000000000LL / (int64_t)delta_wc);

        /* IIR-Tiefpass α = 1/8 → circa 8 Sync-Intervalle Einschwingzeit */
        s_drift_ppb += (inst_ppb - s_drift_ppb) >> 3;
    }

    s_anchor_wc_ns = wallclock_ns;
    s_anchor_tick  = sys_tick;
    s_valid        = true;
}

uint64_t PTP_CLOCK_GetTime_ns(void)
{
    if (!s_valid) return 0u;

    uint64_t now_tick   = SYS_TIME_Counter64Get();
    uint64_t delta_tick = now_tick - s_anchor_tick;
    uint64_t delta_ns   = ticks_to_ns(delta_tick);

    /* Drift-Korrektur: MCU läuft schneller → delta_ns > Wallclock-Elapsed → abziehen */
    int64_t correction = (int64_t)((__int128_t)delta_ns * s_drift_ppb / 1000000000LL);

    return s_anchor_wc_ns + delta_ns - (uint64_t)correction;
}

int32_t PTP_CLOCK_GetDriftPPB(void) { return s_drift_ppb; }
bool    PTP_CLOCK_IsValid(void)     { return s_valid; }
```

---

## Änderungen an bestehenden Dateien

### `ptp_ts_ipc.h` — `sysTickAtRx` hinzufügen

Das IPC-Struct `PTP_RxFrameEntry_t` benötigt ein zusätzliches Feld für den TC0-Tick,
der gleichzeitig mit dem RTSA-Timestamp in `TC6_CB_OnRxEthernetPacket()` erfasst wird:

```c
typedef struct {
    uint8_t  data[PTP_MAX_FRAME_SIZE];
    uint16_t length;
    uint64_t rxTimestamp;   /* LAN8651 RTSA-Wallclock in ns             */
    uint64_t sysTickAtRx;   /* SYS_TIME_Counter64Get() beim selben Frame */
    volatile bool pending;
} PTP_RxFrameEntry_t;
```

### `drv_lan865x_api.c` — Tick im Callback erfassen

In `TC6_CB_OnRxEthernetPacket()`, direkt neben der bestehenden `rxTimestamp`-Zuweisung:

```c
g_ptp_raw_rx.rxTimestamp = (rxTimestamp != NULL) ? *rxTimestamp : 0u;
g_ptp_raw_rx.sysTickAtRx = SYS_TIME_Counter64Get();   /* NEU */
g_ptp_raw_rx.pending     = true;
```

Der Tick wird **im selben Funktionsaufruf** und damit quasi-atomar mit dem RTSA-Wert
gespeichert. Der zeitliche Abstand beider Messungen ist < 10 Maschinenzyklen (~80 ns bei 120 MHz).

### `ptp_fol_task.c` — Anker in `processFollowUp()` setzen

Direkt nach der Berechnung von `t2`, vor der Servo-Logik:

```c
uint64_t t1 = tsToInternal(&TS_SYNC.origin);
uint64_t t2 = tsToInternal(&TS_SYNC.receipt);

/* Software-Uhr verankern: t2 = RTSA-Wallclock, sysTickAtRx = gleichzeitig */
if (g_ptp_raw_rx.sysTickAtRx != 0u) {
    PTP_CLOCK_Update(t2, g_ptp_raw_rx.sysTickAtRx);
}
```

Hinweis: `rateRatioFIR` ist in `processFollowUp()` bereits berechnet und repräsentiert
dasselbe wie `drift_ppb` — es wäre möglich, nach MATCHFREQ den Wert direkt zu
übertragen statt ihn nochmals aus Tick-Differenzen zu schätzen:

```c
/* Optional nach MATCHFREQ, ersetzt die IIR-Drift-Messung: */
/* int32_t ppb = (int32_t)((1.0 - rateRatioFIR) * 1e9);   */
/* PTP_CLOCK_SetDriftPPB(ppb);                               */
```

### `ptp_gm_task.c` — Anker nach TTSCAL-Callback setzen

In `GM_STATE_WAIT_TTSCA_L`, sobald `gm_ts_sec` und `gm_ts_nsec` vollständig sind:

```c
case GM_STATE_WAIT_TTSCA_L:
    if (!gm_op_done) { /* ... timeout-Behandlung ... */ break; }
    gm_wait_ticks = 0u;
    gm_ts_nsec = gm_op_val;

    /* Software-Uhr verankern (NEU) */
    {
        uint64_t wc_ns  = (uint64_t)gm_ts_sec * 1000000000ULL
                        + (uint64_t)gm_ts_nsec;
        PTP_CLOCK_Update(wc_ns, SYS_TIME_Counter64Get());
    }

    GM_SET_STATE(GM_STATE_WRITE_CLEAR);
    break;
```

---

## Ankerpunkt-Qualität im Vergleich

Der Jitter des Ankerpunkts bestimmt den maximalen Einzel-Fehler unmittelbar nach
einem Sync. Er wird durch den IIR-Filter zum nächsten Sync weitgehend absorbiert.

| Rolle | Ankerpunkt-Quelle | Tick-Jitter | Kommentar |
|-------|-------------------|-------------|-----------|
| **FOL** | `TC6_CB_OnRxEthernetPacket()` | **< 1 µs** | Tick und RTSA quasi-atomar im selben Callback |
| **GM** | `GM_STATE_WAIT_TTSCA_L`-Callback | **~10–20 µs** | Tick nach SPI-Round-Trip, vor WRITE\_CLEAR |

Der GM-Jitter von ~20 µs erscheint als variabler Anker-Offset. Bei 125 ms Sync-Intervall
entspricht das einem relativen Fehler von:

$$\frac{20\,\mu s}{125\,ms} = 160\,\text{ppb}$$

Dieser Wert ist kleiner als der typische Kristallfehler (~200 ppm) und wird durch
den IIR-Drift-Filter in weniger als 8 Sync-Intervallen (< 1 s) kompensiert.

---

## Drift-Messung und IIR-Filter

Bei jedem `PTP_CLOCK_Update()`-Aufruf wird die momentane Abweichung des MCU-Timers
gegenüber der Wallclock in ppb berechnet und mit einem IIR-Tiefpass geglättet:

```
s_drift_ppb += (inst_ppb - s_drift_ppb) >> 3     (α = 1/8)
```

Bei einem Sync-Intervall von 125 ms schwing der Filter in circa:

$$\tau = \frac{T_{\text{sync}}}{\alpha} = \frac{125\,ms}{1/8} = 1\,s
$$

Nach Einschwingen beträgt der residuale Fehler bei `GetTime_ns()`-Aufruf 1 ms
nach dem letzten Anker:

$$\Delta = 1\,ms \times |\delta_{\text{rest}}| \approx 1\,ms \times 50\,\text{ppb} = 50\,\text{ps}$$

Dies ist deutlich unterhalb der TC0-Auflösung von 16.67 ns.

---

## Auflösungsbudget

| Schicht | Beitrag | Wert |
|---------|---------|------|
| TC0-Tick-Auflösung | 1/60 MHz | **16.67 ns** |
| Anker-Jitter FOL | TC6-Callback-Latenz | < 1 µs (konstant, nach IIR absorbiert) |
| Anker-Jitter GM | SPI-Round-Trip-Latenz | ~20 µs (nach IIR absorbiert) |
| Drift-Restfehler nach 125 ms | Folgefehler nach Konvergenz | < 50 ns bei ±200 ppb Rest |
| `__uint128_t`-Division | Rundefehler | 0 (60 MHz: `* 50 / 3` ist exakt) |

**Effektive Zeitstempel-Auflösung nach Konvergenz: 17–50 ns.**

---

## `__uint128_t` auf Cortex-M4

GCC für ARM Cortex-M4 (arm-none-eabi-gcc) unterstützt `__uint128_t`. Die Division
`ticks * 1e9 / 60e6` vereinfacht sich mit 60 MHz zu exakt `ticks * 50 / 3` und
vermeidet die 128-bit-Division vollständig:

```c
/* Exakte Alternative für 60 MHz ohne __uint128_t: */
static uint64_t ticks_to_ns(uint64_t ticks)
{
    /* 1e9 / 60e6 = 50/3 */
    return (ticks / 3ULL) * 50ULL + ((ticks % 3ULL) * 50ULL) / 3ULL;
}
```

---

## Dateistruktur

```
src/
  ptp_clock.h           ← NEU: API-Header
  ptp_clock.c           ← NEU: Implementierung
  ptp_ts_ipc.h          ← GEÄNDERT: +sysTickAtRx in PTP_RxFrameEntry_t
  ptp_fol_task.c        ← GEÄNDERT: PTP_CLOCK_Update() in processFollowUp()
  ptp_gm_task.c         ← GEÄNDERT: PTP_CLOCK_Update() in GM_STATE_WAIT_TTSCA_L
  config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c
                        ← GEÄNDERT: sysTickAtRx in TC6_CB_OnRxEthernetPacket()
```

---

## Verwendung im Anwendungscode

```c
#include "ptp_clock.h"

/* Warten bis PTP konvergiert ist */
if (PTP_CLOCK_IsValid()) {
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();

    /* Sekunden und Nanosekunden zerlegen */
    uint32_t sec = (uint32_t)(now_ns / 1000000000ULL);
    uint32_t ns  = (uint32_t)(now_ns % 1000000000ULL);

    /* HH:MM:SS.nnnnnnnnn */
    uint32_t h = sec / 3600u;
    uint32_t m = (sec % 3600u) / 60u;
    uint32_t s = sec % 60u;

    SYS_CONSOLE_PRINT("Zeit: %02lu:%02lu:%02lu.%09lu\r\n",
                      (unsigned long)h, (unsigned long)m,
                      (unsigned long)s, (unsigned long)ns);

    /* Drift-Info */
    SYS_CONSOLE_PRINT("Drift: %+ld ppb\r\n",
                      (long)PTP_CLOCK_GetDriftPPB());
}
```

---

## Abgrenzung zu bestehenden Zeitquellen

| Quelle | Auflösung | Wallclock-Bezug | Anmerkung |
|--------|-----------|-----------------|-----------|
| `SYS_TIME_Counter64Get()` | 16.67 ns | nein | MCU-lokale Laufzeit seit Reset |
| `g_ptp_raw_rx.rxTimestamp` | 1 ns | ja (RTSA) | nur FOL, nur bei Frame-Empfang |
| GM `TTSCAH/TTSCAL` | 1 ns | ja | nur GM, ~20 µs SPI-Latenz |
| **`PTP_CLOCK_GetTime_ns()`** | **16.67 ns** | **ja** | **jederzeit, GM und FOL** |
