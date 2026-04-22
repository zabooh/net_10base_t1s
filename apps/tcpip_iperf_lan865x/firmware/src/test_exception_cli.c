#include "test_exception_cli.h"

#include <stdint.h>
#include <string.h>

#include "device.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

/*
 * test_exception — deliberately trigger a Cortex-M4 fault to exercise
 * the strong fault handlers in exception_handler.c.  Useful when you
 * suspect the firmware is crashing intermittently (e.g. follower
 * stops responding after several minutes) and want to verify the
 * register-dump-then-reset path actually works on the wire.
 *
 *   test_exception              — print usage
 *   test_exception null_read    — load from NULL → BusFault → escalates
 *   test_exception null_write   — store to NULL  → BusFault → escalates
 *   test_exception unaligned    — unaligned word access → UsageFault if
 *                                 UNALIGN_TRP enabled, BusFault otherwise
 *   test_exception undef        — undefined instruction → UsageFault
 *   test_exception divzero      — integer divide-by-zero → UsageFault if
 *                                 DIV_0_TRP enabled (we set it here)
 *   test_exception svcall       — direct SVC instruction → SVCall
 *                                 (will also exercise stacked dump path)
 */

static void usage(void)
{
    SYS_CONSOLE_PRINT(
        "test_exception <kind>  — deliberately crash the CPU to test the\r\n"
        "                         fault-dump-and-reset path.\r\n"
        "\r\n"
        "Available kinds (type the command shown in the first column):\r\n"
        "\r\n"
        "  test_exception null_read    load  from NULL  -> BusFault (PRECISERR)\r\n"
        "  test_exception null_write   store to   NULL  -> BusFault (PRECISERR)\r\n"
        "  test_exception unaligned    unaligned 32-bit load -> UsageFault\r\n"
        "                              (UNALIGNED) once UNALIGN_TRP is set\r\n"
        "  test_exception undef        execute undefined instr -> UsageFault\r\n"
        "                              (UNDEFINSTR)\r\n"
        "  test_exception divzero      integer / 0 -> UsageFault (DIVBYZERO)\r\n"
        "                              once DIV_0_TRP is set\r\n"
        "  test_exception svcall       SVC #0 -> SVCall_Handler dump path\r\n"
        "  test_exception hang         busy-loop, IRQs ON  -> WDT Early-Warning\r\n"
        "                              fires after ~1 s and dumps 'WatchdogEW'\r\n"
        "  test_exception hang_irqoff  busy-loop, IRQs OFF -> EW masked,\r\n"
        "                              hardware WDT reset @ ~2 s (no dump)\r\n"
        "\r\n"
        "All faulting kinds dump CFSR/HFSR/MMFAR/BFAR + R0-R3,R12,LR,PC,xPSR\r\n"
        "via SERCOM1 then issue NVIC_SystemReset(); decode with\r\n"
        "find_exception.py.\r\n");
}

/* Each trigger function is no-inline + volatile-cast to defeat the
 * compiler's "I see what you're doing" optimiser. */

static void __attribute__((noinline)) trigger_null_read(void)
{
    volatile uint32_t *p = (volatile uint32_t *)0;
    volatile uint32_t v;
    v = *p;          /* fault here */
    (void)v;
}

static void __attribute__((noinline)) trigger_null_write(void)
{
    volatile uint32_t *p = (volatile uint32_t *)0;
    *p = 0xDEADBEEFu; /* fault here */
}

static void __attribute__((noinline)) trigger_unaligned(void)
{
    volatile uint8_t  buf[16] = {0};
    volatile uint32_t *p = (volatile uint32_t *)(uintptr_t)(&buf[1]);
    volatile uint32_t v;
    v = *p;          /* unaligned 32-bit load */
    (void)v;
}

static void __attribute__((noinline)) trigger_undef(void)
{
    /* Encoding 0xF7F0A000 is a permanently undefined instruction in
     * the Thumb-2 encoding map (UDF.W #0). */
    __asm volatile (".word 0xF7F0A000");
}

static void __attribute__((noinline)) trigger_divzero(void)
{
    /* Need DIV_0_TRP set in CCR for this to fault.  Set it here in
     * case it isn't on by default. */
    SCB->CCR |= SCB_CCR_DIV_0_TRP_Msk;
    __DSB();
    __ISB();
    volatile int32_t a = 1;
    volatile int32_t b = 0;
    volatile int32_t r;
    r = a / b;       /* fault on UDIV/SDIV by zero */
    (void)r;
}

static void __attribute__((noinline)) trigger_svcall(void)
{
    /* SVC traps to SVCall_Handler (different vector — Harmony's weak
     * default just spins).  Useful for verifying the dump path
     * when you replace SVCall too. */
    __asm volatile ("svc #0");
}

static void __attribute__((noinline, noreturn)) trigger_hang(void)
{
    /* Plain busy-loop with interrupts still enabled → WDT Early-Warning
     * fires after ~1 s and dumps "WatchdogEW" via the shared
     * exception path. */
    SYS_CONSOLE_PRINT("test_exception: hanging in while(1) "
                      "(IRQs on) — WDT EW should fire in ~1 s\r\n");
    for (;;) { __asm volatile ("nop"); }
}

static void __attribute__((noinline, noreturn)) trigger_hang_irqoff(void)
{
    /* Disable interrupts globally so the WDT EW NVIC vector cannot
     * fire — the hardware WDT reset at 2 s is the only escape route.
     * No dump in this scenario, but the controller DOES recover. */
    SYS_CONSOLE_PRINT("test_exception: hanging in while(1) with PRIMASK "
                      "set — only hardware WDT reset @ 2 s saves us\r\n");
    __asm volatile ("cpsid i" ::: "memory");
    for (;;) { __asm volatile ("nop"); }
}

static void test_exception_cmd(SYS_CMD_DEVICE_NODE *p, int argc, char **argv)
{
    (void)p;
    if (argc < 2) { usage(); return; }
    const char *k = argv[1];

    /* Make sure UsageFault and BusFault are individually enabled in
     * SHCSR, otherwise they all escalate to HardFault.  HardFault still
     * gets dumped, but the per-fault status bits in CFSR are clearer
     * when the dedicated handler fires. */
    SCB->SHCSR |= SCB_SHCSR_USGFAULTENA_Msk
                |  SCB_SHCSR_BUSFAULTENA_Msk
                |  SCB_SHCSR_MEMFAULTENA_Msk;
    /* Also enable trap for unaligned word/halfword access. */
    SCB->CCR   |= SCB_CCR_UNALIGN_TRP_Msk;
    __DSB();
    __ISB();

    SYS_CONSOLE_PRINT("test_exception: about to trigger '%s' — controller "
                      "should dump + reset.\r\n", k);

    if      (strcmp(k, "null_read")   == 0) trigger_null_read();
    else if (strcmp(k, "null_write")  == 0) trigger_null_write();
    else if (strcmp(k, "unaligned")   == 0) trigger_unaligned();
    else if (strcmp(k, "undef")       == 0) trigger_undef();
    else if (strcmp(k, "divzero")     == 0) trigger_divzero();
    else if (strcmp(k, "svcall")      == 0) trigger_svcall();
    else if (strcmp(k, "hang")        == 0) trigger_hang();
    else if (strcmp(k, "hang_irqoff") == 0) trigger_hang_irqoff();
    else { usage(); return; }

    /* If we get here the trigger didn't fault — usually means the CCR
     * trap bit wasn't honoured (e.g. divzero on a chip without UDIV
     * trap support). */
    SYS_CONSOLE_PRINT("test_exception: trigger returned without faulting\r\n");
}

static const SYS_CMD_DESCRIPTOR test_exc_cmd_tbl[] = {
    {"test_exception", (SYS_CMD_FNC)test_exception_cmd,
     ": deliberately trigger a CPU fault (test_exception <kind>)"},
};

void TEST_EXCEPTION_CLI_Register(void)
{
    (void)SYS_CMD_ADDGRP(test_exc_cmd_tbl,
                         (int)(sizeof(test_exc_cmd_tbl) / sizeof(*test_exc_cmd_tbl)),
                         "test-exception",
                         ": deliberate-fault trigger commands");
}
