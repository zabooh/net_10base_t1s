# PD10 Cross-Board Synchronicity Test

`pd10_sync_test.py` — focused Saleae-only measurement of how well-aligned
the two boards' visible-blink GPIO edges (PD10) are after PTP lock.
Used to characterise the precision floor of the cyclic_fire timing
backend (TC1 compare-match ISR vs main-loop-polled tfuture).

---

## 1. What it measures

Both boards run `standalone_demo` which decimates the cyclic_fire
half-period callback (250 µs, derived from PTP_CLOCK) down to a 1 Hz
visible blink mirrored onto **PD10** (active-high rectangle, EXT1
header pin 5).  When the boards are PTP-synced the two PD10 signals
should rise within a few µs of each other; the residual offset and
jitter is the cross-board precision floor of the cyclic_fire path.

For each rising edge on Saleae Ch0 (Board A) the test finds the closest
rising edge on Ch1 (Board B) within ±300 ms, reports the signed delta
in µs, and computes statistical summaries over the full capture window.

Saleae sample rate: **50 MS/s** (20 ns resolution) so the achievable
precision floor (~ns range) is well above the sampling resolution.

---

## 2. Prerequisites

- Both boards flashed with the iperf-payload-test branch firmware
  (or any branch that ships `standalone_demo` + `cyclic_fire_isr`).
- Saleae Logic 2 running with at least 2 digital channels enabled,
  Ch0 → Board A PD10, Ch1 → Board B PD10, GND shared.
- Both boards in `DEMO_SYNCED` state (LED2 solid on both).  Either
  press SW1 on one board / SW2 on the other and wait for LED2 solid,
  or send `ptp_mode master` / `ptp_mode follower` via the CLI.

---

## 3. Usage

```
python pd10_sync_test.py                          # interactive, 30 s
python pd10_sync_test.py --duration-s 60          # longer for more samples
python pd10_sync_test.py --label tc1_isr          # tag the output dir
python pd10_sync_test.py --no-prompt              # skip operator prompt
```

Output directory `pd10_sync_<ts>[_label]/`:

- `run_<ts>.log` — full tee'd test log
- `digital.csv` — raw Saleae capture
- `deltas_us.csv` — per-edge timing data (one row per pair)
- `summary.csv` — one row aggregate (n, median, MAD, p99, max, spread)

---

## 4. Measured precision: TC1 ISR vs polled tfuture

Direct A/B run on the same board pair, identical 30 s captures at
50 MS/s, only the `cyclic_fire_use_isr_path(bool)` flag differs.

| Backend | n | median | **MAD** | p99 | spread (max−min) | \|max\| |
|---|---:|---:|---:|---:|---:|---:|
| **TC1 compare-match ISR** (Phase-2 default) | 30 | −96.0 µs | **22.5 µs** | +69.4 µs | 257 µs | 179 µs |
| Polled tfuture + busy-wait spin (legacy)  | 30 | −38.3 µs | 36.9 µs | +126.6 µs | 249 µs | 130 µs |

### Interpretation

- **MAD drops from 36.9 → 22.5 µs (≈ −39 %)** — the cross-board
  jitter floor is now dominated by the PTP servo's residual (~24 µs
  stdev per board, see [ntp_reference.md](../ptp/ntp_reference.md) §8) rather than the main-loop spin
  threshold of the polled path.
- p99 magnitude improves from 126 µs → 69 µs.
- The systematic median bias (≈ −60 to −100 µs depending on board pair)
  comes from the LAN865x RX-pipeline-delay calibration constant
  `PTP_FOL_ANCHOR_OFFSET_NS = 10 ms` which was tuned on a different
  board pair (R25 calibration).  It is **independent of the cyclic_fire
  backend** — both the ISR and tfuture paths show comparable median
  bias on this pair.  See [implementation.md](../ptp/implementation.md) §R25 for the calibration procedure.
- Total spread (max − min) is similar (~250 µs both) because that's
  dominated by occasional servo-jump outliers, not by the timing
  backend.

The TC1 ISR backend therefore delivers what was advertised in the
proposal (Phase 1+2 commit message): the polled-path's jitter floor is
removed and the PTP servo is now the limiting factor.  Reaching below
~20 µs MAD will require improving the servo (longer drift-IIR window,
servo-side anchor capture) — the cyclic_fire path itself can't do
better without a more accurate clock.

---

## 5. Reproducing the A/B test

1. Edit `src/standalone_demo.c` and toggle the line
   `cyclic_fire_use_isr_path(true|false);` in `standalone_demo_init`.
2. `./build.bat` and `python flash.py` to program both boards.
3. Bring the boards into `DEMO_SYNCED` state (CLI shortcut, no buttons
   needed):
   ```
   python -c "import serial,time
   gm=serial.Serial('COM10',115200); fol=serial.Serial('COM8',115200)
   gm.write(b'reset\r\n'); fol.write(b'reset\r\n'); time.sleep(4)
   gm.write(b'ptp_mode master\r\n'); time.sleep(0.5)
   fol.write(b'ptp_mode follower\r\n'); time.sleep(8)
   gm.close(); fol.close()"
   ```
4. `python pd10_sync_test.py --no-prompt --duration-s 30 --label <tag>`
5. Compare `summary.csv` rows from each run.

---

## 6. Caveats

- **Edge count**: the LED1/PD10 rectangle is 1 Hz so a 30 s capture
  yields ~30 rising-edge pairs.  For tighter MAD confidence intervals
  use `--duration-s 60` or longer.
- **Saleae 50 MS/s = 20 ns resolution**: well below the measured MAD,
  so the sampling floor doesn't influence the result.
- **First-edge alignment**: the very first rising edge after a fresh
  cyclic_fire start can differ by up to one half-period from the
  steady-state edges (re-arm catch-up).  The test brackets each Ch0
  edge to the closest Ch1 edge within ±300 ms, so an isolated
  half-period mismatch becomes a single outlier and doesn't shift the
  median.
- **Board-pair median bias** is not tested for here — that's the
  domain of `cyclic_fire_hw_test.py` which calibrates
  `PTP_FOL_ANCHOR_OFFSET_NS` for a specific pair.  This test
  characterises the **MAD / spread** regardless of the absolute
  bias.
