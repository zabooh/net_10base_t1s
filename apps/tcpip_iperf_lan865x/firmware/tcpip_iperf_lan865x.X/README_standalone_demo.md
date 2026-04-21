# Standalone PTP Synchronisation Demo

Self-contained two-board demonstration of PTP over 10BASE-T1S on
LAN865x + ATSAME54P20A.  Lives on branch **`ptp-standlone-demo`**.

Purpose: make the effect of PTP synchronisation visible to the naked eye
using only the on-board user LEDs — no scope, no serial console, no PC
software needed during the demo itself.  The Python companion test
(`standalone_demo_test.py`) optionally verifies the result with Saleae
Logic 2.

---

## 1. What the demo shows

Two SAM E54 Curiosity Ultra boards each flashed with the same firmware,
connected through a 10BASE-T1S link.  On each board:

| LED | Pin  | Meaning                                                  |
|-----|------|----------------------------------------------------------|
| LED1 (green)  | PC21 | 1 Hz visible blink — toggles every 500 ms         |
| LED2 (yellow) | PA16 | State indicator (see §3)                          |
| PD10          | —    | Saleae probe pin mirroring LED1 (active-high)     |

| Button | Pin  | Action                                                |
|--------|------|-------------------------------------------------------|
| SW1    | PD00 | Make this board the PTP **follower**                  |
| SW2    | PD01 | Make this board the PTP **master**                    |

Before PTP is active the two boards run off independent TC0 crystals
(~100 ppm mismatch) — their 1 Hz LED1 rectangles visibly drift apart
after a few seconds.  Once the PTP role-selection buttons are pressed
and both LED2 go solid, the LED1 edges snap back into lock-step and stop
drifting — the whole point of PTP.

---

## 2. Demo flow (operator view)

1. **Power-cycle** both boards at the same time (or within a few seconds).
   Both boards boot in the `FREE` state:
   - LED1 toggles 1 Hz on each board, but the two boards are **not
     synchronised** — over a few seconds their edges drift apart.
   - LED2 is **OFF** on both boards.
2. **Press SW1 on one board** (call it *Board A*) to make it the PTP
   follower.
   - LED2 on Board A starts **blinking at 2 Hz**.
3. **Press SW2 on the other board** (call it *Board B*) to make it the
   PTP master.
   - LED2 on Board B starts **blinking at 2 Hz**.
   - Board B also begins transmitting PTP Sync frames; Board A's servo
     sees them and begins converging.
4. **Wait ~2 s.** Both LED2 transition from blinking to **SOLID ON** —
   Board A when its servo reaches `FINE`, Board B after a fixed 2 s
   blink window matching Board A's typical lock time.
5. **Observe LED1**: the two boards' 1 Hz rectangles are now phase-locked
   and do not drift apart over time.

Order of SW1 / SW2 doesn't have to be exact — as long as one board ends
up as follower and the other as master, the demo works.  Subsequent
button presses are ignored (so you can't accidentally break the demo by
pressing again).  To reset, power-cycle both boards.

---

## 3. LED2 state machine

```
DEMO_FREE         OFF           boot; buttons unpressed
  │ SW1 pressed → DEMO_SYNCING_FOL
  │ SW2 pressed → DEMO_SYNCING_GM
  ▼
DEMO_SYNCING_FOL  2 Hz blink    follower servo converging
  │ PTP_FOL_GetServoState()==FINE
  ▼
DEMO_SYNCED       SOLID ON      PTP lock achieved
```

```
DEMO_SYNCING_GM   2 Hz blink    master active, follower probably still converging
  │ 2 s elapsed  (fixed, matches follower lock time for visual symmetry)
  ▼
DEMO_SYNCED       SOLID ON      master treats itself as "done"
```

---

## 4. How the 1 Hz LED1 phase is made deterministic

Earlier iterations of the demo used per-board counters (`s_led1_div_count
++` inside the cyclic_fire callback, toggling LED1 every N callbacks).
Under PTP_CLOCK jumps at role change, one board could catch up over
many callbacks while the other skipped or ran them normally, leaving
the two boards with different counter values → LED1 locked but 180° out
of phase.

The current implementation derives the LED state **statelessly from the
scheduled PTP target**:

```c
bool led1_on = (((target_ns / 500000000ULL) & 1ULL) != 0ULL);
```

Once both boards are PTP-locked, each `fire_callback()` invocation gets
an identical `target_ns` on both sides → identical LED phase, by
construction.  No per-board state to drift.

---

## 5. Firmware architecture

```
APP_Initialize()
    cyclic_fire_set_user_callback(demo_decimator)
    PTP_CLOCK_ForceSet(0)
    cyclic_fire_start_ex(500 µs, anchor=0, PATTERN_SILENT)
    standalone_demo_init()       // buttons, LED pins, pull-ups

main-loop IDLE state
    standalone_demo_service(current_tick)
        SW1 / SW2 edge detection (20 ms debounce)
        → PTP_FOL_SetMode(PTP_SLAVE)  on SW1
        → PTP_FOL_SetMode(PTP_MASTER) + PTP_GM_Init()  on SW2
        DEMO_SYNCING_* → DEMO_SYNCED transitions

cyclic_fire fire_callback every 250 µs (half-period of 500 µs)
    s_user_cb(target_ns) → demo_decimator
        LED1 / PD10 <- bit 0 of (target_ns / 500 ms)
        LED2        <- bit 0 of (target_ns / 250 ms)  only if SYNCING
```

### Key firmware changes on this branch

1. **`cyclic_fire` user callback hook** — new `cyclic_fire_set_user_callback()`
   API invoked at the end of every `fire_callback()` firing.  Lets a
   higher-level module decimate the 4 kHz tick into visible LED rates
   without scheduling its own `tfuture` timer.
2. **`CYCLIC_FIRE_PATTERN_SILENT`** — skips the native PD10 toggle.
   Without it, the decimator's PD10 writes race with cyclic_fire's own
   250 µs toggle, producing ~100 ns glitches that Saleae captures as
   spurious 4 kHz edges.  SILENT gives a clean 1 Hz rectangle on PD10.
3. **`PTP_FOL_GetServoState()`** — new accessor returning `UNINIT` (0)
   through `FINE` (4).  Used by the demo to know when the follower has
   locked.
4. **Robust `cyclic_fire` re-arm** — if `tfuture_arm_at_ns()` refuses
   the next target (because the PTP_CLOCK anchor jumped at role change
   and the computed `next_target_ns` is inconsistent with the new
   PTP_CLOCK domain), fall back to `PTP_CLOCK_GetTime_ns() +
   half_period` and try again.  Prevents the callback chain from
   silently dying mid-demo.
5. **Stateless LED phase** — `target_ns / SLOT_NS & 1` replaces per-board
   divider counters; see §4.

---

## 6. Python companion test

`standalone_demo_test.py` walks the operator through the demo flow and
optionally verifies the post-sync phase alignment with Saleae Logic 2.

### Quick start

```
python standalone_demo_test.py --a-port COM10 --b-port COM8
```

Wiring:
- Saleae **Ch0 → Board A** PD10 (= GPIO1 on EXT1 header, pin 5 from top)
- Saleae **Ch1 → Board B** PD10
- Saleae **GND → either board's GND** (shared)
- `--a-port` is the serial port of the board wired to Saleae Ch0
- `--b-port` is the serial port of the board wired to Saleae Ch1

The test guides the operator through three phases:

1. **Step 0** — confirm setup: both boards power-cycled, LED1 blinking,
   Saleae connected.
2. **Step 1** — 4 s free-run baseline capture.  Expect the two boards'
   edges to visibly drift (spread > 0).
3. **Step 2** — operator presses SW1 on Board A, SW2 on Board B, waits
   for both LED2 solid.
4. **Step 3** — 6 s post-sync capture.  Expect median cross-board
   rising-edge delta well under 50 ms (the human-visual threshold) and
   ideally under 1 ms.

### Useful flags

| Flag                   | Purpose                                            |
|------------------------|----------------------------------------------------|
| `--duration-s 12`      | Longer post-sync capture for more edge samples     |
| `--threshold-ms 10`    | Tighter PASS gate                                  |
| `--skip-free`          | Skip the free-run baseline capture                 |
| `--no-cli`             | Run Saleae only; skip the CLI debug dumps          |
| `--a-port` / `--b-port`| Serial port override (default COM10 / COM8)        |

### What the CLI debug dumps capture

At four critical points the test queries both boards via UART and logs
the response to `run_<ts>.log`:

- after Step 0 setup (FREE baseline)
- immediately after the operator confirms both LED2 solid
- after the 2 s post-lock settle (pre-capture)
- after the post-sync capture (post-mortem if the capture fails)

Commands dumped: `clk_get`, `ptp_status`, `ptp_mode`, `cyclic_status`,
`tfuture_status`.  The first time the demo went wrong on the bench, the
CLI dump showed Board B with `cyclic_status running=yes` but
`tfuture state: idle` and a frozen `cycles` counter — pointing directly
at the re-arm-after-PTP-jump bug that §5 item 4 fixes.

### Output files

Each run produces `standalone_demo_<ts>/`:

- `run_<ts>.log` — full tee'd log of prose + dumps + captures
- `free/digital.csv`, `synced/digital.csv` — raw Saleae CSVs
- `summary.csv` — one row per phase: n, median_ms, max_abs_ms, min/max
