/*
 * exception_handler.c — Cortex-M4 fault crash dump + reset
 *
 * Strong overrides of the weak fault handlers shipped in
 * config/default/exceptions.c.  When any of them fires (typically after
 * a stack overflow / null pointer / unaligned access in an ISR), we:
 *
 *   1. Capture the stack pointer (MSP or PSP, whichever was active)
 *      via a naked-function trampoline so the C-level dump function
 *      sees the original stacked frame.
 *   2. Format a textual register dump — including the "exception
 *      address" register (PC at fault) — directly into SERCOM1's
 *      transmit register, bypassing Harmony's console layer (whose
 *      DMA / queue state is unreliable in fault context).
 *   3. Wait for the SERCOM TX shift register to drain (TXC=1) so the
 *      last byte actually leaves the wire before we reset.
 *   4. NVIC_SystemReset() — controller reboots from scratch.
 *
 * The dump is intentionally small and self-contained: no heap, no
 * printf, no Harmony API calls inside the fault context.
 */

#include <stdint.h>
#include <stdbool.h>
#include "device.h"           /* SERCOM1_REGS, SCB, NVIC, etc.       */

/* SAM E54 SERCOM USART register layout (already used by Harmony's
 * SERCOM1 console driver; see plib_sercom1_usart.c).  We don't
 * reconfigure it — we just push bytes into the existing TX path. */
#define UART_SERCOM     SERCOM1_REGS->USART_INT
#define UART_DRE_BIT    SERCOM_USART_INT_INTFLAG_DRE_Msk
#define UART_TXC_BIT    SERCOM_USART_INT_INTFLAG_TXC_Msk

/* -------------------------------------------------------------------------
 * Polled UART output — works even when interrupts are disabled and DMA
 * has stopped.  No queueing, no buffering, no dependency on Harmony's
 * console state machine.
 * ---------------------------------------------------------------------- */

static void uart_putc_raw(char c)
{
    /* Wait for the TX holding register to be empty, then write. */
    while ((UART_SERCOM.SERCOM_INTFLAG & UART_DRE_BIT) == 0u) { /* spin */ }
    UART_SERCOM.SERCOM_DATA = (uint32_t)(uint8_t)c;
}

/* All higher-level emitters route through uart_putc which injects CR
 * before LF — terminals on the bench were showing the classic stair-
 * step output before this was applied to the per-character path
 * (uart_put_label wrote '\n' directly via the raw putc). */
static void uart_putc(char c)
{
    if (c == '\n') {
        uart_putc_raw('\r');
    }
    uart_putc_raw(c);
}

static void uart_puts(const char *s)
{
    while (*s != '\0') {
        uart_putc(*s++);
    }
}

static void uart_drain(void)
{
    /* Block until the shift register has actually transmitted the last
     * byte (TXC = transmit complete).  Otherwise the system reset can
     * cut the wire mid-byte. */
    while ((UART_SERCOM.SERCOM_INTFLAG & UART_TXC_BIT) == 0u) { /* spin */ }
}

static void uart_put_hex32(uint32_t v)
{
    static const char hex[] = "0123456789abcdef";
    uart_putc('0'); uart_putc('x');
    for (int i = 7; i >= 0; --i) {
        uart_putc(hex[(v >> (i * 4)) & 0xFu]);
    }
}

static void uart_put_label(const char *label, uint32_t value)
{
    uart_puts("    ");
    uart_puts(label);
    uart_puts(" = ");
    uart_put_hex32(value);
    uart_putc('\n');
}

/* -------------------------------------------------------------------------
 * Fault dump — common C routine fed the active stack pointer + the name
 * of the fault that triggered us.  Reads the stacked register frame
 * (R0..xPSR) plus the Cortex-M4 fault status registers, prints, drains,
 * resets.
 * ---------------------------------------------------------------------- */

typedef struct __attribute__((packed)) {
    uint32_t r0;
    uint32_t r1;
    uint32_t r2;
    uint32_t r3;
    uint32_t r12;
    uint32_t lr;
    uint32_t pc;        /* program counter at the moment of fault */
    uint32_t xpsr;
} cortex_m_stack_frame_t;

/* Externally visible (no `static`) so the inline-asm `b
 * fault_dump_and_reset` in the trampolines below can resolve it; also
 * marked `used` so LTO / -ffunction-sections doesn't drop it because
 * the only references are from asm. */
void __attribute__((noreturn, used))
fault_dump_and_reset(uint32_t *sp, const char *fault_name, uint32_t exc_return)
{
    const cortex_m_stack_frame_t *frame = (const cortex_m_stack_frame_t *)sp;

    uart_puts("\n\n");
    uart_puts("================ EXCEPTION ================\n");
    uart_puts("Fault   : ");
    uart_puts(fault_name);
    uart_putc('\n');

    /* Cortex-M4 fault status registers (System Control Block). */
    uart_puts("System Control Block:\n");
    uart_put_label("CFSR  (UFSR<<16|BFSR<<8|MMFSR)", SCB->CFSR);
    uart_put_label("HFSR  (Hard Fault Status)     ", SCB->HFSR);
    uart_put_label("DFSR  (Debug Fault Status)    ", SCB->DFSR);
    uart_put_label("MMFAR (Mem Manage Addr)       ", SCB->MMFAR);
    uart_put_label("BFAR  (Bus Fault Addr)        ", SCB->BFAR);
    uart_put_label("AFSR  (Aux Fault Status)      ", SCB->AFSR);
    uart_put_label("ICSR  (Int Ctrl/State)        ", SCB->ICSR);
    uart_put_label("SHCSR (Sys Handler Ctrl/State)", SCB->SHCSR);
    uart_put_label("EXC_RETURN (LR at entry)      ", exc_return);

    uart_puts("Stacked frame (active SP at fault):\n");
    uart_put_label("SP    (active stack pointer)  ", (uint32_t)sp);
    uart_put_label("R0                            ", frame->r0);
    uart_put_label("R1                            ", frame->r1);
    uart_put_label("R2                            ", frame->r2);
    uart_put_label("R3                            ", frame->r3);
    uart_put_label("R12                           ", frame->r12);
    uart_put_label("LR    (return address)        ", frame->lr);
    uart_put_label("PC    (EXCEPTION ADDRESS)     ", frame->pc);
    uart_put_label("xPSR  (program status reg)    ", frame->xpsr);

    uart_puts("=== Resetting controller ===\n");
    uart_drain();

    /* AIRCR: write key 0x05FA in [31:16] together with SYSRESETREQ. */
    SCB->AIRCR = (0x05FAu << SCB_AIRCR_VECTKEY_Pos) | SCB_AIRCR_SYSRESETREQ_Msk;
    /* DSB to make sure the write completes before we wait for reset. */
    __asm volatile ("dsb" ::: "memory");
    for (;;) { /* never returns */ }
}

/* -------------------------------------------------------------------------
 * Naked trampolines per fault type — pick MSP or PSP based on EXC_RETURN
 * bit 2, then jump into the common C dumper.
 *
 * We can't write the SP-selection logic in C because the act of
 * entering a normal function pushes registers onto whichever stack
 * is currently active, perturbing the very frame we want to dump.
 * Naked + inline asm captures SP atomically before any prologue runs.
 * ---------------------------------------------------------------------- */

#define DEFINE_FAULT_HANDLER(name, label)                                    \
    void __attribute__((naked, noreturn)) name(void)                         \
    {                                                                        \
        __asm volatile (                                                     \
            "tst   lr, #4         \n"   /* bit 2 = 0?MSP : 1?PSP        */  \
            "ite   eq             \n"                                        \
            "mrseq r0, msp        \n"                                        \
            "mrsne r0, psp        \n"                                        \
            "mov   r1, %0         \n"                                        \
            "mov   r2, lr         \n"                                        \
            "b     fault_dump_and_reset \n"                                  \
            : : "r" (label) : "r0", "r1", "r2"                               \
        );                                                                   \
    }

/* The four Cortex-M4 fault vectors that Harmony provides as weak. */
DEFINE_FAULT_HANDLER(HardFault_Handler,        "HardFault")
DEFINE_FAULT_HANDLER(MemoryManagement_Handler, "MemoryManagement")
DEFINE_FAULT_HANDLER(BusFault_Handler,         "BusFault")
DEFINE_FAULT_HANDLER(UsageFault_Handler,       "UsageFault")
