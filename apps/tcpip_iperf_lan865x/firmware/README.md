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
build.bat                   # compile  (summary printed automatically)
python flash.py             # flash both boards
```

### `tcpip_iperf_lan865x.X/build_summary.py`
New file. Called automatically by `build.bat` after every successful build.
Parses the linker output files and the ELF symbol table to print a concise
human-readable summary.

**Sources used:**
| Source | Information extracted |
|--------|----------------------|
| `memoryfile.xml` (linker XML) | Flash used/free/total, RAM used/free/total |
| `mem.map` (linker map) | `_min_heap_size` (heap reserved by linker script) |
| `default.elf` via `xc32-nm` | Active interrupt handler names |
| `default.elf` binary scan | Embedded build timestamp (`__DATE__` / `__TIME__`) |

**Build timestamp extraction:**  
`app.c` embeds the compile-time timestamp as a literal string via:
```c
SYS_CONSOLE_PRINT("[APP] Build: " __DATE__ " " __TIME__ "\r\n");
```
`build_summary.py` scans the ELF binary for this pattern and extracts the
timestamp (e.g. `Apr  8 2026 17:08:51`). It appears in the summary header and
drives the filename of the image artefacts.

**Image output — `out/tcpip_iperf_lan865x/image/`:**  
After printing the summary, the script creates (or overwrites) two files in the
`image/` subdirectory:

| File | Contents |
|------|----------|
| `tcpip_iperf_lan865x_<YYYYMMDD_HHMMSS>.hex` | Copy of `default.hex`, named with the build timestamp |
| `build_summary_<YYYYMMDD_HHMMSS>.txt` | Full summary text as written to stdout |

The `image/` directory is tracked by git (`!**/image/*.hex` negation rule in
`.gitignore`) so that released binaries are always available after `git clone`
without a rebuild.

**Example output:**
```
==============================================================
  BUILD SUMMARY
==============================================================

  Build      : Apr  8 2026 17:08:51

  Flash (program memory)
    Used   :  131,877 bytes  ( 128.8 KiB)  12.6%
    Free   :  916,699 bytes  ( 895.2 KiB)
    Total  : 1,048,576 bytes  (1024.0 KiB)
    [####--------------------------]

  RAM (data memory)
    Used   :   15,937 bytes  (  15.6 KiB)  6.1%
    Free   :  246,207 bytes  ( 240.4 KiB)
    Total  :  262,144 bytes  ( 256.0 KiB)
    [##----------------------------]

  Linker-Reserved Regions
    Heap   :   44,960 bytes  (  43.9 KiB)  (_min_heap_size)
    Stack  :       -- not found in map --

  Interrupt Handlers
    Core IRQs        ( 7): BusFault, DebugMonitor, HardFault,
                           MemoryManagement, NonMaskableInt, Reset, UsageFault
    Peripheral IRQs  ( 5):
      - DMAC_0
      - DMAC_1
      - SERCOM0_SPI
      - SERCOM1_USART
      - TC0_Timer

  Note: Heap is active (43.9 KiB).
        Used by: musl malloc (XC32 libc), TCPIP internal heap.
        Runtime heap consumption is not measurable at link time.

==============================================================

  Image HEX  : ...image\tcpip_iperf_lan865x_20260408_170851.hex
  Summary    : ...image\build_summary_20260408_170851.txt
```

**Interrupt classification:**  
Only non-dummy handlers are listed. Weak symbols (`W`) that point to
`Dummy_Handler` are silently skipped. File-local sub-handlers (lowercase `t`,
e.g. `SERCOM1_USART_ISR_RX_Handler`) are excluded — only the top-level IRQ
vector entry is shown.

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

### `tcpip_iperf_lan865x.X/cmake/tcpip_iperf_lan865x/default/user.cmake`
New file. Workaround for a CMake + xc32-gcc incompatibility that causes
`build.bat rebuild` (or any clean build where `out/` does not yet exist) to
fail at the final bin2hex step with `default.elf: No such file`.

**Root cause — MINGW backslash stripping in xc32-gcc:**  
The XC32 toolchain binaries are MINGW executables. MINGW strips backslashes
from command-line arguments when passing them to the child process. In the
generated `build.ninja`, the linker output path is written as a relative
backslash path:
```
TARGET_FILE = out\default.elf
```
The linker receives `-o out\default.elf` → MINGW strips the backslash →
xc32-gcc creates a file literally named `outdefault.elf` in the build
directory (no path separator). The canonical path `out\tcpip_iperf_lan865x\default.elf`,
which `build_summary.py` and `flash.py` expect, is therefore never written and
the build fails.

**Symptom:**
```
build.bat rebuild
```
Fails at step 152 (bin2hex):
```
xc32-bin2hex: error: out\tcpip_iperf_lan865x\default.elf: No such file or directory
```

**Fix — `user.cmake`:**  
`CMakeLists.txt` already contains `include(user.cmake OPTIONAL)` — the
standard CMake user extension point. The new `user.cmake` file:

1. Creates `<BUILD_DIR>/out/` at CMake configure time (`file(MAKE_DIRECTORY)`).
2. Redirects the linker's `TARGET_FILE` to that directory via
   `RUNTIME_OUTPUT_DIRECTORY` — Ninja now calls xc32-gcc with
   `-o <BUILD_DIR>/out/default.elf` (absolute forward-slash path → unaffected
   by the MINGW bug).
3. Adds a `POST_BUILD` command that copies the resulting ELF to the canonical
   location `out/tcpip_iperf_lan865x/default.elf` (relative to the source
   tree) so that `bin2hex`, `build_summary.py`, and `flash.py` find it where
   they expect it.

The file is committed to the repository; no manual action is required.

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

---

## 13. PTP Role-Swap Bug Fix

### Background

A **role-swap** describes the scenario where PTP mode is disabled on both boards
(e.g. `ptp_mode off`) and then restarted with the Grandmaster and Follower roles
swapped between the two boards.  Before this fix the new Follower never reached
FINE state; its offset was permanently stuck at approximately **−3.13 ms**.

---

### Root Cause

The hang was caused by two independent bugs in `src/ptp_gm_task.c`:

#### Bug 1 — `PTP_GM_Init()` overwrote the crystal calibration (primary)

During the first PTP session the FOL servo runs its MATCHFREQ phase and
measures the board's actual crystal frequency by fine-tuning `MAC_TI` and
`MAC_TISUBN`.  For board 1, for example, the calibrated value is `MAC_TI = 39`
(nominal is 40), reflecting a real crystal frequency slightly below 25 MHz.

When roles were swapped and the same board became the GM, `PTP_GM_Init()`
unconditionally wrote `MAC_TI = 40` (nominal) to the register — destroying the
calibration and making the GM clock run approximately 2.5 % too fast.  The new
FOL then tried to correct this 2.5 % frequency error with a small PI servo
whose correction range is far too narrow.  Because the error magnitude matched
the servo's maximum per-step correction exactly, the offset converged to a fixed
point and stayed there forever:

```
drift per sync = (40 − 39) / 40 × 125 ms = 3.125 ms
```

This is why the stuck offset was always ≈ −3.13 ms and never changed with time.

#### Bug 2 — `gm_deinit_vals` set `MAC_TI = 0` (secondary)

After `ptp_mode off` the deinit sequence wrote `MAC_TI = 0` to the register,
which stopped the PTP hardware clock entirely.  This had no visible effect
during normal operation (mode is off) but left the hardware in a broken state
that could affect subsequent role assignments.

---

### Fix

#### `src/PTP_FOL_task.c` and `src/PTP_FOL_task.h` — export calibrated values

A new function is added that lets other modules read the TI/TISUBN values that
the FOL servo settled on at the end of its MATCHFREQ phase:

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

Implementation in `PTP_FOL_task.c` reads the existing module-level statics
`calibratedTI_value` / `calibratedTISUBN_value` that are already stored at the
UNINIT→MATCHFREQ→HARDSYNC transition:

```c
void PTP_FOL_GetCalibratedClockInc(uint32_t *pTI, uint32_t *pTISUBN)
{
    if (pTI)     *pTI     = calibratedTI_value;
    if (pTISUBN) *pTISUBN = calibratedTISUBN_value;
}
```

#### `src/ptp_gm_task.c` — use calibrated TI on init, keep clock running on deinit

**Init sequence** (`GM_INIT_WRITE_COUNT` changed 8 → 9, `gm_init_vals[]` made
non-const):

| Index | Register | Before fix | After fix |
|-------|----------|-----------|-----------|
| 6 | `MAC_TISUBN` | not present | filled dynamically from `PTP_FOL_GetCalibratedClockInc()` |
| 7 | `MAC_TI` | `40` (hardcoded nominal) | filled dynamically from `PTP_FOL_GetCalibratedClockInc()` |
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

**Deinit sequence** — `gm_deinit_vals[6]` (MAC_TI) changed `0u` → `40u`:
```c
/* was: 0u  — froze the hardware PTP clock after ptp_mode off  */
/* now: 40u — clock keeps ticking at nominal rate after deinit */
static const uint32_t gm_deinit_vals[] = { ..., 40u, ... };
```

---

### Test Script — `ptp_role_swap_test.py`

Location: `tcpip_iperf_lan865x.X/ptp_role_swap_test.py`

Automated two-phase test that verifies correct convergence after a role swap.

#### Usage
```bat
cd tcpip_iperf_lan865x.X
python ptp_role_swap_test.py --board1-port COM8 --board2-port COM10
```

Optional arguments:
```
--board1-port PORT     Serial port for board 1 (default: COM8)
--board2-port PORT     Serial port for board 2 (default: COM10)
--board1-ip   IP       IP address of board 1   (default: 192.168.0.30)
--board2-ip   IP       IP address of board 2   (default: 192.168.0.20)
--convergence-timeout  Seconds to wait for FINE (default: 30)
--verbose              Print every offset sample to stdout
```

#### Test Phases

| Phase | Board 1 | Board 2 | Description |
|-------|---------|---------|-------------|
| Phase 1 | Follower | Grandmaster | Normal session; wait for FINE, collect 10 offset samples |
| — | `ptp_mode off` | `ptp_mode off` | Stop both, pause 5 s |
| Phase 2 | **Grandmaster** | **Follower** | Roles swapped; wait for FINE, collect 10 offset samples |

The script detects a stuck-offset failure (`abs(mean) > 1 000 000 ns`) and
prints a root-cause hint pointing to the crystal-calibration overwrite.

#### Pass Criteria
- Both phases reach FINE within `--convergence-timeout`
- Phase 2 post-swap mean offset within ±500 ns
- Phase 2 stdev < 100 ns
- At least 9/10 samples within ±500 ns

---

### Test Results

#### Before fix

| Metric | Phase 1 | Phase 2 (after role swap) |
|--------|---------|--------------------------|
| FINE reached | 2.7 s | **never** (timeout at 30 s) |
| mean offset | +52 ns | **−3 138 186 ns** (stuck) |
| Verdict | PASS | **FAIL** |

#### After fix (two confirmed runs)

| Run | Phase 1 FINE | Phase 1 mean | Phase 2 FINE | Phase 2 mean | Phase 2 stdev | ≤±500 ns |
|-----|-------------|-------------|-------------|-------------|--------------|---------|
| Run 1 | 2.7 s | +50.8 ns | **2.7 s** | **−4.0 ns** | 12.8 ns | 10/10 |
| Run 2 | 3.1 s | +57.9 ns | **2.9 s** | **−9.2 ns** | 21.2 ns | 10/10 |

Overall: **PASS** (6/6 test checks, both runs).

---

## 14. PTP Software Clock — `src/ptp_clock.c` / `src/ptp_clock.h`

A nanosecond-resolution software wallclock based on the TC0 hardware timer
(60 MHz / GCLK0-div-2).  Works identically on both GM and FOL boards after PTP
convergence.

### Design

An anchor point `(wallclock_ns, sys_tick)` is recorded on every PTP Sync.
`PTP_CLOCK_GetTime_ns()` interpolates the current time using the TC0 tick delta
since the last anchor:

```
tick_delta → ns:  ticks × 50/3  (exact integer decomposition, avoids floats)
```

No SPI transfers, no mutex, and no blocking at query time.

### Anchor Sources

| Board role | Called from | Trigger |
|-----------|-------------|---------|
| Follower | `PTP_FOL_task.c` via `PTP_CLOCK_Update()` | Every Sync/FollowUp received (~125 ms) |
| Grandmaster | `ptp_gm_task.c` via `PTP_CLOCK_Update()` | TX timestamp captured (TTSCAA) |

### TC0 Tick-Rate Correction (TISUBN)

The FOL servo writes the crystal-calibrated value to `MAC_TISUBN` once at
UNINIT→MATCHFREQ.  This corrects the LAN865x hardware timer frequency to match
the GM.  From MATCHFREQ onward the anchor-based interpolation is accurate to
within the re-anchoring residual (~65–130 µs stdev).

### `drift_ppb` — Residual Frequency Error

After the TISUBN correction, `drift_ppb` reports the remaining frequency error
observed by the PTP servo (in parts per billion):

```c
int32_t PTP_CLOCK_GetDriftPPB(void);   // read
void    PTP_CLOCK_SetDriftPPB(int32_t); // written by PTP_FOL_task.c
```

`drift_ppb` is updated at every Sync frame when the servo is in COARSE or FINE
state, computed from the FIR-filtered rate ratio:

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

## 15. PTP Sync Before/After Test — `tcpip_iperf_lan865x.X/ptp_sync_before_after_test.py`

Demonstrates PTP synchronisation in a single automated run with a before/after
comparison.

### Test Phases

| Phase | Description |
|-------|-------------|
| **Phase 0 — Free Running** | Both clocks zeroed simultaneously, `clk_get` pairs collected for 60 s. Linear regression shows raw crystal drift. |
| **PTP Setup** | IP config, start Follower + Grandmaster, wait for FINE. |
| **Phase 1 — PTP Active** | Clocks re-zeroed, `clk_get` pairs collected for 60 s with PTP running. |
| **Comparison** | Side-by-side table: slope, residual stdev, drift reduction %. |

### Usage

```bat
cd tcpip_iperf_lan865x.X
python ptp_sync_before_after_test.py --gm-port COM8 --fol-port COM10
```

### PASS Criteria

| Criterion | Default threshold |
|-----------|------------------|
| `|slope_ptp| < threshold` | 2.0 ppm |
| `residual stdev < threshold` | 500 µs |

### Measurement Method — Swap Symmetry

Alternating samples query GM first, then FOL; the next sample queries FOL
first, then GM. The send-time skew between the two parallel threads is subtracted
from the `clk_get` difference, eliminating systematic bias from the host PC
scheduler.

### Synopsis of Results (10 runs total)

| Run | Free-run (ppm) | PTP slope (ppm) | PTP stdev (µs) | drift stdev (ppb) | FINE (s) |
|-----|---------------|-----------------|----------------|-------------------|----------|
| 1 | +179.3 | −0.243 | 68 | 0¹ | 2.8 |
| 2 | +209.0 | −0.183 | 66 | 0¹ | 2.9 |
| 3 | −75.4 | +0.146 | 129 | 0¹ | 2.7 |
| 4 | −518.5 | +0.666 | 92 | 6 | 2.7 |
| 5 | −229.3 | +0.641 | 121 | 10 | 2.7 |
| 6–10² | −532…−46 | −0.43…+0.75 | 60…78 | 6–10 | 2.7 |

¹ Runs 1–3 predated the `drift_ppb` firmware update (always 0).  
² Runs 6–10 from the reproducibility test.

**All 10 runs: PASS**

---

## 16. PTP Reproducibility Test — `tcpip_iperf_lan865x.X/ptp_reproducibility_test.py`

Runs `ptp_sync_before_after_test.py` N times (default 5) and aggregates all
results in a single summary table. Purpose: automated verification of test
reproducibility.

### Usage

```bat
cd tcpip_iperf_lan865x.X
python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10
python ptp_reproducibility_test.py --gm-port COM8 --fol-port COM10 --runs 3
```

All arguments (`--free-run-s`, `--ptp-s`, `--slope-threshold-ppm`, etc.) are
forwarded to the sub-test.

### Log File Integrity

For each run the reproducibility test generates a unique log filename
(`ptp_sync_before_after_test_<YYYYMMDD_HHMMSS>.log`) **before** starting the
sub-test and passes it via `--log-file`.  After `subprocess.run()` returns
(blocking), that exact file is parsed — no directory search, no ambiguity.

### Summary Table Columns

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

### Confirmed Reproducibility Run (2026-04-10)

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

## 17. HW/SW Timer Synchronisation Test — `tcpip_iperf_lan865x.X/hw_timer_sync_test.py`

Validates that the TC0-based software clock (`PTP_CLOCK`) works correctly
**without** PTP Ethernet synchronisation.  It measures the raw crystal
frequency difference between the two boards and verifies that the TC0
tick-to-nanosecond interpolation is internally consistent.

### Purpose

| What it proves | What it does NOT prove |
|----------------|----------------------|
| TC0 tick-to-ns conversion is correct on both boards | Correct PTP anchor capture (→ `ptp_time_test.py`) |
| `PTP_CLOCK_GetTime_ns()` interpolation is consistent | PTP Ethernet timestamping (RTSA / TTSCAL) |
| UART serialisation latency correction (`perf_counter_ns`) works | |
| Crystal frequency ratio between the two boards is measured accurately | |

### Test Steps

| Step | Description |
|------|-------------|
| 0 — Simultaneous `clk_set 0` | Both clocks zeroed in parallel threads; thread launch skew measured. |
| 1 — Settle | 2 s pause so both boards start collecting from a stable baseline. |
| 2 — Collect N paired samples | 100 swap-symmetrised `clk_get` pairs at 100 ms intervals (~12 s total). Linear regression over `diff(t) = intercept + slope * t` removes the crystal trend; residuals must be below threshold. |

### PASS Criterion

`residual stdev < 500 µs`

The growing mean offset (linear drift) is **expected** and does NOT cause a
FAIL — two free-running crystal oscillators always drift apart at a constant
rate.  The PASS check only verifies that the *residuals after removing that
linear trend* are small, which confirms that the TC0 interpolation is
self-consistent.

### Output Quantities

| Quantity | Meaning |
|----------|---------|
| `intercept` | Clock offset right after `clk_set 0` — combined effect of thread launch skew, UART latency, and FreeRTOS scheduling. Typical: −700 … 0 µs. |
| `slope` (ppm) | Crystal frequency difference Board B − Board A. Varies run-to-run (−400 … +10 ppm) because the TISUBN register retains a PTP-era correction from the preceding session. |
| `residual stdev` | Measurement noise = UART serialisation jitter. Typical: 60 … 200 µs. |
| `drift A / B` | Reported `drift_ppb` — always 0 ppb when PTP is inactive. |

### Usage

```bat
cd tcpip_iperf_lan865x.X
python hw_timer_sync_test.py --a-port COM8 --b-port COM10
```

Optional arguments: `--n <samples>` (default 100), `--pause-ms` (default 100),
`--threshold-us` (default 500), `--settle-s` (default 2), `--log-file <path>`.

### Confirmed Results (6 valid runs, 2026-04-09 / 2026-04-10)

| Run | Date/time | Slope (ppm) | Intercept (µs) | Res. stdev (µs) | Result |
|-----|-----------|-------------|----------------|-----------------|--------|
| 1 | 2026-04-09 17:56 | −321.2 | −687 | 91 | PASS |
| 2 | 2026-04-09 17:57 | −266.0 | −424 | 134 | PASS |
| 3 | 2026-04-10 09:08 | −0.0¹ | −22 | 145 | PASS |
| 4 | 2026-04-10 09:08 | −392.4 | −453 | 192 | PASS |
| 5 | 2026-04-10 09:12 | +5.5 | −66 | 62 | PASS |
| 6 | 2026-04-10 09:19 | −139.9 | −691 | 149 | PASS |

¹ Run 3 was executed immediately after a PTP session; the TISUBN register
retained its PTP-era crystal-rate correction, making the apparent slope ≈ 0.
The TC0 interpolation was still verified consistent (stdev 145 µs < 500 µs).

**All 6 runs: PASS (3/3 steps each)**
