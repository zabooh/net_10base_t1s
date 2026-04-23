# Firmware Modules — Function, Description, API

This document describes each source module in `apps/tcpip_iperf_lan865x/firmware/src/` as a candidate for **reuse in other projects**. For every module you get: what it does, how to drop it in, what it depends on, and its public API.

## Table of Contents

- [Reuse Matrix](#reuse-matrix)
- [Core Time & Sync](#core-time--sync)
  - [ptp_clock](#ptp_clock)
  - [filters](#filters)
- [PTP Protocol (IEEE 1588)](#ptp-protocol-ieee-1588)
  - [PTP_FOL_task](#ptp_fol_task)
  - [ptp_gm_task](#ptp_gm_task)
  - [ptp_ts_ipc](#ptp_ts_ipc)
  - [ptp_rx](#ptp_rx)
- [Diagnostics & Instrumentation](#diagnostics--instrumentation)
  - [ptp_log](#ptp_log)
  - [loop_stats](#loop_stats)
  - [ptp_offset_trace](#ptp_offset_trace)
  - [sw_ntp_offset_trace](#sw_ntp_offset_trace)
- [Applications Over PTP](#applications-over-ptp)
  - [sw_ntp](#sw_ntp)
  - [tfuture](#tfuture)
  - [cyclic_fire](#cyclic_fire)
  - [pd10_blink](#pd10_blink)
- [CLI Adapters](#cli-adapters)
  - [lan_regs_cli](#lan_regs_cli)
  - [ptp_cli](#ptp_cli)
  - [sw_ntp_cli](#sw_ntp_cli)
  - [tfuture_cli](#tfuture_cli)
  - [loop_stats_cli](#loop_stats_cli)
  - [cyclic_fire_cli](#cyclic_fire_cli)
  - [pd10_blink_cli](#pd10_blink_cli)
- [Application Glue](#application-glue)
  - [app](#app)
- [Reuse Guidelines](#reuse-guidelines)

---

## Reuse Matrix

Portability rating for dropping a module into a different project:

| Module                  | Portability      | Platform Deps                         | Protocol Deps                  |
| ----------------------- | ---------------- | ------------------------------------- | ------------------------------ |
| `filters`               | fully portable   | plain C99                             | none                           |
| `ptp_clock`             | portable w/ tick | 64-bit monotonic tick source          | none                           |
| `loop_stats`            | portable         | `SYS_TIME_Counter64Get`               | none                           |
| `ptp_offset_trace`      | fully portable   | plain C99                             | none                           |
| `sw_ntp_offset_trace`   | fully portable   | plain C99                             | none                           |
| `ptp_log`               | portable         | `SYS_CONSOLE_PRINT`                   | none                           |
| `tfuture`               | portable         | `SYS_TIME_Counter64Get` + `ptp_clock` | none                           |
| `cyclic_fire`           | portable         | `tfuture` + `SYS_PORT_PinToggle`      | none                           |
| `pd10_blink`            | fully portable   | `SYS_TIME_Counter64Get` + `SYS_PORT_PinToggle` | none                  |
| `sw_ntp`                | portable         | Harmony TCP/IP UDP API                | UDP                            |
| `PTP_FOL_task`          | moderate         | LAN865x driver API                    | IEEE 1588                      |
| `ptp_gm_task`           | moderate         | LAN865x driver API                    | IEEE 1588                      |
| `ptp_ts_ipc`            | headers-only     | LAN865x TC6 driver                    | PTP 0x88F7                     |
| `ptp_rx`                | moderate         | Harmony TCP/IP stack                  | PTP 0x88F7                     |
| `*_cli` (all eight)     | swap CLI layer   | Harmony `SYS_CMD`                     | depends on wrapped module      |
| `app`                   | not reusable     | Harmony Application template          | —                              |

"Moderate" = works on any MCU/framework once the LAN865x driver, SYS_TIME, and the Harmony TCP/IP stack are available; the PTP protocol implementation itself is hardware-agnostic.

---

## Core Time & Sync

### ptp_clock

**Function** — Software PTP wallclock with nanosecond resolution.

**Description** — Maintains an anchor `(wallclock_ns, TC0_tick)` set on every PTP sync. Between anchors, `PTP_CLOCK_GetTime_ns()` interpolates using TC0 (60 MHz) and compensates for MCU crystal drift via an IIR low-pass filter. No SPI transfer or hardware timer needed per query — fully non-blocking, safe from any context including ISR.

Works identically on Grandmaster and Follower. The anchor source differs (`PTP_FOL_task` on follower, `ptp_gm_task` on master) but clients call the same API.

**Dependencies** — Only a 64-bit monotonic tick source (`SYS_TIME_Counter64Get`). Nothing PTP-specific in the core logic — can be driven by any timing source that yields `(now_time, now_tick)` pairs.

**API** — [ptp_clock.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.h)

```c
void     PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick);
uint64_t PTP_CLOCK_GetTime_ns(void);
int32_t  PTP_CLOCK_GetDriftPPB(void);
void     PTP_CLOCK_SetDriftPPB(int32_t drift_ppb);
bool     PTP_CLOCK_IsValid(void);
void     PTP_CLOCK_ForceSet(uint64_t wallclock_ns);
```

**Reuse note** — Replace `SYS_TIME_Counter64Get` and the 60 MHz tick rate in `filters.h` (`CLOCK_CYCLE_NS`) to adapt to a different tick source.

---

### filters

**Function** — Generic low-pass filters used by the PTP servo.

**Description** — Two FIR low-pass filter variants (int32 and double) plus a single-pole exponential low-pass. All dependency-free plain C99.

**Dependencies** — None (just `<stdint.h>`).

**API** — [filters.h](../../apps/tcpip_iperf_lan865x/firmware/src/filters.h)

```c
double firLowPassFilter(int32_t input, lpfState  *state);
double firLowPassFilterF(double  input, lpfStateF *state);
double lowPassExponential(double input, double average, double factor);
```

The state structs (`lpfState`, `lpfStateF`) hold a caller-supplied buffer — size chosen by the caller, lets you pick FIR length without edits.

**Reuse note** — Pure utility. Drop into any project that needs simple LPF building blocks.

---

## PTP Protocol (IEEE 1588)

### PTP_FOL_task

**Function** — PTP follower (slave) state machine and Delay_Req initiator.

**Description** — Implements the full IEEE 1588 follower protocol on top of the LAN865x hardware timestamping unit. Consumes Sync + Follow_Up frames, builds and sends Delay_Req frames, receives Delay_Resp, and converges a servo that periodically calls `PTP_CLOCK_Update()`.

State machine covers UNINIT → MATCHFREQ → HARD_SYNC → COARSE → FINE.

**Dependencies** — LAN865x driver API (`DRV_LAN865X_*`), `ptp_clock`, `filters`, `ptp_log`, `ptp_offset_trace`, `ptp_ts_ipc`.

**API** — [ptp_fol_task.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_fol_task.h)

```c
void      PTP_FOL_Init(void);
void      PTP_FOL_Service(void);                         /* main-loop tick, 1 ms */
void      PTP_FOL_OnFrame(const uint8_t *pData, uint16_t len, uint64_t rxTimestamp);
void      PTP_FOL_SetMode(ptpMode_t mode);               /* MASTER/SLAVE/DISABLED */
ptpMode_t PTP_FOL_GetMode(void);
void      PTP_FOL_GetOffset(int64_t *pOffset, uint64_t *pOffsetAbs);
int64_t   PTP_FOL_GetMeanPathDelay(void);
void      PTP_FOL_Reset(void);
void      PTP_FOL_SetMac(const uint8_t *pMac);
void      PTP_FOL_SetVerbose(bool verbose);
void      PTP_FOL_SetTrace(bool enable);
void      PTP_FOL_GetCalibratedClockInc(uint32_t *pTI, uint32_t *pTISUBN);
```

**Reuse note** — The protocol layer (frame parsing, servo, timestamp math) is portable. The LAN865x register access is encapsulated in a few helpers — replace them to retarget to a different PHY.

---

### ptp_gm_task

**Function** — PTP grandmaster state machine (Sync + Follow_Up transmitter, Delay_Resp responder).

**Description** — Emits periodic Sync frames at a configurable interval, captures the transmit hardware timestamp via LAN865x TTSCA register, sends Follow_Up with the captured t1, and responds to incoming Delay_Req with Delay_Resp containing the captured t4.

Also exposes a configurable anchor delay (`PTP_GM_SetExtraAnchorDelay`) used to characterize measurement systems.

**Dependencies** — LAN865x driver, `ptp_clock`, `ptp_log`.

**API** — [ptp_gm_task.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.h)

```c
void PTP_GM_Init(void);
void PTP_GM_Service(void);                    /* main-loop tick, 1 ms */
void PTP_GM_Deinit(void);
void PTP_GM_GetStatus(uint32_t *pSyncCount, uint32_t *pState);
void PTP_GM_SetSyncInterval(uint32_t intervalMs);
void PTP_GM_SetDstMode(ptp_gm_dst_mode_t mode);
ptp_gm_dst_mode_t PTP_GM_GetDstMode(void);
void PTP_GM_OnDelayReq(const uint8_t *pData, uint16_t len, uint64_t rxTimestamp);
void PTP_GM_SetVerbose(bool verbose);
void PTP_GM_SetTrace(bool enable);
void PTP_GM_SetExtraAnchorDelay(int64_t ns);
int64_t PTP_GM_GetExtraAnchorDelay(void);
void PTP_GM_RequestRegDump(void);
```

**Reuse note** — Same portability story as the follower. The 0x88F7 EtherType and PTP frame builders are hardware-independent.

---

### ptp_ts_ipc

**Function** — IPC structure for passing hardware RX timestamps from the LAN865x driver callback to the application.

**Description** — Headers-only module defining two shared globals populated by `TC6_CB_OnRxEthernetPacket()`:

- `g_ptp_rx_ts` — single most-recent RX timestamp (back-compat single-entry form)
- `g_ptp_raw_rx` — full frame capture (data + length + timestamp + sys-tick) used by the PTP dispatcher

**Dependencies** — Defined by the TC6 driver callback; only the header is consumed elsewhere.

**API** — [ptp_ts_ipc.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_ts_ipc.h)

```c
extern volatile PTP_RxTimestampEntry_t g_ptp_rx_ts;
extern volatile PTP_RxFrameEntry_t     g_ptp_raw_rx;
```

**Reuse note** — Pattern (driver-callback → IPC struct → application poll) is portable. Substitute the trigger callback for any other MAC with hardware timestamping.

---

### ptp_rx

**Function** — PTP frame filter + dispatcher between the Harmony TCP/IP stack and the PTP tasks.

**Description** — Registers a packet handler with the TCP/IP stack that consumes EtherType 0x88F7 frames so they never reach the IP layer. Separately, `PTP_RX_Poll()` drains the `g_ptp_raw_rx` buffer (filled by the driver callback) and routes it to `PTP_FOL_OnFrame()` or `PTP_GM_OnDelayReq()` based on current mode.

**Dependencies** — Harmony TCP/IP stack, `ptp_ts_ipc`, `PTP_FOL_task`, `ptp_gm_task`.

**API** — [ptp_rx.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_rx.h)

```c
bool PTP_RX_Register(TCPIP_NET_HANDLE hNet);   /* call once after NetIsUp() */
void PTP_RX_Poll(void);                        /* main-loop tick */
```

**Reuse note** — `PTP_RX_Register` is Harmony-specific; `PTP_RX_Poll` is trivial to retarget by swapping the dispatch destinations.

---

## Diagnostics & Instrumentation

### ptp_log

**Function** — Deferred log queue that serializes console output from multiple tasks.

**Description** — `PTP_LOG(fmt, ...)` enqueues a formatted message into a ring buffer. `ptp_log_flush()` is called once per main-loop iteration and drains the buffer via a single print site, so messages from interrupt/task contexts can never interleave.

**Dependencies** — `SYS_CONSOLE_PRINT` (swap for any thread-unsafe printer).

**API** — [ptp_log.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_log.h)

```c
void ptp_log_enqueue(const char *fmt, ...);
void ptp_log_flush(void);
#define PTP_LOG ptp_log_enqueue
```

**Reuse note** — Useful anywhere `printf` can only be called from one place but callers live in multiple contexts.

---

### loop_stats

**Function** — Per-subsystem main-loop timing instrumentation.

**Description** — Record timing brackets around each subsystem called from `SYS_Tasks()`. Tracks max and average elapsed time per subsystem since the last reset. Originally built to hunt 9 ms main-loop stalls causing PTP measurement outliers.

**Dependencies** — `SYS_TIME_Counter64Get`.

**API** — [loop_stats.h](../../apps/tcpip_iperf_lan865x/firmware/src/loop_stats.h)

```c
typedef enum {
    LOOP_STATS_SUBSYS_SYS_CMD, LOOP_STATS_SUBSYS_TCPIP,
    LOOP_STATS_SUBSYS_LOG_FLUSH, LOOP_STATS_SUBSYS_APP,
    LOOP_STATS_SUBSYS_TOTAL, LOOP_STATS_SUBSYS_COUNT
} loop_stats_subsys_t;

void LOOP_STATS_RecordStart(loop_stats_subsys_t ss);
void LOOP_STATS_RecordEnd  (loop_stats_subsys_t ss);
void LOOP_STATS_Reset(void);
void LOOP_STATS_Print(void);
```

**Reuse note** — Replace the enum and bracket calls to profile any fixed set of code sections in any bare-metal or RTOS main loop.

---

### ptp_offset_trace

**Function** — Ring buffer for PTP follower offset time-series.

**Description** — Each `ptp_offset_trace_record()` stores one hardware-derived `offset = (t2 − t1) − mean_path_delay` into a 1024-entry ring buffer. The dump CLI prints the full series so a Python post-processor can compute mean/stdev/Allan deviation without UART traffic distorting the samples themselves.

**Dependencies** — None (plain C99 + `SYS_CONSOLE_PRINT` for dump).

**API** — [ptp_offset_trace.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_offset_trace.h)

```c
void     ptp_offset_trace_record(int32_t offset_ns, uint8_t sync_status);
void     ptp_offset_trace_reset(void);
void     ptp_offset_trace_dump(void);
uint32_t ptp_offset_trace_count(void);
```

**Reuse note** — Works as a generic time-series capture for any int32 quantity. Just rename.

---

### sw_ntp_offset_trace

**Function** — Parallel ring buffer for software-NTP offset samples (int64).

**Description** — Same pattern as `ptp_offset_trace` but stores `int64_t` because SW-NTP offsets grow unbounded when HW-PTP is off and crystal drift accumulates. Each entry carries offset + status byte (0=timeout, 1=valid).

**API** — [sw_ntp_offset_trace.h](../../apps/tcpip_iperf_lan865x/firmware/src/sw_ntp_offset_trace.h)

```c
void     sw_ntp_offset_trace_record(int64_t offset_ns, uint8_t valid);
void     sw_ntp_offset_trace_reset(void);
void     sw_ntp_offset_trace_dump(void);
uint32_t sw_ntp_offset_trace_count(void);
```

---

## Applications Over PTP

### sw_ntp

**Function** — Minimal software-NTP over UDP that measures application-layer sync accuracy.

**Description** — Master/follower pair modeled on the PTP timing exchange, but using `PTP_CLOCK_GetTime_ns()` for all four timestamps (t1..t4) at the application layer — **after** the TCP/IP stack. All SPI + stack + RTOS latencies therefore show up as jitter. Follower only measures — never corrects — so HW-PTP remains the sole regulator and Phase-A (HW off) vs Phase-B (HW on) stats are directly comparable.

Single 32-byte UDP frame (opcode + three int64 timestamps) per request. Port 12345 (avoids NTP/123 and iperf/5001 collisions).

**Dependencies** — `ptp_clock`, Harmony TCP/IP UDP socket API.

**API** — [sw_ntp.h](../../apps/tcpip_iperf_lan865x/firmware/src/sw_ntp.h)

```c
void          sw_ntp_init(void);
void          sw_ntp_service(void);        /* main-loop tick every iter */
void          sw_ntp_set_mode(sw_ntp_mode_t mode);   /* OFF/MASTER/FOLLOWER */
sw_ntp_mode_t sw_ntp_get_mode(void);
void          sw_ntp_set_master_ip(uint32_t ipv4_host_order);
void          sw_ntp_set_poll_interval_ms(uint32_t ms);
uint32_t      sw_ntp_get_poll_interval_ms(void);
void          sw_ntp_set_verbose(bool verbose);
void          sw_ntp_get_stats(uint32_t *samples, uint32_t *timeouts, int64_t *last_offset_ns);
```

**Reuse note** — Swap the Harmony UDP socket API for any other UDP stack (lwIP, BSD sockets) to port.

---

### tfuture

**Function** — Coordinated single-shot firing at an absolute PTP wallclock time.

**Description** — Arm with an absolute `target_ns` or relative `ms_from_now`. The module translates the target into a raw TC0 tick once (with drift correction from `PTP_CLOCK_GetDriftPPB()`), then hybrid-fires: coarse-check each main-loop pass until within 1 ms, then tight busy-wait to the exact tick. Delivers ~17 ns precision without TC-compare-interrupt programming.

Two PTP-synchronized boards armed with the same `target_ns` fire within hundreds of ns of each other — which is what the `tfuture_sync_test.py` driver quantifies across the link. Also records a 256-entry ring buffer of `(target, actual)` pairs so Python can build histograms.

**Dependencies** — `ptp_clock`, `SYS_TIME_Counter64Get`.

**API** — [tfuture.h](../../apps/tcpip_iperf_lan865x/firmware/src/tfuture.h)

```c
void            tfuture_init(void);
void            tfuture_service(void);             /* main-loop tick every iter */
bool            tfuture_arm_at_ns(uint64_t target_wc_ns);
bool            tfuture_arm_in_ms(uint32_t ms_from_now);
void            tfuture_cancel(void);
tfuture_state_t tfuture_get_state(void);           /* IDLE/PENDING/FIRED */
void            tfuture_get_last(uint64_t *target_ns, uint64_t *actual_ns);
uint32_t        tfuture_get_fire_count(void);
void            tfuture_set_drift_correction(bool enable);
bool            tfuture_get_drift_correction(void);

/* Post-fire callback hook: invoked inside tfuture_service() right after
 * the spin-wait exits, BEFORE state goes IDLE.  Callback may re-arm via
 * tfuture_arm_at_ns() to produce periodic firing (see `cyclic_fire`). */
typedef void (*tfuture_fire_cb_t)(uint64_t target_ns, uint64_t actual_ns);
void            tfuture_set_fire_callback(tfuture_fire_cb_t cb);

/* Runtime-configurable busy-wait threshold.  Default 1000 µs matches the
 * PTP service cadence.  Lower it (e.g. 100 µs) for cyclic sub-ms firing
 * so other main-loop services still get CPU per cycle. */
void            tfuture_set_spin_threshold_us(uint32_t us);
uint32_t        tfuture_get_spin_threshold_us(void);

void            tfuture_trace_reset(void);
void            tfuture_trace_dump(void);
uint32_t        tfuture_trace_count(void);
```

**Reuse note** — Fully portable once `ptp_clock` and a 64-bit tick counter are available.  The ns-per-tick conversion uses `SYS_TIME_FrequencyGet()` at runtime, so the module adapts to any TC0 clock configuration.  For a periodic-callback use case, pair with the `cyclic_fire` module instead of rolling your own re-arm loop.

---

### cyclic_fire

**Function** — PTP-synchronous periodic GPIO toggle at a configurable rate.

**Description** — Builds on `tfuture` via its post-fire callback hook.  `cyclic_fire_start(period_us, phase_anchor_ns)` arms `tfuture` for the first rising edge, then each callback acts on `PD10` and re-arms `tfuture` for the next half-period (`period_us / 2`) in PTP-wallclock time.  `period_us` is the FULL rectangle period, so 1000 µs → 1 kHz.  Given two PTP-locked boards started with the same `phase_anchor_ns` + `period_us`, both boards fire at identical PTP moments — scope-verifiable synchronous signals.

**Two output patterns** (select via `cyclic_fire_start_ex`):
- `CYCLIC_FIRE_PATTERN_SQUARE` (default): one toggle per callback → 50/50 square wave.  Best for rate/phase measurement.
- `CYCLIC_FIRE_PATTERN_MARKER`: 10-half-period cycle.  Phase 0 sets HIGH, phase 2 clears LOW, phases 1 + 3..9 leave the pin alone.  Result: one rising edge every 5 × `period_us`, signal HIGH for 1 period, LOW for 4 periods.  Isolated rising edge makes cross-board "who fires first?" unambiguous on a scope.

Trade-off — shorter periods mean more CPU spent in `tfuture`'s busy-wait window.  `cyclic_fire_start` lowers `tfuture_set_spin_threshold_us()` to 100 µs on entry (and restores on stop) so `PTP_FOL_Service` / TCP-IP still get CPU between fires.  Periods below ~400 µs are not recommended (half-period ≈ spin threshold).

**Dependencies** — `tfuture`, `ptp_clock`, Harmony `SYS_PORT_PinToggle / PinSet / PinClear`.

**API** — [cyclic_fire.h](../../apps/tcpip_iperf_lan865x/firmware/src/cyclic_fire.h)

```c
#define CYCLIC_FIRE_DEFAULT_PERIOD_US  1000u  /* full rectangle period → 1 kHz on PD10 */

typedef enum {
    CYCLIC_FIRE_PATTERN_SQUARE = 0,
    CYCLIC_FIRE_PATTERN_MARKER = 1,
} cyclic_fire_pattern_t;

bool     cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns);
bool     cyclic_fire_start_ex(uint32_t period_us, uint64_t phase_anchor_ns,
                              cyclic_fire_pattern_t pattern);
void     cyclic_fire_stop(void);
bool     cyclic_fire_is_running(void);
uint32_t cyclic_fire_get_period_us(void);
uint64_t cyclic_fire_get_cycle_count(void);
uint64_t cyclic_fire_get_missed_count(void);
```

**Reuse note** — Swap `CYCLIC_FIRE_PIN` in `cyclic_fire.c` to retarget to another GPIO.

---

### pd10_blink

**Function** — Simple main-loop rectangle generator on `PD10`, PTP-independent.

**Description** — Toggles `PD10` at a configurable frequency using only `SYS_TIME_Counter64Get()` + `SYS_PORT_PinToggle()`.  No tfuture, no PTP, no spin-wait — just a scheduled-tick comparison per main-loop iteration.  Starts silent; enabled via the `blink` CLI.  Primary uses: post-flash scope-probe verification, wiring checks, a background reference tick while measuring other subsystems.

Frequency semantics: the argument to `pd10_blink_set_hz()` is the **rectangle** frequency.  The underlying toggle rate is `2 × hz`.  The half-period is `ticks_per_sec / (2 × hz)` ticks; `set_hz()` returns `false` if this collapses to 0 ticks (frequency too high for the SYS_TIME resolution).

Drift behaviour: the scheduled toggle tick advances by exactly one half-period each time (`s_next_toggle_tick += half_period`), so minor per-iteration jitter does not accumulate.  However, because `pd10_blink` uses the MCU-local quartz directly (no PTP discipline), the rectangle frequency sits ~1000 ppm off nominal on typical dev-board crystals — as expected.

**Dependencies** — `SYS_TIME_Counter64Get`, `SYS_PORT_PinToggle`.  Nothing else.

**API** — [pd10_blink.h](../../apps/tcpip_iperf_lan865x/firmware/src/pd10_blink.h)

```c
void     pd10_blink_init(void);                       /* called from APP_Initialize */
void     pd10_blink_service(uint64_t current_tick);   /* main-loop tick every iter */
bool     pd10_blink_set_hz(uint32_t hz);              /* 0 → stop; returns false if hz too high */
bool     pd10_blink_is_running(void);
uint32_t pd10_blink_get_hz(void);
```

**Reuse note** — The simplest reusable block in the whole tree: replace `PD10_BLINK_PIN` in `pd10_blink.c` and the module is ready for any other GPIO on any other target that has a 64-bit monotonic tick counter.

---

## CLI Adapters

These seven modules were extracted from `app.c` during the `refactor/app-split` work (six) plus `cyclic_fire_cli` on top.  Each is a thin wrapper that registers commands with Harmony `SYS_CMD` and forwards them to the underlying functional module. Swap the CLI layer (e.g. for a plain UART REPL) and the underlying modules stay unchanged.

Pattern for every adapter:

```c
void MODULE_CLI_Register(void);     /* call once at startup    */
```

### lan_regs_cli

**Function** — `lan_read` / `lan_write` CLI for direct LAN865x register access, plus the async state machine that drives it.

**Description** — `lan_read <addr>` and `lan_write <addr> <value>` dispatch to the `DRV_LAN865X_ReadRegister` / `WriteRegister` async APIs. A small state machine tracks pending operations, times out after 200 ms, and prints the result.

**Dependencies** — `DRV_LAN865X_*`, `SYS_CMD`, `SYS_TIME_Counter64Get`.

**API** — [lan_regs_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/lan_regs_cli.h)

```c
void LAN_REGS_CLI_Register(void);
void LAN_REGS_CLI_Service(uint64_t current_tick, uint64_t ticks_per_ms);
```

**Reuse note** — Only CLI extract that also exports a service call, because the driver API is fully async and needs per-loop servicing.

---

### ptp_cli

**Function** — CLI for all PTP/clock/offset-trace management commands.

**Description** — 14 commands: `ptp_mode`, `ptp_status`, `ptp_time`, `ptp_interval`, `ptp_offset`, `ptp_reset`, `ptp_trace`, `ptp_dst`, `clk_set`, `clk_get`, `clk_set_drift`, `ptp_gm_delay`, `ptp_offset_reset`, `ptp_offset_dump`.

**Dependencies** — `PTP_FOL_task`, `ptp_gm_task`, `ptp_clock`, `ptp_offset_trace`, `SYS_CMD`.

**API** — [ptp_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/ptp_cli.h)

```c
void PTP_CLI_Register(void);
```

---

### sw_ntp_cli

**Function** — CLI for SW-NTP management + IP parser helper.

**Description** — 6 commands: `sw_ntp_mode` (off/master/follower <ip>), `sw_ntp_poll`, `sw_ntp_status`, `sw_ntp_trace`, `sw_ntp_offset_reset`, `sw_ntp_offset_dump`. Contains a static `parse_ip` helper for "a.b.c.d" → host-order uint32.

**Dependencies** — `sw_ntp`, `sw_ntp_offset_trace`, `SYS_CMD`.

**API** — [sw_ntp_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/sw_ntp_cli.h)

```c
void SW_NTP_CLI_Register(void);
```

---

### tfuture_cli

**Function** — CLI for coordinated-firing management and trace.

**Description** — 7 commands: `tfuture_at <ns>`, `tfuture_in <ms>`, `tfuture_cancel`, `tfuture_status`, `tfuture_reset`, `tfuture_dump`, `tfuture_drift` (on/off).

**Dependencies** — `tfuture`, `ptp_clock` (for drift display), `SYS_CMD`.

**API** — [tfuture_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/tfuture_cli.h)

```c
void TFUTURE_CLI_Register(void);
```

---

### loop_stats_cli

**Function** — CLI for the main-loop timing instrumentation.

**Description** — One command: `loop_stats [reset]`. Prints per-subsystem max/avg or resets the counters.

**Dependencies** — `loop_stats`, `SYS_CMD`.

**API** — [loop_stats_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/loop_stats_cli.h)

```c
void LOOP_STATS_CLI_Register(void);
```

---

### cyclic_fire_cli

**Function** — CLI for the periodic GPIO-toggle module.

**Description** — 5 commands: `cyclic_start [period_us [anchor_ns]]` (defaults to 1000 µs rectangle period = 1 kHz, no explicit anchor; requires PTP sync; 50/50 square wave), `cyclic_start_marker [period_us [anchor_ns]]` (same arming but 1-period-high + 4-period-low pulse pattern — isolated rising edges make cross-board "who fires first?" visually unambiguous on a scope), `cyclic_start_free [period_us]` (same but bootstraps PTP_CLOCK to local TC0 — boards run on independent crystals, edges drift apart; intended to demo the "before sync" state), `cyclic_stop`, `cyclic_status` (running flag + period + cycle + miss counters).

**Dependencies** — `cyclic_fire`, `SYS_CMD`.

**API** — [cyclic_fire_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/cyclic_fire_cli.h)

```c
void CYCLIC_FIRE_CLI_Register(void);
```

---

### pd10_blink_cli

**Function** — CLI for the standalone `PD10` rectangle generator.

**Description** — One command: `blink [<hz>|stop]`.  No argument starts at the default 1000 Hz; `<hz>` as any positive integer starts / retunes; `0` or `stop` halts.  Non-numeric arguments print a short usage message instead of silently being interpreted as 0.

Note on `MAX_CMD_GROUP`: adding this module pushed the number of SYS_CMD groups to 9, above the default Harmony limit of 8.  The limit has been raised to 16 in both `config/default/system/command/sys_command.h` and `config/FreeRTOS/system/command/sys_command.h` — any further CLI module extraction will fit without re-bumping for a while.

**Dependencies** — `pd10_blink`, `SYS_CMD`.

**API** — [pd10_blink_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/pd10_blink_cli.h)

```c
void PD10_BLINK_CLI_Register(void);
```

---

## Application Glue

### app

**Function** — Harmony Application-template state machine and 1-ms dispatcher.

**Description** — `APP_Initialize()` calls the five `*_CLI_Register()` aggregator + `sw_ntp_init` + `tfuture_init`. `APP_Tasks()` waits for the TCP/IP stack to come up, registers the PTP packet handler via `PTP_RX_Register`, then spins in the IDLE state servicing:

- `LAN_REGS_CLI_Service` (async LAN865x ops)
- `PTP_GM_Service` / `PTP_FOL_Service` at 1 ms cadence (mode-dependent)
- `sw_ntp_service` and `tfuture_service` every iteration
- `PTP_RX_Poll` to drain the driver-captured frame buffer
- GM re-init recovery on LAN865x reinit

**Dependencies** — All of the above.

**API** — [app.h](../../apps/tcpip_iperf_lan865x/firmware/src/app.h)

```c
void APP_Initialize(void);
void APP_Tasks(void);
```

**Reuse note** — **Not a reusable module.** This is application glue. When porting, use the other modules to build your own state machine.

---

## Reuse Guidelines

When pulling one of these modules into another project:

1. **Start with the dependency chain.** `ptp_clock` → `tfuture` → `tfuture_cli`, for example. You can always pull upward in the chain without pulling downward.

2. **Swap the CLI layer first.** All `*_cli` modules are thin wrappers around `SYS_CMD_ADDGRP`. If your target has a different REPL, write one new adapter and the underlying modules stay untouched.

3. **The LAN865x-specific bits are localized.** Only `PTP_FOL_task`, `ptp_gm_task`, and `lan_regs_cli` touch the LAN865x driver directly. The protocol state machines use it through a narrow interface — retargeting to a different PHY is manageable.

4. **Time sources are pluggable.** `ptp_clock`, `tfuture`, and `loop_stats` all call `SYS_TIME_Counter64Get`. Replace the three call sites with your platform's monotonic 64-bit tick source and you're portable.

5. **Trace modules are fully self-contained.** `ptp_offset_trace`, `sw_ntp_offset_trace`, and the `tfuture` ring buffer depend only on `<stdint.h>` + a print function. Useful as generic sample captures in any diagnostics context.

6. **`sw_ntp` and `tfuture` are the flagship reusable pieces.** They demonstrate what you can build on top of a synchronized clock and are light enough to drop into any project that has UDP (sw_ntp) or just a clock and a tick source (tfuture).
