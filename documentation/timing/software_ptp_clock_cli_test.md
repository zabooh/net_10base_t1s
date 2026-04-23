# Software PTP Clock — CLI-Test

Dieses Dokument beschreibt wie der nanosekunden-aufgelöste Software-Timestamp
(siehe [software_ptp_clock_design.md](software_ptp_clock_design.md)) auf zwei Boards (Grandmaster + Follower) über den
seriellen CLI getestet werden kann.

---

## Voraussetzung: `ptp_time`-Befehl in der Firmware

Der Test benötigt einen CLI-Befehl `ptp_time` der den aktuellen Wert von
`PTP_CLOCK_GetTime_ns()` ausgibt. Der Befehl muss in `app.c` registriert sein:

```c
static void ptp_time_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();
    if (now_ns == 0u) {
        SYS_CONSOLE_PRINT("ptp_time: not valid\r\n");
        return;
    }
    uint32_t sec = (uint32_t)(now_ns / 1000000000ULL);
    uint32_t ns  = (uint32_t)(now_ns % 1000000000ULL);
    uint32_t h   = sec / 3600u;
    uint32_t m   = (sec % 3600u) / 60u;
    uint32_t s   = sec % 60u;
    SYS_CONSOLE_PRINT("ptp_time: %02lu:%02lu:%02lu.%09lu  drift=%+ldppb\r\n",
                      (unsigned long)h, (unsigned long)m,
                      (unsigned long)s, (unsigned long)ns,
                      (long)PTP_CLOCK_GetDriftPPB());
}
```

Beispielausgabe:
```
ptp_time: 00:02:14.387521043  drift=+12ppb
```

---

## Warum man nicht einfach manuell vergleichen kann

Wenn man auf zwei separaten Terminals `ptp_time` eintippt, liegen die Eingaben
mehrere Sekunden auseinander. Bei einer erwarteten Uhren-Differenz von < 1 µs
ist dieser Abstand völlig unbrauchbar als Vergleich.

**Lösung**: Ein Python-Skript sendet den Befehl auf beiden Boards nahezu
gleichzeitig in zwei parallelen Threads, misst den Sendezeitpunkt per
PC-Hochauflösungstimer (`time.perf_counter_ns()`) und korrigiert die Differenz
entsprechend.

---

## Messprinzip

```
PC-Thread 1                         PC-Thread 2
    │                                   │
    │  t_send_master = perf_counter()   │  t_send_follower = perf_counter()
    │  → "ptp_time\r\n" → COM8         │  → "ptp_time\r\n" → COM10
    │                                   │
    ▼                                   ▼
Board 1 (Master)                   Board 2 (Follower)
    │  ptp_time = WC_master             │  ptp_time = WC_follower
    │  ← Antwort ←                      │  ← Antwort ←
    │                                   │
    ▼                                   ▼
PC: parse WC_master                PC: parse WC_follower

Rohdifferenz  = WC_follower − WC_master
Sendedifferenz = t_send_follower − t_send_master   (PC-Jitter)

Korrigierte Differenz = Rohdifferenz − Sendedifferenz
```

Die **Sendedifferenz** kompensiert den unvermeidlichen PC-Thread-Startjitter
(typisch 100–500 µs auf Windows). Nach Korrektur ist der verbleibende Fehler
durch die Board-seitige UART-Task-Latenz begrenzt (~0–1 ms), die durch
Wiederholung und Mittelung weiter reduziert wird.

---

## Fehlerquellen und deren Behandlung

### 1. PC-Thread-Startjitter (~100–500 µs)
**Zufällig, normalverteilt.** Wird durch die `send_delta`-Korrektur aus dem
Messwert entfernt. Nicht messbar pro Einzelmessung, aber im Mittel = 0.

### 2. Board-seitige UART-Task-Latenz (~0–1 ms)
**Gleichverteilt** zwischen 0 und der Task-Periode (1 ms). Der Erwartungswert
der *Differenz* beider Latenzen ist 0, die Streuung ist ±500 µs.
→ Wird durch Mittelung über N Messungen um Faktor √N reduziert.

### 3. Systematischer Versatz durch konstant unterschiedliche Task-Phasenlage
**Konstant**, erscheint im Mittelwert. Wird durch **Swap-Symmetrisierung**
eliminiert: die Sendreihenfolge (Board1-zuerst vs. Board2-zuerst) wechselt
jede zweite Messung. Der Mittelwert beider Hälften kürzt den systematischen
Versatz heraus.

### 4. PTP-Restjitter der Uhren (< ±500 ns nach FINE)
**Zufällig.** Erscheint in der Stdev, nicht im Mittelwert. Ist die eigentliche
Messgröße der PTP-Qualität.

---

## Testskript `ptp_time_test.py`

Location: `tcpip_iperf_lan865x.X/ptp_time_test.py`

### Usage

```bat
cd tcpip_iperf_lan865x.X
python ptp_time_test.py --master-port COM8 --follower-port COM10
```

Optionale Parameter:

```
--master-port   PORT   Serieller Port des GM-Boards    (default: COM8)
--follower-port PORT   Serieller Port des FOL-Boards   (default: COM10)
--baudrate      BAUD   Baudrate beider Ports           (default: 115200)
--n             N      Anzahl Messungen                (default: 50)
--pause-ms      MS     Pause zwischen Messungen in ms  (default: 150)
--threshold-us  US     PASS/FAIL-Schwelle in µs        (default: 5.0)
--no-swap              Swap-Symmetrisierung deaktivieren
```

### Testablauf

1. Beide serielle Ports öffnen
2. N Messrunden durchführen:
   - Gerade Runden: Master-Thread zuerst gestartet
   - Ungerade Runden: Follower-Thread zuerst gestartet (Swap)
   - Beide Threads senden `ptp_time\r\n` und lesen die Antwort
   - `t_send`-Timestamps per `time.perf_counter_ns()` erfassen
   - Korrigierte Differenz berechnen
3. Ausreisser entfernen (> 2σ vom Mittelwert)
4. Mittelwert, Stdev und SEM (Standard Error of Mean) berechnen
5. PASS/FAIL gegen Schwelle

### Vollständiger Quellcode

```python
#!/usr/bin/env python3
"""
ptp_time_test.py — Vergleicht PTP_CLOCK_GetTime_ns() auf zwei Boards.

Sendet 'ptp_time' gleichzeitig auf beiden COM-Ports, korrigiert den
PC-Thread-Jitter und mittelt über N Messungen.
"""

import argparse
import re
import serial
import statistics
import threading
import time

RE_TIME = re.compile(
    r'ptp_time:\s+(\d+):(\d+):(\d+)\.(\d{9})\s+drift=([+-]?\d+)ppb'
)

def parse_ns(raw):
    """Parst die ptp_time-Ausgabe, gibt (wallclock_ns, drift_ppb) zurück."""
    m = RE_TIME.search(raw)
    if not m:
        return None, None
    h, mn, s = int(m[1]), int(m[2]), int(m[3])
    ns9      = int(m[4])
    drift    = int(m[5])
    wc_ns    = (h * 3600 + mn * 60 + s) * 1_000_000_000 + ns9
    return wc_ns, drift


def query_board(ser, result_dict, key, timeout_s=1.0):
    """
    Sendet 'ptp_time' und liest die Antwort.
    Speichert {'raw': str, 't_send_ns': int} in result_dict[key].
    """
    ser.reset_input_buffer()
    t_send = time.perf_counter_ns()
    ser.write(b'ptp_time\r\n')

    resp     = b''
    deadline = time.perf_counter_ns() + int(timeout_s * 1e9)
    while time.perf_counter_ns() < deadline:
        chunk = ser.read(ser.in_waiting or 1)
        resp += chunk
        # Fertig sobald 'ptp_time:' + Zeilenende vorhanden
        idx = resp.find(b'ptp_time:')
        if idx >= 0 and b'\n' in resp[idx:]:
            break

    result_dict[key] = {
        'raw':      resp.decode(errors='replace'),
        't_send_ns': t_send,
    }


def single_measurement(ser_m, ser_f, swap=False):
    """
    Eine Messung mit optionalem Swap der Startreihenfolge.
    Gibt die korrigierte Zeitdifferenz (FOL − GM) in ns zurück, oder None.
    """
    results = {}
    if swap:
        ta = threading.Thread(target=query_board, args=(ser_f, results, 'follower'))
        tb = threading.Thread(target=query_board, args=(ser_m, results, 'master'))
    else:
        ta = threading.Thread(target=query_board, args=(ser_m, results, 'master'))
        tb = threading.Thread(target=query_board, args=(ser_f, results, 'follower'))

    ta.start(); tb.start()
    ta.join();  tb.join()

    wc_m, drift_m = parse_ns(results.get('master',   {}).get('raw', ''))
    wc_f, drift_f = parse_ns(results.get('follower', {}).get('raw', ''))

    if wc_m is None or wc_f is None:
        return None, None, None

    send_delta = (results['follower']['t_send_ns']
                - results['master']['t_send_ns'])
    diff = (wc_f - wc_m) - send_delta
    return diff, drift_m, drift_f


def run_test(master_port, follower_port, baudrate=115200,
             n=50, pause_ms=150, threshold_us=5.0, no_swap=False):

    print(f"Öffne {master_port} (Master) und {follower_port} (Follower)...")
    ser_m = serial.Serial(master_port,   baudrate, timeout=2)
    ser_f = serial.Serial(follower_port, baudrate, timeout=2)
    time.sleep(0.1)

    samples  = []
    drifts_m = []
    drifts_f = []
    n_err    = 0

    print(f"Starte {n} Messungen (pause={pause_ms} ms) ...\n")

    for i in range(n):
        swap = (not no_swap) and (i % 2 == 1)
        diff, dm, df = single_measurement(ser_m, ser_f, swap=swap)

        if diff is None:
            n_err += 1
            print(f"  [{i+1:3d}] FEHLER (keine Antwort)")
        else:
            samples.append(diff)
            drifts_m.append(dm)
            drifts_f.append(df)
            swap_tag = ' [swap]' if swap else '       '
            print(f"  [{i+1:3d}]{swap_tag}  diff={diff/1000:+9.2f} µs  "
                  f"drift M={dm:+d} F={df:+d} ppb")

        time.sleep(pause_ms / 1000.0)

    ser_m.close()
    ser_f.close()

    print()
    if len(samples) < 3:
        print(f"ERROR: Zu wenige gültige Messungen ({len(samples)}/{n})")
        return

    # ---- Ausreisser entfernen (> 2σ) ----
    mean0  = statistics.mean(samples)
    stdev0 = statistics.stdev(samples)
    clean  = [x for x in samples if abs(x - mean0) <= 2 * stdev0]
    n_out  = len(samples) - len(clean)

    mean_ns  = statistics.mean(clean)
    stdev_ns = statistics.stdev(clean)
    sem_ns   = stdev_ns / (len(clean) ** 0.5)

    mean_drift_m = statistics.mean(drifts_m) if drifts_m else 0
    mean_drift_f = statistics.mean(drifts_f) if drifts_f else 0

    print("=" * 60)
    print(f"Messungen  : {len(clean)}/{n} gültig "
          f"({n_err} Fehler, {n_out} Ausreisser entfernt)")
    print(f"Mittelwert : {mean_ns:+.0f} ns  ({mean_ns/1000:+.3f} µs)")
    print(f"Stdev      : {stdev_ns:.0f} ns  ({stdev_ns/1000:.3f} µs)")
    print(f"SEM (±)    : {sem_ns:.0f} ns  ({sem_ns/1000:.3f} µs)")
    print(f"Drift GM   : {mean_drift_m:+.0f} ppb")
    print(f"Drift FOL  : {mean_drift_f:+.0f} ppb")
    print("=" * 60)

    threshold_ns = threshold_us * 1000.0
    if abs(mean_ns) < threshold_ns:
        print(f"PASS  |mean| = {abs(mean_ns)/1000:.3f} µs < {threshold_us} µs")
    else:
        print(f"FAIL  |mean| = {abs(mean_ns)/1000:.3f} µs >= {threshold_us} µs")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='PTP Software-Clock Synchronisierungstest')
    ap.add_argument('--master-port',   default='COM8',   help='COM-Port des GM-Boards')
    ap.add_argument('--follower-port', default='COM10',  help='COM-Port des FOL-Boards')
    ap.add_argument('--baudrate',      default=115200, type=int)
    ap.add_argument('--n',             default=50,     type=int,   help='Anzahl Messungen')
    ap.add_argument('--pause-ms',      default=150,    type=int,   help='Pause in ms')
    ap.add_argument('--threshold-us',  default=5.0,    type=float, help='PASS-Schwelle µs')
    ap.add_argument('--no-swap',       action='store_true')
    args = ap.parse_args()

    run_test(
        master_port   = args.master_port,
        follower_port = args.follower_port,
        baudrate      = args.baudrate,
        n             = args.n,
        pause_ms      = args.pause_ms,
        threshold_us  = args.threshold_us,
        no_swap       = args.no_swap,
    )
```

---

## Beispielausgabe (PASS nach FINE)

```
Öffne COM8 (Master) und COM10 (Follower)...
Starte 50 Messungen (pause=150 ms) ...

  [  1]         diff=  +312.45 µs  drift M=+12 F=-8 ppb
  [  2] [swap]  diff=  -289.11 µs  drift M=+12 F=-8 ppb
  [  3]         diff=  +441.02 µs  drift M=+11 F=-9 ppb
  [  4] [swap]  diff=  -398.77 µs  drift M=+11 F=-7 ppb
  ...
  [ 50] [swap]  diff=  -201.34 µs  drift M=+10 F=-6 ppb

============================================================
Messungen  : 48/50 gültig (0 Fehler, 2 Ausreisser entfernt)
Mittelwert : +183 ns  (+0.183 µs)
Stdev      : 327451 ns  (327.451 µs)
SEM (±)    : 47267 ns  (47.267 µs)
Drift GM   : +11 ppb
Drift FOL  : -8 ppb
============================================================
PASS  |mean| = 0.183 µs < 5.0 µs
```

### Interpretation

| Wert | Bedeutung |
|------|-----------|
| `Mittelwert = +183 ns` | Systematischer Offset zwischen beiden Software-Uhren (< 1 µs = sehr gut) |
| `Stdev = 327 µs` | Dominiert von UART-Task-Latenz-Jitter (±0–1 ms), **kein Indikator für PTP-Qualität** |
| `SEM = 47 µs` | Messgenauigkeit des Mittels bei N=48 — weiter reduzierbar mit mehr Messungen |
| `Drift GM=+11 ppb, FOL=-8 ppb` | MCU-Kristall läuft mit +11 ppb über / −8 ppb unter der jeweiligen Wallclock |

Die **hohe Stdev** ist erwartet und kein Fehler. Sie spiegelt ausschließlich den
UART-Verarbeitungsjitter der Boards wider (~±500 µs gleichverteilt), nicht die
tatsächliche Zeitdifferenz der PTP-Uhren. Der **Mittelwert** ist die relevante
Kenngröße.

---

## Grenzwerte nach PTP-Konvergenzphase

| PTP-Phase | Erwarteter `|mean_ns|` | Stdev |
|-----------|-----------------------|-------|
| `not valid` (kein PTP) | — | — |
| MATCHFREQ | ~100 µs – 3 ms | ~500 µs |
| HARDSYNC | ~10–100 µs | ~500 µs |
| COARSE | ~1–10 µs | ~500 µs |
| **FINE** | **< 1 µs** | ~500 µs |

---

## Methodik: Swap-Symmetrisierung

Ohne Swap würde ein konstanter Versatz in der Task-Phasenlage beider Boards
systematisch in den Mittelwert eingehen. Das Prinzip der Symmetrisierung:

```
Runde 1 (normal): Thread_M startet zuerst → send_delta = t_F_send − t_M_send > 0
Runde 2 (swap):   Thread_F startet zuerst → send_delta = t_F_send − t_M_send < 0
```

Die Board-seitigen Task-Latenzen sind in beiden Runden gleich, der PC-Jitter
wechselt das Vorzeichen. Im Mittel beider Runden kürzt sich der systematische
PC-Jitter-Anteil heraus. Ohne Swap weist ±100 µs PC-Thread-Jitter selbst bei
N=50 noch ~15 µs Restfehler im Mittelwert auf.

---

## Voraussetzungen

- Python 3.8+
- `pyserial`: `pip install pyserial`
- Firmware mit implementiertem `ptp_time`-Befehl und `PTP_CLOCK_GetTime_ns()`
- PTP aktiv: Board 1 als `master`, Board 2 als `follower` (oder umgekehrt)
- Beide Boards über 10BASE-T1S-Bus verbunden
