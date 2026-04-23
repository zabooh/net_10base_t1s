# Exception Dump + Watchdog + find_exception.py

A self-contained crash-diagnostics subsystem for the SAME54P20A firmware:
when anything goes wrong (CPU fault OR silent main-loop hang), the firmware
prints a Cortex-M4 register dump over the existing UART, then resets. A
companion Python tool decodes that dump back to the C source line.

## Table of Contents

- [1. Why](#1-why)
- [2. Architecture Overview](#2-architecture-overview)
- [3. What the Dump Looks Like](#3-what-the-dump-looks-like)
- [4. Firmware Components](#4-firmware-components)
  - [4.1 exception_handler.c — Fault Vectors and Naked Trampolines](#41-exception_handlerc--fault-vectors-and-naked-trampolines)
  - [4.2 watchdog.c — SAM E54 WDT with Early-Warning](#42-watchdogc--sam-e54-wdt-with-early-warning)
  - [4.3 test_exception_cli.c — Deliberate Fault Triggers](#43-test_exception_clic--deliberate-fault-triggers)
- [5. find_exception.py — Decode the Dump](#5-find_exceptionpy--decode-the-dump)
  - [5.1 Input Modes](#51-input-modes)
  - [5.2 Decoded Output](#52-decoded-output)
  - [5.3 Address Resolution](#53-address-resolution)
- [6. End-to-End Walkthrough](#6-end-to-end-walkthrough)
- [7. Reading the Decoded Output](#7-reading-the-decoded-output)
- [8. Limitations and Edge Cases](#8-limitations-and-edge-cases)
- [9. Files](#9-files)

---

## 1. Why

The firmware runs on a board that is often deployed without a debugger
attached — bench bring-up, demo boxes, customer trials. When something goes
wrong in the field two failure modes need post-mortem:

- **CPU fault** (NULL dereference, unaligned access, stack corruption,
  divide-by-zero, escalation to HardFault). Cortex-M4 traps these into a
  vector but the default Harmony handler is just a tight infinite loop —
  the operator sees the board freeze with no clue what happened.
- **Silent hang** (deadlock in an ISR, infinite loop in a state machine,
  starvation by a runaway interrupt). No fault, no trap — the main loop
  simply stops iterating. Looks identical to a fault from the outside.

This subsystem catches both classes, prints enough register state to
identify where the CPU was when it died, and resets so the system
recovers automatically. It uses **no Harmony APIs**, no heap, no `printf`,
and works even after a stack overflow has corrupted the queue state of the
console driver.

---

## 2. Architecture Overview

```
                 ┌─────────────────────────────────────────────┐
  CPU fault  →   │  HardFault_Handler / MemMgmt / BusFault /   │
  (vector)       │  UsageFault / NonMaskableInt — naked        │
                 │  trampolines in exception_handler.c         │
                 └────────────────────┬────────────────────────┘
                                      │  pick MSP or PSP via lr.bit2
                                      ▼
                 ┌─────────────────────────────────────────────┐
  WDT EW IRQ →   │  WDT_Handler — naked trampoline in          │
  (silent hang)  │  watchdog.c                                 │
                 └────────────────────┬────────────────────────┘
                                      │  (label "WatchdogEW…")
                                      ▼
                 ┌─────────────────────────────────────────────┐
                 │  fault_dump_and_reset(sp, label, exc_return)│
                 │    ├ format ASCII dump via SERCOM1 raw      │
                 │    │   register writes (no Harmony console) │
                 │    ├ wait TXC=1 to flush last byte          │
                 │    └ AIRCR.SYSRESETREQ → controller resets  │
                 └─────────────────────────────────────────────┘
                                      │
                                      ▼
                          (UART output captured by
                          TeraTerm / minicom / etc.)
                                      │
                                      ▼
                 ┌─────────────────────────────────────────────┐
                 │  find_exception.py                          │
                 │    ├ parse register/value pairs from dump   │
                 │    ├ decode CFSR / HFSR / EXC_RETURN / ICSR │
                 │    ├ resolve PC + stacked LR via            │
                 │    │   xc32-addr2line                       │
                 │    └ grep disassembly listing for context   │
                 └─────────────────────────────────────────────┘
```

Two design choices keep the dump path robust under all conditions:

- **Naked trampolines** capture the active stack pointer (MSP or PSP,
  selected by `EXC_RETURN.bit2`) before any C function prologue runs —
  otherwise the prologue's pushes would perturb the very frame we want to
  dump.
- **Direct SERCOM1 register writes** bypass Harmony's console
  driver (DMA, ring buffer, mutex). When a fault has just trashed the
  stack or a deadlock has occurred inside a different ISR, the console
  driver state is unreliable, but the SERCOM TX shift register itself
  almost always still works.

All five fault handlers AND the WDT EW handler funnel into a single
`fault_dump_and_reset()` C routine — one place to maintain the dump
format, one source of truth for the reset behaviour.

---

## 3. What the Dump Looks Like

A real example produced by `test_exception null_read`:

```
================ EXCEPTION ================
Fault   : BusFault
System Control Block:
    CFSR  (UFSR<<16|BFSR<<8|MMFSR) = 0x00008200
    HFSR  (Hard Fault Status)      = 0x00000000
    DFSR  (Debug Fault Status)     = 0x00000000
    MMFAR (Mem Manage Addr)        = 0x00000000
    BFAR  (Bus Fault Addr)         = 0x00000000
    AFSR  (Aux Fault Status)       = 0x00000000
    ICSR  (Int Ctrl/State)         = 0x00000805
    SHCSR (Sys Handler Ctrl/State) = 0x00070008
    EXC_RETURN (LR at entry)       = 0xfffffff9
Stacked frame (active SP at fault):
    SP    (active stack pointer)   = 0x20003fa0
    R0                             = 0x00000000
    R1                             = 0x00000000
    R2                             = 0x20003fb8
    R3                             = 0xdeadbeef
    R12                            = 0x00000000
    LR    (return address)         = 0x000249e1
    PC    (EXCEPTION ADDRESS)      = 0x000249d4
    xPSR  (program status reg)     = 0x21000000
=== Resetting controller ===
```

The `PC (EXCEPTION ADDRESS)` line is the most important — it points to the
instruction that faulted. Everything else helps narrow down WHY.

---

## 4. Firmware Components

### 4.1 [exception_handler.c](../../apps/tcpip_iperf_lan865x/firmware/src/exception_handler.c) — Fault Vectors and Naked Trampolines

Provides strong overrides for the weak fault handlers in
`config/default/exceptions.c`:

| Vector | Override | When it fires |
|---|---|---|
| HardFault_Handler        | yes | NULL fetch, escalation from any other fault, data abort |
| MemoryManagement_Handler | yes | MPU violation (MPU not currently enabled, but ready) |
| BusFault_Handler         | yes | Precise/imprecise data bus error, NULL data access |
| UsageFault_Handler       | yes | Undefined instruction, divide-by-zero, unaligned word |
| NonMaskableInt_Handler   | yes | Clock-failure detection (rare, but loud when it happens) |
| SVCall_Handler           | NO  | Used by FreeRTOS / Harmony — overriding would break it |
| DebugMonitor_Handler     | NO  | Only fires with a JTAG/SWD probe attached |
| PendSV_Handler           | NO  | Used by FreeRTOS context-switch |

Each override is a `__attribute__((naked, noreturn))` function. The naked
attribute suppresses the C function prologue so the entry inline-asm sees
the original SP that the CPU pushed the exception frame to. The asm:

```asm
tst   lr, #4                ; bit 2 of EXC_RETURN: 0=MSP, 1=PSP
ite   eq
mrseq r0, msp               ; thread mode came in on MSP
mrsne r0, psp               ; thread mode came in on PSP
mov   r1, <fault-name-str>
mov   r2, lr                ; save EXC_RETURN for the C dumper
b     fault_dump_and_reset  ; tail-call, never returns
```

`fault_dump_and_reset()` reads the stacked frame via the captured SP, dumps
all relevant SCB registers + stacked R0-R3,R12,LR,PC,xPSR + EXC_RETURN +
ICSR via the polled SERCOM1 path, drains TXC, then triggers
`SCB->AIRCR = (0x05FA<<16) | SYSRESETREQ` for a controller reset.

### 4.2 [watchdog.c](../../apps/tcpip_iperf_lan865x/firmware/src/watchdog.c) — SAM E54 WDT with Early-Warning

The CPU-fault path catches *traps*. It does not catch *deadlocks* — a
spinning ISR or a starvation loop produces no exception. For those we use
the SAM E54 Watchdog Timer's Early-Warning interrupt, which fires on a
timer that the main loop must periodically reset by calling
`watchdog_kick()`.

Configuration:

| Parameter | Value | Effect |
|---|---|---|
| Clock source | CLK_WDT_OSC = OSCULP32K / 32 = 1024 Hz | No GCLK setup needed |
| `WDT_CONFIG.PER` | CYC4096 | 4 s total timeout (hardware reset) |
| `WDT_EWCTRL.EWOFFSET` | CYC2048 | EW interrupt fires 2 s before reset |
| `NVIC` priority | 3 | Same as other custom ISRs — tail-chains cleanly |
| `WDT_CTRLA.ALWAYSON` | OFF | Allows debug pause / `PTP_FOL_Reset()` to disable WDT temporarily |

**`watchdog_init()` is called on first entry to `APP_STATE_IDLE`, NOT in
`APP_Initialize()`** — Harmony's TCP/IP stack + LAN865x bring-up + PTP
follower init can take 2-3 seconds before the main loop starts iterating,
and an active WDT during that window would reset the chip into a boot
loop. Bringing it up at the first idle entry guarantees the kicker is
already running.

**`watchdog_kick()` is non-blocking.** A naive implementation (write
`WDT_CLEAR_KEY` → wait for `SYNCBUSY.CLEAR` to clear) takes ~3 ms because
the WDT runs at 1024 Hz. Calling that from a multi-kHz main loop blocks
the loop almost continuously — observed on the bench as **PTP lock time
stretching from 2.6 s to over 30 s**. The fix is to skip the kick when
`SYNCBUSY.CLEAR` is still asserted from the previous call:

```c
if ((WDT_REGS->WDT_SYNCBUSY & WDT_SYNCBUSY_CLEAR_Msk) != 0u) {
    return;        /* previous kick still syncing — skip this one */
}
WDT_REGS->WDT_CLEAR = WDT_CLEAR_CLEAR_KEY;
```

At a few-kHz main-loop rate this still issues hundreds of kicks per
second, far more than enough for the 4 s timeout.

The WDT EW interrupt vector (`WDT_Handler`) is the same naked-trampoline
pattern as the fault handlers, with label `"WatchdogEW (firmware hung —
main loop did not kick the WDT for ~1 s)"`. The captured PC is the
instruction the CPU was executing when the EW interrupt preempted —
usually inside whatever ISR or busy-loop is causing the hang.

### 4.3 [test_exception_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/test_exception_cli.c) — Deliberate Fault Triggers

Available via the `test_exception` CLI command on either UART. Used to
verify the dump-and-reset path works on the wire after firmware changes.

Type `test_exception` (no args) to see the full kind list. Each kind:

| Sub-command | Mechanism | Expected handler & CFSR bit |
|---|---|---|
| `test_exception null_read`    | `*(uint32_t*)0`                     | BusFault, BFSR.PRECISERR |
| `test_exception null_write`   | `*(uint32_t*)0 = 0xDEADBEEF`        | BusFault, BFSR.PRECISERR |
| `test_exception unaligned`    | unaligned 32-bit load               | UsageFault, UFSR.UNALIGNED (CCR.UNALIGN_TRP set by cmd) |
| `test_exception undef`        | encoded `UDF.W #0` (`0xF7F0A000`)   | UsageFault, UFSR.UNDEFINSTR |
| `test_exception divzero`      | `1 / 0`                             | UsageFault, UFSR.DIVBYZERO (CCR.DIV_0_TRP set by cmd) |
| `test_exception svcall`       | `svc #0`                            | SVCall_Handler (only for testing the path; SVCall not currently overridden) |
| `test_exception hang`         | `for(;;) nop;` with IRQs ON         | WDT EW fires after ~1 s, dump labelled "WatchdogEW" |
| `test_exception hang_irqoff`  | `cpsid i; for(;;) nop;`             | EW masked → only the hardware WDT reset @ 2 s saves us (no dump, but recovery is verified) |

Before each trigger the command sets `SHCSR.{USG,BUS,MEM}FAULTENA` plus
`CCR.UNALIGN_TRP` and `CCR.DIV_0_TRP` so the fault is dispatched to its
dedicated handler instead of always escalating to HardFault — this gives
cleaner CFSR sub-bit diagnostics in the dump.

---

## 5. [find_exception.py](../../tools/test-harness/find_exception.py) — Decode the Dump

The Python companion that turns the raw UART dump into a source-line
pointer. Lives next to the build output so it can find the ELF
automatically. Requires only the standard library + the XC32 toolchain
already installed on a Microchip workstation (`xc32-objdump` and
`xc32-addr2line`).

### 5.1 Input Modes

Pick whichever is least friction after a crash:

| Mode | Command | When to use |
|---|---|---|
| PC-only | `python find_exception.py 0x000249d4` | You only have the PC (e.g. read off a screen) |
| Clipboard | `python find_exception.py --clipboard` | Select dump in TeraTerm → Edit → Copy → run script. Avoids all paste quirks |
| Interactive paste | `python find_exception.py --paste` | Console paste; end with empty line + `Ctrl+Z` (Windows) / `Ctrl+D` (Linux) |
| File | `python find_exception.py --file crash.txt` | Saved log file |
| stdin | `type crash.txt | python find_exception.py --stdin` | Pipe-friendly for scripts |

The clipboard mode uses Python's stdlib `tkinter` — no `pip install`
required. It also strips zero-width / non-breaking-space noise that some
terminals inject around copy operations, so `clipboard_get()` returns
clean text even if TeraTerm wrapped it in formatting.

### 5.2 Decoded Output

When a full dump (not just a PC) is supplied, the script also pretty-prints
the Cortex-M4 fault status registers. Sample (corresponds to the BusFault
dump in §3):

```
========================================================================
  Decoded crash dump
========================================================================
  Fault entry      : BusFault_Handler
  CFSR  = 0x00008200  -> BFSR.PRECISERR (precise data bus error),
                         BFSR.BFARVALID (BFAR holds the faulting address)
  HFSR  = 0x00000000  -> no flags set
  BFAR  = 0x00000000  (VALID — faulting address)
  EXC_RETURN = 0xfffffff9  -> MSP, Thread mode, basic 8-word frame
  ICSR  = 0x00000805  -> VECTACTIVE=5 (BusFault)
```

Bit decoding tables for **CFSR** (split into UFSR / BFSR / MMFSR), **HFSR**,
and **EXC_RETURN** are defined inline at the top of `find_exception.py` —
edit them if Cortex-M ever adds new bits or if you want different labels.

### 5.3 Address Resolution

For both the PC and the stacked LR (return address), the script:

1. Generates `xc32-objdump -d -S` of `out/.../default.elf` into
   `out/.../exception_listing.lst`. Cached — re-generated only if the
   ELF mtime is newer than the listing, or `--refresh` is passed.
2. Runs `xc32-addr2line -f -i -C -e <elf> <pc>` to get the function name,
   file:line, and any inlined-frame chain.
3. Greps the listing for `<addr>:` and prints ~30 instructions of
   context around the hit, with a `>>> ` marker on the faulting
   instruction itself. Source lines come through via the `-S` flag, so
   you usually see the C code intermixed with the assembly.

If `addr2line` returns `(no debug info for this address)` the address is
likely inside libc or boot code. The disassembly grep still works as a
fallback.

---

## 6. End-to-End Walkthrough

Reproduce the BusFault example yourself:

**Step 1 — Trigger the fault.** With the firmware running and TeraTerm
attached to either board's UART:

```
> test_exception null_read
test_exception: about to trigger 'null_read' — controller should dump + reset.
```

**Step 2 — Capture the dump.** TeraTerm prints the dump as the chip
crashes. Either:

- `Edit → Select All → Copy` (clipboard mode), OR
- `File → Save Log` (file mode).

**Step 3 — Decode.** From the project firmware directory:

```bash
cd apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X
python find_exception.py --clipboard
```

Output (abridged):

```
[INFO] Reusing existing listing: out/.../exception_listing.lst

========================================================================
  Decoded crash dump
========================================================================
  Fault entry      : BusFault_Handler
  CFSR  = 0x00008200  -> BFSR.PRECISERR, BFSR.BFARVALID
  ...

========================================================================
  PC : 0x000249d4  (Thumb-cleaned 0x000249d4)
========================================================================
--- addr2line for PC ---
trigger_null_read
.../firmware/src/test_exception_cli.c:46

--- Disassembly context for PC (line 12453 of exception_listing.lst) ---
        ldr   r0, [r3]      @ [test_exception_cli.c:46]
  >>>   249d4: 6818          ldr     r0, [r3, #0]
        str   r0, [r2]      @ [test_exception_cli.c:46]
        ...
```

The script told us the BusFault was triggered by `ldr r0, [r3, #0]` at
`test_exception_cli.c:46`, which is `v = *p;` where `p == NULL`. Total
turn-around from "board froze" to "I know which line did it" is about 30
seconds.

For a silent hang (`test_exception hang`), the dump shows
`Fault: WatchdogEW (firmware hung — main loop did not kick the WDT for
~1 s)`, the PC points wherever the CPU was when the EW interrupt
preempted (typically inside the spinning `nop` loop), and `find_exception.py`
walks you straight to the offending line.

---

## 7. Reading the Decoded Output

Quick reference for the most common fault signatures:

| Pattern | Meaning |
|---|---|
| `CFSR = ...8200`, BFAR = `0x...` | NULL or wild-pointer **data** access — BFAR holds the faulting address |
| `CFSR = ...8000` only | Imprecise BusFault — write somewhere caused trouble *some time ago*, no precise address. Often a stack overflow |
| `CFSR = 0x01000000` (UFSR.UNDEFINSTR) | Either flash corruption, jump to bad pointer, or — much more likely — function pointer table not initialised |
| `CFSR = 0x02000000` (UFSR.UNALIGNED) | Unaligned word load. Almost always pointer arithmetic on a `char*` cast to `uint32_t*` |
| `CFSR = 0x02000000` (UFSR.DIVBYZERO) | Trap-on-divide-by-zero. Check the operand source |
| `HFSR = 0x40000000` (FORCED) | A configurable fault escalated to HardFault (the per-fault enable bit was clear). With `test_exception_cli.c` we explicitly enable `SHCSR.{USG,BUS,MEM}FAULTENA` to avoid this; in production it can mask the real cause |
| `EXC_RETURN = 0xFFFFFFF1` | Faulted in **handler mode** — i.e. inside another ISR. Look at `ICSR.VECTACTIVE` for which one |
| `EXC_RETURN = 0xFFFFFFFD` | Faulted in **thread mode using PSP** — FreeRTOS task. The PSP frame is on the task's stack, separate from the system MSP |
| `Fault: WatchdogEW...` | The main loop did not call `watchdog_kick()` for ~1 s. PC = where the CPU was stuck when EW preempted. Could be a spinning ISR (check `ICSR.VECTACTIVE`), a long busy-wait, or a deadlock |

`ICSR.VECTACTIVE` is the underused gem — when it reports a non-zero
vector number, the fault happened **inside that handler**, not in thread
code. That fact alone narrows the search by 10×.

---

## 8. Limitations and Edge Cases

- **Stack overflow into the dump path.** If the stack has grown into the
  region used by `fault_dump_and_reset()`'s locals, the dump itself can
  fault (HardFault inside HardFault → CPU lockup). The hardware WDT
  reset at the 4 s mark still recovers in that case, but no dump is
  produced. Mitigation: keep the stack reservation in the linker script
  comfortable (currently several KB, well above the dump path's needs).
- **Fault inside a critical section that disabled SERCOM1.** Possible in
  theory; not observed. The polled `uart_putc_raw()` path doesn't depend
  on SERCOM1 interrupts being enabled — only on the peripheral itself
  being clocked, which is true throughout `APP_STATE_IDLE`.
- **Imprecise BusFault.** When the BFSR shows `IMPRECISERR` and not
  `PRECISERR`, BFAR is invalid and the PC may point to an instruction
  several cycles later than the actual erroneous one. Common with
  buffered writes; usually indicates a stack-corruption-by-write
  scenario where the offending code path has already returned.
- **`hang_irqoff` produces no dump on purpose.** Disabling all
  interrupts (`cpsid i`) masks the WDT EW vector. The hardware WDT reset
  at 2 s still saves the controller, but the operator only sees a clean
  reboot. The kind exists to verify the hardware-reset escape route.
- **find_exception.py needs the matching ELF.** Address resolution
  silently produces wrong source lines if the loaded firmware doesn't
  match `out/.../default.elf`. Always rebuild + reflash + run the test on
  the same ELF you decode against. If the listing seems stale, pass
  `--refresh`.

---

## 9. Files

| File | Role |
|---|---|
| [apps/tcpip_iperf_lan865x/firmware/src/exception_handler.c](../../apps/tcpip_iperf_lan865x/firmware/src/exception_handler.c) | Strong overrides + naked trampolines + fault_dump_and_reset() |
| [apps/tcpip_iperf_lan865x/firmware/src/watchdog.c](../../apps/tcpip_iperf_lan865x/firmware/src/watchdog.c) | WDT init, non-blocking kick, WDT_Handler trampoline |
| [apps/tcpip_iperf_lan865x/firmware/src/watchdog.h](../../apps/tcpip_iperf_lan865x/firmware/src/watchdog.h) | API: `watchdog_init()`, `watchdog_kick()` |
| [apps/tcpip_iperf_lan865x/firmware/src/test_exception_cli.c](../../apps/tcpip_iperf_lan865x/firmware/src/test_exception_cli.c) | `test_exception` CLI with all kinds |
| [apps/tcpip_iperf_lan865x/firmware/src/test_exception_cli.h](../../apps/tcpip_iperf_lan865x/firmware/src/test_exception_cli.h) | CLI registration prototype |
| [apps/tcpip_iperf_lan865x/firmware/src/app.c](../../apps/tcpip_iperf_lan865x/firmware/src/app.c) | Calls `watchdog_init()` on first IDLE entry, `watchdog_kick()` per main-loop iteration |
| [find_exception.py](../../tools/test-harness/find_exception.py) | Decode + addr2line + disassembly context |

Related documentation:

- [implementation.md](../ptp/implementation.md) §4 — main-loop structure that the WDT
  protects.
- [standalone_demo.md](../features/standalone_demo.md) — uses a *separate*
  software watchdog inside the demo state machine to detect cyclic_fire
  liveness. Don't confuse the two: that one detects an application-level
  stall (cycles counter not advancing), this one is the SAM E54 hardware
  WDT peripheral that catches all-firmware hangs.
