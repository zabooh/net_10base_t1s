#include "demo_cli.h"

#include <stdlib.h>
#include <string.h>

#include "standalone_demo.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

/* demo_autopilot [on | off]
 *   (no arg) → print current state
 *   on       → re-hook the cyclic_fire user-callback; the demo's
 *              watchdog will restart cyclic_fire on its next tick.
 *   off      → unhook the user-callback AND stop cyclic_fire, leaving
 *              PD10 and cyclic_fire ownership to the CLI caller.  The
 *              watchdog-driven auto-restart is gated off.  Intended
 *              for bench test scripts that need to control cyclic_fire
 *              period / pattern without the demo racing them.
 */
static void demo_autopilot_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;

    if (argc < 2) {
        SYS_CONSOLE_PRINT("demo_autopilot: %s\r\n",
                          standalone_demo_is_enabled() ? "on" : "off");
        return;
    }

    if (strcmp(argv[1], "on") == 0) {
        standalone_demo_set_enabled(true);
        SYS_CONSOLE_PRINT("demo_autopilot: on (user_cb re-hooked; watchdog armed)\r\n");
    } else if (strcmp(argv[1], "off") == 0) {
        standalone_demo_set_enabled(false);
        SYS_CONSOLE_PRINT("demo_autopilot: off (user_cb unhooked; cyclic_fire stopped)\r\n");
    } else {
        SYS_CONSOLE_PRINT("Usage: demo_autopilot [on|off]  "
                          "(no arg -> show state)\r\n");
    }
}

/* demo_pd10_slot [us]
 *   (no arg) → print current LED1/PD10 half-period slot in µs
 *   <us>     → set slot to <us> µs (half-period).  Full rectangle
 *              period = 2·<us> µs, frequency = 500_000 / <us> Hz.
 *              Minimum is the fire_callback half-period (250 µs);
 *              smaller values are silently clamped up.
 *   0        → reset to default (500_000 µs = 1 Hz rectangle)
 * Because the decimator reads PTP_CLOCK on every fire, raising the
 * rate does NOT degrade cross-board sync — unlike cyclic_start's
 * SQUARE pattern which fires on TC1 local-crystal time.
 */
static void demo_pd10_slot_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        uint64_t cur_ns = standalone_demo_get_led1_slot_ns();
        SYS_CONSOLE_PRINT("demo_pd10_slot: %lu us  (rectangle freq = %lu Hz)\r\n",
                          (unsigned long)(cur_ns / 1000ULL),
                          (unsigned long)(500000000ULL / cur_ns));
        return;
    }
    unsigned long us = strtoul(argv[1], NULL, 0);
    if (us == 0u) {
        standalone_demo_set_led1_slot_ns(500000000ULL);
        SYS_CONSOLE_PRINT("demo_pd10_slot: reset to default (500000 us = 1 Hz)\r\n");
        return;
    }
    standalone_demo_set_led1_slot_ns((uint64_t)us * 1000ULL);
    uint64_t actual_ns = standalone_demo_get_led1_slot_ns();
    SYS_CONSOLE_PRINT("demo_pd10_slot: %lu us  (actual after clamp = %lu us; "
                      "rectangle freq = %lu Hz)\r\n",
                      us,
                      (unsigned long)(actual_ns / 1000ULL),
                      (unsigned long)(500000000ULL / actual_ns));
}

/* demo_cyclic_period [us]
 *   (no arg) → print current cyclic_fire period in µs
 *   <us>     → hot-apply new period.  fire_callback rate becomes
 *              2/period (i.e. every period/2 µs).  Sharper PD10 edge
 *              timing at lower values, less ISR load at higher values.
 *   0        → reset to compile-time default (500 µs).
 *
 * Keeps the demo running — the setter internally does cyclic_fire_stop
 * + cyclic_fire_start_ex with the new period and SILENT pattern, so
 * PD10 continues without a visible interruption.
 */
static void demo_cyclic_period_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        uint32_t cur_us = standalone_demo_get_cyclic_period_us();
        SYS_CONSOLE_PRINT("demo_cyclic_period: %lu us  "
                          "(fire rate = %lu kHz)\r\n",
                          (unsigned long)cur_us,
                          (unsigned long)(2000UL / cur_us));
        return;
    }
    unsigned long us = strtoul(argv[1], NULL, 0);
    standalone_demo_set_cyclic_period_us((uint32_t)us);
    uint32_t actual_us = standalone_demo_get_cyclic_period_us();
    SYS_CONSOLE_PRINT("demo_cyclic_period: %lu us  "
                      "(fire rate = %lu kHz; PD10 slot auto-clamped to "
                      "%lu us if smaller than half-period)\r\n",
                      (unsigned long)actual_us,
                      (unsigned long)(2000UL / actual_us),
                      (unsigned long)(standalone_demo_get_led1_slot_ns() / 1000ULL));
}

static const SYS_CMD_DESCRIPTOR demo_cmd_tbl[] = {
    {"demo_autopilot",     (SYS_CMD_FNC) demo_autopilot_cmd,
     ": enable/disable demo autopilot (watchdog + decimator) — "
     "off hands PD10 ownership to the caller"},
    {"demo_pd10_slot",     (SYS_CMD_FNC) demo_pd10_slot_cmd,
     ": set/get LED1/PD10 half-period in us (default 500000 = 1 Hz)"},
    {"demo_cyclic_period", (SYS_CMD_FNC) demo_cyclic_period_cmd,
     ": set/get cyclic_fire rectangle period in us (default 500; "
     "controls decimator sample rate)"},
};

void DEMO_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(demo_cmd_tbl,
                         (int)(sizeof(demo_cmd_tbl) / sizeof(*demo_cmd_tbl)),
                         "demo", ": standalone_demo control");
}
