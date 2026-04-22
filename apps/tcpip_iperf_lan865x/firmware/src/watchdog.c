#include "watchdog.h"

#include <stdint.h>
#include "device.h"

/* fault_dump_and_reset() lives in exception_handler.c.  Re-used here
 * for the Early-Warning crash dump so we don't duplicate the UART
 * formatter.  See exception_handler.c for prototype + behaviour. */
extern void fault_dump_and_reset(uint32_t *sp,
                                 const char *label,
                                 uint32_t exc_return) __attribute__((noreturn));

/* -------------------------------------------------------------------------
 * Bring-up
 * ------------------------------------------------------------------------ */

void watchdog_init(void)
{
    /* APBA clock gate for the WDT peripheral. */
    MCLK_REGS->MCLK_APBAMASK |= MCLK_APBAMASK_WDT_Msk;

    /* WDT runs from CLK_WDT_OSC = OSCULP32K / 32 = 1024 Hz on this part.
     * No GCLK setup required.
     *
     * PER       = CYC2048  → 2048 / 1024 = 2.000 s total timeout
     * EWOFFSET  = CYC1024  → 1024 / 1024 = 1.000 s elapsed when EW fires
     * → Main loop has ~1 s to keep kicking before EW.  After EW fires,
     *   another ~1 s of breathing room before the hardware reset.
     *
     * If WDT was already enabled (e.g. by a previous run after software
     * reset), disable it first — CTRLA writes are otherwise ignored
     * while ENABLE=1. */
    if ((WDT_REGS->WDT_CTRLA & WDT_CTRLA_ENABLE_Msk) != 0u) {
        WDT_REGS->WDT_CTRLA = 0u;
        while ((WDT_REGS->WDT_SYNCBUSY & WDT_SYNCBUSY_ENABLE_Msk) != 0u) { }
    }

    /* PER=CYC4096 → 4 s total (was 2 s — too tight for the boot path:
     * Harmony stack + LAN865x init + PTP_FOL_Init can take 2-3 s and
     * the main loop wasn't kicking yet, causing a reset-loop on boot).
     * EW @ CYC2048 → 2 s elapsed leaves another 2 s before reset. */
    WDT_REGS->WDT_CONFIG  = WDT_CONFIG_PER_CYC4096;
    WDT_REGS->WDT_EWCTRL  = WDT_EWCTRL_EWOFFSET_CYC2048;
    WDT_REGS->WDT_INTFLAG = WDT_INTFLAG_EW_Msk;       /* W1C any stale flag */
    WDT_REGS->WDT_INTENSET = WDT_INTENSET_EW_Msk;

    /* NVIC: priority equal to the other custom ISRs (3) so the EW
     * fires at the next breathing window even if a long-running ISR
     * is currently active (NVIC priority same → tail-chain). */
    NVIC_SetPriority(WDT_IRQn, 3);
    NVIC_EnableIRQ(WDT_IRQn);

    /* Enable.  ALWAYSON would prevent later disable for a sleep mode —
     * leave it OFF so PTP_FOL_Reset() / debug pause can still stop
     * the WDT if needed.  Once ENABLE is set the timer starts
     * counting immediately and CONFIG/EWCTRL/etc. are read-only until
     * the next SWRST. */
    WDT_REGS->WDT_CTRLA  = WDT_CTRLA_ENABLE_Msk;
    while ((WDT_REGS->WDT_SYNCBUSY & WDT_SYNCBUSY_ENABLE_Msk) != 0u) { }

    /* First kick so the operator gets a clean 2-s window even if init
     * happened mid-cycle. */
    watchdog_kick();
}

void watchdog_kick(void)
{
    /* Wait for any pending CLEAR sync from the previous kick — a
     * back-to-back kick from an ISR could otherwise be silently
     * dropped.  At 1024 Hz the sync window is 1 ms max, so this loop
     * is in practice always taken zero or one times. */
    while ((WDT_REGS->WDT_SYNCBUSY & WDT_SYNCBUSY_CLEAR_Msk) != 0u) { }
    WDT_REGS->WDT_CLEAR = WDT_CLEAR_CLEAR_KEY;
}

/* -------------------------------------------------------------------------
 * Early-Warning ISR — the firmware has been silent for ~1 s.  Capture
 * the active stack pointer the same way the fault trampolines do and
 * jump into the shared dumper.  Naked function so no prologue
 * perturbs the captured frame.
 * ------------------------------------------------------------------------ */

void __attribute__((naked, noreturn)) WDT_Handler(void)
{
    __asm volatile (
        "tst   lr, #4              \n"   /* MSP or PSP?               */
        "ite   eq                  \n"
        "mrseq r0, msp             \n"
        "mrsne r0, psp             \n"
        "ldr   r1, =wdt_label_str  \n"
        "mov   r2, lr              \n"
        "b     fault_dump_and_reset\n"
    );
}

/* Label string referenced from inline asm above.  Defined here at
 * file scope so the ldr =wdt_label_str literal load resolves. */
const char wdt_label_str[] __attribute__((used)) =
    "WatchdogEW (firmware hung — main loop did not kick the WDT for ~1 s)";
