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

Button semantics are **context-dependent** — each button's meaning
depends on the board's current role:

| Button | in **FREE** role         | on **master** board         | on **follower** board       |
|--------|--------------------------|-----------------------------|------------------------------|
| SW1    | become PTP **follower**  | toggle **iperf TCP server** | toggle follower off → FREE   |
| SW2    | become PTP **master**    | toggle master off → FREE    | toggle **iperf TCP client**  |

In short: whichever button picked the role is the "role off" toggle for
that role.  The *opposite* button on each role runs the iperf payload
(server on master, client on follower).  See §7 for the iperf flow.

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
4. **Wait ~3 s.** Both LED2 transition from blinking to **SOLID ON** —
   Board A when its servo reaches `FINE` (typically 2.7 s after SW1, with
   the adaptive drift filter described in
   [README_drift_filter.md](README_drift_filter.md)), Board B after a fixed
   2 s blink window matching Board A's typical lock time.
5. **Observe LED1**: the two boards' 1 Hz rectangles are now phase-locked
   and do not drift apart over time.

Order of SW1 / SW2 doesn't have to be exact — as long as one board ends
up as follower and the other as master, the demo works.

**Role toggles**: pressing the same button that picked the role a
second time disables that role and returns the board to the FREE
state (master press SW2 again → master off; follower press SW1 again
→ follower off).  The follower also self-recovers on sync loss — see §8.

**iperf payload**: once synchronised, the master's SW1 toggles an iperf
TCP server and the follower's SW2 toggles an iperf TCP client.  See §7.

---

## 3. LED2 state machine

```
DEMO_FREE         OFF           boot; buttons unpressed
  │ SW1 pressed → DEMO_SYNCING_FOL    (this board = follower)
  │ SW2 pressed → DEMO_SYNCING_GM     (this board = master)
  ▼
DEMO_SYNCING_FOL  2 Hz blink    follower servo converging
  │ PTP_FOL_GetServoState()==FINE
  ▼
DEMO_SYNCED       ~6 % PWM      PTP lock — follower = DIMMED
  │ no Sync for 1000 ms
  ▼
DEMO_LOST         OFF           GM silent (cable / reset / power off)
  │ Sync arrives again       → back to DEMO_SYNCED
```

```
DEMO_SYNCING_GM   2 Hz blink    master active
  │ 2 s elapsed  (fixed, matches follower lock time for visual symmetry)
  ▼
DEMO_SYNCED       SOLID ON      master = FULL brightness
```

In `DEMO_SYNCED` the two boards are visually distinct at a glance:
**master LED2 full brightness, follower LED2 at ~6 %** (~1-in-16 slot
PWM at ~250 Hz from the decimator — far above flicker threshold, so
the eye sees a uniformly dimmer LED, not a blink).  If the master
disappears the follower LED2 goes dark (DEMO_LOST); when the master
returns the follower automatically recovers.

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
3. **`PTP_FOL_GetServoState()`** / **`PTP_FOL_GetLastSyncTick()`** —
   accessors returning the servo state and the tick latched on the last
   received Sync frame.  Used by the demo to know when the follower has
   locked and to detect GM silence (DEMO_LOST).
4. **Robust `cyclic_fire` re-arm** — if `tfuture_arm_at_ns()` refuses
   the next target (because the PTP_CLOCK anchor jumped at role change
   and the computed `next_target_ns` is inconsistent with the new
   PTP_CLOCK domain), fall back to `PTP_CLOCK_GetTime_ns() +
   half_period` and try again.
5. **`cyclic_fire` watchdog in demo_service** — if the cycles counter
   doesn't advance for 500 ms (typically after a PTP_CLOCK backward jump
   armed tfuture far in the future), stop + restart cyclic_fire with a
   fresh anchor.  Keeps LED1 blinking through any role change.
6. **Stateless LED phase** — `target_ns / SLOT_NS & 1` replaces per-board
   divider counters; see §4.
7. **Sync-loss detection + recovery** — see §8.
8. **iperf payload** — `iperf_control` module lets the demo
   programmatically start / stop Harmony's iperf TCP server/client; see §7.

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
   ideally under 1 ms.  Measured 2026-04-23 with the adaptive drift
   filter + TC1-ISR cyclic_fire backend: **median −32 µs, MAD 39 µs,
   0.0 ppm cross-board rate match** on a 60 s capture (full numbers and
   methodology in [README_drift_filter.md](README_drift_filter.md) §5).

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

---

## 7. iperf payload (TCP throughput over the synchronised link)

Once both boards are in `DEMO_SYNCED` the demo exposes iperf over the
10BASE-T1S link — lets you visualise "PTP sync enables data payload on
the same wires".

Button mapping recap:

| Board role | Button | Action                                                  |
|------------|--------|---------------------------------------------------------|
| master     | SW1    | set IP 192.168.0.10/24, start iperf TCP server on :5001 |
| master     | SW1 again | stop iperf server                                    |
| follower   | SW2    | set IP 192.168.0.20/24, start iperf TCP client → 192.168.0.10:5001 |
| follower   | SW2 again | stop iperf client                                    |

The client is rate-capped at **4 Mbps** via `-b 4000000` so the
10BASE-T1S PLCA doesn't backpressure the MAC and flood the console with
`TCPIPStack_Assert` warnings.  4 Mbps is inside the observed 4-6 Mbps
sustainable goodput on this PHY.

### Typical UART output

Master after SW1 press:
```
[IPERF] IP set to 192.168.0.10 / 255.255.255.0
[IPERF] starting TCP server on :5001
iperf: Starting session instance 0
iperf: Server listening on TCP port 5001
```

Follower after SW2 press:
```
[IPERF] IP set to 192.168.0.20 / 255.255.255.0
[IPERF] connecting TCP client to 192.168.0.10:5001 (cap 4 Mbps)
iperf: instance 0 started ...
    - Local  192.168.0.20 port 1024 connected with
    - Remote 192.168.0.10 port 5001
    - Target rate = 4000000 bps, period = 2 ms
[ 0.0-10.0 sec] 5.01 MBytes 4.01 Mbits/sec
```

### Implementation

- `config/default/library/tcpip/src/iperf.c` — `CommandIperfStart` and
  `CommandIperfStop` made non-static (4 single-word edits) so higher
  layers can invoke them directly instead of going through the
  SYS_CMD console parser.
- `src/iperf_control.{c,h}` — stub `SYS_CMD_DEVICE_NODE` whose output
  API routes iperf's banner / BW reports to `SYS_CONSOLE_PRINT`, plus
  a `TCPIP_STACK_NetAddressSet()` helper.  Exposes three entry points:
  `iperf_control_server_start()`, `iperf_control_client_start(const char
  *ip)`, `iperf_control_stop()`.
- `src/standalone_demo.c` — new opposite-role button-press handling;
  `s_iperf_running` tracks the toggle state per board; disabling a
  role via its primary button also tears down any running iperf
  session it owns.

---

## 8. Sync-loss detection on the follower (DEMO_LOST)

Once in `DEMO_SYNCED` the follower polls `PTP_FOL_GetLastSyncTick()`
each main-loop iteration.  If no Sync has arrived for
`SYNC_LOSS_TIMEOUT_MS` (1 s = 8 missed Syncs at the default 125 ms
interval), the demo moves to `DEMO_LOST`:

- `LED2` goes dark (visual cue to the operator that the link is down)
- `PTP_FOL_Reset()` is called so `ptp_sync_sequenceId` drops back to
  `-1` — that way whatever the new master's starting sequence-id is
  (typically 0 after a power-cycle) will be accepted on the very first
  incoming Sync instead of tripping the "Large sequence mismatch" guard
  inside `processSync()`.
- Every `SYNC_LOST_RETRY_MS` (3 s) the reset is re-issued in case the
  master was still booting at the first reset.

When the GM comes back, the next incoming Sync updates
`s_last_sync_tick` (at the top of `processSync()`, before any
sequence-id decision).  The demo detects the freshness and returns to
`DEMO_SYNCED`; LED2 resumes the dimmed-PWM state.
