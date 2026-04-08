# tcpip_iperf_lan865x — Firmware Modifications

This document describes all manual changes applied on top of the MCC-generated
Harmony 3 project for the ATSAME54P20A + LAN865x 10BASE-T1S demo.

---

## 1. Build Infrastructure

### `tcpip_iperf_lan865x.X/setup_compiler.py`
New file. One-time setup tool — scans `C:\Program Files\Microchip\xc32\` for
installed XC32 versions, lets the user pick one, patches `toolchain.cmake` with
the selected version, and saves the choice to `setup_compiler.config` (JSON).
Must be run once after `git clone`, and again whenever a different XC32 version
should be used.

```
python setup_compiler.py
```

Example session:
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

The script replaces the version string in all compiler-path entries inside
`cmake/.generated/toolchain.cmake` (both forward-slash and double-backslash
forms, covering all 17 occurrences: `CMAKE_C_COMPILER`, `CMAKE_AR`, `MP_BIN2HEX`,
etc.). `build.bat` then uses the patched file directly — no `-D` overrides
needed on the CMake command line.

`setup_compiler.config` is listed in `.gitignore` (machine-specific).

### `tcpip_iperf_lan865x.X/build.bat`
New file. Runs a CMake + Ninja build in a single command.

**Usage:**
```
build.bat [incremental|clean|rebuild|help]
```

| Parameter | Behaviour |
|-----------|-----------|
| *(none)* | Incremental build — only recompiles changed files (default) |
| `incremental` | Same as above, explicit |
| `clean` | Deletes the temporary build directory |
| `rebuild` | Clean followed by a full build |
| `help` | Prints the available options |

**Compiler check:**  
At startup `build.bat` reads `setup_compiler.config` (written by
`setup_compiler.py`). If the file is missing it aborts with an error message.
If the configured `xc32-gcc.exe` path does not exist on this machine (e.g.
a different XC32 version is installed) it aborts with a clear message.
Otherwise it prints the selected version before the CMake step:
```
Compiler  : XC32 v5.10  (C:\Program Files\Microchip\xc32\v5.10\bin\xc32-gcc.exe)
```

**Build directory:**  
To avoid Windows MAX_PATH (260 character) issues caused by the deep project
path, the CMake intermediate files (`.o`, `.d`, `build.ninja`, `CMakeCache.txt`)
are placed **outside the repository**:

```
C:\work\ptp\AN1847\harmony\temp\tcpip_iperf_lan865x\default\
```

The path is derived automatically at runtime relative to `build.bat` using
`pushd`/`popd` — no hardcoded path, works after `git clone` on any machine
(provided the project is checked out inside a `harmony\` parent directory).

The build output (`.elf`, `.hex`) continues to be written to the project's
`out\tcpip_iperf_lan865x\` directory as before.

**`.gitignore` additions:**  
`**/_build/`, `*.hex`, `**/setup_flasher.config`, and `**/setup_compiler.config`
were added so that no temporary or machine-specific files are tracked by git.

**Out-of-box workflow after `git clone`:**
```bat
python setup_compiler.py    # select XC32 version (patches toolchain.cmake)
python setup_flasher.py     # assign Board 1 / Board 2 to connected debuggers
build.bat                   # compile
python flash.py             # flash both boards
```

### `tcpip_iperf_lan865x.X/setup_flasher.py`
New file. One-time setup tool — detects connected Microchip/Atmel EDBG debuggers,
lets the user assign Board 1 (Grandmaster) and Board 2 (Follower), and saves the
result to `setup_flasher.config` (JSON). Must be run once after `git clone` or
whenever the boards are connected to different USB ports.

```
python setup_flasher.py
```

Detection heuristics: USB VID `0x03EB` (Atmel/Microchip), serial number prefix
`ATML`, or manufacturer string containing `microchip` / `atmel`.

`setup_flasher.config` is listed in `.gitignore` (machine-specific, not committed).

### `tcpip_iperf_lan865x.X/flash.py` + `tcpip_iperf_lan865x.X/mdb_flash.py`
New files. Flash the compiled `default.hex` to one or both target boards using
MPLAB MDB (Microchip Debugger).

Board serial numbers and COM ports are read from `setup_flasher.config`.
If the config file is missing, `flash.py` aborts with an error and instructs the
user to run `setup_flasher.py` first.

```
python flash.py [--board1-only | --board2-only] [--hex <path>] [--swd-khz <n>]
```

---

## 2. MAC Address Randomisation

### `src/config/default/initialization.c`

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

---

## 3. LAN865x Register Access CLI Commands

### `src/app.c`

Two CLI commands added to the `Test` command group for run-time LAN865x SPI
register access without a debugger.

**New commands:**
| Command | Description |
|---------|-------------|
| `Test lan_read <addr_hex>` | Read a LAN865x register and print the result |
| `Test lan_write <addr_hex> <value_hex>` | Write a LAN865x register |

**Implementation details:**
- Non-blocking state machine: `app_lan_state_t` (IDLE / WAIT_READ / WAIT_WRITE).
- Callbacks `lan_read_callback()` / `lan_write_callback()` set volatile flags.
- 200 ms timeout (`APP_LAN_TIMEOUT_MS`) via `SYS_TIME_Counter64Get()`.
- Commands call `DRV_LAN865X_ReadRegister()` / `DRV_LAN865X_WriteRegister()` on
  driver instance 0 and print the result to the console.
- `Command_Init()` registers the group via `SYS_CMD_ADDGRP()`, called from
  `APP_Initialize()`.

---

## 4. PTP Hardware Timestamping

PTP (IEEE 1588 Precision Time Protocol) support was ported from the reference
project `C:\work\ptp\AN1847\t1s_100baset_bridge\` to this project.  
The implementation supports both **Grandmaster (GM)** and **Follower (FOL)**
roles, switchable at runtime via CLI.

### 4.1 New Source Files

| File | Description |
|------|-------------|
| `src/ptp_gm_task.c/.h` | PTP Grandmaster state machine. Sends Sync + FollowUp frames at a configurable interval, arms the LAN865x TX-Match hardware for TX timestamp capture. |
| `src/PTP_FOL_task.c/.h` | PTP Follower state machine. Receives Sync/FollowUp, computes clock offset with FIR low-pass filter, and slaves the local time. |
| `src/ptp_ts_ipc.h` | Shared IPC header: `PTP_RxTimestampEntry_t` struct + `g_ptp_rx_ts` extern declaration. |
| `src/filters.c/.h` | FIR low-pass filter and exponential low-pass filter used by the Follower servo. |

### 4.2 LAN865x Driver — `src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`

#### `DELAY_UNLOCK_EXT`
```c
// Before:
#define DELAY_UNLOCK_EXT  (100u)
// After:
#define DELAY_UNLOCK_EXT  (5u)
```
The TX Timestamp Capture Available (TTSCAA) bit appears in STATUS0 approximately
1 ms after the EXST signal. The original 100 ms timeout caused missed timestamps
(TTSCMA). Reduced to 5 ms.

#### `drvTsCaptureStatus0[]`
```c
static volatile uint32_t drvTsCaptureStatus0[DRV_LAN865X_INSTANCES_NUMBER];
```
Shadow register that saves STATUS0 bits 8–10 (TTSCAA/B/C) in `_OnStatus0()`
before the W1C-clear. Read and atomically cleared by
`DRV_LAN865X_GetAndClearTsCapture()`. This avoids the race condition where the
driver clears TTSCAA before the GM state machine can read it.

#### `g_ptp_rx_ts` — RX Timestamp IPC
```c
typedef struct { uint64_t rxTimestamp; bool valid; } PTP_RxTimestampEntry_t;
volatile PTP_RxTimestampEntry_t g_ptp_rx_ts = {0u, false};
```
Defined at the top of `TC6_CB_OnRxEthernetPacket()`. The callback now saves the
hardware RX timestamp into this struct when `rxTimestamp != NULL`. The application
reads it in `pktEth0Handler()` when a PTP frame (EtherType 0x88F7) arrives.

#### TC6_MEMMAP — Init Register Map
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

#### `_InitConfig` — Cases 46 and 47
```c
// Case 46: PADCTRL RMW — enables TX timestamp pad output
TC6_ReadModifyWriteRegister(tc, 0x000A0088u, 0x00000100u, 0x00000300u, ...);

// Case 47: PPSCTL — enables PPS clock for TSU counter  
TC6_WriteRegister(tc, 0x000A0239u, 0x0000007Du, ...);
```
Previously case 46 wrote `0xC000` to `0x000400E0`. Replaced with the PADCTRL
RMW required for TX hardware timestamping.

#### `_InitUserSettings` — Case 8
```c
regVal = 0x9026u;
regVal |= 0x80u;  // FTSE: Frame Timestamp Enable
regVal |= 0x40u;  // FTSS: 64-bit timestamps
```
Enables frame-level timestamping in CONFIG0. Required for TTSCAA TX capture
and the TC6 driver's RTSA 8-byte timestamp stripping on RX.

#### `_OnStatus0` — TTSCAA Saving and Debug Print
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

#### New Public Functions
```c
bool     DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf,
             uint16_t len, uint8_t tsc,
             DRV_LAN865X_RawTxCallback_t cb, void *pTag);

bool     DRV_LAN865X_IsReady(uint8_t idx);

uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx);
```
| Function | Description |
|----------|-------------|
| `SendRawEthFrame` | Sends a raw Ethernet frame via TC6 with a selectable TSC flag (0x01 = Capture A for Sync, 0x00 = no capture). |
| `IsReady` | Returns `true` when the driver instance is fully initialised and ready. Used to detect Loss-of-Framing recovery. |
| `GetAndClearTsCapture` | Atomically reads and clears `drvTsCaptureStatus0[idx]`. Called by the GM state machine to retrieve TTSCAA/B/C bits. |

### 4.3 LAN865x Driver Header — `src/config/default/driver/lan865x/drv_lan865x.h`

Added after the existing `DRV_LAN865X_ReadModifyWriteRegister` declaration:
- `DRV_LAN865X_RawTxCallback_t` typedef
- Declarations for `DRV_LAN865X_SendRawEthFrame()`, `DRV_LAN865X_IsReady()`,
  `DRV_LAN865X_GetAndClearTsCapture()`

### 4.4 Application — `src/app.c`

#### New includes
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

### 4.5 Application Header — `src/app.h`

Added `APP_STATE_IDLE` to the `APP_STATES` enumeration.

### 4.6 CMake Build — `cmake/.../file.cmake`

Added three new source files to the compile list:
```cmake
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/filters.c"
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/PTP_FOL_task.c"
"${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/ptp_gm_task.c"
```

---

## 5. PTP Runtime CLI Commands

The following commands are available after flashing (from `ptp_gm_task.h` /
`PTP_FOL_task.h` CLI integration in the reference project — implemented in
the GM/FOL task files):

| Command | Description |
|---------|-------------|
| `ptp_mode off` | Disable PTP |
| `ptp_mode follower` | Enable Follower mode (PTP_SLAVE) — silent |
| `ptp_mode follower v` | Enable Follower mode with per-Sync verbose line (overwriting) |
| `ptp_mode master` | Enable Grandmaster mode (PTP_MASTER) |
| `ptp_status` | Show current mode, sync count, servo state |
| `ptp_interval <ms>` | Set GM Sync interval in ms (default 125) |
| `ptp_offset` | Show follower clock offset in ns |
| `ptp_reset` | Reset follower servo to UNINIT |
| `ptp_dst [multicast\|broadcast]` | Set PTP destination MAC |

---

## 6. Hardware Configuration

- **MCU**: ATSAME54P20A (Cortex-M4F, 120 MHz)
- **Ethernet**: LAN865x 10BASE-T1S via SPI (TC6 protocol)
- **Framework**: MPLAB Harmony 3 (bare-metal, no FreeRTOS)
- **Build**: CMake 4.1 + Ninja
- **HEX output**: `tcpip_iperf_lan865x.X/out/tcpip_iperf_lan865x/default.hex`
- **Programmer**: MPLAB MDB (`flash.py`)

### Board Serials
| Board | Serial |
|-------|--------|
| Board 1 | `ATML3264031800001049` |
| Board 2 | `ATML3264031800001290` |

---

## 7. Architecture — PTP Timestamp Flow

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

## 8. tc6.c Modifications

### `src/config/default/driver/lan865x/src/dynamic/tc6/tc6.c`

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

---

## 9. Test Script — `ptp_onoff_test.py`

Location: `tcpip_iperf_lan865x.X/ptp_onoff_test.py`

Automated resilience test: starts PTP, measures baseline offset, stops GM,
observes drift, restarts GM, verifies re-convergence and post-restart accuracy.

### Usage
```bat
cd tcpip_iperf_lan865x.X
python ptp_onoff_test.py --gm-port COM10 --fol-port COM8
```

### Test Phases
| Step / Phase | Description |
|-------------|-------------|
| Step 0 | Reset both boards |
| Step 1 | Set IP addresses (`setip eth0 ...`) |
| Step 2 | Bidirectional ping |
| Step 3 | Start PTP, wait for FOL FINE state |
| Phase A | Baseline: 10 offset samples while GM running |
| Phase B | Blackout: stop GM, monitor FOL offset for 5 s |
| Phase C | Re-convergence: restart GM, wait for FINE again |
| Phase D | Post-restart: 10 offset samples, verify ±100 ns |

### Key Architecture Note — Windows Serial Threading
**Rule**: Never read the same `pyserial.Serial` port from two threads simultaneously  
on Windows — corrupts overlapped I/O handles → access violation in heap allocation.

Fixed in `test_step_3_start_ptp()`:
```python
# 1. FOL command — main thread owns fol_ser exclusively
resp = send_command(self.fol_ser, "ptp_mode follower", ...)
time.sleep(0.5)
# 2. Now start convergence thread (sole reader of fol_ser from here on)
self.fol_ser.reset_input_buffer()
self._start_convergence_thread()
# 3. GM command — uses gm_ser only, safe to run concurrently
self.gm_ser.write(b"ptp_mode master\r\n")
```

### Detection Patterns
| Pattern | Firmware string matched |
|---------|------------------------|
| `RE_HARD_SYNC` | `"Hard sync completed"` |
| `RE_MATCHFREQ` | `"UNINIT->MATCHFREQ"` |
| `RE_COARSE` | `"PTP COARSE"` |
| `RE_FINE` | `"PTP FINE"` |

---

## 11. Test Results

### Run: `ptp_onoff_20260408_002157.log` — **PASS**

Test parameters: GM on=10 s, off=5 s, 1 cycle, convergence timeout=30 s

| Phase | Result |
|-------|--------|
| Step 3 initial convergence | **FINE in 2.7 s** (HARD\_SYNC@0.4s, MATCHFREQ@2.3s) |
| Phase A baseline (n=10) | mean=**+36.5 ns**, stdev=16.5 ns, min=+5 ns, max=+57 ns |
| Phase B blackout (5 s) | Offset frozen at **+78 ns**, drift range = **0 ns** — local clock holds |
| Phase C re-convergence | **FINE in 0.8 s** (HARD\_SYNC@0.4s) — saved TI/TISUBN reused, MATCHFREQ skipped |
| Phase D post-restart (n=10) | mean=**+43.9 ns**, stdev=14.9 ns, **10/10 within ±100 ns** |

Overall: **PASS** (5/5 steps/cycles)

---

## 12. Console Output — Verbose Mode and Format

### Overview

All periodic PTP prints use carriage-return-only (`\r`, no `\n`) to overwrite
the same terminal line continuously. State-transition messages are prefixed with
`\r\n` so they scroll normally without corrupting the overwriting line.

### GM output (`ptp_gm_task.c`)

One overwriting line per FollowUp sent. Removed all per-Sync chatty prints
(`Sync #N sent`, `Sync #N TX confirmed`, `TTSCAA via CB`):

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

### Follower verbose mode (`PTP_FOL_task.c`)

Activated with `ptp_mode follower v`. One overwriting line per received Sync,
showing GM TX time (`t1`), local RX time (`t2`), servo state, and offset:

```
[V] FINE       t1=00:01:28.804334810  t2=00:01:28.804391620  off=       +57 ns
```

Format: `[V] <STATE>  t1=HH:MM:SS.nnnnnnnnn  t2=HH:MM:SS.nnnnnnnnn  off=<±offset> ns\r`

Both `t1` (GM origin timestamp from FollowUp) and `t2` (local RX timestamp)
are `uint64_t` nanosecond values decomposed identically:
```c
uint32_t sec = (uint32_t)(t / 1000000000ULL);
uint32_t ns  = (uint32_t)(t % 1000000000ULL);
uint32_t h   = sec / 3600u;
uint32_t m   = (sec % 3600u) / 60u;
uint32_t s   = sec % 60u;
```

State names (fixed-width 9 chars): `UNINIT   `, `MATCHFREQ`, `HARDSYNC `,
`COARSE   `, `FINE     `.

### State transition log lines

State transitions (`COARSE`, `FINE`) are printed with `\r\n` prefix so they do
not overwrite the running verbose line:
```c
PTP_LOG("\r\nPTP COARSE  offset=%d\r\n", (int)offset);
PTP_LOG("\r\nPTP FINE    offset=%d\r\n", (int)offset);
```

### `PTP_FOL_SetVerbose()` API (`PTP_FOL_task.h`)
```c
void PTP_FOL_SetVerbose(bool verbose);
```
Called from `ptp_mode_cmd()` in `app.c`:
```c
bool verbose = (argc >= 3) && (strcmp(argv[2], "v") == 0);
PTP_FOL_SetVerbose(verbose);
```
