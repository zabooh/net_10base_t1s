# tfuture — Coordinated Firing at an Absolute PTP_CLOCK Time

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. How it Works](#2-how-it-works)
  - [2.1 The idea in one paragraph](#21-the-idea-in-one-paragraph)
  - [2.2 Timing mechanism — hybrid precision](#22-timing-mechanism--hybrid-precision)
  - [2.3 wc_ns → TC0 tick conversion](#23-wc_ns--tc0-tick-conversion)
- [3. Module Structure](#3-module-structure)
- [4. CLI Commands](#4-cli-commands)
- [5. Firmware Flow](#5-firmware-flow)
  - [5.1 Arm](#51-arm)
  - [5.2 Service](#52-service)
  - [5.3 Ring buffer](#53-ring-buffer)
- [6. Test Script](#6-test-script)
- [7. Expected Results](#7-expected-results)
- [8. Limitations and Next Steps](#8-limitations-and-next-steps)

---

## 1. Purpose

`tfuture` (time-future) schedules a **single-shot firing event** at a specified
absolute point on the PTP_CLOCK timeline. When two HW-PTP-synchronised boards
arm the **same** target value, each fires when its own PTP_CLOCK reaches that
value — which, because HW-PTP keeps the two clocks aligned to ~50 ns, produces
two physical firings that occur within hundreds of nanoseconds of each other.

The module is the capstone of the time-sync chain documented in
[README_PTP.md](README_PTP.md) and [README_NTP.md](README_NTP.md):

```
 IEEE 1588 protocol           → ptp_trace_debug_test.py
        │
        ▼
 LAN865x HW timestamps        → ptp_offset_capture.py  (~50 ns at SFD)
        │
        ▼
 PTP_CLOCK anchor + TC0 interp → PTP_CLOCK_GetTime_ns()
        │
        ▼
 Application reading clock    → sw_ntp_vs_ptp_test.py  (~25 µs SW floor)
        │
        ▼
 Application ACTING on clock  → tfuture_sync_test.py   (this module)
```

Where SW-NTP only *observes* the clock, `tfuture` *acts* on it. It closes the
loop from "we have a synchronised clock" to "we can perform a coordinated
action at a specified moment." This is the end-user-visible payoff of the whole
PTP stack.

---

## 2. How it Works

### 2.1 The idea in one paragraph

Each board, independently, is told an absolute PTP_CLOCK value `T_ns`. The
module records the current `(wc_ns, tc0_tick)` pair, projects `T_ns` onto the
raw TC0 tick counter (with drift correction), then every main-loop iteration
checks "has the TC0 counter reached my target tick yet?" — fires once it does,
and logs the actual PTP_CLOCK-read at the firing moment. Since both boards use
the same `T_ns` and their PTP_CLOCKs are aligned, both fire nearly simultaneously.

### 2.2 Timing mechanism — hybrid precision

Two regimes in a single service routine:

1. **Coarse phase** (target is more than 1 ms away):
   `tfuture_service()` is called from the main loop every iteration, compares
   `SYS_TIME_Counter64Get()` against the stored `target_tick`, and returns
   immediately if there is still more than 60 000 ticks (1 ms at 60 MHz) to go.
2. **Tight spin** (target within 1 ms):
   Service enters a busy-wait on `SYS_TIME_Counter64Get()` until the exact
   target tick is reached, captures `PTP_CLOCK_GetTime_ns()` one instruction
   later, and transitions back to IDLE.

This gives near-tick-level precision (~17 ns resolution, typically <1 µs
jitter) without requiring direct TC-compare interrupt programming (registers,
NVIC, IRQ handler). The trade-off is that a single firing can block the main
loop for up to 1 ms; other services (PTP, SW-NTP, TCP/IP) are briefly paused.
Acceptable for a diagnostic module armed every few seconds; not suitable for
high-frequency continuous operation.

### 2.3 wc_ns → TC0 tick conversion

The PTP_CLOCK output is computed as:

```
wc_ns = anchor_wc_ns + ticks_to_ns_corrected(current_tick − anchor_tick, drift_ppb)
```

where `ticks_to_ns_corrected(t, d) = t × (50/3) × (1 − d/1e9)` — TC0 ticks at
60 MHz, scaled by the filtered drift estimate.

To arm at absolute `target_wc_ns`, we need the inverse:

```
delta_wc_ns  = target_wc_ns − current_wc_ns
base_ticks   = delta_wc_ns × 3 / 50                     (nominal)
adj_ticks    = base_ticks × drift_ppb / 1e9              (first-order correction)
target_tick  = current_tick + base_ticks + adj_ticks
```

The module reads `(current_wc_ns, current_tick)` atomically at arm time — close
enough in time that the difference between the two reads is negligible (~ns).
The projection is exact only at arm time; if the PTP servo later adjusts
`drift_ppb` or re-anchors `(anchor_wc_ns, anchor_tick)`, the fixed `target_tick`
drifts slightly from the original `target_wc_ns` (worst case ~5 µs over 5 s of
lead time at moderate drift change). This is below the module's own
firing-jitter floor, so ignoring it is acceptable for the MVP.

---

## 3. Module Structure

```
apps/tcpip_iperf_lan865x/firmware/src/
├── tfuture.h          # Public API + state enum + ring-buffer size
└── tfuture.c          # Arm/cancel, service, ring buffer, dump
```

Integration in `app.c`:

| Location                 | Call                     |
|--------------------------|--------------------------|
| `APP_Initialize()`       | `tfuture_init()`         |
| `APP_Tasks()` STATE_IDLE | `tfuture_service()`      |

`tfuture_service()` is called on every main-loop iteration, not once per ms.
This is essential so the tight-spin phase starts as soon as the target is
within the 1-ms window.

Build integration (add to `user.cmake`):

```cmake
target_sources(... PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/tfuture.c"
)
```

---

## 4. CLI Commands

| Command                | Description                                                                 |
|------------------------|-----------------------------------------------------------------------------|
| `tfuture_at <abs_ns>`  | Arm firing at absolute PTP_CLOCK nanosecond value.                          |
| `tfuture_in <ms>`      | Convenience: arm at current PTP_CLOCK + `<ms>` milliseconds.                |
| `tfuture_cancel`       | Cancel a pending firing. No effect if IDLE/FIRED.                           |
| `tfuture_status`       | Show state, total fires, last target_ns, last actual_ns, last delta.        |
| `tfuture_reset`        | Clear the ring buffer (does not affect any pending arm).                    |
| `tfuture_dump`         | Dump all recorded fires, one line per record: `<target_ns> <actual_ns> <delta_ns>`. |

Arming fails (and prints `tfuture_at FAIL`) when PTP_CLOCK is not yet valid,
the target is in the past, or another firing is already pending.

Dump format:

```
tfuture_dump: start count=20 overwrites=0 capacity=256
1234567890123456 1234567890123488 +32
1234567892123456 1234567892123620 +164
...
tfuture_dump: end
```

The third column (`delta = actual − target`) is redundant with the first two
but is included so Python parsers can read it directly without computation.

---

## 5. Firmware Flow

### 5.1 Arm

```c
bool tfuture_arm_at_ns(uint64_t target_wc_ns)
{
    if (state == PENDING)        return false;  // must cancel first
    if (!PTP_CLOCK_IsValid())    return false;  // need live clock

    uint64_t now_wc   = PTP_CLOCK_GetTime_ns();
    uint64_t now_tick = SYS_TIME_Counter64Get();
    if (target_wc_ns <= now_wc)  return false;  // must be future

    delta_wc    = target_wc_ns − now_wc;
    base_ticks  = delta_wc × 3 / 50;                 // exact integer 50/3
    adj         = base_ticks × drift_ppb / 1e9;      // first-order drift
    target_tick = now_tick + base_ticks + adj;

    state       = PENDING;
    return true;
}
```

### 5.2 Service

```c
void tfuture_service(void)
{
    if (state != PENDING)  return;

    int64_t ticks_left = (int64_t)(target_tick − SYS_TIME_Counter64Get());
    if (ticks_left > 60000)  return;                // >1 ms, come back later

    // Within 1 ms — tight spin
    while ((int64_t)(target_tick − SYS_TIME_Counter64Get()) > 0) { /* spin */ }

    // Fire!
    actual_ns = PTP_CLOCK_GetTime_ns();
    trace_record(target_ns, actual_ns);
    fires++;
    state = IDLE;
}
```

### 5.3 Ring buffer

- **256 entries** × 16 bytes (uint64 target + uint64 actual) = 4 KB.
- Overflow wraps; the `overwrites` counter in the dump header flags wrap-around.
- Dump is rate-limited (4 lines per 20 ms pause) to avoid `SYS_CONSOLE_PRINT`
  overruns; the full 256-entry dump completes in ~1.3 s.
- **No UART activity during measurement.** The ring buffer accumulates silently
  in RAM; all UART I/O happens via `tfuture_reset` (before) or `tfuture_dump`
  (after). Firing-jitter measurement is not distorted by UART serialization.

---

## 6. Test Script

`tfuture_sync_test.py` drives a dual-board coordinated-firing experiment:

```
python tfuture_sync_test.py --gm-port COM8 --fol-port COM10
python tfuture_sync_test.py --rounds 50 --lead-ms 2000 --csv fires.csv
```

Flow:

1. Reset both boards, configure IPs.
2. Enable HW-PTP, wait for FOL `PTP FINE`.
3. Settle for `--settle-s` seconds (default 5 s).
4. For `--rounds` iterations (default 20, max 256):
   - Query GM's current PTP_CLOCK via `clk_get`
   - Compute `target_ns = gm_now + --lead-ms × 1 000 000` (default 2 s)
   - Send `tfuture_at <target_ns>` to **both** boards
   - Wait `lead_ms + 200 ms` for both to fire
5. Dump both ring buffers via `tfuture_dump`.
6. Join records by `target_ns` (identical across boards by construction).
7. Compute three metric series per round:
   - `self_jitter_GM  = actual_GM  − target` — module precision on GM
   - `self_jitter_FOL = actual_FOL − target` — module precision on FOL
   - `inter_board     = actual_GM  − actual_FOL` — physical firing coincidence
8. Print robust (median/MAD/p05..p95) and classical (mean/stdev) statistics.

The `inter_board` series is the headline number: it measures how closely two
boards physically fire when given the same future target, and is bounded from
below by HW-PTP inter-board clock alignment (~50 ns) plus the sum of both
boards' self-jitter.

### Logic of target pairing

The CLI round-trip from Python to each board takes ~10–50 ms, and the two arms
are sequential. As long as `--lead-ms` exceeds that round-trip with margin
(default 2000 ms is generous), both arms complete well before the target, and
the test is insensitive to CLI timing jitter. Reducing lead-ms below ~500 ms
risks arming one board after the target has already passed.

Optional CSV output columns:

```
round, target_ns, actual_gm_ns, actual_fol_ns,
       self_jitter_gm, self_jitter_fol, inter_board
```

---

## 7. Expected Results

Rough predictions for this hardware (ATSAME54P20A + LAN865x + HW-PTP at FINE):

| Metric                  | Median   | Robust stdev | Notes |
|-------------------------|---------:|-------------:|:------|
| `self_jitter_GM`        | 0–100 ns |     100–500 ns | TC0 tick quantisation + service-call phase |
| `self_jitter_FOL`       | 0–100 ns |     100–500 ns | same as GM |
| `inter_board`           | within ±300 ns of 0 | 100–500 ns | self-jitter difference + PTP-sync error |

Outliers are possible when a tfuture fire coincides with a PTP Sync or SPI
burst, adding up to a few µs of latency. The robust statistics are chosen to
expose the typical case even when outliers skew mean/stdev.

*Numbers will be filled in once the test has been executed on hardware.*

---

## 8. Limitations and Next Steps

### Current MVP constraints

- **Single pending arm** at a time. No queue; `tfuture_at` fails if state is
  `PENDING`. Sufficient for a diagnostic; trivially extensible if needed.
- **Main-loop blocking during spin** up to 1 ms. Other services (PTP, SW-NTP,
  TCP/IP) pause briefly at the firing moment. Harmless for a one-shot demo;
  problematic if you arm at a high rate.
- **No GPIO output.** The firing is purely a software event plus a ring-buffer
  record. You cannot hang an oscilloscope on an output pin without adding code.
- **No cross-board capture.** The "physical coincidence" number comes from each
  board self-reporting its firing time — it assumes the PTP_CLOCKs are accurate.
  This is a reasonable assumption after the three PTP tests validated the
  clock, but independent verification would require an EIC-based capture pin.
- **Arm-time projection only.** `target_tick` is frozen at arm time; later PTP
  anchor adjustments are not re-applied. Worst-case error ≈ few µs over 5 s
  lead time — below the module's own jitter floor.

### Natural extensions

1. **GPIO output** — add a CLI `tfuture_gpio <port> <pin>` that toggles a pin
   inside the tight-spin exit path. ~20 lines of firmware.
2. **EIC capture pin** on the other board — independent verification of firing
   coincidence without relying on PTP_CLOCK self-reports. ~100 lines + MCC
   config change.
3. **Periodic firing** — a "fire every N ms starting at T" mode. Useful for
   driving a synchronised sample rate across multiple boards.
4. **TC compare interrupt** instead of spin — eliminates the 1 ms main-loop
   blocking. Gains ~zero precision but scales to continuous operation.
