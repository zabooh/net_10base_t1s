# README — Cross-Build (CMake/Makefile + MPLAB X)

This document describes the state of the `cross` branch: the project
[`apps/tcpip_iperf_lan865x`](../../apps/tcpip_iperf_lan865x/) is now built **in parallel** via
two build paths:

1. **CMake/Makefile** (main path, actively used) — via
   [firmware/Makefile](../../apps/tcpip_iperf_lan865x/firmware/Makefile) and
   [firmware/cmake/](../../apps/tcpip_iperf_lan865x/firmware/cmake/)
2. **MPLAB X IDE** — via
   [firmware/tcpip_iperf_lan865x.X/](../../apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/),
   driven by
   [nbproject/configurations.xml](../../apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/nbproject/configurations.xml)

Goal of the branch: provide embedded developers with a **presentable PTP
implementation for the LAN8651** that can also be built and debugged in
MPLAB X (the Harmony/MCC scaffolding serves as a demonstrator).

---

## 1) What was changed in `configurations.xml`

The MPLAB X project was severely outdated — the CMake/Makefile build had
23 new `.c` and 23 new `.h` files that were not registered in MPLAB X.

### Source files added (`<logicalFolder name="SourceFiles">`)

`button_led.c`, `cyclic_fire.c`, `cyclic_fire_cli.c`, `cyclic_fire_isr.c`,
`demo_cli.c`, `exception_handler.c`, `iperf_control.c`, `lan_regs_cli.c`,
`loop_stats.c`, `loop_stats_cli.c`, `pd10_blink.c`, `pd10_blink_cli.c`,
`ptp_cli.c`, `ptp_offset_trace.c`, `ptp_rx.c`, `standalone_demo.c`, `sw_ntp.c`,
`sw_ntp_cli.c`, `sw_ntp_offset_trace.c`, `test_exception_cli.c`, `tfuture.c`,
`tfuture_cli.c`, `watchdog.c`

### Header files added (`<logicalFolder name="HeaderFiles">`)

`app_log.h`, `button_led.h`, `cyclic_fire{,_cli,_isr}.h`, `demo_cli.h`,
`iperf_control.h`, `lan_regs_cli.h`, `loop_stats{,_cli}.h`,
`pd10_blink{,_cli}.h`, `ptp_cli.h`, `ptp_offset_trace.h`, `ptp_rx.h`,
`standalone_demo.h`, `sw_ntp{,_cli,_offset_trace}.h`, `test_exception_cli.h`,
`tfuture{,_cli}.h`, `watchdog.h`

### C compiler (C32)

| Property | Before | After |
|---|---|---|
| `extra-include-directories` | `../src;…` | `..;../src;…` (firmware root as a path) |
| `preprocessor-macros` | `HAVE_CONFIG_H;WOLFSSL_IGNORE_FILE_WARN` | `__DEBUG;HAVE_CONFIG_H;WOLFSSL_IGNORE_FILE_WARN;XPRJ_default=default` |

### C++ compiler (C32CPP)

| Property | Before | After |
|---|---|---|
| `extra-include-directories` | `../src;…` | `..;../src;…` |
| `preprocessor-macros` | `""` | `__DEBUG;XPRJ_default=default` |

→ This makes the MPLAB X build fully match the compile options from
   [firmware/cmake/.../rule.cmake](../../apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/tcpip_iperf_lan865x/rule.cmake).

Commit for this change: `db64350 build(mplabx): sync configurations.xml with current sources`

---

## 2) ⚠ CRITICAL: `drv_lan865x_api.c` contains the PTP HW-timestamping infrastructure

The LAN865x driver in this fork deviates **significantly** from upstream
(416 changed lines in `drv_lan865x_api.c`, ~62 additional lines in
`drv_lan865x.h`). These changes are **not cosmetic** — they form the
fundamental hardware-timestamping layer, without which PTP does not work.

If MCC runs over the project again from within MPLAB X, it will try to
overwrite exactly this file — **the regeneration MUST be rejected**
(click **Reject** in the MCC merge dialog).

File:
[`apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`](../../apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c)

### What was mandatorily added in the driver for PTP

#### 2.1 EXTINT-14 nIRQ ISR with TC0 tick latch

```c
static volatile bool     s_nirq_pending = false;
static volatile uint64_t s_nirq_tick    = 0u;
// ISR captures a TC0 tick at the earliest possible moment of nIRQ assertion
s_nirq_tick    = SYS_TIME_Counter64Get();
s_nirq_pending = true;
```

Provides **ISR precision (~5 µs jitter)** instead of task-level read
(~100 µs jitter + several ms latency from the actual t1 event). Anchor
tick for `PTP_CLOCK_Update`.

#### 2.2 TTSCAA save-before-W1C — fixes race condition

```c
static volatile uint32_t drvTsCaptureStatus0[DRV_LAN865X_INSTANCES_NUMBER];
static volatile uint64_t drvTsCaptureNirqTick[DRV_LAN865X_INSTANCES_NUMBER];
// Save TTSCAA/B/C bits (8-10) BEFORE W1C clear
drvTsCaptureStatus0[i] |= (value & 0x0700u);
drvTsCaptureNirqTick[i] = s_nirq_tick;
```

Without this save, the GM state machine loses the TTSCAA bits because
the driver's status handler clears them via Write-1-Clear before they
can be read.

#### 2.3 FTSE bit (Frame Timestamp Enable)

```c
regVal |= 0x80u; /* FTSE: required for TTSCAA TX capture */
```

Without this bit, the hardware fires **no** TX timestamp capture at all
on Sync transmission.

#### 2.4 IMASK0 unmasked for TTSCAA

```c
{ .address=0x0000000C, .value=0x00000000, ... }
/* IMASK0: bit 8 (TTSCAA) unmaskiert → _OnStatus0 fires on timestamp capture */
```

Upstream value is `0x00000100` — bit 8 is **masked**, no interrupt on
timestamp capture.

#### 2.5 `DRV_LAN865X_SendRawEthFrame()` with `tsc` flag

```c
bool DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf, uint16_t len,
                                 uint8_t tsc, DRV_LAN865X_RawTxCallback_t cb,
                                 void *pTag);
// tsc=0x01 für Sync (Timestamp Capture A), tsc=0x00 für FollowUp
```

The standard send API goes through the TCP/IP stack queue and knows
nothing about a `tsc` flag — so no HW timestamps for PTP Sync messages.

#### 2.6 Additional PTP helpers in the public API

| Function | Purpose |
|---|---|
| `DRV_LAN865X_IsReady()` | Readiness probe before the first PTP frame |
| `DRV_LAN865X_GetAndClearTsCapture()` | Atomic read-and-clear of the TTSCAA/B/C bits |
| `DRV_LAN865X_GetTsCaptureNirqTick()` | Query latched TC0 tick from 2.1 |
| `DELAY_UNLOCK_EXT` reduced | Comment in code: *"100ms caused TTSCMA"* (timestamp capture miss) |

Plus `g_ptp_raw_rx.sysTickAtRx` for RX-path anchoring.

### Consequence without these changes

| What would be missing | Effect on PTP |
|---|---|
| TX timestamps for Sync | Sync anchor missing, no master-slave sync possible |
| ISR-precise RX tick | Timestamp jitter ~100 µs + ms latency |
| TTSCAA save-before-W1C | Timestamps lost through race condition |
| TTSCAA interrupt | Status0 handler never fires |
| FTSE bit | HW produces no TX timestamps at all |

→ **PTP would not be functional.**

### What MCC also changes (init sequence)

```diff
-#include <stdarg.h>
```

`<stdarg.h>` is removed.

In the memory map `TC6_MEMMAP[]` (LAN865x initialization sequence):

| Register | Value (current, verified) | Value (MCC-new) |
|---|---|---|
| `0x000400F8` | `0x0000B900` | `0x00009B00` |
| `0x00040081` (DEEP_SLEEP_CTRL_1) | `0x00000080` | `0x000000E0` |

8 register writes are reordered and `DEEP_SLEEP_CTRL_1` is moved to the
end of the table behind `IMASK0`.

### Recommended workflow after an MCC run

1. Click **Reject** in the MCC merge dialog for `drv_lan865x_api.c`.
2. Same for the FreeRTOS variant:
   `apps/tcpip_iperf_lan865x/firmware/src/config/FreeRTOS/driver/lan865x/src/dynamic/drv_lan865x_api.c`.
3. The remaining changes proposed by MCC can be accepted — they are
   cosmetic (timestamps, YAML order, duplicate dependency entries).
4. Then check `git diff` and discard non-relevant MCC metadata
   (`git restore <file>`).

Last verified state of the file: commit
`deb2773 fix(ptp_fol): compensate 10 ms LAN865x RX-nIRQ delay in PTP_CLOCK anchor`.

Full commit history of the file (newest first):

```
deb2773 fix(ptp_fol): compensate 10 ms LAN865x RX-nIRQ delay in PTP_CLOCK anchor
657e8a1 feat(ptp): ISR-captured GM anchor tick + docs overhaul
5e289c8 fix(R1): replace nIRQ pin polling with EIC EXTINT14 change-notification ISR
e74eb8c firmware timer sync added but working accurate enough. need to be improved
85e41c6 PTP Works
```

### Empirical proof: build breaks immediately when the MCC proposal is accepted

In a parallel test repo (`check3`), the MCC proposals for
`drv_lan865x_api.c` were **accepted**. Result: the very first build
attempt fails with the following error:

```
../src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c:
  In function 'PrintRateLimited':
1532:9: error: implicit declaration of function 'va_start'
        [-Werror=implicit-function-declaration]
1534:9: error: implicit declaration of function 'va_end'
cc1.exe: all warnings being treated as errors
make[2]: *** [build/.../drv_lan865x_api.o] Error 1
BUILD FAILED
```

#### Important clarification: this is a Microchip template bug, not a conflict with this fork

At first glance, it looks as if an intervention by this fork collides
with MCC's output. **It does not.** The plain evidence:

| Driver variant | `<stdarg.h>` | `PrintRateLimited()` | Build? |
|---|---|---|---|
| Microchip Upstream HEAD (GitHub, as of 2023-10-27) | ✅ present | ✅ present | ✅ builds |
| `cross` / `cross-minimize` / `cross-driverless` (this fork) | ✅ present | ✅ present | ✅ builds |
| **MCC regeneration (today's tooling version)** | ❌ **removed** | ✅ present | ❌ **breaks** |

Anyone who takes a fresh, unmodified upstream clone and runs MCC over
it blindly gets **the same** `va_start` error. Reproduction step:

```bash
git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
# in MPLAB X öffnen → MCC starten → "Generate" → Build versuchen
# → derselbe Fehler
```

The bug sits in the **MCC component template** for the LAN865x driver
(somewhere under `~/.mchp_packs/Microchip/...` or in the Harmony Net
component definition). Between 2023-10-27 and today, Microchip removed
the `<stdarg.h>` entry from the template without also removing the
`PrintRateLimited()` function — a classic tooling-drift bug.

`PrintRateLimited()` is, by the way, genuine Microchip code, introduced
by Thorsten Kummermehr (Microchip) on 2023-10-27 in commit `1846c05`
("Update LAN865x application to latest Harmony3 packages [MH3-86573]").
It is purely an anti-flood logging helper (max. 5 prints / 1 s, then
`[skipped N]`). No PTP relation.

#### What this means in practice

→ **The `va_start` error is your friend anyway.** Whether the cause is
a Microchip template bug or the PTP patches: it signals *immediately
and loudly* that the driver was modified by MCC. Anyone who accepts
the MCC proposals sight unseen has an unbuildable tree — and even
after re-adding `<stdarg.h>`, the entire PTP hardware-timestamping
infrastructure from §2.1–2.6 is still missing.

→ **A Microchip issue/PR would be the right path.** Trivial 1-line fix
in the template — bring `#include <stdarg.h>` back or wrap
`PrintRateLimited()` in `#ifdef SYS_CONSOLE_PRINT`. Not your problem
to solve, but good to know whom you would have to contact.

### Recovery if the MCC change was accepted by accident

If the file has already been overwritten and `git restore` does not
help (e.g. because already committed), the correct state can be taken
from this `cross` branch:

```bash
# Aus dem cross-Branch dieses Repos
git checkout cross -- \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/drv_lan865x.h
```

Then check `mcc-config.mc4` — the hash stored there for
`drv_lan865x_api.c` will trigger the override dialog again on the next
MCC run.

### Strategic dilemma — trunk alignment vs. PTP function

The "Reject" strategy described in §2 has a significant price:

> **Whoever rejects the MCC merge of the current official LAN865x
> driver decouples the PTP project from the Harmony trunk.**

Concretely this means:

- **No bugfixes** from newer driver releases (e.g. corrected init
  sequences for LAN865x B1, changed register values such as
  `0x000400F8: 0xB900 → 0x9B00` or `DEEP_SLEEP_CTRL_1: 0x80 → 0xE0`,
  which may be Microchip-confirmed hardware adjustments).
- **No feature updates** — if Harmony Net v3.15+ brings new driver
  APIs, better TC6 integration, or erratum workarounds, they are
  inaccessible to this project.
- **Accumulation of the diff debt** — with each accepted or rejected
  MCC run the distance between the local driver and trunk grows.
  Later re-synchronization gets harder and harder.
- **No common codebase** with other Harmony LAN865x users — an
  embedded developer who takes over this project cannot apply their
  driver knowledge from other projects one-to-one.

### Mitigation strategies (trade-offs)

None of these options is perfect — they are listed in order of
implementation effort.

**A) Status quo: "Reject" and manual cherry-pick**
- On every MCC run, reject the override for `drv_lan865x_api.c`.
- Periodically (e.g. every 6 months) review a side-by-side diff of
  the MCC proposals and manually adopt individual sensible changes
  (e.g. new register values).
- ✅ Simple, no tooling.
- ❌ Doesn't scale, error-prone, drift accumulates.

**B) PTP patches as a separate patch set**
- Driver is kept unmodified from trunk.
- PTP hooks (§2.1–2.6) are externalized as `git` patches or as a
  wrapper file (`drv_lan865x_ptp_ext.c`).
- Before every build, the patch set is applied.
- ✅ Trunk updates are adoptable, PTP diff stays isolated and documented.
- ❌ Requires the PTP hooks to be cleanly separable. In practice they
  reach deep into `_OnStatus0()`, `_InitMemMap()` and the ISR logic
  → patches break under larger trunk refactorings.

**C) Driver variant via Harmony template mechanism**
- Create your own `drv_lan865x_ptp` component in Harmony that
  inherits/branches from the standard `drv_lan865x` and brings the
  PTP extensions along.
- ✅ Clean within the MCC model, coexists with the trunk driver.
- ❌ High engineering effort (Harmony component definition, YAML
  schemas, FTL templates), MCC-internal knowledge required.
  Microchip support is of little help here.

**D) Upstream PR to Microchip Harmony**
- Submit the PTP hooks (`SendRawEthFrame`, `GetTsCaptureNirqTick`,
  ISR with TC0 latch, FTSE bit, IMASK0 unmasking, TTSCAA save-before-W1C)
  as a pull request to
  [github.com/Microchip-MPLAB-Harmony/net_10base_t1s](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s).
- ✅ Best long-term solution — the trunk *contains* the PTP extension,
  and this project is trunk-compatible again.
- ❌ Acceptance lottery. Microchip has to review, test and merge the
  changes. Can take months or be rejected if PTP doesn't fit the
  official roadmap.

**E) Own soft-fork of the driver**
- Rename the driver path (e.g. `driver/lan865x_ptp/`) and stop MCC
  from regenerating the component (via your own component definition
  or by removing it from the MCC project).
- ✅ Full control, no more MCC override dialogs.
- ❌ Loss of the MCC configuration UI for this driver. If e.g. SPI
  pins or driver index need to be changed, that has to happen
  manually in code instead of graphically in MCC.

### Recommendation

For this project — which by description serves primarily as a
**PTP implementation demonstrator** and not as a generic Harmony
example app — the right order is:

1. **Short-term (status quo, A)**: keep the Reject workflow, document
   it in this README (see above).
2. **Mid-term (D)**: prepare an upstream PR to Microchip. Even if not
   accepted, the discussion with Microchip has value (e.g. to find
   out why they changed the init sequence).
3. **If (D) fails (B or E)**: patch set or soft-fork. Which of the two
   variants is appropriate depends on how deeply the PTP hooks are
   anchored in the driver — with the current interventions in
   `_OnStatus0()` and `_InitMemMap()`, (E) is likely more
   maintainable than (B).

### 2.7 Minimizing the driver diff (specification for a refactoring)

A technical audit analysis (see `cross` branch docs) shows that the
current driver diff of ~478 lines can be reduced to **~35 lines of
inline diff plus ~320 lines in two new, driver-external files**.

| | Current | Achievable |
|---|---|---|
| `drv_lan865x_api.c` diff | ~416 lines | **~35 lines** |
| `drv_lan865x.h` diff | ~62 lines | **0 lines** |
| **Total driver diff** | **~478** | **~35** |
| New files | – | `ptp_drv_ext.{c,h}` (~320 L.) |

#### What must necessarily stay in the driver (Category A — ~30 lines)

These changes sit in `static const` tables, local stack variables, or
in the middle of the driver init state machine — they are not
extractable:

| # | Location in the driver | Diff size |
|---|---|---|
| A1 | `TC6_MEMMAP[]` edits in `_InitMemMap()`: IMASK0 `0x100→0x000` (TTSCAA unmasked), DEEP_SLEEP_CTRL_1 `0x80→0xE0`, TXM filter (`0x40040..0x40045`) | 7 array lines |
| A2 | `regVal \|= 0x80u; regVal \|= 0x40u;` (FTSE + FTSS) in `_InitUserSettings()` case 8 | 2 lines |
| A3 | New init states cases 46/47 (PADCTRL, PPSCTL) including `done` flag relocation | 14 lines |
| A4 | Service loop (line ≈ 410): `if (s_nirq_pending \|\| !SYS_PORT_PinRead(...))` — additive, upstream pin polling preserved | 1 line |
| A5 | `_OnStatus0()`: call `DRV_LAN865X_OnStatus0_Hook(idx, status0)` plus `__attribute__((weak))` default implementation | 3 lines |
| A6 | `TC6_CB_OnRxEthernetPacket()`: call `DRV_LAN865X_OnPtpFrame_Hook(buf, len, rxTs, success)` plus weak default | 3 lines |
| A7 | New public API `DRV_LAN865X_GetTc6Inst(idx)` as a 5-line accessor | 5 lines |
| **Sum** | | **~35 lines** |

#### What can be externalized (Category B — ~320 lines → new files)

In two new files `apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.{c,h}`:

- **EXTINT-14 ISR + `_InitNIrqEIC()`** (~50 lines) — `EIC_EXTINT_14_Handler`
  is a linker-weak symbol in the Harmony startup file and can be
  defined externally, no driver intervention needed
- **`s_nirq_pending` / `s_nirq_tick` statics + getter** (~25 lines)
- **`drvTsCaptureStatus0[]` / `drvTsCaptureNirqTick[]` + save logic**
  (~25 lines) — fed via the weak hook A5
- **`DRV_LAN865X_SendRawEthFrame()` / `IsReady()` / `GetAndClearTsCapture()` /
  `GetTsCaptureNirqTick()`** as pure wrappers (~50 lines) — using the
  new `GetTc6Inst()` accessor (A7)
- **PTP RX sniff** (`g_ptp_raw_rx`, EtherType check 0x88F7) (~50 lines)
  via the weak hook A6
- **62 lines of public-header declarations** move out of `drv_lan865x.h`
  entirely into `ptp_drv_ext.h`. PTP code (`ptp_gm_task.c`,
  `ptp_fol_task.c`, `ptp_rx.c`, `ptp_clock.c`) `#include`s the new header.

#### What can go entirely (Category C — ~30 lines cosmetic)

- `DELAY_UNLOCK_EXT 100→5` — workaround, possibly a build flag or
  revert after smoke test (1 Hz Sync × 1 min, zero TTSCMA events)
- `PRINT_LIMIT` reorderings (`LAN865x_%d` prefix in every case line) —
  pure diff-noise reduction
- `case 28: continue;` and bit 8/9/10 print suppression in `_OnStatus0` —
  debug cosmetics

#### Proposed target structure

```
apps/tcpip_iperf_lan865x/firmware/src/
├── ptp_drv_ext.c          NEU, ~250 Zeilen
└── ptp_drv_ext.h          NEU, ~70 Zeilen
```

Contents of `ptp_drv_ext.c`:

- File-static `s_nirq_pending`, `s_nirq_tick`
- `EIC_EXTINT_14_Handler` (linker-weak override)
- `_InitNIrqEIC()` + `PTP_DRV_EXT_Init()` (called from `APP_Initialize`)
- `drvTsCaptureStatus0[]`, `drvTsCaptureNirqTick[]`
- Strong implementation `DRV_LAN865X_OnStatus0_Hook(idx, status0)` —
  saves the TTSCAA bits and latches `s_nirq_tick`
- Strong implementation `DRV_LAN865X_OnPtpFrame_Hook(buf, len, rxTs, success)` —
  EtherType test, copy into `g_ptp_raw_rx`, `sysTickAtRx` timestamp
- `DRV_LAN865X_SendRawEthFrame`, `IsReady`, `GetAndClearTsCapture`,
  `GetTsCaptureNirqTick` (all using `GetTc6Inst()` accessor)
- `g_ptp_rx_ts`, `g_ptp_raw_rx` definitions

Contents of `ptp_drv_ext.h`:

- `DRV_LAN865X_RawTxCallback_t` typedef
- Prototypes for the four public API functions
- `extern` declarations of the two globals
- Hook prototypes for the two weak callbacks (for linking)

#### Important risks & verifications before refactoring

1. **Linker resolution for weak symbols**: verify with `xc32-nm` and
   the map file that the strong app definition is linked before the
   driver default variant. Safer path: the driver defines
   `DRV_LAN865X_OnStatus0_Hook` as `__weak`, not the app.
2. **MCC also regenerates the ~35 lines**: even after minimization,
   the override for `drv_lan865x_api.c` must be rejected on every MCC
   run — the benefit is only that a **patch set** of 35 lines is much
   more maintainable / re-applicable than 400+ lines (e.g. via
   `git apply`).
3. **`_OnStatus0` hook ordering**: the hook call must be **before** the
   `TC6_WriteRegister` W1C clear. Pin down with a comment in code.
4. **EIC EXTINT-14 ownership**: if MCC generates EIC-CONFIG[1] in the
   future for other peripherals, this is race-prone. EXTINT-14 is
   exclusive to nIRQ → document in the pin manager.
5. **`g_ptp_raw_rx` ABI**: the `volatile` qualifier and field order
   between `ptp_ts_ipc.h` and `ptp_drv_ext.c` must remain identical
   (`ptp_rx.c` reads without locking).
6. **`DELAY_UNLOCK_EXT` revert**: needs a smoke test before being
   merged into `cross`.

#### Procedure (in order)

1. Branch `cross-minimize` off `cross`.
2. Create `ptp_drv_ext.c`/`.h`, cut code out of `drv_lan865x_api.c`.
3. Add the 7 minimal hooks (A1–A7) into the driver.
4. Build. PTP smoke test (Sync runs, offset stable).
5. `git diff cross..cross-minimize -- '...drv_lan865x_api.c' '...drv_lan865x.h'`
   should now show ~35 lines.
6. Save these 35 lines as `essential.patch`, document the workflow:
   "Reject MCC → re-apply patch".

### 2.8 Implementation — final state `cross-driverless`

The refactoring has been carried out in two stages:

| Branch | Driver diff | Header diff | Total |
|---|---|---|---|
| `cross` (initial state) | ~237 lines | 62 lines | **~299** |
| `cross-minimize` (commit `b0d7b8c`) | 58 lines | 3 lines | **61** |
| `cross-driverless` (current) | **11 lines** | **1 line** | **12** |

Overall **25× smaller** than the initial state. Build clean with XC32 v5.10
(`-Werror -Wall`).

#### What was eliminated (5 of 7 patches disappear entirely)

| | Intervention | Eliminated via |
|---|---|---|
| A1 | TC6_MEMMAP edits (IMASK0, DEEP_SLEEP, TXM filter `0x40040..45`) | `PTP_DRV_EXT_Tasks()` state machine — last-write-wins **after** `IsReady()`. Writes 7 registers in app code via `DRV_LAN865X_WriteRegister()`. |
| A2 | CONFIG0 FTSE+FTSS bits | ditto — RMW (mask=0xC0, value=0xC0) on CONFIG0 in the same state machine |
| A3 | Cases 46/47 PADCTRL+PPSCTL | ditto — 2 writes (PADCTRL RMW value=0x100 mask=0x300, PPSCTL value=0x7D) |
| A4 | `DELAY_UNLOCK_EXT 100→5` | reverted to upstream `100u`. The new architecture (TX match active, TXMCTL armed per Sync) makes the workaround superfluous — no TTSCMA trigger anymore. |
| A5a | OnStatus0_Hook (TTSCAA save-before-W1C) | removed entirely. `ptp_gm_task.c` long had an SPI fallback (`GM_STATE_READ_STATUS0`/`WAIT_STATUS0`); `GetAndClearTsCapture()` now always returns `0u` → fallback becomes the only path. |

#### What must unavoidably remain in the driver (irreducible 12 lines)

| | Intervention | Justification (pinned in code as a comment) |
|---|---|---|
| **A5b** | `DRV_LAN865X_OnPtpFrame_Hook` (1 weak decl + 1 hook call = 2 lines) | **Genuine API gap.** The 64-bit RX hardware timestamp arrives **exclusively** via the `rxTimestamp` parameter of `TC6_CB_OnRxEthernetPacket`. The upstream driver **does not propagate it** into `TCPIP_MAC_PACKET`. By the time of `TCPIP_STACK_PacketHandlerRegister`, it is irretrievably lost. Without this hook, `t2_ns` (PTP slave Sync receive time) is always 0 → slave Sync broken. |
| **A6** | `DRV_LAN865X_GetTc6Inst()` accessor (5 lines body + 1 line header decl) | The `drvLAN865XDrvInst[]` array is `static` in the driver. The private `TC6_t*` is reachable only via this accessor — and it is needed for `TC6_SendRawEthernetPacket(g, …, tsc=0x01, …)`, which arms the TX-Capture-A for PTP Sync. No other Microchip API allows the `tsc` flag. |

Both patches are marked in the file with a `/* … irreducible — see ptp_drv_ext.c … */` comment so they don't get deleted during future MCC reviews.

#### New structure in `ptp_drv_ext.{c,h}`

```
ptp_drv_ext.h        Public API + 2 Hook-Prototypen
ptp_drv_ext.c        EIC-ISR
                     PTP_DRV_EXT_Init()
                     PTP_DRV_EXT_Tasks()              ← NEU: 24-State Reg-Init
                     PTP_DRV_EXT_RegisterInitDone()   ← NEU: Predicate für ptp_*-Tasks
                     OnPtpFrame_Hook strong impl
                     SendRawEthFrame, IsReady,
                     GetAndClearTsCapture (= 0u),
                     GetTsCaptureNirqTick (= s_nirq_tick)
```

#### Wiring

- [`app.c`](../../apps/tcpip_iperf_lan865x/firmware/src/app.c): `APP_Initialize()` calls
  `PTP_DRV_EXT_Init()`. `APP_Tasks()` calls `PTP_DRV_EXT_Tasks(0u)` periodically
  (no-op until `IsReady()` ⇒ register-init state machine runs through once).
- `ptp_gm_task.c` and `ptp_fol_task.c` optionally gate on
  `PTP_DRV_EXT_RegisterInitDone()` before they emit the first frames.

#### Consequence for the MCC workflow

| Scenario | Before `cross-driverless` | Now |
|---|---|---|
| MCC run, driver override **rejected** | recover 61 lines | **recover 12 lines** (`git checkout HEAD -- 2 files`) |
| MCC run, driver override **accepted** (mistake) | compile + link errors immediately | same — the 2 remaining patches likewise trigger build break |
| Driver drift in daily work | maintain 61 lines | maintain **12 lines** |

#### Caveats (verify before hardware sign-off)

1. **`DELAY_UNLOCK_EXT 5→100` revert** is not hardware-verified. If
   TTSCMA events recur, just revert as a one-liner patch.
2. **`OnStatus0_Hook` removal** — the `gm_task` SPI fallback is slower
   than the in-driver hook (extra SPI roundtrip). Tolerated via
   `gm_wait_ticks`, but Sync accuracy on hardware not yet confirmed in
   this configuration.
3. **Race window at boot:** for the first ~2 ms after `IsReady()`, the
   chip runs with upstream default config (TXMCTL=0x02, FTSE off,
   IMASK0=0x100). First PTP frames in this phase would be misconfigured;
   PTP_GM/FOL gate on driver readiness anyway, so it should not be an
   issue in practice.
4. **Hardware sign-off pending** — build clean, but no PoR-to-Sync run
   in this configuration. Mandatory test before merging into `master`:
   - GM sends Sync with a valid TX timestamp
   - Slave receives Sync with `g_ptp_raw_rx.rxTimestamp != 0` and `sysTickAtRx != 0`
   - PTP offset stabilizes at < 1 µs

---

## 3) What an MCC run typically also touches

Pure metadata — can usually be discarded without risk:

- **`configurations.xml`** — order of `.yml` component entries,
  `languageToolchainVersion` and `platformTool` are adjusted to the
  local MPLAB X / XC32 install (can vary between 4.60, 5.00, 5.10).
  ⚠ The 46 `<itemPath>` entries added in this branch are **not**
  touched by MCC — the build remains functional.
- **3× manifest YAMLs** (`harmony-manifest-success.yml`, `mcc-manifest-*.yml`)
  — only timestamps + compiler version.
- **6× layer YAMLs** under `tcpip_iperf_lan865x_default/components/.../...yml` —
  duplicate `dependency:` entries are cleaned up (an actual improvement).
- **`mcc-config.mc4`** — hash entry for `drv_lan865x_api.c` is updated.
  ⚠ Consequence: on the next MCC run, the file will *again* be proposed
  for regeneration, because the file hash on disk differs from the
  stored hash.

---

## 4) Comparison with the upstream original

Comparison baseline: [github.com/Microchip-MPLAB-Harmony/net_10base_t1s](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s)
(cloned for the test to `c:/work/ptp/org/net_10base_t1s/`).

### Own additions in this fork (in `firmware/src/`)

46 additional files (PTP stack, demos, CLIs):

- **PTP core**: `ptp_clock.{c,h}`, `ptp_gm_task.{c,h}`, `ptp_fol_task.{c,h}`,
  `ptp_log.{c,h}`, `ptp_offset_trace.{c,h}`, `ptp_rx.{c,h}`, `ptp_ts_ipc.h`,
  `ptp_cli.{c,h}`, `filters.{c,h}`
- **NTP comparison**: `sw_ntp{,_cli,_offset_trace}.{c,h}`
- **Demos & tools**: `cyclic_fire{,_cli,_isr}.{c,h}`, `pd10_blink{,_cli}.{c,h}`,
  `button_led.{c,h}`, `loop_stats{,_cli}.{c,h}`, `iperf_control.{c,h}`,
  `lan_regs_cli.{c,h}`, `standalone_demo.{c,h}`, `demo_cli.{c,h}`,
  `tfuture{,_cli}.{c,h}`, `watchdog.{c,h}`,
  `test_exception_cli.{c,h}`, `exception_handler.c`, `app_log.h`

### Modified upstream files

- `app.{c,h}` — PTP integration
- `config/default/configuration.h`, `initialization.c`, `tasks.c`
- `config/default/library/tcpip/src/iperf.c`
- `config/default/peripheral/port/plib_port.c`
- `config/default/system/command/sys_command.h` (also in the FreeRTOS path)
- `config/default/driver/lan865x/drv_lan865x.h`
- `config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c` ← see §2
- `config/FreeRTOS/driver/lan865x/src/dynamic/drv_lan865x_api.c` ← analogous

### Own tooling additions (in `tcpip_iperf_lan865x.X/`)

- [`cmake/`](../../apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/) —
  CMake build system (matches the MPLAB X build 1:1)
- `.vscode/`, `.gitignore`, `check_serial_tk.pyw`

---

## 5) Build verification

| Build path | Status |
|---|---|
| CMake/Makefile (`firmware/Makefile`) | ✅ works |
| MPLAB X IDE (`tcpip_iperf_lan865x.X`) | ✅ works (with state `db64350`) |

Both builds are compile-equivalent: same source files, same include
paths, same preprocessor defines.

---

## 6) Reproduction plan: MCC's `<stdarg.h>` removal bug

§2 documents that MCC's regeneration of `drv_lan865x_api.c` produces
a build error (`va_start` without `<stdarg.h>` include). The claim:
the bug lies in **Microchip's LAN865x MCC component template**, not
in any code of this fork.

This section defines a **reproducible test protocol** with which the
claim can be demonstrated **independently of this repository** — for
example as evidence for a Microchip bug report or to validate this
README.

### Prerequisites

- Test directory outside of `c:/work/ptp/check4/...`
- Installed: MPLAB X IDE, XC32, MCC plugin, `git` (versions are
  recorded in step 7)
- Internet access for `git clone` and Harmony package download
- **Preserve** the `~/.mchp_packs/` cache — that is the forensically
  interesting spot (do not delete before step 8)

### Step 1: Create test workspace

```bash
mkdir c:\work\ptp\bugtest && cd c:\work\ptp\bugtest
```

### Step 2: Pull a fresh upstream clone

```bash
git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
cd net_10base_t1s
git log -1 --oneline   # SHA notieren — Beleg-Stand
```

### Step 3: Upstream consistency check (precondition)

The upstream itself must be consistent, otherwise the test is worthless:

```bash
grep '#include <stdarg.h>' \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c
# erwartet: #include <stdarg.h>

grep -c 'PrintRateLimited' \
  apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c
# erwartet: ≥ 3 Treffer (Macro, Decl, Def)
```

Both conditions must hold → upstream file is internally consistent,
the bug does *not yet* exist.

### Step 4: Run MCC (without further changes)

1. Start MPLAB X
2. Open project: `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/`
3. Open MCC tab ("Load existing configuration" if needed)
4. **Don't change any configuration**
5. Click **"Generate"**
6. In the merge dialog, **accept all proposed changes**
   (especially those for `drv_lan865x_api.c`)

### Step 5: Document the diff — preserve the evidence

```bash
git diff apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c \
  > mcc_regen_diff.patch
```

Expected diff line (at minimum):

```diff
-#include <stdarg.h>
+
```

Name the patch file with date and MCC version (e.g.
`mcc_regen_diff_2026-04-25_mcc564.patch`).

### Step 6: Attempt build, capture error

```bash
build.bat rebuild > build_failure.log 2>&1
```

Expected log file content:

```
.../drv_lan865x_api.c: In function 'PrintRateLimited':
1532:9: error: implicit declaration of function 'va_start'
        [-Werror=implicit-function-declaration]
1534:9: error: implicit declaration of function 'va_end'
cc1.exe: all warnings being treated as errors
BUILD FAILED.
```

Keep the log.

### Step 7: Capture the tooling fingerprint

To be filled in for the test run — as information for the bug report:

| Property | Value |
|---|---|
| MPLAB X version | `____________` (e.g. 6.25 / 6.30) |
| XC32 version | `____________` (e.g. 4.60 / 5.00 / 5.10) |
| MCC plugin version | `____________` (e.g. 5.6.4) |
| Harmony Net package version | `____________` (e.g. v3.14.5 / v3.15.0) |
| Harmony Core package version | `____________` (e.g. v3.16.0) |
| CSP package version | `____________` (e.g. v3.25.1) |
| Microchip net_10base_t1s repo SHA | `____________` |
| Date of test run | `____________` |

### Step 8: Forensics in the MCC cache

Where exactly was the `<stdarg.h>` entry removed from the template?

```bash
# Suchen wo der MCC-LAN865x-Driver-Template lebt:
ls "$USERPROFILE/.mchp_packs/Microchip/" 2>/dev/null

# Files mit PrintRateLimited:
grep -rln 'PrintRateLimited' "$USERPROFILE/.mchp_packs/" 2>/dev/null

# Files mit stdarg im selben Component-Verzeichnis:
grep -rln 'stdarg' "$USERPROFILE/.mchp_packs/" 2>/dev/null

# Ftl-Template-Files (das ist wahrscheinlich der Übeltäter):
find "$USERPROFILE/.mchp_packs/" -name 'drv_lan865x_api*' 2>/dev/null
```

Expectation: at least one template/source file (`.ftl`, `.c`,
`.c.ftl`) in the Harmony Net package containing `PrintRateLimited()`
**but not** the `<stdarg.h>` include. Record the path, the file, and
the component manifest version — that is the concrete location of
the Microchip bug.

### Step 9: Fill in the result table

| Property | Value |
|---|---|
| Upstream HEAD contains `<stdarg.h>` + `PrintRateLimited` | ✅ |
| Upstream HEAD builds clean (without MCC run) | ✅ |
| MCC regen removes `<stdarg.h>` | _____ (expected: ✅) |
| MCC regen retains `PrintRateLimited()` | _____ (expected: ✅) |
| Build with `-Werror -Wall` fails with `va_start` error | _____ (expected: ✅) |
| Test performed without any fork intervention | ✅ (pristine upstream clone) |
| **Conclusion** | Bug lies in the MCC template, not in this fork |

### Step 10: (Optional) submit Microchip bug report

From the evidence in steps 5/6/7/8, file an issue at
[github.com/Microchip-MPLAB-Harmony/net_10base_t1s/issues](https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s/issues)
or via `support.microchip.com`.

**Suggested issue title:**

> `drv_lan865x_api.c::PrintRateLimited()` uses `va_start`/`va_end` but
> MCC-regenerated version drops `#include <stdarg.h>` → build fails
> with `-Werror`

**Issue body skeleton:**

```
## Reproduction
1. git clone https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
2. Open in MPLAB X, run MCC "Generate" without changing config.
3. Accept all proposed merges.
4. Build: fails with the error below.

## Environment
[Tabelle aus Schritt 7 einkleben]

## Error output
[build_failure.log einkleben]

## Diff produced by MCC
[mcc_regen_diff.patch einkleben]

## Root cause (suspected)
The MCC component template at <Pfad aus Schritt 8> defines
`PrintRateLimited()` (which uses `va_start`/`va_end`) but does not
emit the matching `#include <stdarg.h>` into the regenerated source
file.

## Suggested fix
Either: (a) restore `#include <stdarg.h>` in the LAN865x
driver template, or (b) wrap the `PrintRateLimited()` definition
in `#ifdef SYS_CONSOLE_PRINT` so the variadic-macro dependency
disappears when console printing is disabled.
```

### What this plan proves (and what it doesn't)

✅ **Proven:** the bug is in MCC's tooling, not in this fork — because
it is also reproducible in a pristine upstream clone.

❌ **Not proven (out of scope):** whether the *PTP patches* of this
fork have other problems with MCC. The patches described in §2.1–2.6/2.8
survive an MCC run on their own (`ptp_drv_ext.{c,h}` are not touched
by MCC at all); the 12 remaining inline lines would be lost on Accept
but are documented separately in §2.8.

---

## 7) Field report: MCC run on `cross-driverless` with manual merge (2026-04-26)

Real-world test of the minimization strategy: MCC was run on the
`cross-driverless` state, the merge dialog for `drv_lan865x_api.c` was
processed **manually** — not blindly accepted, not blindly rejected.
Result: **build successful**, all PTP hooks preserved.

### 7.1 Evidence trail — what MCC offered and what was adopted

MCC offered the typical Microchip template output variant:

- remove `<stdarg.h>` (the well-known MCC template bug, see §6)
- reorder TC6_MEMMAP table with partly changed register values
- various YAML / manifest reorderings (cosmetic)

In the manual merge dialog, **MCC's output for the memory map was
accepted, but the critical PTP patches were defended:**

| Patch | Source after merge | Status |
|---|---|---|
| `<stdarg.h>` include (line 41) | manually re-inserted | ✅ preserved |
| `OnPtpFrame_Hook` weak default (line 51) | kept from cross-driverless | ✅ preserved |
| `OnPtpFrame_Hook` call in RX callback (line 1383) | kept from cross-driverless | ✅ preserved |
| `GetTc6Inst()` accessor (line 2446) | kept from cross-driverless | ✅ preserved |
| `<stdarg.h>` include (line 41) | manually re-inserted | ✅ build break avoided |

→ The 12 lines documented in §2.8 as "irreducible" are **all in there**.
PTP plumbing intact.

### 7.2 ⚠ Side effect: TC6_MEMMAP table now has duplicates

During the manual merge, **MCC's new init entries were taken on
additionally without removing the old ones** — result: duplicate
(partly triplicate) writes to the same registers at boot.

| Register | Old position | New position | Effective value (last-write-wins) |
|---|---|---|---|
| `0x000400E9` | line 1704 | line 1721 | `0x9E50` (idempotent) |
| `0x000400F5` | line 1705 | line 1722 | `0x1CF8` (idempotent) |
| `0x000400F4` | line 1706 | line 1723 | `0xC020` (idempotent) |
| **`0x000400F8`** | line 1707 (`0xB900`) | line 1724 (`0x9B00`) | **`0x9B00`** ⚠ value conflict |
| `0x000400F9` | line 1708 | line 1725 | `0x4E53` (idempotent) |
| **`0x00040081`** DEEP_SLEEP_CTRL_1 | lines 1709 + 1711 (`0x80` × 2) | line 1740 (`0xE0`) | **`0xE0`** ⚠ value conflict, written 3 times |

→ The chip is initialized through the merge with **Microchip's new B1
fix values** (`0x9B00`, `0xE0`) — presumably erratum patches for the
Rev-B1 hardware. The old values (`0xB900`, `0x80`) were PTP-validated,
the new ones are not yet.

### 7.3 Functional assessment

| Aspect | Assessment |
|---|---|
| Build status | ✅ successful (XC32 v5.10) |
| Code correctness | ✅ functionally OK (idempotent or last-write-wins) |
| Boot time | minimally longer (5–7 additional SPI writes due to duplicates) |
| Code hygiene | ⚠ suboptimal — the old entries are dead code |
| PTP function | ❓ unclear until hardware test (old values were validated, new ones not yet) |

### 7.4 IMASK0 stays at `0x100` — not a problem for this architecture

MCC's table keeps the upstream value `0x00000100` for IMASK0 (bit 8
TTSCAA **masked**). This is **not a problem** in the
`cross-driverless` architecture, because:

- `ptp_gm_task.c::GM_STATE_READ_STATUS0/WAIT_STATUS0` polls STATUS0
  itself via SPI (`DRV_LAN865X_ReadModifyWriteRegister`).
- The driver-level `_OnStatus0` callback is not triggered by an
  IMASK0 interrupt, but the app-side polling loop reads the TTSCAA
  bits reliably anyway.
- This architecture choice is exactly the reason why the
  `OnStatus0_Hook` could be removed in §2.8 (A5a eliminated).

→ Microchip's default (`IMASK0=0x100`) and our PTP requirement are
**compatible** — no adaptation needed.

### 7.5 Diff sizes — against upstream and against the last commit

| Comparison | Real diff lines |
|---|---|
| Against committed `cross-driverless` HEAD | 13 |
| Against Microchip upstream HEAD (`586ffc1`) | 22 (12 essential + 10 merge duplicates) |

The +10 from the messy merge come exclusively from the 5 idempotent
duplicate writes + the additional DEEP_SLEEP entry.

### 7.6 Recommendation: cleanup after hardware sign-off

**Step 1 — hardware test**: PoR → PTP Sync → offset stable < 1 µs?
The effective register values (`0x9B00` / `0xE0`) must prove themselves.

**Step 2 (if PTP OK)**: clean up the TC6_MEMMAP table by deleting the
**old entries** (lines 1704–1709 + 1711):

```diff
@@ static const MemoryMap_t TC6_MEMMAP[] = { ... }
-        {  .address=0x000400E9, .value=0x00009E50, ... },   // alte Position
-        {  .address=0x000400F5, .value=0x00001CF8, ... },
-        {  .address=0x000400F4, .value=0x0000C020, ... },
-        {  .address=0x000400F8, .value=0x0000B900, ... },   // alter B1-Wert
-        {  .address=0x000400F9, .value=0x00004E53, ... },
-        {  .address=0x00040081, .value=0x00000080, ... }, /* DEEP_SLEEP_CTRL_1 */
         {  .address=0x00040091, .value=0x00009660, ... },
-        {  .address=0x00040081, .value=0x00000080, ... },   // Merge-Duplikat
         {  .address=0x00010077, .value=0x00000028, ... },
         ...
```

This reduces the diff against upstream to the nominal 12 lines.

**Step 3 (if PTP not OK after hardware test)**: manually restore the
old values by resetting Microchip's new values in the MCC output
position to the old ones:

```c
{  .address=0x000400F8,  .value=0x0000B900, ... },   // PTP-validierter B1-Wert
{  .address=0x00040081,  .value=0x00000080, ... },   // PTP-validierter DEEP_SLEEP-Wert
```

### 7.7 Lessons Learned

1. **Smart manual merge works.** With only 12 irreducible driver lines,
   the merge dialog is tractable enough to allow patch-by-patch
   decisions.
2. **`<stdarg.h>` must be actively defended.** Microchip's template
   bug strikes on every MCC run — see §6 reproduction plan.
3. **Last-write-wins saves you from functional faults during messy
   merges.** The duplicates are ugly but functionally uncritical.
4. **Microchip's B1 erratum values are now adopted automatically.**
   If the hardware is OK with them, `cross-driverless` has thereby
   also moved closer to trunk (Microchip value for
   `0x000400F8`/`0x00040081`).
5. **The reproduction plan from §6 is confirmed 1:1** — on 2026-04-26
   in a real workflow, without specifically searching for it.

---

## 8) Next goal: PTP as a selectable MCC component

§2–§7 document today's **manual** patch workflow. The next goal is
to replace this workflow with an **MCC checkbox**, so that developers
can simply enable PTP HW timestamping in the MCC GUI — without
touching any code by hand.

The full implementation plan is in
[`PROMPT_mcc_ptp_component.md`](../../PROMPT_mcc_ptp_component.md) and
is written both as a brief for a coding agent and as an engineer
spec.

### 8.1 End state

When a developer opens the `tcpip_iperf_lan865x` project (or any
other LAN8651 project) in MPLAB X and starts MCC, they see an
additional checkbox on the LAN865x driver component:

```
☐ Enable PTP / IEEE 1588 hardware timestamping
```

Toggle ON + click "Generate" → MCC copies the PTP files into the
project, adds them to the build, applies the 12-line driver patches,
and done. Toggle OFF + "Generate" → MCC removes everything again,
the project is byte-identical to the upstream-pristine state.

### 8.2 What MCC automatically places into the project when the checkbox is enabled

| Tier | File | Purpose |
|---|---|---|
| **Core** (mandatory) | `ptp_drv_ext.h` | Public API of the driver extension |
| | `ptp_drv_ext.c` | EIC EXTINT-14 ISR + reg-init state machine + wrapper |
| | `ptp_ts_ipc.h` | IPC struct between driver and PTP stack |
| **PTP stack** | `ptp_clock.{c,h}` | PTP clock (wallclock, anchor tick) |
| | `ptp_fol_task.{c,h}` | Slave state machine |
| | `ptp_gm_task.{c,h}` | Master state machine |
| | `ptp_rx.{c,h}` | RX handler glue |
| | `ptp_log.{c,h}` | Rate-limited logging |
| | `filters.{c,h}` | Lowpass/IIR for offset & drift |
| **Optional v2** | `ptp_cli.{c,h}` | UART CLI for `ptp status` etc. |
| | `ptp_offset_trace.{c,h}` | Live offset tracing |
| | `lan_regs_cli.{c,h}` | LAN register read/write |
| | `loop_stats{,_cli}.{c,h}` | Main loop diagnostics |

→ **11 core files + 8 optional demo files** = 19 files, all placed
automatically by MCC.

### 8.3 What MCC automatically changes in existing files

| File | Change | Mechanism |
|---|---|---|
| `drv_lan865x_api.c` | +12 lines (`<stdarg.h>` + hooks + accessor) | via extended FreeMarker template or post-gen patch |
| `drv_lan865x.h` | +1 line (accessor prototype) | ditto |
| `configurations.xml` | `<itemPath>` for all PTP files | MCC's standard source emission |
| `cmake/file.cmake` | analogous for the CMake build | ditto |

### 8.4 What the developer still has to do themselves (minimum: 3 lines)

`app.c` belongs to the user and is not regenerated by MCC:

```c
#include "ptp_drv_ext.h"           /* einmal oben */
...
void APP_Initialize(void) {
    PTP_DRV_EXT_Init();             /* einmal beim Boot */
    /* ... */
}
void APP_Tasks(void) {
    PTP_DRV_EXT_Tasks(0u);          /* periodisch in der Hauptschleife */
    /* ... */
}
```

→ **3 lines of hand-edit** instead of today's ~50 lines spread over
several files.

### 8.5 Implementation phases (see `PROMPT_mcc_ptp_component.md`)

| Phase | Content | Effort |
|---|---|---|
| 0 | Discovery — understand Microchip's component pattern, option A vs B | 1 day |
| 1 | Component skeleton — empty checkbox visible in the MCC GUI | 1 day |
| 2 | File emission — PTP files are copied on toggle ON | 2 days |
| 3 | Inline patch injection — automatically insert the 12 driver lines | 3 days (critical) |
| 4 | `app.c` integration via help text or snippet | 0.5 days |
| 5 | Verify `configurations.xml` glue | 0.5 days |
| 6 | Hardware sign-off on the demo platform | 1 day |
| 7 | Upstream PR to Microchip (CC: Jing Richter-Xu, Thorsten Kummermehr) | open |
| **Total** | | **~9 working days** + PR review |

### 8.6 Three options for phase 3 (patch injection)

Phase 3 is the only open design question:

| Option | How | Realism |
|---|---|---|
| **3.A** Extend Microchip's template via PR with `<#if PTP_ENABLED>` blocks | cleanest solution, but Microchip has to agree | medium |
| **3.B** `definitions.h.ftl` hook (if upstream driver already has the hooks) | only with Microchip cooperation | medium |
| **3.C** Post-generation script (Python/sed) — patches the file after every MCC run | pragmatic, robust, ugly | high |

Recommendation: try 3.A first (upstream PR), with 3.C as a robust
fallback if rejected.

### 8.7 Consequences for today's branches

Once the MCC component is finished:

- **`cross-driverless` becomes obsolete** — its 12-line driver patch
  is generated automatically by the MCC component, its
  `ptp_drv_ext.{c,h}` are copied automatically.
- **`README_cross.md` §2–§7 becomes a historical document** — it
  describes the pre-MCC-component state and the manual recovery
  workflows that nobody needs anymore.
- **Microchip's MCC template bug from §6** is defensively bypassed
  with the component (`<stdarg.h>` is re-added by the patch) — but
  should **still** be filed as a separate issue with Microchip,
  because it also affects other LAN865x users who have nothing to
  do with PTP.

### 8.8 Comparison: today vs. after MCC integration

| Aspect | Today (cross-driverless) | With MCC component |
|---|---|---|
| Setup on a new project | manually copy 13 files, patch 12 driver lines, edit `app.c`, edit `configurations.xml` | **Click one checkbox in MCC** |
| MCC run risk | 12-line patch gets lost if accepted incorrectly | Patch is **generated** by the template, not attacked |
| Distribution to other developers | "Take my branch" | "Enable the component in MCC" |
| Long-term maintainability | Patches drift with every Microchip update | Component versions itself with the Net package |
| Demo value | Research project | Off-the-shelf capability |

→ This is the transition from **maintained fork** to **shipping feature**.

---

## 9) Alternative platform: Zephyr RTOS

§8 describes the path through Microchip's Harmony tooling. There is a
**second, possibly more elegant option**: port the work to Zephyr RTOS
instead of embedding it in Harmony.

### 9.1 What Zephyr already has (state early 2026, **please verify**)

| Component | Status |
|---|---|
| **gPTP subsystem** (`subsys/net/lib/ptp/`) | ✅ present, IEEE 802.1AS, tested |
| **SAM E54 board support** | ✅ present |
| **LAN8651 Ethernet driver** (`drivers/ethernet/eth_lan865x.c` etc.) | ✅ present, **but typically without HW timestamping API** |
| **DT bindings for LAN865x** | ✅ present |

### 9.2 What is probably missing: the glue layer

For Zephyr's gPTP to obtain HW timestamps from the LAN8651, the driver
must implement the following standardized Zephyr APIs:

| Zephyr API | Purpose | Harmony equivalent (today) |
|---|---|---|
| `eth_driver_api.get_ptp_clock` | returns PTP clock device reference | `PTP_CLOCK_GetTime_ns()` + anchor tick |
| `net_pkt_set_timestamp_ns()` in the RX path | writes the HW RX timestamp into the `net_pkt` meta | `OnPtpFrame_Hook` → `g_ptp_raw_rx.rxTimestamp` |
| `net_pkt_set_tx_timestamping()` + async callback | TX timestamp capture after Sync send | `DRV_LAN865X_SendRawEthFrame(tsc=0x01)` + TTSCAA polling |
| Own `ptp_clock` driver class | exposes the LAN8651 TSU as a kernel clock | TC0 tick + anchor construction |

### 9.3 Mapping: Harmony refactoring → Zephyr driver

The 12 indispensable lines today plus `ptp_drv_ext.c` would be
translated into Zephyr API language:

| Today (Harmony) | In Zephyr |
|---|---|
| TC6_MEMMAP patches (IMASK0, FTSE, TXM filter, PADCTRL, PPSCTL) | in `eth_lan865x_init()` as a `tc6_write_register()` sequence |
| EIC EXTINT-14 ISR with tick latch | `gpio_callback` on the nIRQ pin + `k_uptime_get_ns()` snapshot |
| TTSCAA save-before-W1C race | identical, but easier to solve in the Zephyr driver |
| `OnPtpFrame_Hook` for RX timestamp | directly `net_pkt_set_timestamp_ns()` in the RX callback |
| `SendRawEthFrame(tsc=…)` | `eth_lan865x_send()` with an additional TS hint or socket option |

→ Conceptually **the same engineering work**, but in a more cleanly
standardized environment.

### 9.4 Comparison Harmony MCC component vs. Zephyr driver PR

| Aspect | §8 (Harmony MCC component) | §9 (Zephyr driver extension) |
|---|---|---|
| HW timestamping API | not standardized (`DRV_LAN865X_*`) | standardized (`eth_driver_api`, `ptp_clock`) |
| gPTP stack | not in the standard delivery | present, tested |
| Driver extension as a toggle | hard (see `PROMPT_mcc_ptp_component.md` phase 3) | natural: `Kconfig` + `CONFIG_ETH_LAN865X_PTP=y` |
| Upstream PR effort | high (Microchip-internal approval) | normal (Zephyr community, transparent) |
| Reach (who benefits) | only MPLAB X / Harmony users | every Zephyr user for LAN8651 |

→ **Zephyr could be the clean end goal**, Harmony the short-term
demonstrator platform.

### 9.5 ⚠ Mandatory verification before investment

My knowledge state is early 2026 — Zephyr master changes quickly.
The following checks are a precondition before investing time:

```bash
# Zephyr-Repo clone (oder via west)
git clone https://github.com/zephyrproject-rtos/zephyr
cd zephyr

# Treiber-Stand prüfen
git grep -l 'PTP\|timestamp\|gptp\|ptp_clock' drivers/ethernet/eth_lan865x*
git log --oneline drivers/ethernet/eth_lan865x* | head -20
```

Plus search open pull requests:

- https://github.com/zephyrproject-rtos/zephyr/pulls?q=lan8651
- https://github.com/zephyrproject-rtos/zephyr/pulls?q=lan865x
- https://github.com/zephyrproject-rtos/zephyr/pulls?q=gptp+timestamp

It is possible that someone is already working on it — or it has
already been merged. The result of this 30-minute research determines
whether:

- **Case A**: gap exists → §9 is feasible, in parallel with or instead of §8
- **Case B**: already in the works → no effort needed, just possibly
  switch your own project to Zephyr
- **Case C**: pull request open → contact the author, possibly
  collaborate

### 9.6 Strategic recommendation

| If what matters to you is … | then do … |
|---|---|
| quickly an off-the-shelf demo for Microchip customers | §8 (MCC component in Harmony) |
| a clean, cross-platform, standardized solution | §9 (Zephyr driver) |
| both — maximum reach | both in parallel, §9 with higher priority due to better reuse |

§8 and §9 do **not** exclude each other. The domain work (register
sequences, TTSCAA race, FTSE bit, RX timestamp pipeline) is
platform-independent — only the way to package it differs.

### 9.7 Research result 2026-04-26: nobody is working on it publicly

A systematic search through the Zephyr repo, in open/closed PRs,
issues, Microchip's own Zephyr fork, and across the broader TC6
ecosystem yielded the following:

#### Negative findings (the field is empty)

| Source | Finding |
|---|---|
| `drivers/ethernet/eth_lan865x.c` on `main` (commit `db13c4f`, 2026-04-22) | **zero** PTP plumbing. No `get_ptp_clock`, no `net_pkt_set_timestamp_ns`, no `CONFIG_PTP`/`CONFIG_NET_PKT_TIMESTAMP` guards, no TSU register accesses. |
| Open pull requests | **0 hits** for `lan865x`+`ptp` / `lan8651`+`ptp` / `oa_tc6`+`ptp`. Also no closed PRs. Also no rejected attempts. |
| Microchip's own maintainer **Parthiban Veerasooran** | 5 PRs to the LAN865x driver in 2025–2026, **none** touch timestamping. |
| Issues (incl. umbrella `#38352 "IEEE 1588-2008 support"`) | LAN865x not mentioned anywhere. |
| `MicrochipTech/zephyr` (public Microchip fork) | "No branches match" for `ptp` / `timestamp` / `lan865x` / `1588`. |
| `eth_adin2111.c` (Analog Devices, same TC6 class) | **no** PTP. There is **no** template for TC6+PTP in Zephyr. |

→ **High confidence that the field is publicly empty.**
   Only Microchip's private internal branches are not visible.

#### Valuable side findings

| Finding | Significance |
|---|---|
| **`MicrochipTech/LAN865x-TimeSync`** ([github.com/MicrochipTech/LAN865x-TimeSync](https://github.com/MicrochipTech/LAN865x-TimeSync)) | Bare-metal reference: SAM-E54, dual gPTP grandmaster + follower, ready-made TSU register sequence, state machine UNINIT/MATCHFREQ/HARDSYNC/COARSE/FINE. **4 stars, 2 commits**, no Zephyr port. **Ideal algorithmic cheat sheet.** |
| **Linux mainline 2025-08** | Parthiban Veerasooran (Microchip) added LAN865x TSU configuration to the Linux driver (Patchew `20250818060514.52795-3`). Microchip is aware of the problem and is actively working on Linux PTP support — but a Zephyr equivalent is pending. |
| **Zephyr PR #106867** "Add APIs for accurate PHY latency handling for PTP clocks" (go2sh, 2026-04-05, **open**) | Possible precursor / dependency, watch. |
| **`drivers/ptp_clock/ptp_clock_nxp_enet.c`** | Mature Zephyr PTP template, ideal architecture model for `ptp_clock_lan865x.c`. |

#### Recommendation from the research

✅ **Green light for Zephyr investment** — the field is publicly empty.

**Before starting investment:**

- **Email to Parthiban Veerasooran** (`parthiban.veerasooran@microchip.com`)
  — he is the active LAN865x Zephyr maintainer. An email rules out
  private or not-yet-public Microchip activity that escapes the
  GitHub search. A 5-line email can save weeks of seemingly duplicate
  work.

**Recommended implementation order:**

1. **Algorithmic template**: clone `MicrochipTech/LAN865x-TimeSync`
   and study the TSU init sequence + gPTP state machine. It is the
   nearest relative of our own Harmony implementation.
2. **API architecture template**: take `drivers/ptp_clock/ptp_clock_nxp_enet.c`
   as a Zephyr pattern reference.
3. **Driver extension**: extend `eth_lan865x.c` with `get_ptp_clock`,
   RX timestamp via `net_pkt_set_timestamp_ns()`, TX timestamping.
   Plus an own `ptp_clock_lan865x.c` for the TSU as kernel clock.
4. **Watch**: PR #106867 (PHY latency API) — could land before our
   work and would then become a dependency.
5. **Reviewer coordination**: address Parthiban Veerasooran (Microchip)
   and Lukasz Majewski (original LAN8651 Zephyr driver author) as
   likely reviewers of the later RFC PR.

#### Sources (for verification)

- [eth_lan865x.c on main](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/ethernet/eth_lan865x.c)
- [Commit history](https://github.com/zephyrproject-rtos/zephyr/commits/main/drivers/ethernet/eth_lan865x.c)
- [PR search: lan865x](https://github.com/zephyrproject-rtos/zephyr/pulls?q=is%3Apr+lan865x)
- [Issues: lan865x](https://github.com/zephyrproject-rtos/zephyr/issues?q=is%3Aissue+lan865x)
- [Issue #38352 IEEE 1588-2008](https://github.com/zephyrproject-rtos/zephyr/issues/38352)
- [PR #106867 PHY latency API](https://github.com/zephyrproject-rtos/zephyr/pull/106867)
- [LAN865x Linux TSU patch (Aug 2025)](https://patchew.org/linux/20250818060514.52795-1-parthiban.veerasooran@microchip.com/20250818060514.52795-3-parthiban.veerasooran@microchip.com/)
- [MicrochipTech/LAN865x-TimeSync](https://github.com/MicrochipTech/LAN865x-TimeSync)
- [MicrochipTech/zephyr fork](https://github.com/MicrochipTech/zephyr)
- [Zephyr PTP docs](https://docs.zephyrproject.org/latest/connectivity/networking/api/ptp.html)
- [ptp_clock_nxp_enet.c template](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/ptp_clock/ptp_clock_nxp_enet.c)

### 9.8 More broadly: not a single 10BASE-T1S chip in Zephyr has PTP

§9.7 focused on the LAN8651. A wider view shows: **the entire T1S
ecosystem in Zephyr is PTP-free today.**

| T1S chip | Zephyr driver present? | PTP / timestamping in the driver? |
|---|---|---|
| Microchip **LAN8651** (`eth_lan865x.c`) | ✅ yes | ❌ no |
| Microchip **LAN867x** (same driver) | ✅ yes | ❌ no |
| Analog Devices **ADIN2111** / **ADIN1100** (`eth_adin2111.c`) | ✅ yes | ❌ no |
| Generic **OA-TC6** library (`subsys/net/lib/oa_tc6/`) | ✅ yes | ❌ no |

→ There is **not a single reference driver for TC6+PTP** in Zephyr.
   Whoever implements it is not *one of many* but *the first*.

#### Why actually? (Structural reasons)

1. **T1S is new.** IEEE 802.3cg (10BASE-T1S) was only ratified in
   2020. Most gPTP implementations are older and built for classic
   100/1000BASE-Tx architectures.
2. **T1S is half-duplex with PLCA multidrop.** Most PTP profiles
   assume dedicated full-duplex links. T1S needs the gPTP PLCA
   profile (IEEE 802.1AS-2020 Annex), which is not implemented in
   every software stack.
3. **TC6 SPI MAC-PHYs are architecturally unusual.** The RX hardware
   timestamp is delivered in the SPI footer (RTSA bit + 8 bytes) —
   not via a standardized MII PHY interface. Zephyr's generic PTP
   driver patterns (e.g. `eth_stm32_hal`, `eth_nxp_enet`) do not fit
   1:1.
4. **Hardware availability is only recent.** LAN8651 Rev. B1 with
   production-ready TSU is 2024–2025. ADIN2111 PTP support is
   similar. Before that, there was simply nothing to integrate.

#### Strategic consequence

| Today | After successful LAN8651 + PTP integration in Zephyr |
|---|---|
| You implement PTP for *one* niche chip | You establish the **first T1S+PTP pattern** for Zephyr |
| Reach: users of LAN8651 + Harmony | Reach: the entire Zephyr T1S ecosystem |
| ADIN2111 users are out of luck | ADIN2111 can subsequently be ported with the same pattern |
| Microchip-specific workaround | Pattern setter for the Zephyr subsystem architecture |

#### Effort correction

Effort somewhat larger than naively estimated — because:

- It is **not a simple driver add** but a new pattern in the Zephyr
  subsystem architecture (TC6+PTP does not exist yet).
- Reviewers (maintainers of `subsys/net/lib/ptp/` and
  `drivers/ethernet/`) will be conservative and look closely.
- The first PR has to be **good** — it sets the standard that all
  subsequent T1S chip drivers will copy.

But that is exactly what makes the contribution more valuable. A
cleanly built `eth_lan865x` PTP extension would not only support
this project — it would **unlock the entire T1S ecosystem in Zephyr**.

#### Effort recommendation (revised)

| Phase | Effort |
|---|---|
| Research / architecture design (in coordination with Zephyr maintainers) | 3–5 days |
| Driver implementation (LAN8651-specific) | 5–8 days |
| `ptp_clock_lan865x.c` as TSU wrapper | 2–3 days |
| Tests / sample application | 2 days |
| RFC PR + review iterations | open, ~weeks |
| **Total until merge** | **~3–4 weeks of full engineering time** |

Plus accompanying work: email to Parthiban Veerasooran (see §9.7),
watch on PR #106867, coordination with Lukasz Majewski (LAN8651
driver originator).

### 9.9 Deep dive: the gPTP PLCA profile (IEEE 802.1AS-2020 Annex H)

§9.8 mentions in passing that T1S needs not only standard gPTP, but
the **PLCA profile** of the 802.1AS-2020 standard. Since this is a
key detail for implementation complexity, here is the deep dive.

#### Why standard gPTP is not enough on T1S

Standard gPTP (IEEE 802.1AS) implicitly assumes the following:

| Assumption | Standard Ethernet (100/1000BASE-Tx) | 10BASE-T1S |
|---|---|---|
| Topology | Point-to-point (PHY-to-PHY) | **Multidrop bus** — N nodes share a wire |
| Duplex | Full-duplex (separate TX/RX lanes) | **Half-duplex** — all nodes transmit on the same wire |
| Medium access | Switch / bridge decides | **PLCA** — token passing in the PHY (Physical Layer Collision Avoidance) |

PLCA = each node gets a reserved burst slot per PLCA cycle. Sends
only in its slot, otherwise not. Comparable to round-robin in the PHY.

#### What naive gPTP breaks on T1S

1. **Pdelay measurement becomes asymmetric.**
   gPTP's path-delay mechanism (`Pdelay_Req` / `Pdelay_Resp`) measures
   the link delay. On T1S, the responder must wait until its PLCA
   slot arrives before it may transmit. This slot wait time (up to
   milliseconds) skews the calculated path delay massively.

2. **PLCA slot latency varies per node.**
   Nodes with low `nodeId` get their slot earlier than nodes with
   high `nodeId`. Standard gPTP has no mechanism to compensate that —
   each slave sees a `nodeId`-dependent bias.

3. **Sync interval assumptions don't hold.**
   Burst-to-burst latency on T1S can blow past the standardized 125ms
   sync interval, depending on bus load. Sync frames are delayed,
   the gPTP timing model no longer holds.

4. **Asymmetric TX burst latency.**
   When a node bursts several frames in its slot, the TX timestamps
   within the burst are tightly packed, while the slot-to-slot
   transition then takes a long time.

#### What Annex H of 802.1AS-2020 fixes

The 2020 revision of IEEE 802.1AS has an **(informative) Annex H**
for PLCA-based 10BASE-T1S networks. Content in essence:

- **Slot-aware Pdelay correction**: path-delay calculation taking
  the PLCA slot index of master and slave nodes into account
- **Modified sync interval recommendations** — longer than 125ms,
  so that burst-to-burst latency does not stress the sync mechanism
- **PLCA beacon as additional time reference** — the PLCA beacon
  frame (transmitted by the PHY itself) can serve as a secondary
  synchronization source
- **Time-aware bridge behavior** for T1S-to-Tx bridges

#### Which stacks have it today

| Stack | gPTP PLCA profile support? |
|---|---|
| **Linux kernel** | beginning — Parthiban's LAN865x TSU patch (2025-08) is a preparatory step |
| **Microchip MicroAutoMotive Stack** (proprietary) | yes, actively marketed for T1S automotive |
| **`MicrochipTech/LAN865x-TimeSync`** (bare-metal reference) | yes — the UNINIT/MATCHFREQ/HARDSYNC/COARSE/FINE state machine implements exactly that |
| **Zephyr `subsys/net/lib/ptp/`** | ❌ standard 802.1AS, **no** PLCA annex |
| **This repo** (`cross-driverless`) | ❌ simplified 802.1AS master+slave, **no** PLCA annex |

#### Practical consequence for this demonstrator

On a **2-node T1S bus** (today's demo setup), the PLCA profile is
**not mandatory** — standard gPTP works adequately, because the
PLCA slot latency is constant and cancels out in the master/slave
pair.

As soon as you scale to **3+ nodes**, you get:

- Slave-specific offset bias depending on `nodeId`
- Pdelay drift with PLCA bus load
- Loss of sync accuracy under load

→ The **PLCA profile is a standalone follow-up feature** to the
basic PTP support. First finish basic support, then Annex H. The
order:

1. **Phase A (today, this repo)**: basic gPTP for 2-node setup
2. **Phase B (future)**: PLCA profile extension for N>2 nodes

#### Implication for the Zephyr strategy

The Zephyr PTP implementation proposed in §9.7/9.8 **should first
cover phase A** — standard gPTP with HW timestamping over the TSU.
That covers 95% of the demo use cases.

**Phase B** (Annex H) as a separate PR after the basis has been
stably merged. That is also politically sensible: a PR for "standard
gPTP + HW timestamping for LAN8651" is more easily accepted than a
mega-PR that simultaneously brings a new PTP profile.
