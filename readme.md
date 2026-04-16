# LAN8651 PTP IEEE 1588-2008 (PTPv2) based on AppNote AN1847 

This document describes all manual changes applied on top of the MCC-generated
Harmony 3 project for the ATSAME54P20A + LAN865x 10BASE-T1S demo.
The goal is to enable PTP (IEEE 1588) hardware timestamping with sub-microsecond
synchronisation accuracy over 10BASE-T1S.

### See Also

For the full PTP implementation reference — state machine pseudocode, IEEE 1588-2008
compliance verification, Mermaid flow diagrams, servo design, register reference,
and the automated regression test — see:

**[apps/tcpip\_iperf\_lan865x/firmware/tcpip\_iperf\_lan865x.X/README\_PTP.md](apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_PTP.md)**

---

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
  - [Pre-Built HEX — Skip Build & Flash Immediately](#pre-built-hex--skip-build--flash-immediately)
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
  - [5.6 IEEE 1588 Compliance + Convergence Regression](#56-ieee-1588-compliance--convergence-regression--ptp_trace_debug_testpy)
- [6. Hardware & Build Setup](#6-hardware--build-setup)
  - [6.1 Hardware Configuration](#61-hardware-configuration)
  - [6.2 Build Infrastructure](#62-build-infrastructure)
  - [6.3 Building with MPLAB X IDE](#63-building-with-mplab-x-ide)
  - [6.4 Building with Visual Studio Code](#64-building-with-visual-studio-code)
- [7. Reinforcement Learning — Coding with AI](#7-reinforcement-learning--coding-with-ai)
  - [7.1 Closed-Loop RL — The Core Idea](#71-closed-loop-rl--the-core-idea)
  - [7.2 Closing the Loop — What the Orchestrator Needs](#72-closing-the-loop--what-the-orchestrator-needs)
  - [7.3 Firmware Parameters Worth Tuning](#73-firmware-parameters-worth-tuning)
  - [7.4 Concrete Example — Tuning PTP_SYNC_INTERVAL](#74-concrete-example--tuning-ptp_sync_interval)
- [8. Python Dependency Management](#8-python-dependency-management)
- [9. PTP Implementation — In-Depth Analysis](#9-ptp-implementation--in-depth-analysis)
  - [9.1 Key Source Files](#91-key-source-files)
  - [9.2 Grandmaster Implementation](#92-grandmaster-implementation)
    - [Initialisation](#initialisation)
    - [Sending a Sync Message](#sending-a-sync-message)
    - [Grandmaster Data Structures](#grandmaster-data-structures)
  - [9.3 Follower Implementation](#93-follower-implementation)
    - [Initialisation](#initialisation-1)
    - [Sync Reception Path](#sync-reception-path)
    - [FollowUp Processing and Servo](#followup-processing-and-servo)
    - [Servo State Machine](#servo-state-machine)
    - [Register-Write State Machine](#register-write-state-machine)
  - [9.4 Synchronisation Flow — Complete Message Trace](#94-synchronisation-flow--complete-message-trace)
  - [9.5 Pseudo-Code of the Synchronisation Procedure](#95-pseudo-code-of-the-synchronisation-procedure)
  - [9.6 Key Data Structures](#96-key-data-structures)
  - [9.7 Data-Flow Summary](#97-data-flow-summary)

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
| **IEEE 1588 compliance fixes** | `ptp_gm_task.c`, `PTP_FOL_task.c` | 5 fixes: `twoStepFlag`, `tsmt` bytes, sequence-ID verification, TXMPATL pattern |
| MAC randomisation | `initialization.c` | Unique MAC addresses via hardware TRNG |
| LAN865x register CLI | `app.c` | `lan_read` / `lan_write` without a debugger |
| Build tooling | `build.bat`, `setup_compiler.py`, `setup_flasher.py`, `setup_debug.py`, `flash.py`, `build_summary.py`, `user.cmake` | Reproducible one-command builds; `setup_debug.py` fixes a DFP tool-pack bug that prevents VS Code debugging |

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

### Pre-Built HEX — Skip Build & Flash Immediately

A ready-to-use firmware image is already included in the repository:

```
apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/out/tcpip_iperf_lan865x/image/tcpip_iperf_lan865x_20260416_141706.hex
```

Flash this file onto **both** boards using **MPLAB X IPE** (Integrated Programming
Environment) as a standalone programmer — no build toolchain required:

1. Open **MPLAB X IPE** (Start → Microchip → MPLAB X IPE vX.XX).
2. **Device:** select `ATSAME54P20A`.
3. **Tool:** select the EDBG/debugger of the first board from the drop-down
   (connect the board via the USB debugger port beforehand).
4. **Hex File:** click *Browse* and navigate to the `.hex` path above.
5. Click **Connect**, then **Program**.  
   IPE erases, programs, and verifies in one step. Wait for *"Programming/Verify
   complete"*.
6. Repeat steps 3–5 for the second board (select its EDBG from the Tool drop-down).

> Both boards run identical firmware. The role (Grandmaster / Follower) is
> assigned at runtime via the `ptp_mode master` / `ptp_mode follower` CLI
> command — no separate images are needed.

If you want to modify the firmware, follow the full [Clone → Build → Flash](#clone)
flow below instead.

---

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
python setup_debug.py      # fix SAME54_DFP tool-pack bug (required for VS Code debugging)
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
| TXMPATL | 0x00040042 | *(not present)* | 0xF700 | EtherType low 0xF7 + PTP messageType 0x00 (Sync, transportSpecific=0) |
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

### 5.6 IEEE 1588 Compliance + Convergence Regression — `ptp_trace_debug_test.py`

End-to-end regression test that verifies full PTP convergence and the complete
Delay_Req/Delay_Resp exchange with hardware timestamp capture. Unlike §5.2–5.5
(which measure offset quality over time), this test focuses on **protocol
correctness** and is the primary verification tool after any change to the PTP
frame-building or timestamp-capture code.

#### Key design differences from the earlier test scripts

- `ptp_trace on` is activated **immediately** after PTP start — before the first
  Sync frame is even sent — so no trace event is ever missed.
- Both serial ports are read by permanent **background threads** from the start;
  there is no read race between convergence polling and trace capture.
- The test does **not abort** if FINE is not reached — trace analysis and
  assertions run regardless, followed by a detailed `STUCK-STATE DIAGNOSE`
  section.
- Convergence timeout is 60 s (vs. 30 s in earlier scripts).

#### Usage

```bat
python ptp_trace_debug_test.py --gm-port COM10 --fol-port COM8
python ptp_trace_debug_test.py --gm-port COM10 --fol-port COM8 ^
    --convergence-timeout 90 --trace-time 20
```

#### Test Phases

| Step | Action | Pass condition |
|------|--------|----------------|
| 0 | Reset both boards, wait 8 s | Boot completes |
| 1 | `setip eth0` on GM + FOL | IP set confirmed |
| 2 | `ping` bidirectional | `Ping: done.` on both sides |
| 3 | `ptp_mode follower` → `ptp_trace on` (FOL, immediately) | Trace enabled before first Sync |
| 3 | `ptp_mode master` → `ptp_trace on` (GM, immediately) | Trace enabled |
| 3 | Poll FOL for `PTP FINE` (≤ 60 s) | FINE reached; milestones logged |
| 4 | Collect 10 s additional trace | All Delay exchanges captured |
| 5 | `ptp_trace off`, `ptp_mode off` | Clean shutdown |

#### Assertions A–I

| Assertion | What it verifies |
|-----------|------------------|
| **A** | FOL sent at least one `DELAY_REQ_SENT` |
| **B** | GM received at least one `GM_DELAY_REQ_RECEIVED` |
| **C** | GM sent at least one `GM_DELAY_RESP_SENT` |
| **D** | FOL received at least one `DELAY_RESP_RECEIVED` |
| **E** | At least one `DELAY_CALC` shows non-zero, plausible delay |
| **F** | `GM_DELAY_RESP_SKIPPED_TX_BUSY` count ≤ limit (default 0) |
| **G** | Last valid delay in range `0 < delay < 10 ms` |
| **H** | At least one `DELAY_CALC` shows `hw=1` (t3 from LAN865x TTSCA) |
| **I** | Zero `DELAY_RESP_WRONG_SEQ` events (IEEE 1588 §11.3.3 seq-ID check) |

#### Confirmed run (2026-04-16 — after all 5 compliance fixes)

```
[PASS] Step 3: PTP Start + ptp_trace ON (immediately) + Convergence  — FINE@2.7s
[PASS] A: FOL DELAY_REQ_SENT                  count=45
[PASS] B: GM GM_DELAY_REQ_RECEIVED            count=45
[PASS] C: GM GM_DELAY_RESP_SENT               count=45
[PASS] D: FOL DELAY_RESP_RECEIVED             received=45
[PASS] E: FOL DELAY_CALC non-zero delay       last_valid_delay=3788 ns
[PASS] F: GM TX-busy skips <= limit           skips=0 limit=0
[PASS] G: Delay in plausible range            3788 ns
[PASS] H: FOL t3 HW-Capture (hw=1)           hw_captures=45/45
[PASS] I: No DELAY_RESP_WRONG_SEQ             no WRONG_SEQ — seq-ID check correct
OVERALL: PASS
```

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

### 6.3 Building with MPLAB X IDE

The project can also be opened and built directly in **MPLAB X IDE** as an
alternative to `build.bat`. The `nbproject/` directory contains all necessary
project metadata; MPLAB X generates the required `Makefile-impl.mk` and
`Makefile-variables.mk` automatically when the project is opened.

#### Steps

1. Open MPLAB X IDE
2. **File → Open Project** → select `tcpip_iperf_lan865x.X/`
3. MPLAB X detects the project type (`com.microchip.mplab.nbide.embedded.makeproject`)
   and generates the missing Makefile fragments automatically
4. Select the `default` configuration
5. **Production → Build Main Project** (or press F11)

#### Notes

- The XC32 toolchain version configured in MPLAB X must match the version used
  by `setup_compiler.py` / `build.bat` to ensure identical compiler flags
- All PTP source files (`ptp_clock.c`, `ptp_gm_task.c`, `PTP_FOL_task.c`,
  `filters.c`) are registered in `nbproject/configurations.xml` and will appear
  in the MPLAB X project tree automatically
- MPLAB X uses its own intermediate build directory (inside `build/`) — this is
  separate from the CMake build tree under `C:\...\temp\`
- For flashing, `flash.py` (MPLAB MDB) remains the recommended method; MPLAB X
  can also program via **Run → Run Main Project** if a debugger is attached

### 6.4 Building with Visual Studio Code

The project can be opened and built in **Visual Studio Code** using the
pre-configured CMake integration. VS Code acts as the editor and build frontend;
the actual compiler is still XC32 via CMake + Ninja.

#### Prerequisites

- [CMake Tools extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode.cmake-tools)
- [C/C++ extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode.cpptools) (for IntelliSense)
- `setup_compiler.py` run at least once (creates `setup_compiler.config`)

#### Steps

1. Open VS Code in the project working directory:
   ```bat
   code .
   ```
   (from `tcpip_iperf_lan865x.X\`)
2. VS Code detects the `cmake/` folder and the CMake Tools extension activates
   automatically
3. Select the **default** configure preset when prompted
4. **Build**: press `Ctrl+Shift+B` or use the CMake Tools status bar button
   (`▶ Build`)

#### Notes

- Build output (`.o`, `.elf`, `.hex`) goes to the same location as `build.bat`:
  `C:\...\temp\tcpip_iperf_lan865x\default\`
- `build_summary.py` is **not** called automatically from VS Code — run
  `python build_summary.py` manually after a build if needed
- IntelliSense uses `compile_commands.json` generated by CMake in the temp
  build directory; the C/C++ extension picks it up automatically once CMake has
  configured at least once
- For flashing, run `python flash.py` from the integrated terminal

#### Debugging in VS Code

The repository includes a pre-configured `launch.json` with two debug
configurations:

| Config | Description |
|--------|-------------|
| **Launch tcpip_iperf_lan865x: default** | Builds, programs, and starts the debugger in one step |
| **Attach (after flash.py)** | Attaches to an already-flashed device (workaround for tool pack bugs) |

**One-time prerequisite:** run `setup_debug.py` once per machine after clone:

```bat
python setup_debug.py
```

This patches `%USERPROFILE%\.mchp_packs\Microchip\SAME54_DFP\<ver>\scripts\dap_cortex-m4.py`
to add a missing global variable `is_debug_build` that tool pack version 1.6.762
omits. Without this patch, every debug session fails at the *Erasing...* step with:

```
NameError: global name 'is_debug_build' is not defined
Failed to start session: Debugger::program : Failed to program the target device
```

The fix is idempotent — running `setup_debug.py` multiple times is safe.

---

## 7. Reinforcement Learning — Coding with AI

This chapter covers two related but distinct concepts:

- **Coding with AI:** GitHub Copilot / Claude write the orchestrator code — this happens *during development* and is already standard practice in this project.
- **RL in the loop:** An algorithm (ranging from a simple for-loop up to an LLM) selects the next firmware parameters *at runtime*, builds the firmware, flashes the board, and interprets the measurement as a reward signal.

The core argument: the **hardware-in-the-loop environment** is already fully present in this project. Only the orchestrator that drives it is missing.

### 7.1 Closed-Loop RL — The Core Idea

Reinforcement Learning (RL) is a framework in which an **Agent** repeatedly takes **Actions**, observes the resulting **State** of an **Environment**, and receives a scalar **Reward** signal. The agent's goal is to maximise cumulative reward over time.

Applied to embedded firmware development the loop looks like this:

```
 ┌─────────────────────────────────────────────────────────────┐
 │                    AGENT  (see §7.4 for three levels)       │
 │                                                             │
 │  Level 1 — for-loop / grid search  (no AI model)           │
 │  Level 2 — Bayesian optimiser      (statistical model)     │
 │  Level 3 — LLM as policy           (real AI API call)      │
 └───────────────────┬──────────────────────▲──────────────────┘
                     │ Action               │ Reward + next State
                     │ (new #define values) │ (stdev, fine_s, pass)
                     ▼                      │
 ┌─────────────────────────────────────────────────────────┐
 │                    ENVIRONMENT                          │
 │                                                         │
 │  1. render_params()  — writes #define values to params.h│
 │  2. build.bat        — compiles → hex file              │
 │  3. flash.py         — programs the board               │
 │  4. ptp_reproducibility_test.py  — measures, emits JSON │
 │       • stdev_ns   (lower = better)                     │
 │       • slope_ppm  (closer to 0 = better)               │
 │       • fine_s     (lower = better)                     │
 │       • pass       (boolean)                            │
 └─────────────────────────────────────────────────────────┘
```

**Key point:** The Environment is identical across all three levels. Only the Agent changes.

The **environment already exists** in this project:

| Environment step | Tool already present |
|------------------|----------------------|
| Edit firmware    | `PTP_FOL_task.h`, `filters.h` (plain `#define` values) |
| Build            | `build.bat` (one-command, reproducible) |
| Flash            | `flash.py` (one-command, automatic port detection) |
| Measure          | `ptp_reproducibility_test.py` (structured JSON output) |

Only the **orchestrator layer** is missing — a script that strings these four steps together and drives a search algorithm or AI model over the parameter space.

### 7.2 Closing the Loop — What the Orchestrator Needs

Regardless of the chosen agent level, the orchestrator must:

1. **Parameterise the firmware** — substitute numeric `#define` values before each build.  
   Safest approach: a `params_template.h` with `{{PLACEHOLDER}}` tokens; the orchestrator renders it into the real header before calling `build.bat`.

2. **Build and flash without operator interaction** — both tools already support this; the orchestrator calls them as subprocesses and checks the exit code.

3. **Parse the reward signal** — `ptp_reproducibility_test.py` has a `--json` output mode; the orchestrator reads the resulting file.

4. **Implement a search or learning policy** — Level 1: for-loop. Level 2: Bayesian optimiser. Level 3: LLM (see §7.4).

5. **Gate dangerous actions** — optionally require a `y/n` confirmation before flashing if the proposed parameter value is outside a safe operating envelope (e.g. a TI value that would exceed hardware limits).

Minimal orchestrator skeleton (shared by all three levels):

```python
import subprocess, json, pathlib

PARAMS_HEADER = pathlib.Path("src/params.h")  # re-created before every build

def render_params(params: dict) -> None:
    """Write a params.h containing the desired #define values."""
    lines = [f"#define {k} {v}" for k, v in params.items()]
    PARAMS_HEADER.write_text("\n".join(lines) + "\n")

def build() -> bool:
    return subprocess.run(["build.bat"], check=False).returncode == 0

def flash() -> bool:
    return subprocess.run(["python", "flash.py"], check=False).returncode == 0

def measure(port_gm: str, port_fol: str) -> dict:
    result = subprocess.run(
        ["python", "ptp_reproducibility_test.py",
         "--gm", port_gm, "--fol", port_fol, "--json", "result.json"],
        check=False,
    )
    if result.returncode != 0:
        return {"pass": False, "stdev_ns": 1e9, "slope_ppm": 1e6, "fine_s": 1e6}
    return json.loads(pathlib.Path("result.json").read_text())

def reward(metrics: dict) -> float:
    """Reward function: lower stdev and faster FINE lock = higher reward."""
    if not metrics["pass"]:
        return -1000.0
    return -metrics["stdev_ns"] - 0.1 * metrics["fine_s"]

def run_episode(params: dict, port_gm: str, port_fol: str) -> float:
    """One full cycle: parameters → build → flash → measure → reward."""
    render_params(params)
    if not build() or not flash():
        return -1000.0
    return reward(measure(port_gm, port_fol))
```

### 7.3 Firmware Parameters Worth Tuning

The servo has several numeric constants that are good candidates for automated optimisation.  All live in plain C headers — no MCC regeneration required.

| Parameter | Location | Current value | Effect |
|-----------|----------|---------------|--------|
| `PTP_SYNC_INTERVAL` | `PTP_FOL_task.h:80` | 500 ms | Sync message period; lower = faster reaction, higher = smoother frequency estimate |
| `FIR_FILER_SIZE` | `filters.h:40` | 16 taps | Rate-ratio FIR smoothing; larger = less noise, more lag |
| `FIR_FILER_SIZE_FINE` | `filters.h:41` | 3 taps | Offset FIR in COARSE/FINE state; trades jitter vs. responsiveness |
| `HARDSYNC_COARSE_THRESHOLD` | `PTP_FOL_task.h:86` | 300 ns | Offset boundary HARDSYNC→COARSE; too small = coarse never reached; too large = noise triggers coarse |
| `HARDSYNC_FINE_THRESHOLD` | `PTP_FOL_task.h:87` | 150 ns | Offset boundary COARSE→FINE; governs when TISUBN fine-tuning activates |
| `MATCHFREQ_RESET_THRESHOLD` | `PTP_FOL_task.h:83` | 100 000 000 ns | Safety guard: offset above this resets to MATCHFREQ |

Parameters interact non-linearly: a smaller `HARDSYNC_FINE_THRESHOLD` makes FINE reachable faster but requires a correspondingly small `FIR_FILER_SIZE_FINE` to avoid oscillation. This coupling is exactly why manual hand-tuning is tedious and RL is attractive.

### 7.4 Concrete Example — Tuning `PTP_SYNC_INTERVAL`

`PTP_SYNC_INTERVAL` is a 1-D optimisation problem — a good first test for the infrastructure. The three levels below show how the agent becomes progressively smarter while the Environment remains unchanged.

**Reward function** (identical for all three levels):

$$r = -\sigma_{\text{ns}} \;-\; 0.1 \cdot t_{\text{FINE}}$$

$\sigma$ = standard deviation of the offset in ns, $t_{\text{FINE}}$ = seconds to reach FINE lock

---

#### Level 1 — Grid Search (no AI model, just a for-loop)

> **No AI call.** The agent is a for-loop; `run_episode` is the only thing invoked.

```python
# grid_search_interval.py
import argparse, json, pathlib
from orchestrator import run_episode

CANDIDATES = [62, 125, 250, 500, 1000]  # milliseconds

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gm",  required=True)
    ap.add_argument("--fol", required=True)
    args = ap.parse_args()

    results = []
    for interval in CANDIDATES:                          # ← this IS the entire "agent"
        params = {"PTP_SYNC_INTERVAL": f"{interval}u"}
        r = run_episode(params, args.gm, args.fol)      # build → flash → measure
        print(f"interval={interval:5d} ms  reward={r:10.1f}")
        results.append({"interval_ms": interval, "reward": r})

    best = max(results, key=lambda x: x["reward"])
    print(f"\nBest: {best['interval_ms']} ms  (reward {best['reward']:.1f})")
    pathlib.Path("grid_results.json").write_text(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
```

Runtime: 5 × (≈30 s build/flash + ≈30 s test) ≈ **5 minutes** — a full characterisation in the time it takes to make a coffee.

Expected output (values illustrative):

```
interval=   62 ms  reward=   -87.3
interval=  125 ms  reward=   -45.1
interval=  250 ms  reward=   -38.9
interval=  500 ms  reward=   -42.2
interval= 1000 ms  reward=   -61.7

Best: 250 ms  (reward -38.9)
```

---

#### Level 2 — Bayesian Optimisation (statistical model, no LLM)

> **No LLM call.** A Gaussian process model (`skopt`) learns a surrogate function over the parameter space and selects the next candidate so as to maximise information gain. This significantly reduces the number of required build-flash cycles — important when the search space is larger (multiple parameters simultaneously).

```python
# bayes_search_interval.py
import argparse
from skopt import gp_minimize
from skopt.space import Integer
from orchestrator import run_episode

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gm",  required=True)
    ap.add_argument("--fol", required=True)
    ap.add_argument("--calls", type=int, default=12)   # total number of builds
    args = ap.parse_args()

    def objective(params):
        interval = params[0]
        r = run_episode({"PTP_SYNC_INTERVAL": f"{interval}u"}, args.gm, args.fol)
        print(f"interval={interval:5d} ms  reward={r:10.1f}")
        return -r   # skopt minimises → negate

    result = gp_minimize(
        objective,
        dimensions=[Integer(62, 1000, name="interval_ms")],
        n_calls=args.calls,    # ← Bayesian model picks every next point
        random_state=42,
    )
    print(f"\nBest interval: {result.x[0]} ms  (reward {-result.fun:.1f})")

if __name__ == "__main__":
    main()
```

With `--calls 12`, Bayesian optimisation typically matches the result of a full grid search over 20–30 points. The benefit grows significantly when tuning multiple parameters simultaneously (§7.3).

---

#### Level 3 — LLM as Policy (real AI API call)

> **This is where AI is called.** The LLM sees the accumulated measurement history and proposes the next candidate — as a chat prompt. It acts as an "intelligent" agent that can also provide reasoning.

```python
# llm_search_interval.py
import argparse, json
import openai
from orchestrator import run_episode

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gm",  required=True)
    ap.add_argument("--fol", required=True)
    ap.add_argument("--rounds", type=int, default=8)
    args = ap.parse_args()

    client = openai.OpenAI()   # API key from environment variable OPENAI_API_KEY
    history = []

    for round_nr in range(args.rounds):
        # ── Step 1: ask the LLM which value to test next ─────────────────────
        system_prompt = (
            "You are an optimisation assistant for a PTP timestamp servo. "
            "Your task: choose the next value for PTP_SYNC_INTERVAL (ms) "
            "to maximise the reward r = -stdev_ns - 0.1*fine_s. "
            "Allowed values: 62..2000 ms (integer). "
            "Reply with a single integer only, no explanation."
        )
        user_msg = (
            f"Results so far: {json.dumps(history, indent=2)}\n\n"
            f"Round {round_nr + 1}/{args.rounds}: which value should I test next?"
        )
        response = client.chat.completions.create(   # ← AI API call
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=10,
            temperature=0.2,
        )
        interval = int(response.choices[0].message.content.strip())
        print(f"[LLM suggests] interval={interval} ms")

        # ── Step 2: build, flash, measure ────────────────────────────────────
        r = run_episode({"PTP_SYNC_INTERVAL": f"{interval}u"}, args.gm, args.fol)
        history.append({"round": round_nr + 1, "interval_ms": interval, "reward": r})
        print(f"  → reward={r:.1f}")

    best = max(history, key=lambda x: x["reward"])
    print(f"\nBest result: {best['interval_ms']} ms  (reward {best['reward']:.1f})")

if __name__ == "__main__":
    main()
```

Example console output:

```
[LLM suggests] interval=250 ms
  → reward=-41.3
[LLM suggests] interval=200 ms
  → reward=-38.1
[LLM suggests] interval=175 ms
  → reward=-36.8
[LLM suggests] interval=150 ms
  → reward=-39.7
...
Best result: 175 ms  (reward -36.8)
```

After each measurement the LLM sees the complete `history` JSON and can explain its next suggestion — e.g. "175 ms was better than 200 ms, so I'll try 160 ms next". This explainability distinguishes Level 3 from Level 2.

---

#### Summary of the three levels

| Level | Agent | AI model | Builds for a good result | Setup required |
|-------|-------|----------|--------------------------|----------------|
| 1 — Grid Search | for-loop | none | N (all candidates) | no external library |
| 2 — Bayesian Opt. | `skopt` GP model | statistical | ~12–15 | `pip install scikit-optimize` |
| 3 — LLM Policy | GPT-4o / Copilot | LLM (API call) | ~8–12 | OpenAI API key |

> **Note on "Coding with AI":** The orchestrator scripts, the `run_episode` skeleton, and this entire chapter were written with the help of GitHub Copilot (Claude Sonnet). The workflow — Copilot proposes code → firmware is built and measured → results feed back into the next prompt — is itself the closed loop described in §7.1, applied at the level of the development process rather than inside the firmware.

---

## 8. Python Dependency Management

Several Python scripts in this repository require third-party packages (e.g. `pyserial`).
Two helper files automate the detection and installation of these packages.

### Files

| File | Purpose |
|------|---------|
| `analyze_dependencies.py` | Scans all `.py` files, detects third-party imports, writes `requirements.txt` |
| `install_dependencies.bat` | Windows batch script that installs every package listed in `requirements.txt` |
| `requirements.txt` | Auto-generated list of pip packages required by this repository |

### Workflow

**Step 1 — Analyze (run once, or after adding new Python scripts)**

```
python analyze_dependencies.py
```

The script walks the entire repository, parses every `.py` file with Python's `ast`
module, filters out standard-library modules and local files, and writes a fresh
`requirements.txt`.

**Step 2 — Install (Windows)**

Double-click `install_dependencies.bat` or run it from a command prompt:

```
install_dependencies.bat
```

The batch script:
1. Checks that Python is installed and available on `PATH`
2. Checks that `pip` is available
3. Upgrades pip to the latest version
4. Runs `pip install -r requirements.txt`
5. Shows clear status messages and error hints if anything goes wrong

### Prerequisites

- Python 3.8 or newer — https://www.python.org/downloads/
  *(check "Add Python to PATH" during installation)*
- An internet connection for the initial package download

---

## 9. PTP Implementation — In-Depth Analysis

This chapter provides a detailed, file-level analysis of the PTP (IEEE 1588)
implementation: Grandmaster (GM) and Follower (FOL), the complete synchronisation
message flow, and annotated pseudo-code.

### 9.1 Key Source Files

| File | Role |
|------|------|
| `src/ptp_gm_task.c` | GM state machine — sends Sync/FollowUp, reads TX timestamps |
| `src/ptp_gm_task.h` | GM public API, register macros, timing constants |
| `src/PTP_FOL_task.c` | FOL message processing, clock servo algorithm |
| `src/PTP_FOL_task.h` | FOL public API, all PTP wire-format structs |
| `src/ptp_clock.c/.h` | Software PTP wallclock (ns resolution, TC0 interpolation) |
| `src/ptp_ts_ipc.h` | IPC structs for HW RX-timestamps between driver and app |
| `src/app.c` | Application state machine — service dispatcher for GM and FOL |
| `src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c` | LAN865x driver, `TC6_CB_OnRxEthernetPacket()` |

---

### 9.2 Grandmaster Implementation

#### Initialisation

`PTP_GM_Init()` is called when the user issues `ptp_mode master`
(`app.c:167`, `ptp_gm_task.c:349`).

```
PTP_GM_Init()
  → read board MAC via TCPIP_STACK_NetAddressMac()
  → adopt calibrated TI/TISUBN values from FOL servo (if available)
  → enter state GM_STATE_RMW_CONFIG0_READ
```

**Pre-init RMW sequence** (mirrors `TC6_ptp_master_init` steps 8 and 9):

1. **OA_CONFIG0 RMW** (`0x00000004`): set bits 7 and 6 (FTSE) — enables hardware
   timestamping (`ptp_gm_task.c:729–777`, mask `0xC0`).
2. **PADCTRL RMW** (`0x000A0088`): set bit 8, clear bit 9
   (`ptp_gm_task.c:779–829`).

**Normal init sequence** — 9 sequential register writes
(`ptp_gm_task.c:157–173`):

| Register | Address | Value | Purpose |
|----------|---------|-------|---------|
| `GM_TXMCTL` | `0x00040040` | `0x0000` | Reset TX-Match detector |
| `GM_TXMLOC` | `0x00040045` | `30` | Match position inside frame |
| `GM_TXMPATH` | `0x00040041` | `0x88` | Pattern: EtherType high byte |
| `GM_TXMPATL` | `0x00040042` | `0xF710` | Pattern: EtherType low byte + offset |
| `GM_TXMMSKH/L` | `0x00040043/44` | `0x00` | Pattern masks |
| `MAC_TISUBN` | `0x0001006F` | calibrated sub-increment | Sub-nanosecond clock increment |
| `MAC_TI` | `0x00010077` | calibrated TI (default: 40) | Nanosecond clock increment |
| `PPSCTL` | `0x000A0239` | `0x7D` | Enable 1PPS output |

#### Sending a Sync Message

`PTP_GM_Service()` is called every 1 ms from `APP_STATE_IDLE` (`app.c:489`).
The full per-cycle sequence:

```
GM_STATE_WAIT_PERIOD  ── every 125 ms ──▶ GM_STATE_SEND_SYNC
  build_sync()
  WriteRegister(GM_TXMCTL, 0x0002)         // TXME=1: arm TX-Match detector
  wait write callback
  SendRawEthFrame(sync_buf, tsc=1)         // tsc=1 → capture TX timestamp
  wait TX-done callback
  → GM_STATE_READ_STATUS0
  DRV_LAN865X_GetAndClearTsCapture()       // check TTSCAA/B/C
  ReadRegister(GM_OA_TTSCAH)               // t1 seconds
  ReadRegister(GM_OA_TTSCAL)               // t1 nanoseconds
  WriteRegister(GM_OA_STATUS0, status0)    // W1C: clear capture flags
  → GM_STATE_SEND_FOLLOWUP
  build_followup(t1_sec, t1_nsec + 7650)  // apply PTP_GM_STATIC_OFFSET
  SendRawEthFrame(followup_buf, tsc=0)
  wait TX-done callback
  PTP_CLOCK_Update(t1 + GM_ANCHOR_OFFSET_NS, SYS_TIME_Counter64Get())
  gm_seq_id++
  → GM_STATE_WAIT_PERIOD
```

**Sync frame layout** (`build_sync`, `ptp_gm_task.c:263–289`):
- 14-byte Ethernet header — EtherType `0x88F7`, destination broadcast or
  PTP L2 multicast `01:80:C2:00:00:0E`
- 44-byte `syncMsg_t` — `tsmt=0x10`, `version=0x02`, `messageLength=0x002C`,
  `sequenceID`, `flags[0]=0x02, flags[1]=0x08`
- `originTimestamp` is zero — the precise value is carried by the FollowUp

**FollowUp frame layout** (`build_followup`, `ptp_gm_task.c:291–319`):
- `preciseOriginTimestamp` = TTSCA + `PTP_GM_STATIC_OFFSET` (7 650 ns TX-path
  compensation)
- Organisation-Specific TLV type `0x0003`, OUI `00:80:C2`, sub-type `01`
  (cumulative rate-ratio)

#### Grandmaster Data Structures

```c
// ptp_gm_task.c:70–97 — runtime state
gmState_t gm_state;            // current state-machine state
uint32_t  gm_ts_sec/nsec;      // TX timestamp from TTSCA registers
uint16_t  gm_seq_id;           // PTP sequence ID
uint32_t  gm_sync_interval_ms; // default 125 ms

// frame buffers
uint8_t gm_sync_buf[60];       // 14 (ETH) + 44 (syncMsg_t) + 2 pad
uint8_t gm_followup_buf[90];   // 14 (ETH) + 76 (followUpMsg_t)
```

---

### 9.3 Follower Implementation

#### Initialisation

`PTP_FOL_Init()` is called from `APP_STATE_IDLE` on first entry (`app.c:412`)
and again from `resetSlaveNode()` on every follower reset
(`PTP_FOL_task.c:638`):

```
PTP_FOL_Init()
  → WriteRegister(PPSCTL, 0x02)              // stop PPS output
  → WriteRegister(SEVINTEN, PPSDONE_Msk)     // enable PPS-done interrupt
  → memset(TS_SYNC, 0)                       // clear timestamp store
  → initialise FIR/IIR filter buffers
```

Follower operation starts when `PTP_FOL_SetMode(PTP_SLAVE)` is called
(e.g. `ptp_mode follower` CLI command, `app.c:170`), which in turn calls
`resetSlaveNode()` (`PTP_FOL_task.c:663–668`).

#### Sync Reception Path

Hardware data flow:

```
LAN865x SPI footer carries the RTSA timestamp
  → TC6_CB_OnRxEthernetPacket()   [drv_lan865x_api.c:1372]
      checks EtherType == 0x88F7
      copies frame → g_ptp_raw_rx.data
      g_ptp_raw_rx.rxTimestamp = RTSA  (sec[63:32] | ns[31:0])
      if SYNC (rxTimestamp != NULL):
          g_ptp_raw_rx.sysTickAtRx = SYS_TIME_Counter64Get()
      g_ptp_raw_rx.pending = true

app.c:505  (APP_STATE_IDLE polling loop)
  → PTP_FOL_OnFrame(data, length, rxTimestamp)

PTP_FOL_OnFrame()   [PTP_FOL_task.c:688]
  → extracts sec/nsec from rxTimestamp
  → handlePtp(pData, len, sec, nsec)

handlePtp()         [PTP_FOL_task.c:614]
  → parses messageType = ptpHeader.tsmt & 0x0F
  → MSG_SYNC      → processSync()  + stores TS_SYNC.receipt = {sec, nsec}
  → MSG_FOLLOW_UP → processFollowUp()  (servo core)
```

`processSync()` (`PTP_FOL_task.c:369`) validates the sequence ID and stores t2
(the RTSA hardware receive timestamp) in `TS_SYNC.receipt`.

#### FollowUp Processing and Servo

`processFollowUp()` (`PTP_FOL_task.c:392`) is the clock-servo core:

```c
// t1 — precise GM send time from FollowUp frame
t1 = preciseOriginTimestamp (byte-swapped) + correctionField / 65536

// t2 — local receive time from RTSA (stored during processSync)
t2 = TS_SYNC.receipt

// update the software clock anchor
PTP_CLOCK_Update(t2, g_ptp_raw_rx.sysTickAtRx)

// frequency error measurement
diffLocal  = t2_now - t2_prev   // local interval between two SYNCs
diffRemote = t1_now - t1_prev   // GM interval between two SYNCs
rateRatio  = diffRemote / diffLocal
rateRatioFIR = FIR_filter(rateRatio)   // averaged over 16 samples

// offset calculation
offset = t2 - t1   // positive: follower is ahead; negative: follower lags
```

> **Note on delay measurement:** no Delay_Req / Delay_Resp exchange is
> implemented.  The system uses one-way hardware timestamping only.
> Propagation delay is compensated by the static constant
> `PTP_GM_STATIC_OFFSET = 7 650 ns` added to the FollowUp timestamp on the
> GM side (`ptp_gm_task.h:61`).

#### Servo State Machine

(`PTP_FOL_task.c:485–572`)

| State | Entry condition | Action |
|-------|----------------|--------|
| `UNINIT` | initial state | accumulate 16 rate-ratio samples |
| `UNINIT` → `MATCHFREQ` | after 16 samples | compute TI/TISUBN → `FOL_ACTION_SET_CLOCK_INC` |
| `MATCHFREQ` | `\|offset\| > 100 000 000 ns` | `hardResync = 1` |
| `MATCHFREQ` → `HARDSYNC` | `\|offset\| ≤ 100 000 000 ns` | — |
| `HARDSYNC` | `\|offset\| > 0x3FFF FFFF` | → `UNINIT` (full reset) |
| `HARDSYNC` | `\|offset\| > 16 777 215 ns` | capped direct adjust → `FOL_ACTION_ADJUST_OFFSET` |
| `HARDSYNC` → `COARSE` | `\|offset\| > 300 ns` | FIR-coarse filter → `FOL_ACTION_ADJUST_OFFSET` |
| `COARSE/FINE` | `\|offset\| > 150 ns` | FIR-fine filter → `FOL_ACTION_ADJUST_OFFSET` → `FINE` |

When `hardResync == 1` the follower writes `t1` directly into `MAC_TSL` /
`MAC_TN` to hard-set the LAN865x hardware clock
(`FOL_ACTION_HARD_SYNC`, `PTP_FOL_task.c:421–426`).

#### Register-Write State Machine

`PTP_FOL_Service()` is called every 1 ms (`app.c:496`).  It serialises all
LAN865x SPI writes through the `fol_pending_action` flag
(`PTP_FOL_task.c:143–291`):

| Action | Registers written | Effect |
|--------|-----------------|--------|
| `FOL_ACTION_HARD_SYNC` | `MAC_TSL`, `MAC_TN` | Hard-set LAN865x clock to t1 |
| `FOL_ACTION_SET_CLOCK_INC` | `MAC_TISUBN`, `MAC_TI` | Apply crystal-drift correction |
| `FOL_ACTION_ADJUST_OFFSET` | `MAC_TA` | Fine-adjust LAN865x clock (signed offset) |
| `FOL_ACTION_ENABLE_PPS` | `PPSCTL` | Enable 1PPS output after first lock |

---

### 9.4 Synchronisation Flow — Complete Message Trace

**Timestamp notation**
- **t1** — precise Sync send time on the GM LAN865x (from TTSCA registers)
- **t2** — Sync receive time on the FOL LAN865x (from RTSA in SPI footer)

```
GRANDMASTER                                  FOLLOWER
(ptp_gm_task.c)                              (PTP_FOL_task.c / drv_lan865x_api.c)
│                                                        │
│   ── every 125 ms ──                                   │
│                                                        │
│  1. build_sync()                                       │
│     WriteRegister(GM_TXMCTL, 0x0002)                  │
│                                                        │
│  2. SendRawEthFrame(sync_buf, tsc=1)                   │
│     ──── SYNC (EtherType 0x88F7) ────────────────────▶ │
│     LAN865x: captures t1 via TX-Match detector         │
│                                                        │  LAN865x: captures t2 via RTSA
│                                                        │  TC6_CB_OnRxEthernetPacket:
│                                                        │    g_ptp_raw_rx.rxTimestamp = t2
│                                                        │    g_ptp_raw_rx.sysTickAtRx = TC0 tick
│                                                        │    g_ptp_raw_rx.pending = true
│                                                        │
│  3. Read OA_STATUS0  → check TTSCAA                   │
│  4. Read TTSCA_H  (seconds of t1)                      │
│  5. Read TTSCA_L  (nanoseconds of t1)                  │
│  6. Write OA_STATUS0  (W1C clear)                      │
│                                                        │
│  7. build_followup(t1 + 7 650 ns)                      │
│     SendRawEthFrame(followup_buf, tsc=0)               │
│     ──── FOLLOW_UP ───────────────────────────────────▶ │
│                                                        │  app.c: g_ptp_raw_rx.pending:
│  8. PTP_CLOCK_Update(t1 + anchor_offset, TC0 tick)     │    PTP_FOL_OnFrame()
│     (update GM software clock)                         │    handlePtp → processSync:
│                                                        │      TS_SYNC.receipt = t2
│                                                        │    handlePtp → processFollowUp:
│                                                        │      t1 from preciseOriginTimestamp
│                                                        │      offset = t2 − t1
│                                                        │      rateRatio = diffRemote/diffLocal
│                                                        │      PTP_CLOCK_Update(t2, sysTickAtRx)
│                                                        │      → servo state machine
│                                                        │      → fol_pending_action set
│                                                        │
│                                                        │  PTP_FOL_Service() (1 ms tick):
│                                                        │    WriteRegister(MAC_TSL / MAC_TN)
│                                                        │    or WriteRegister(MAC_TI / TISUBN)
│                                                        │    or WriteRegister(MAC_TA)
│                                                        │
│   ── next 125 ms period ──                             │
```

---

### 9.5 Pseudo-Code of the Synchronisation Procedure

```
//=======================================================================
// PTP GRANDMASTER — main service loop (called every 1 ms)
//=======================================================================

PROCEDURE PTP_GM_Service():
    every 125 ms:
        // Step 1 — arm TX-Match detector
        WriteRegister(GM_TXMCTL, 0x0002)   // TXME = 1
        wait write callback

        // Step 2 — send SYNC with timestamp-capture request
        syncFrame = buildSyncFrame(sequenceID = gm_seq_id)
        SendRawEthFrame(syncFrame, tsc = 1)
        wait TX callback  // frame is now on the wire

        // Step 3 — read TX timestamp from LAN865x hardware
        wait STATUS0.TTSCAA == 1           // capture slot available
        t1_sec  = ReadRegister(GM_OA_TTSCAH)
        t1_nsec = ReadRegister(GM_OA_TTSCAL)
        WriteRegister(GM_OA_STATUS0, status0)  // W1C: clear capture flags

        // Step 4 — apply static TX-path compensation
        t1_nsec += PTP_GM_STATIC_OFFSET    // 7 650 ns
        if t1_nsec >= 1_000_000_000:
            t1_sec  += 1
            t1_nsec -= 1_000_000_000

        // Step 5 — send FOLLOW_UP carrying t1
        followUpFrame = buildFollowUpFrame(
            sequenceID             = gm_seq_id,
            preciseOriginTimestamp = { t1_sec, t1_nsec }
        )
        SendRawEthFrame(followUpFrame, tsc = 0)
        wait TX callback

        // Step 6 — update software clock anchor
        wc_ns = t1_sec * 1_000_000_000 + t1_nsec + GM_ANCHOR_OFFSET_NS
        PTP_CLOCK_Update(wc_ns, SYS_TIME_Counter64Get())

        gm_seq_id++


//=======================================================================
// FOLLOWER — driver callback (interrupt context)
//=======================================================================

CALLBACK TC6_CB_OnRxEthernetPacket(frame, len, rxTimestamp):
    if EtherType == 0x88F7:                    // PTP frame
        g_ptp_raw_rx.data        = frame
        g_ptp_raw_rx.rxTimestamp = rxTimestamp  // t2: RTSA (sec[63:32] | ns[31:0])
        if rxTimestamp != NULL:                 // SYNC only — FollowUp has no TS
            g_ptp_raw_rx.sysTickAtRx = SYS_TIME_Counter64Get()
        g_ptp_raw_rx.pending = true


//=======================================================================
// FOLLOWER — application task (polling, 1 ms)
//=======================================================================

PROCEDURE APP_STATE_IDLE():
    if g_ptp_raw_rx.pending:
        g_ptp_raw_rx.pending = false
        PTP_FOL_OnFrame(data, len, rxTimestamp)


//=======================================================================
// FOLLOWER — frame entry point
//=======================================================================

PROCEDURE PTP_FOL_OnFrame(pData, len, rxTimestamp):
    sec  = rxTimestamp[63:32]
    nsec = rxTimestamp[31:0]
    handlePtp(pData, len, sec, nsec)

PROCEDURE handlePtp(pData, len, sec, nsec):
    messageType = pData[14].tsmt & 0x0F    // after 14-byte ETH header
    if messageType == MSG_SYNC:
        processSync(pData)
        TS_SYNC.receipt = { sec, nsec }    // t2: hardware receive timestamp
    elif messageType == MSG_FOLLOW_UP:
        processFollowUp(pData)


//=======================================================================
// FOLLOWER — SYNC processing
//=======================================================================

PROCEDURE processSync(syncMsg):
    seqId = syncMsg.header.sequenceID  (byte-swapped)
    if mismatch > 10: resetSlaveNode()
    else:             syncReceived = 1


//=======================================================================
// FOLLOWER — FOLLOW_UP processing and clock servo
//=======================================================================

PROCEDURE processFollowUp(followUpMsg):
    // Extract t1 from wire
    t1 = followUpMsg.preciseOriginTimestamp  (byte-swapped)
         + correctionField / 65536

    // t2 was stored during processSync
    t2 = TS_SYNC.receipt

    // Update software clock anchor
    PTP_CLOCK_Update(t2, g_ptp_raw_rx.sysTickAtRx)

    // Frequency-error measurement
    diffLocal  = t2_now - t2_prev       // local interval
    diffRemote = t1_now - t1_prev       // GM interval
    rateRatio  = diffRemote / diffLocal
    rateRatioFIR = FIR_filter(rateRatio)   // 16-tap mean
    PTP_CLOCK_SetDriftPPB((rateRatioFIR - 1.0) * 1e9)

    // Clock offset
    offset = t2 - t1

    // Servo state machine
    switch syncStatus:

        case UNINIT:
            if runs >= 16:
                // compute crystal-drift compensation
                mac_ti     = floor(40.0 * rateRatioFIR)
                mac_tisubn = frac(40.0 * rateRatioFIR) * 16_777_216
                schedule FOL_ACTION_SET_CLOCK_INC
                syncStatus = MATCHFREQ

        case MATCHFREQ:
            if |offset| > 100_000_000 ns: hardResync = 1
            else:                          syncStatus = HARDSYNC

        case HARDSYNC (and beyond):
            if |offset| > 0x3FFF_FFFF:
                syncStatus = UNINIT   // full reset

            elif |offset| > 16_777_215 ns:
                ta = sign(offset) | min(|offset|, 16_777_215)
                schedule FOL_ACTION_ADJUST_OFFSET

            elif |offset| > 300 ns:
                ta = FIR_coarse_filter(offset)
                schedule FOL_ACTION_ADJUST_OFFSET
                syncStatus = COARSE

            else:
                ta = FIR_fine_filter(offset)
                schedule FOL_ACTION_ADJUST_OFFSET
                syncStatus = FINE

    if hardResync:
        // Hard-set LAN865x clock directly to GM time
        fol_reg_values.tsl = t1.seconds
        fol_reg_values.tn  = t1.nanoseconds
        schedule FOL_ACTION_HARD_SYNC
        hardResync = 0


//=======================================================================
// FOLLOWER — register-write serialiser (called every 1 ms)
//=======================================================================

PROCEDURE PTP_FOL_Service():
    switch fol_pending_action:
        FOL_ACTION_HARD_SYNC:
            WriteRegister(MAC_TSL, t1_seconds)    // set LAN865x seconds
            WriteRegister(MAC_TN,  t1_nanosecs)   // set LAN865x nanoseconds

        FOL_ACTION_SET_CLOCK_INC:
            WriteRegister(MAC_TISUBN, tisubn)     // sub-nanosecond increment
            WriteRegister(MAC_TI,     ti)         // nanosecond increment

        FOL_ACTION_ADJUST_OFFSET:
            WriteRegister(MAC_TA, sign_bit | |offset|)  // signed offset step

        FOL_ACTION_ENABLE_PPS:
            WriteRegister(PPSCTL, 0x7D)           // enable 1PPS output


//=======================================================================
// SOFTWARE PTP CLOCK  (ptp_clock.c)
//=======================================================================

PROCEDURE PTP_CLOCK_Update(wallclock_ns, sys_tick):
    s_anchor_wc_ns = wallclock_ns   // last known PTP time
    s_anchor_tick  = sys_tick       // TC0 tick captured at that moment
    s_valid        = true

FUNCTION PTP_CLOCK_GetTime_ns():
    delta_tick = SYS_TIME_Counter64Get() - s_anchor_tick
    delta_ns   = delta_tick * (50 / 3)   // 60 MHz → 50/3 ns per tick (exact)
    return s_anchor_wc_ns + delta_ns
```

---

### 9.6 Key Data Structures

**`ptpSync_ct`** (`PTP_FOL_task.h:222–228`) — follower timestamp store:
```c
typedef struct {
    timeStamp_t origin;       // t1: GM send time (from FollowUp)
    timeStamp_t origin_prev;  // t1 from the previous Sync cycle
    timeStamp_t receipt;      // t2: local receive time (from RTSA)
    timeStamp_t receipt_prev; // t2 from the previous Sync cycle
} ptpSync_ct;
```

**`PTP_RxFrameEntry_t`** (`ptp_ts_ipc.h:33–39`) — IPC between driver and app:
```c
typedef struct {
    uint8_t  data[128];        // raw frame bytes
    uint16_t length;
    uint64_t rxTimestamp;      // RTSA: sec[63:32] | ns[31:0]
    uint64_t sysTickAtRx;      // TC0 tick captured at SYNC arrival
    bool     pending;          // true = app must process this frame
} PTP_RxFrameEntry_t;
```

**`syncMsg_t` / `followUpMsg_t`** (`PTP_FOL_task.h:183–198`) — PTP wire formats:
```c
typedef struct {
    ptpHeader_t    header;
    ptpTimeStamp_t originTimestamp;       // zero in SYNC; filled in FollowUp
} syncMsg_t;

typedef struct {
    ptpHeader_t    header;
    ptpTimeStamp_t preciseOriginTimestamp; // exact t1 from GM hardware clock
    tlv_followUp_t tlv;                   // Organisation-Specific TLV
} followUpMsg_t;
```

---

### 9.7 Data-Flow Summary

```
GM LAN865x hardware clock  (TTSCA registers)
    ──▶ t1  (seconds + nanoseconds of the SYNC send event)
    ──▶ FollowUp frame  (preciseOriginTimestamp = t1 + 7 650 ns)
    ──▶ PTP_CLOCK_Update(t1 + anchor_offset, TC0 tick)   ← GM software clock

FOL LAN865x hardware clock  (RTSA in SPI footer)
    ──▶ t2  (64-bit: sec[63:32] | ns[31:0])
    ──▶ g_ptp_raw_rx.rxTimestamp
    ──▶ handlePtp → processFollowUp
    ──▶ offset = t2 − t1
    ──▶ servo → write MAC_TA / MAC_TI / MAC_TSL
    ──▶ PTP_CLOCK_Update(t2, sysTickAtRx)                ← FOL software clock

Software PTP Clock  (ptp_clock.c — both boards):
    anchor (wc_ns, tick) + TC0 interpolation
    ──▶ PTP_CLOCK_GetTime_ns()  ← used by CLI ptp_time and ptp_time_test.py
```
