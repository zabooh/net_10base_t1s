# tcpip_iperf_lan865x — Firmware Modifications

This document describes all manual changes applied on top of the MCC-generated
Harmony 3 project for the ATSAME54P20A + LAN865x 10BASE-T1S demo.
The goal is to enable PTP (IEEE 1588) hardware timestamping with sub-microsecond
synchronisation accuracy over 10BASE-T1S.

### Origin

This repository is a **fork** of the Microchip Harmony 3 `net_10base_t1s` package:

> **https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s.git**
> Base commit: `586ffc15708fcc2c02b182967d872837a15f69f7` (tag: **v1.4.3**)

The PTP implementation added on top is derived from **Microchip Application Note AN1847**
(*Precision Time Protocol over 10BASE-T1S*) and its accompanying open-source
reference project, which is itself a fork of:

> **https://github.com/MicrochipTech/LAN865x-TimeSync.git**

The AN1847 reference project targets a different Harmony 3 demo application
(`t1s_100baset_bridge`). The work described here ports and adapts that
implementation to the `tcpip_iperf_lan865x` project, resolves two bugs found
during testing (see §2), and adds the `ptp_clock.c` software wallclock which
is not present in the original.

---

## Table of Contents

- [Overview](#overview)
  - [Summary of Changes](#summary-of-changes)
  - [Architecture — PTP Data Flow](#architecture--ptp-data-flow)
- [How To Reproduce](#how-to-reproduce)
  - [Prerequisites](#prerequisites)
  - [Clone](#clone)
  - [Tool Setup](#tool-setup)
  - [Build & Flash](#build--flash)
  - [First Console Test](#first-console-test)
- [1. PTP Implementation](#1-ptp-implementation)
  - [1.1 New Source Files](#11-new-source-files)
  - [1.2 LAN865x Driver Changes](#12-lan865x-driver-changes)
  - [1.3 Application Changes](#13-application-changes)
  - [1.4 CLI Commands](#14-cli-commands)
  - [1.5 Console Output Format](#15-console-output-format)
- [2. Bug Fixes](#2-bug-fixes)
  - [2.1 TX Timestamp: DELAY\_UNLOCK\_EXT 100 ms → 5 ms](#21-tx-timestamp-delay_unlock_ext-reduced-from-100-ms-to-5-ms)
  - [2.2 Role-Swap: Crystal Calibration Overwrite](#22-role-swap-crystal-calibration-overwrite)
- [3. PTP Software Clock](#3-ptp-software-clock--srcptp_clockc--srcptp_clockh)
  - [Purpose](#purpose)
  - [Design — Anchor + TC0 Interpolation](#design--anchor--tc0-interpolation)
  - [What You See Without PTP — Free-Running Crystal Drift](#what-you-see-without-ptp--free-running-crystal-drift)
  - [What You See With PTP Active — Locked](#what-you-see-with-ptp-active--locked)
  - [TC0 Tick-Rate Correction (TISUBN)](#tc0-tick-rate-correction-tisubn)
  - [drift\_ppb — Residual Frequency Error](#drift_ppb--residual-frequency-error)
- [4. Further Firmware Changes](#4-further-firmware-changes)
  - [4.1 MAC Address Randomisation](#41-mac-address-randomisation)
  - [4.2 LAN865x Register Access CLI](#42-lan865x-register-access-cli)
- [5. Test Scripts & Validation](#5-test-scripts--validation)
  - [How the Tests Control the Firmware](#how-the-tests-control-the-firmware)
  - [5.1 Baseline: TC0 Timer Consistency](#51-baseline-tc0-timer-consistency--hw_timer_sync_testpy)
  - [5.2 PTP On/Off Resilience](#52-ptp-onoff-resilience--ptp_onoff_testpy)
  - [5.3 Role-Swap Validation](#53-role-swap-validation--ptp_role_swap_testpy)
  - [5.4 PTP Sync Before/After](#54-ptp-sync-beforeafter--ptp_sync_before_after_testpy)
  - [5.5 PTP Reproducibility](#55-ptp-reproducibility--ptp_reproducibility_testpy)
- [6. Hardware & Build Setup](#6-hardware--build-setup)
  - [6.1 Hardware Configuration](#61-hardware-configuration)
  - [6.2 Build Infrastructure](#62-build-infrastructure)

---

## Overview

### Summary of Changes

| Area | Changed / New File(s) | Purpose |
|------|-----------------------|---------|
| PTP hardware timestamping | `drv_lan865x_api.c`, `drv_lan865x.h`, `tc6.c` | TX/RX timestamps for PTP Sync/FollowUp |
| PTP state machines | `ptp_gm_task.c/.h`, `PTP_FOL_task.c/.h`, `filters.c/.h`, `ptp_ts_ipc.h` | Grandmaster + Follower roles |
| PTP software clock | `ptp_clock.c/.h` | ns-resolution wallclock via TC0 |
| Application integration | `app.c`, `app.h` | PTP services, packet handler, CLI |
| **Bug fix** — TX timestamp | `drv_lan865x_api.c` | `DELAY_UNLOCK_EXT` 100 ms → 5 ms; fixes missed timestamps |
| **Bug fix** — role-swap | `ptp_gm_task.c`, `PTP_FOL_task.c/.h` | −3.13 ms stuck offset after GM↔FOL swap |
| MAC randomisation | `initialization.c` | Unique MAC addresses via hardware TRNG |
| LAN865x register CLI | `app.c` | `lan_read` / `lan_write` without a debugger |
| Build tooling | `build.bat`, `setup_compiler.py`, `setup_flasher.py`, `flash.py`, `build_summary.py`, `user.cmake` | Reproducible one-command builds |

### Architecture — PTP Data Flow

```
LAN865x SPI Hardware
       |
       | TX Sync frame → TTSCAA bit set in STATUS0
       ↓
_OnStatus0() [drv_lan865x_api.c]
  → saves STATUS0 bits 8-10 into drvTsCaptureStatus0[]
  → W1C-clears STATUS0
       |
       ↓
DRV_LAN865X_GetAndClearTsCapture(0)  [called by PTP_GM_Service]
  → GM reads TX timestamp from TTSCAH/AL registers
  → sends FollowUp with corrected timestamp

-- RX path (primary) --

TC6_CB_OnRxEthernetPacket() [drv_lan865x_api.c]
  → copies frame + rxTimestamp into g_ptp_raw_rx (global, ptp_ts_ipc.h)
  → sets g_ptp_raw_rx.pending = true
       |
       ↓
APP_STATE_IDLE [app.c]
  → checks g_ptp_raw_rx.pending first
  → PTP_FOL_OnFrame(data, length, rxTimestamp)
  → Follower servo computes offset and adjusts local clock

-- TCP/IP stack path (frame suppression) --

TC6_CB_OnRxEthernetPacket() → TCP/IP stack → pktEth0Handler() [app.c]
  → EtherType 0x88F7: TCPIP_PKT_PacketAcknowledge(TCPIP_MAC_PKT_ACK_RX_OK)
  → returns true (consumed) — IP stack does not process PTP frames
  → no frame copy; driver path (g_ptp_raw_rx) is the single source of truth
```

---

## How To Reproduce

The following steps take you from a fresh `git clone` to live PTP output on two
boards in about 10 minutes.

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Hardware** | 2× ATSAME54-Curiosity-Ultra + LAN865x click board, connected via T1S bus |
| **MPLAB XC32** | v4.60 or v5.x, installed under `C:\Program Files\Microchip\xc32\` |
| **CMake ≥ 4.1 + Ninja** | Must be on `PATH` |
| **MPLAB X IDE / MDB** | Required by `flash.py` for programming |
| **Python 3.9+** | `pip install pyserial` for the serial test scripts |
| **Terminal emulator** | Two independent windows, 115200 8N1 (e.g. PuTTY, Tera Term) |

### Clone

```bat
git clone https://github.com/zabooh/net_10base_t1s.git
cd net_10base_t1s\apps\tcpip_iperf_lan865x\firmware\tcpip_iperf_lan865x.X
```

The repository contains pre-built HEX images in
`apps/tcpip_iperf_lan865x/firmware/out/tcpip_iperf_lan865x/image/`
(tracked via a `.gitignore` negation rule) — a rebuild is optional for a quick
first test.

### Tool Setup

Run these two scripts once per machine. Both are interactive and save their
result to a git-ignored `.config` file.

All further commands assume you are in the working directory from the Clone step
(`tcpip_iperf_lan865x.X\`):

```bat
python setup_compiler.py   # pick the installed XC32 version
python setup_flasher.py    # assign Board 1 (GM) and Board 2 (FOL) to their debuggers
```

> **Note:** Both boards must be connected via USB (EDBG/debugger port) before
> running `setup_flasher.py`. The script detects all plugged-in EDBG debuggers
> and lets you assign which one is Board 1 (Grandmaster) and which is Board 2
> (Follower). If only one board is connected, the script will not be able to
> configure both entries.

### Build & Flash

```bat
build.bat          # incremental build  (use "build.bat rebuild" for a clean build)
python flash.py    # flash both boards in sequence
```

A build summary (flash/RAM usage, interrupt handlers, HEX path) is printed
automatically after every successful build. See §6.2 for details.

### First Console Test

Open two serial terminal windows (115200 8N1, no flow control):

- **Board 1 — Grandmaster** (COM8 by default)
- **Board 2 — Follower** (COM10 by default)

After reset both boards print the Harmony boot banner and then stay idle.
Activate PTP from each terminal:

**Board 1 — Grandmaster (verbose mode):**
```
> ptp_mode master v
```

**Board 2 — Follower (verbose mode):**
```
> ptp_mode follower v
```

Expected Board 2 output — the servo steps through these states:

```
PTP MATCHFREQ  offset=...
PTP HARDSYNC   offset=...
PTP COARSE     offset=...
PTP FINE       offset=...
[V] FINE  t1=00:00:05.123456789  t2=00:00:05.123514600  off=      +57 ns
[V] FINE  t1=00:00:05.248334810  t2=00:00:05.248392100  off=      +55 ns
```

Once the follower prints `FINE` continuously, synchronisation is established.
Offsets below ±200 ns are typical for the initial lock; steady-state offsets
are usually below ±1 µs.

Check the servo state at any time:
```
> ptp_status
```

---

## 1. PTP Implementation

PTP support was ported from the reference project
`C:\work\ptp\AN1847\t1s_100baset_bridge\` to this project
(see also [Origin](#origin) above).
The implementation supports both **Grandmaster (GM)** and **Follower (FOL)**
roles, switchable at runtime via CLI.

### 1.1 New Source Files

| File | Description |
|------|-------------|
| `src/ptp_gm_task.c/.h` | PTP Grandmaster state machine. Sends Sync + FollowUp frames at a configurable interval, arms the LAN865x TX-Match hardware for TX timestamp capture. |
| `src/PTP_FOL_task.c/.h` | PTP Follower state machine. Receives Sync/FollowUp, computes clock offset with FIR low-pass filter, and slaves the local time. |
| `src/ptp_clock.c/.h` | TC0-based nanosecond software wallclock. See [§3](#3-ptp-software-clock). |
| `src/ptp_ts_ipc.h` | Shared IPC header: `PTP_RxTimestampEntry_t` struct + `g_ptp_rx_ts` extern declaration. |
| `src/filters.c/.h` | FIR low-pass filter and exponential low-pass filter used by the Follower servo. |

Three source files added to the CMake build in `cmake/.../CMakeLists.txt`:

```cmake
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/filters.c"
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/PTP_FOL_task.c"
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_gm_task.c"
```

### 1.2 LAN865x Driver Changes

#### `src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`

##### `drvTsCaptureStatus0[]`

```c
static volatile uint32_t drvTsCaptureStatus0[DRV_LAN865X_INSTANCES_NUMBER];
```

Shadow register that saves STATUS0 bits 8–10 (TTSCAA/B/C) in `_OnStatus0()`
before the W1C-clear. Read and atomically cleared by
`DRV_LAN865X_GetAndClearTsCapture()`. This avoids the race condition where the
driver clears TTSCAA before the GM state machine can read it.

##### `g_ptp_rx_ts` — RX Timestamp IPC

```c
typedef struct { uint64_t rxTimestamp; bool valid; } PTP_RxTimestampEntry_t;
volatile PTP_RxTimestampEntry_t g_ptp_rx_ts = {0u, false};
```

Defined at the top of `TC6_CB_OnRxEthernetPacket()`. The callback now saves the
hardware RX timestamp into this struct when `rxTimestamp != NULL`. The
application reads it in `pktEth0Handler()` when a PTP frame (EtherType 0x88F7)
arrives.

##### TC6_MEMMAP — Init Register Map

The memory map was updated to enable PTP timestamp hardware:

| Register | Address | Old Value | New Value | Comment |
|----------|---------|-----------|-----------|---------|
| TXMPATH | 0x00040041 | *(not present)* | 0x0088 | EtherType high byte 0x88 |
| TXMPATL | 0x00040042 | *(not present)* | 0xF710 | EtherType low 0xF7 + PTP Sync 0x10 |
| TXMMSKH | 0x00040043 | 0x00FF | 0x0000 | No masking — exact match |
| TXMMSKL | 0x00040044 | 0xFFFF | 0x0000 | No masking |
| TXMLOC  | 0x00040045 | 0x0000 | 0x001E | Byte offset 30 (from Microchip PTP demo) |
| TXMCTL  | 0x00040040 | 0x0002 | 0x0000 | Disabled at startup; armed per-Sync |
| IMASK0  | 0x0000000C | 0x0100 | 0x0000 | All interrupts unmasked (incl. TTSCAA bit 8) |
| DEEP_SLEEP_CTRL_1 | 0x00040081 | 0x0080 | 0x00E0 | Updated per reference |
| *(removed)* | 0x000400E0 | 0xC000 | — | Moved to `_InitConfig` case 46 as PADCTRL RMW |

##### `_InitConfig` — Cases 46 and 47

```c
// Case 46: PADCTRL RMW — enables TX timestamp pad output
TC6_ReadModifyWriteRegister(tc, 0x000A0088u, 0x00000100u, 0x00000300u, ...);

// Case 47: PPSCTL — enables PPS clock for TSU counter
TC6_WriteRegister(tc, 0x000A0239u, 0x0000007Du, ...);
```

Previously case 46 wrote `0xC000` to `0x000400E0`. Replaced with the PADCTRL
RMW required for TX hardware timestamping.

##### `_InitUserSettings` — Case 8

```c
regVal = 0x9026u;
regVal |= 0x80u;  // FTSE: Frame Timestamp Enable
regVal |= 0x40u;  // FTSS: 64-bit timestamps
```

Enables frame-level timestamping in CONFIG0. Required for TTSCAA TX capture
and the TC6 driver's RTSA 8-byte timestamp stripping on RX.

##### `_OnStatus0` — TTSCAA Saving

```c
if (0u != (value & 0x0F00u)) {
    SYS_CONSOLE_PRINT("[DBG] _OnStatus0: 0x%08lX\r\n", (unsigned long)value);
}
if (0u != (value & 0x0700u)) {
    for (i = 0u; i < DRV_LAN865X_INSTANCES_NUMBER; i++) {
        if (pDrvInst == &drvLAN865XDrvInst[i]) {
            drvTsCaptureStatus0[i] |= (value & 0x0700u);
            break;
        }
    }
}
// ... then W1C WriteRegister ...
```

STATUS0 bits 8–10 are saved into `drvTsCaptureStatus0[]` *before* the
Write-1-to-Clear operation.

##### New Public Functions

| Function | Description |
|----------|-------------|
| `DRV_LAN865X_SendRawEthFrame(idx, pBuf, len, tsc, cb, pTag)` | Sends a raw Ethernet frame via TC6. TSC flag `0x01` = Capture A for Sync, `0x00` = no capture. |
| `DRV_LAN865X_IsReady(idx)` | Returns `true` when the driver instance is fully initialised. Used to detect Loss-of-Framing recovery. |
| `DRV_LAN865X_GetAndClearTsCapture(idx)` | Atomically reads and clears `drvTsCaptureStatus0[idx]`. Called by the GM state machine to retrieve TTSCAA/B/C bits. |

```c
bool     DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf,
             uint16_t len, uint8_t tsc,
             DRV_LAN865X_RawTxCallback_t cb, void *pTag);
bool     DRV_LAN865X_IsReady(uint8_t idx);
uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx);
```

#### `src/config/default/driver/lan865x/drv_lan865x.h`

Added after the existing `DRV_LAN865X_ReadModifyWriteRegister` declaration:
- `DRV_LAN865X_RawTxCallback_t` typedef
- Declarations for `DRV_LAN865X_SendRawEthFrame()`, `DRV_LAN865X_IsReady()`,
  `DRV_LAN865X_GetAndClearTsCapture()`

#### `src/config/default/driver/lan865x/src/dynamic/tc6/tc6.c`

Two changes vs. the reference project (`t1s_100baset_bridge`) to support correct
PTP TX timestamp capture indexing:

**1. Added include:**

```c
#include "driver/lan865x/src/dynamic/drv_lan865x_local.h"
```

**2. `TC6_Init` — instance slot selection rewritten:**

| Reference project | This project |
|------------------|--------------|
| `for (i=0; i<TC6_MAX_INSTANCES; i++)` loop finds first free slot; uses loop index `i` as TC6 instance number | Uses `DRV_LAN865X_DriverInfo *pDrvInst = (DRV_LAN865X_DriverInfo*)pGlobalTag` and assigns `pDrvInst->index` as the TC6 instance number |

**Reason:** The PTP TX-Timestamp array `drvTsCaptureStatus0[i]` in
`drv_lan865x_api.c` is indexed by driver instance index. This change guarantees
`TC6_t::instance == DRV_LAN865X_DriverInfo::index` — a requirement for correct
timestamp attribution in multi-instance configurations.

### 1.3 Application Changes

#### `src/app.c` — New includes

```c
#include <string.h>
#include "ptp_ts_ipc.h"
#include "PTP_FOL_task.h"
#include "ptp_gm_task.h"
#define TCPIP_THIS_MODULE_ID  TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"
#include "library/tcpip/src/tcpip_packet.h"
```

#### `pktEth0Handler()`

Registered with `TCPIP_STACK_PacketHandlerRegister()` on eth0. Intercepts frames
with EtherType `0x88F7` (PTP), acknowledges and returns `true` (consumed) so the
IP stack does not see them. No buffering — frame data is already captured by the
primary path (`TC6_CB_OnRxEthernetPacket` → `g_ptp_raw_rx`) at driver level
before this handler is called.

#### State machine restructure

| State | Behaviour |
|-------|-----------|
| `APP_STATE_INIT` | Prints build timestamp, sets state → `APP_STATE_SERVICE_TASKS` |
| `APP_STATE_SERVICE_TASKS` | Waits until `TCPIP_STACK_NetIsUp()` is true, then registers `pktEth0Handler`, sets state → `APP_STATE_IDLE` |
| `APP_STATE_IDLE` | First entry: calls `PTP_FOL_Init()`. Main loop: LAN register service, GM/FOL service every 1 ms, FOL frame delivery from driver path, LOFE reinit detection |

#### `APP_STATE_IDLE` — PTP services

```c
// GM: call every 1 ms
if (PTP_FOL_GetMode() == PTP_MASTER && (current_tick - last_gm_tick) >= ticks_per_ms) {
    PTP_GM_Service();
}

// FOL: call every 1 ms
if (PTP_FOL_GetMode() == PTP_SLAVE && (current_tick - last_fol_tick) >= ticks_per_ms) {
    PTP_FOL_Service();
}

// Deliver buffered PTP frame — filled by TC6_CB_OnRxEthernetPacket (driver level)
if (g_ptp_raw_rx.pending) {
    g_ptp_raw_rx.pending = false;
    if (PTP_FOL_GetMode() == PTP_SLAVE)
        PTP_FOL_OnFrame(g_ptp_raw_rx.data, g_ptp_raw_rx.length, g_ptp_raw_rx.rxTimestamp);
}

// Re-run GM init after LAN865x LOFE recovery
bool lan865x_ready = DRV_LAN865X_IsReady(0u);
if (!lan865x_prev_ready && lan865x_ready && PTP_FOL_GetMode() == PTP_MASTER) {
    PTP_GM_Init();
}
lan865x_prev_ready = lan865x_ready;
```

#### `src/app.h`

Added `APP_STATE_IDLE` to the `APP_STATES` enumeration.

### 1.4 CLI Commands

| Command | Description |
|---------|-------------|
| `ptp_mode off` | Disable PTP |
| `ptp_mode follower` | Enable Follower mode (PTP_SLAVE) |
| `ptp_mode follower v` | Enable Follower mode with per-Sync verbose line |
| `ptp_mode master` | Enable Grandmaster mode (PTP_MASTER) |
| `ptp_status` | Show current mode, sync count, servo state |
| `ptp_interval <ms>` | Set GM Sync interval in ms (default 125) |
| `ptp_offset` | Show follower clock offset in ns |
| `ptp_reset` | Reset follower servo to UNINIT |
| `ptp_dst [multicast\|broadcast]` | Set PTP destination MAC |

### 1.5 Console Output Format

All periodic PTP prints use carriage-return-only (`\r`, no `\n`) to overwrite
the same terminal line continuously. State-transition messages are prefixed with
`\r\n` so they scroll normally without corrupting the overwriting line.

#### GM output (`ptp_gm_task.c`)

One overwriting line per FollowUp sent:

```
[GM] #498  t1=00:01:28.804334810
```

Format: `[GM] #<seqId>  t1=HH:MM:SS.nnnnnnnnn\r`

The `sec` value (TSU counter in seconds) is decomposed to HH:MM:SS:

```c
uint32_t h = sec / 3600u;
uint32_t m = (sec % 3600u) / 60u;
uint32_t s = sec % 60u;
SYS_CONSOLE_PRINT("[GM] #%u  t1=%02lu:%02lu:%02lu.%09lu\r", ...);
```

#### Follower verbose mode (`PTP_FOL_task.c`)

Activated with `ptp_mode follower v`. One overwriting line per received Sync:

```
[V] FINE       t1=00:01:28.804334810  t2=00:01:28.804391620  off=       +57 ns
```

Format: `[V] <STATE>  t1=HH:MM:SS.nnnnnnnnn  t2=HH:MM:SS.nnnnnnnnn  off=<±offset> ns\r`

State names (fixed-width 9 chars): `UNINIT   `, `MATCHFREQ`, `HARDSYNC `,
`COARSE   `, `FINE     `.

State transitions are printed with `\r\n` prefix so they do not overwrite the
running verbose line:

```c
PTP_LOG("\r\nPTP COARSE  offset=%d\r\n", (int)offset);
PTP_LOG("\r\nPTP FINE    offset=%d\r\n", (int)offset);
```

#### `PTP_FOL_SetVerbose()` API

```c
void PTP_FOL_SetVerbose(bool verbose);  // PTP_FOL_task.h
```

Called from `ptp_mode_cmd()` in `app.c`:

```c
bool verbose = (argc >= 3) && (strcmp(argv[2], "v") == 0);
PTP_FOL_SetVerbose(verbose);
```

---

## 2. Bug Fixes

### 2.1 TX Timestamp: `DELAY_UNLOCK_EXT` Reduced from 100 ms to 5 ms

**File:** `src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`

```c
// Before:
#define DELAY_UNLOCK_EXT  (100u)
// After:
#define DELAY_UNLOCK_EXT  (5u)
```

**Root cause:** The TX Timestamp Capture Available (TTSCAA) bit appears in
STATUS0 approximately 1 ms after the EXST signal. The original 100 ms timeout
caused missed timestamps (`TTSCMA`). Reduced to 5 ms.

### 2.2 Role-Swap: Crystal Calibration Overwrite

#### Background

A **role-swap** describes the scenario where PTP mode is disabled on both boards
(`ptp_mode off`) and then restarted with the Grandmaster and Follower roles
swapped. Before this fix the new Follower never reached FINE state; its offset
was permanently stuck at approximately **−3.13 ms**.

#### Root Cause

The bug was caused by two independent issues in `src/ptp_gm_task.c`:

**Bug 1 — `PTP_GM_Init()` overwrote the crystal calibration (primary)**

During the first PTP session the FOL servo runs its MATCHFREQ phase and measures
the board's actual crystal frequency by fine-tuning `MAC_TI` and `MAC_TISUBN`.
For board 1, for example, the calibrated value is `MAC_TI = 39` (nominal is 40),
reflecting a real crystal frequency slightly below 25 MHz.

When roles were swapped and the same board became the GM, `PTP_GM_Init()`
unconditionally wrote `MAC_TI = 40` (nominal) — destroying the calibration and
making the GM clock run approximately 2.5 % too fast. The new FOL then tried to
correct this 2.5 % frequency error with a small PI servo whose correction range
is far too narrow. Because the error magnitude matched the servo's maximum
per-step correction exactly, the offset converged to a fixed point and stayed
there forever:

```
drift per sync = (40 − 39) / 40 × 125 ms = 3.125 ms
```

This is why the stuck offset was always ≈ −3.13 ms and never changed with time.

**Bug 2 — `gm_deinit_vals` set `MAC_TI = 0` (secondary)**

After `ptp_mode off` the deinit sequence wrote `MAC_TI = 0`, stopping the PTP
hardware clock entirely. This left the hardware in a broken state for subsequent
role assignments.

#### Fix

**`src/PTP_FOL_task.c` / `src/PTP_FOL_task.h` — export calibrated values**

New function that lets other modules read the TI/TISUBN values the FOL servo
settled on at the end of its MATCHFREQ phase:

```c
/* PTP_FOL_task.h */
/**
 * @brief Returns the crystal-calibrated clock increment values measured by
 *        the FOL servo during its last MATCHFREQ phase.  If the servo has not
 *        yet completed MATCHFREQ, returns nominal defaults (TI=40, TISUBN=0).
 *
 * @param pTI     Receives MAC_TI value (nanoseconds per 25 MHz tick).
 * @param pTISUBN Receives MAC_TISUBN value (sub-nanosecond fractional part).
 */
void PTP_FOL_GetCalibratedClockInc(uint32_t *pTI, uint32_t *pTISUBN);
```

Implementation reads the existing module-level statics
`calibratedTI_value` / `calibratedTISUBN_value`:

```c
void PTP_FOL_GetCalibratedClockInc(uint32_t *pTI, uint32_t *pTISUBN)
{
    if (pTI)     *pTI     = calibratedTI_value;
    if (pTISUBN) *pTISUBN = calibratedTISUBN_value;
}
```

**`src/ptp_gm_task.c` — use calibrated TI on init, keep clock running on deinit**

*Init sequence* (`GM_INIT_WRITE_COUNT` changed 8 → 9, `gm_init_vals[]` made
non-const):

| Index | Register | Before fix | After fix |
|-------|----------|-----------|-----------|
| 6 | `MAC_TISUBN` | not present | filled from `PTP_FOL_GetCalibratedClockInc()` |
| 7 | `MAC_TI` | `40` (hardcoded nominal) | filled from `PTP_FOL_GetCalibratedClockInc()` |
| 8 | `PPSCTL` | index 7 | index 8 (shifted) |

At the start of `PTP_GM_Init()`:

```c
uint32_t calTI = 40u, calTISUBN = 0u;        /* fallback: nominal */
PTP_FOL_GetCalibratedClockInc(&calTI, &calTISUBN);
gm_init_vals[6] = calTISUBN;
gm_init_vals[7] = calTI;
if (calTI != 40u || calTISUBN != 0u) {
    SYS_CONSOLE_PRINT("[PTP-GM] Using calibrated TI=%u TISUBN=0x%08lX\r\n",
                      (unsigned)calTI, (unsigned long)calTISUBN);
}
```

*Deinit sequence* — `gm_deinit_vals[6]` (MAC_TI) changed `0u` → `40u`:

```c
/* was: 0u  — froze the hardware PTP clock after ptp_mode off  */
/* now: 40u — clock keeps ticking at nominal rate after deinit */
static const uint32_t gm_deinit_vals[] = { ..., 40u, ... };
```

---

## 3. PTP Software Clock — `src/ptp_clock.c` / `src/ptp_clock.h`

### Purpose

The LAN865x hardware PTP clock (TSU counter) is accurate but can only be read
via an SPI register access — a blocking operation that takes several hundred
microseconds. `ptp_clock.c` creates a **lightweight MCU-internal mirror** of
that wallclock, queryable in nanoseconds with zero SPI traffic at query time.

This serves two purposes:

- **Observability:** query `clk_get` via UART from Python test scripts and
  compute the inter-board time difference directly, without touching the
  LAN865x over SPI.
- **Before/after comparison:** by calling `clk_set 0` on both boards
  simultaneously and then polling `clk_get`, it is possible to measure the
  raw free-running crystal drift first (no PTP), and then repeat the same
  measurement with PTP active — demonstrating quantitatively what PTP
  synchronisation achieves (see §5.4).

### Design — Anchor + TC0 Interpolation

The core idea is an **anchor point**: a pair `(wallclock_ns, TC0_tick)` captured
at the exact moment a hardware PTP timestamp arrives from the LAN865x.

```
                  anchor captured here
                        |
  PTP wallclock:  ------+---------------------------------------->
                        |<--- TC0 free-running since anchor --->|
                                                                 |
                                       PTP_CLOCK_GetTime_ns()   |
                                       = anchor_wc_ns
                                         + ticks_to_ns(now_tick - anchor_tick)
```

Between anchors the TC0 hardware timer (60 MHz, GCLK0/2) free-runs and provides
sub-microsecond interpolation. The tick-to-nanosecond conversion is exact at
this frequency — no rounding:

```c
// 1 tick = 1e9 / 60e6 ns = 50/3 ns  (exact integer ratio)
static uint64_t ticks_to_ns(uint64_t ticks)
{
    return (ticks / 3ULL) * 50ULL + ((ticks % 3ULL) * 50ULL) / 3ULL;
}
```

Anchors arrive every ~125 ms (one per Sync frame). The maximum interpolation
error within a 125 ms window for a crystal running at ±500 ppm is at most
±62 µs — well below the re-anchoring residual noise of ±65–130 µs.

### Anchor Sources

| Board role | Called from | Hardware event |
|-----------|-------------|----------------|
| Follower | `PTP_FOL_task.c` via `PTP_CLOCK_Update()` | RX timestamp from RTSA footer (stripped by TC6 driver) on every Sync/FollowUp |
| Grandmaster | `ptp_gm_task.c` via `PTP_CLOCK_Update()` | TX timestamp from TTSCAH/AL registers after Sync frame sent (TTSCAA bit) |

In both cases `PTP_CLOCK_Update(wallclock_ns, sys_tick)` stores the pair:

```c
void PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick)
{
    s_anchor_wc_ns = wallclock_ns;   // hardware PTP timestamp (ns)
    s_anchor_tick  = sys_tick;       // TC0 tick at that exact moment
    s_valid        = true;
}
```

### What You See Without PTP — Free-Running Crystal Drift

After `clk_set 0` on both boards simultaneously (anchor set once via
`PTP_CLOCK_ForceSet(0)`, never updated again), TC0 runs freely on each board's
independent crystal:

```
[Board GM]  clk_get: 8392451200 ns   drift=0ppb
[Board FOL] clk_get: 8389312450 ns   drift=0ppb
             difference: +3138750 ns  (~3.1 ms after ~16 s)
```

The difference grows linearly at the crystal frequency error between the two
boards. Measured across all test runs: **−532…+209 ppm** free-run slope. This
is the **Phase 0 baseline** captured by `ptp_sync_before_after_test.py`.

### What You See With PTP Active — Locked

Once the FOL servo reaches FINE state, `PTP_CLOCK_Update()` is called every
~125 ms with the hardware PTP timestamp. TC0 interpolates in between.
Both boards now track the same shared time:

```
[Board GM]  clk_get: 3141592653 ns   drift=0ppb
[Board FOL] clk_get: 3141592718 ns   drift=+6ppb
             difference: +65 ns
```

`drift_ppb` (non-zero on FOL only) shows the residual frequency error the
PTP servo is still trimming after the one-time TISUBN crystal calibration.

### TC0 Tick-Rate Correction (TISUBN)

The FOL servo writes the crystal-calibrated value to `MAC_TISUBN` once at
UNINIT→MATCHFREQ. This corrects the LAN865x TSU counter frequency to match the
GM crystal. From MATCHFREQ onward the hardware-timestamp-based anchors are
frequency-matched, and the remaining interpolation error is only UART
scheduling jitter (~65–130 µs stdev per anchor, measured).

### `drift_ppb` — Residual Frequency Error

After the TISUBN correction, `drift_ppb` reports the remaining frequency error
observed by the PTP servo (in parts per billion):

```c
int32_t PTP_CLOCK_GetDriftPPB(void);    // read — used by clk_get CLI
void    PTP_CLOCK_SetDriftPPB(int32_t); // written by PTP_FOL_task.c after each FIR update
```

Updated in `PTP_FOL_task.c` at every Sync frame in COARSE or FINE:

```c
PTP_CLOCK_SetDriftPPB((int32_t)((rateRatioFIR - 1.0) * 1e9));
```

Observed values: **±0…20 ppb** residual after TISUBN correction.
Resets to 0 on `clk_set 0` (`PTP_CLOCK_ForceSet()`).

### CLI Commands

| Command | Description |
|---------|-------------|
| `clk_get` | Print current wallclock and drift: `clk_get: <ns>  drift=<±ppb>ppb` |
| `clk_set 0` | Zero the wallclock (independent timer baseline) |

---

## 4. Further Firmware Changes

### 4.1 MAC Address Randomisation

**File:** `src/config/default/initialization.c`

Before calling `TCPIP_STACK_Init()`, the last three bytes of the Ethernet MAC
address are randomised using the ATSAME54 hardware TRNG peripheral.

**Changes:**
- `#include <stdio.h>` added.
- `s_macAddrStr0[18]` buffer declared at file scope.
- `APP_RandomizeMacLastBytes()` function added:
  - Enables MCLK for TRNG (`MCLK_APBCMASK_TRNG_Msk`).
  - Enables TRNG (`TRNG_CTRLA_ENABLE_Msk`).
  - Polls `TRNG_INTFLAG_DATARDY_Msk`, reads `TRNG_DATA`.
  - Formats result as `"00:04:25:XX:XX:XX"` into `s_macAddrStr0`.
- Called immediately before `TCPIP_STACK_Init()`.
- `TCPIP_HOSTS_CONFIGURATION[0].macAddr` field set to `s_macAddrStr0`.

### 4.2 LAN865x Register Access CLI

**File:** `src/app.c`

Two CLI commands added to the `Test` command group for run-time LAN865x SPI
register access without a debugger.

| Command | Description |
|---------|-------------|
| `Test lan_read <addr_hex>` | Read a LAN865x register and print the result |
| `Test lan_write <addr_hex> <value_hex>` | Write a LAN865x register |

**Implementation details:**
- Non-blocking state machine: `app_lan_state_t` (IDLE / WAIT_READ / WAIT_WRITE).
- Callbacks `lan_read_callback()` / `lan_write_callback()` set volatile flags.
- 200 ms timeout (`APP_LAN_TIMEOUT_MS`) via `SYS_TIME_Counter64Get()`.
- Commands call `DRV_LAN865X_ReadRegister()` / `DRV_LAN865X_WriteRegister()` on
  driver instance 0.
- `Command_Init()` registers the group via `SYS_CMD_ADDGRP()`, called from
  `APP_Initialize()`.

---

## 5. Test Scripts & Validation

### How the Tests Control the Firmware

The ATSAME54P20A firmware exposes a **Harmony CLI** over UART: a simple
line-based command shell accessible at 115200 baud via the standard SYS_CMD
module. All test scripts remotely drive this CLI over `pyserial`, without any
debugger connection or custom firmware protocol.

The general pattern used by every script:

```
Host PC (Python)                       Board (ATSAME54 firmware)
─────────────────────────────────────────────────────────────────
ser.write(b"ptp_mode follower\r\n")  →  Harmony CLI processes command
                                         SYS_CONSOLE_PRINT("[PTP-FOL] ...")
response = ser.read_until(timeout)    ←  UART TX: status / event strings
```

Parsing is done by regex on the UART output — no binary protocol, no
custom framing. Each script opens two `serial.Serial` instances (one per
board), sends commands and reads responses in parallel Python threads where
necessary (e.g. simultaneous `clk_set 0` for baseline synchronisation).

**Key constraint on Windows:** never read the same `pyserial.Serial` port
from two threads simultaneously — this corrupts the Win32 overlapped I/O
handles and causes heap corruption. Every script either uses a single reader
thread per port, or serialises port access at the call site (see §5.2 for the
specific fix applied to `ptp_onoff_test.py`).

**Prerequisites:** `pip install pyserial`

**Board port assignment (default):** Board 1 = COM8, Board 2 = COM10
(as configured by `setup_flasher.py`).

Run the tests in the order shown — §5.1 is the sanity baseline, §5.2–§5.3
verify specific fix correctness, §5.4–§5.5 demonstrate end-to-end accuracy.

### 5.1 Baseline: TC0 Timer Consistency — `hw_timer_sync_test.py`

Validates that the TC0-based software clock (`PTP_CLOCK`) operates correctly
**without PTP**. Measures the raw crystal frequency difference between the two
boards and verifies that the TC0 tick-to-nanosecond interpolation is internally
consistent. Run this first as a sanity check before any PTP test.

| What it proves | What it does NOT prove |
|----------------|----------------------|
| TC0 tick-to-ns conversion correct on both boards | Correct PTP anchor capture |
| `PTP_CLOCK_GetTime_ns()` interpolation consistent | PTP Ethernet timestamping (RTSA / TTSCAL) |
| UART serialisation latency correction works | |
| Crystal frequency ratio measured accurately | |

#### Test Steps

| Step | Description |
|------|-------------|
| 0 — Simultaneous `clk_set 0` | Zeroes both clocks in parallel threads; measures thread launch skew. |
| 1 — Settle | 2 s pause. |
| 2 — Collect 100 paired samples | 100 swap-symmetrised `clk_get` pairs at 100 ms intervals. Linear regression removes crystal trend; residuals must be < 500 µs. |

The growing mean offset (linear drift) is **expected** — two free-running
crystal oscillators always drift apart at a constant rate. The PASS check only
verifies the *residuals after removing that linear trend*.

#### Usage

```bat
python hw_timer_sync_test.py --a-port COM8 --b-port COM10
```

Optional: `--n <samples>` (default 100), `--pause-ms` (default 100),
`--threshold-us` (default 500), `--settle-s` (default 2), `--log-file <path>`.

#### Results (6 valid runs, 2026-04-09 / 2026-04-10)

| Run | Date/time | Slope (ppm) | Intercept (µs) | Res. stdev (µs) | Result |
|-----|-----------|-------------|----------------|-----------------|--------|
| 1 | 2026-04-09 17:56 | −321.2 | −687 | 91 | PASS |
| 2 | 2026-04-09 17:57 | −266.0 | −424 | 134 | PASS |
| 3 | 2026-04-10 09:08 | −0.0¹ | −22 | 145 | PASS |
| 4 | 2026-04-10 09:08 | −392.4 | −453 | 192 | PASS |
| 5 | 2026-04-10 09:12 | +5.5 | −66 | 62 | PASS |
| 6 | 2026-04-10 09:19 | −139.9 | −691 | 149 | PASS |

¹ Run 3 immediately after a PTP session; TISUBN retained its correction →
apparent slope ≈ 0. Residual stdev still 145 µs < 500 µs.

**All 6 runs: PASS (3/3 steps each)**

---

### 5.2 PTP On/Off Resilience — `ptp_onoff_test.py`

Automated resilience test: starts PTP, measures baseline offset, stops GM,
observes drift, restarts GM, verifies re-convergence and post-restart accuracy.

#### Usage

```bat
python ptp_onoff_test.py --gm-port COM10 --fol-port COM8
```

#### Test Phases

| Phase | Description |
|-------|-------------|
| Steps 0–3 | Reset boards, set IP addresses, bidirectional ping, start PTP, wait for FOL FINE |
| Phase A | Baseline: 10 offset samples while GM running |
| Phase B | Blackout: stop GM, monitor FOL offset for 5 s |
| Phase C | Re-convergence: restart GM, wait for FINE |
| Phase D | Post-restart: 10 offset samples, verify ±100 ns |

#### Detection Patterns

| Pattern | Firmware string matched |
|---------|------------------------|
| `RE_HARD_SYNC` | `"Hard sync completed"` |
| `RE_MATCHFREQ` | `"UNINIT->MATCHFREQ"` |
| `RE_COARSE` | `"PTP COARSE"` |
| `RE_FINE` | `"PTP FINE"` |

#### Architecture Note — Windows Serial Threading

**Rule:** Never read the same `pyserial.Serial` port from two threads
simultaneously on Windows — corrupts overlapped I/O handles → access violation.

```python
# 1. FOL command — main thread owns fol_ser exclusively
resp = send_command(self.fol_ser, "ptp_mode follower", ...)
time.sleep(0.5)
# 2. Start convergence thread (sole reader of fol_ser from here on)
self.fol_ser.reset_input_buffer()
self._start_convergence_thread()
# 3. GM command — uses gm_ser only, safe to run concurrently
self.gm_ser.write(b"ptp_mode master\r\n")
```

#### Result — `ptp_onoff_20260408_002157.log`

| Phase | Result |
|-------|--------|
| Initial convergence | FINE in **2.7 s** (HARD_SYNC @ 0.4 s, MATCHFREQ @ 2.3 s) |
| Phase A baseline (n=10) | mean = **+36.5 ns**, stdev = 16.5 ns |
| Phase B blackout (5 s) | Offset frozen at **+78 ns**, drift = **0 ns** — local clock holds |
| Phase C re-convergence | FINE in **0.8 s** — saved TI/TISUBN reused, MATCHFREQ skipped |
| Phase D post-restart (n=10) | mean = **+43.9 ns**, stdev = 14.9 ns, **10/10 within ±100 ns** |

**Overall: PASS (5/5)**

---

### 5.3 Role-Swap Validation — `ptp_role_swap_test.py`

Verifies correct convergence after a GM↔FOL role swap.
Directly validates [Bug Fix §2.2](#22-role-swap-crystal-calibration-overwrite).

#### Usage

```bat
python ptp_role_swap_test.py --board1-port COM8 --board2-port COM10
```

Optional: `--board1-ip`, `--board2-ip`, `--convergence-timeout` (default 30 s),
`--verbose`.

#### Test Phases

| Phase | Board 1 | Board 2 |
|-------|---------|---------|
| Phase 1 | Follower | Grandmaster — wait for FINE, collect 10 offset samples |
| — | `ptp_mode off` on both, pause 5 s | |
| Phase 2 | **Grandmaster** | **Follower** — wait for FINE, collect 10 offset samples |

#### Pass Criteria

- Both phases reach FINE within `--convergence-timeout`
- Phase 2 mean offset within ±500 ns, stdev < 100 ns, ≥ 9/10 samples within ±500 ns

#### Results

**Before fix:**

| Metric | Phase 1 | Phase 2 |
|--------|---------|---------|
| FINE reached | 2.7 s | **never** (timeout at 30 s) |
| mean offset | +52 ns | **−3 138 186 ns** (stuck) |

**After fix (2 confirmed runs):**

| Run | Ph.1 FINE | Ph.1 mean | Ph.2 FINE | Ph.2 mean | Ph.2 stdev | ≤±500 ns |
|-----|-----------|-----------|-----------|-----------|------------|---------|
| 1 | 2.7 s | +50.8 ns | **2.7 s** | **−4.0 ns** | 12.8 ns | 10/10 |
| 2 | 3.1 s | +57.9 ns | **2.9 s** | **−9.2 ns** | 21.2 ns | 10/10 |

**Overall: PASS (6/6 checks, both runs)**

---

### 5.4 PTP Sync Before/After — `ptp_sync_before_after_test.py`

Demonstrates PTP synchronisation quantitatively: free-running crystal drift
measured first, then again with PTP active — both in a single automated run.

#### Test Phases

| Phase | Description |
|-------|-------------|
| **Phase 0 — Free Running** | Both clocks zeroed simultaneously, `clk_get` pairs collected for 60 s. Linear regression shows raw crystal drift. |
| **PTP Setup** | IP config, start Follower + Grandmaster, wait for FINE. |
| **Phase 1 — PTP Active** | Clocks re-zeroed, `clk_get` pairs collected for 60 s with PTP running. |
| **Comparison** | Side-by-side table: slope, residual stdev, drift reduction %. |

Measurement uses **swap symmetry**: alternating samples query GM first then FOL,
the next queries FOL first then GM. The send-time skew between the two parallel
threads is subtracted, eliminating systematic host PC scheduler bias.

#### Usage

```bat
python ptp_sync_before_after_test.py --gm-port COM8 --fol-port COM10
```

#### PASS Criteria

| Criterion | Default threshold |
|-----------|------------------|
| `|slope_ptp|` | < 2.0 ppm |
| Residual stdev | < 500 µs |

#### Results (10 runs)

| Run | Free-run (ppm) | PTP slope (ppm) | PTP stdev (µs) | drift stdev (ppb) | FINE (s) |
|-----|---------------|-----------------|----------------|-------------------|----------|
| 1 | +179.3 | −0.243 | 68 | 0¹ | 2.8 |
| 2 | +209.0 | −0.183 | 66 | 0¹ | 2.9 |
| 3 | −75.4 | +0.146 | 129 | 0¹ | 2.7 |
| 4 | −518.5 | +0.666 | 92 | 6 | 2.7 |
| 5 | −229.3 | +0.641 | 121 | 10 | 2.7 |
| 6–10² | −532…−46 | −0.43…+0.75 | 60…78 | 6–10 | 2.7 |

¹ Runs 1–3 predated the `drift_ppb` firmware update (value was always 0).  
² Runs 6–10 from the reproducibility test (§5.5).

**All 10 runs: PASS**

---

### 5.5 PTP Reproducibility — `ptp_reproducibility_test.py`

Runs `ptp_sync_before_after_test.py` N times (default 5) and aggregates all
results in a single summary table. Purpose: automated verification of
reproducibility.

#### Usage

```bat
python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10
python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10 --runs 3
```

All arguments (`--free-run-s`, `--ptp-s`, `--slope-threshold-ppm`, etc.) are
forwarded to the sub-test.

Each run generates a unique log filename
(`ptp_sync_before_after_test_<YYYYMMDD_HHMMSS>.log`) **before** starting the
sub-test and passes it via `--log-file` — no directory search, no ambiguity.

#### Summary Table Columns

| Column | Description |
|--------|-------------|
| `Free(ppm)` | Free-run crystal drift (Phase 0 slope) |
| `FreeStd` | Free-run residual stdev |
| `FINE(s)` | PTP convergence time to FINE state |
| `PTP(ppm)` | PTP-active slope (Phase 1) |
| `PTPStd` | PTP-active residual stdev |
| `dFOLstd` | Follower `drift_ppb` stdev during Phase 1 |
| `Reduc%` | Slope reduction by PTP in percent |
| `Dur(s)` | Single-run wall-clock duration |
| `Result` | PASS / FAIL |

#### Confirmed Reproducibility Run (2026-04-10)

5 consecutive runs, 60 s free-run + 60 s PTP each, total **12 minutes**:

| Run | Free (ppm) | PTP (ppm) | PTP stdev | FINE (s) | Reduc. |
|-----|-----------|-----------|-----------|----------|--------|
| 1 | −532.5 | +0.502 | 60 µs | 2.7 s | 99.9% |
| 2 | −517.6 | +0.198 | 61 µs | 2.7 s | 100.0% |
| 3 | −293.3 | +0.745 | 61 µs | 2.7 s | 99.7% |
| 4 | −365.7 | +0.306 | 78 µs | 2.7 s | 99.9% |
| 5 | −45.7 | −0.429 | 62 µs | 2.7 s | 99.1% |

**PTP slope**: mean = +0.26 ppm · stdev = ±0.44 ppm  
**PTP stdev**: mean = 64 µs · stdev = ±7.6 µs  
**FINE time**: 2.7 s in all 5 runs  
**Overall: PASS (5/5)**

---

## 6. Hardware & Build Setup

### 6.1 Hardware Configuration

- **MCU**: ATSAME54P20A (Cortex-M4F, 120 MHz)
- **Ethernet**: LAN865x 10BASE-T1S via SPI (TC6 protocol)
- **Framework**: MPLAB Harmony 3 (bare-metal, no FreeRTOS)
- **Build**: CMake 4.1 + Ninja
- **HEX output**: `tcpip_iperf_lan865x.X/out/tcpip_iperf_lan865x/default.hex`
- **Programmer**: MPLAB MDB (`flash.py`)

| Board | Role (default) | Serial |
|-------|----------------|--------|
| Board 1 | Grandmaster (COM8) | `ATML3264031800001049` |
| Board 2 | Follower (COM10) | `ATML3264031800001290` |

### 6.2 Build Infrastructure

#### Out-of-box workflow after `git clone`

```bat
python setup_compiler.py    # select XC32 version (patches toolchain.cmake)
python setup_flasher.py     # assign Board 1 / Board 2 to connected debuggers
build.bat                   # compile  (summary printed automatically)
python flash.py             # flash both boards
```

#### `setup_compiler.py`

One-time setup tool — scans `C:\Program Files\Microchip\xc32\` for installed
XC32 versions, lets the user pick one, patches all version-string occurrences in
`cmake/.generated/toolchain.cmake` (both forward-slash and double-backslash
forms, covering all 17 entries: `CMAKE_C_COMPILER`, `CMAKE_AR`, `MP_BIN2HEX`,
etc.), and saves the choice to `setup_compiler.config` (JSON, git-ignored).

```
Installed XC32 versions (2 found):
  [1] v4.60       C:\Program Files\Microchip\xc32\v4.60\bin\xc32-gcc.exe
  [2] v5.10       C:\Program Files\Microchip\xc32\v5.10\bin\xc32-gcc.exe  <-- current
  [0] Abort / keep current selection
Select version number: 1
...
Patched toolchain.cmake: v5.10 -> v4.60
Done. build.bat will use XC32 v4.60.
```

#### `build.bat`

Single-command CMake + Ninja build.

| Parameter | Behaviour |
|-----------|-----------|
| *(none)* / `incremental` | Only recompiles changed files |
| `clean` | Deletes temporary build directory |
| `rebuild` | Clean + full build |
| `help` | Prints options |

Reads `setup_compiler.config` at startup; aborts with a clear error if the file
is missing or the configured `xc32-gcc.exe` does not exist.

CMake intermediate files (`.o`, `.d`, `build.ninja`) are placed **outside the
repository** at `C:\work\ptp\AN1847\harmony\temp\tcpip_iperf_lan865x\default\`
to avoid Windows MAX_PATH (260 character) issues. The path is derived
automatically at runtime relative to `build.bat` — no hardcoded absolute path,
works on any machine (provided the project is checked out inside a `harmony\`
parent directory).

#### `build_summary.py`

Called automatically by `build.bat` after every successful build. Parses the
linker output and ELF symbol table to produce a concise human-readable summary.

| Source | Information extracted |
|--------|----------------------|
| `memoryfile.xml` | Flash used/free/total, RAM used/free/total |
| `mem.map` | `_min_heap_size` (heap reserved by linker script) |
| `default.elf` via `xc32-nm` | Active interrupt handler names (weak `Dummy_Handler` symbols silently skipped) |
| `default.elf` binary scan | Build timestamp (`__DATE__` / `__TIME__` embedded by `app.c`) |

Example output:

```
==============================================================
  BUILD SUMMARY
==============================================================

  Build      : Apr  8 2026 17:08:51

  Flash (program memory)
    Used   :  131,877 bytes  ( 128.8 KiB)  12.6%
    Free   :  916,699 bytes  ( 895.2 KiB)
    [####--------------------------]

  RAM (data memory)
    Used   :   15,937 bytes  (  15.6 KiB)  6.1%
    Free   :  246,207 bytes  ( 240.4 KiB)
    [##----------------------------]

  Linker-Reserved Regions
    Heap   :   44,960 bytes  (  43.9 KiB)  (_min_heap_size)

  Interrupt Handlers
    Core IRQs        ( 7): BusFault, DebugMonitor, HardFault,
                           MemoryManagement, NonMaskableInt, Reset, UsageFault
    Peripheral IRQs  ( 5): DMAC_0, DMAC_1, SERCOM0_SPI, SERCOM1_USART, TC0_Timer

  Image HEX  : ...image\tcpip_iperf_lan865x_20260408_170851.hex
  Summary    : ...image\build_summary_20260408_170851.txt
```

Versioned artefacts are written to `out/tcpip_iperf_lan865x/image/` and tracked
by git (`!**/image/*.hex` negation rule in `.gitignore`) so released binaries
are available after `git clone` without a rebuild.

#### `setup_flasher.py`

Detects connected EDBG debuggers (USB VID `0x03EB`, serial number prefix `ATML`,
or manufacturer string containing `microchip` / `atmel`), lets the user assign
Board 1 (Grandmaster) and Board 2 (Follower), saves to `setup_flasher.config`
(JSON, git-ignored).

#### `flash.py` / `mdb_flash.py`

Flash `default.hex` to one or both boards via MPLAB MDB. Board serial numbers
and COM ports are read from `setup_flasher.config`.

```bat
python flash.py [--board1-only | --board2-only] [--hex <path>] [--swd-khz <n>]
```

#### `cmake/.../user.cmake`

Workaround for a CMake + xc32-gcc (MINGW) incompatibility: MINGW strips
backslashes from command-line arguments, so the linker receives
`-o out\default.elf` and creates a file literally named `outdefault.elf` — the
`bin2hex` step then fails with *No such file or directory*.

`user.cmake` redirects the linker output to an absolute forward-slash path
(unaffected by the MINGW bug) and adds a `POST_BUILD` copy step to the canonical
location `out/tcpip_iperf_lan865x/default.elf`. Loaded via the standard
`include(user.cmake OPTIONAL)` hook in `CMakeLists.txt` — no manual action
required.
