#include "pd10_blink_cli.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "pd10_blink.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

/* blink [hz | stop]
 *   (no arg)   → start at 1000 Hz (1 kHz rectangle on PD10)
 *   <hz>       → start / retune to <hz> Hz  (any positive integer)
 *   0  | stop  → stop the blink; PD10 → LOW
 */
static void blink_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;

    if (argc >= 2 && strcmp(argv[1], "stop") == 0) {
        (void)pd10_blink_set_hz(0u);
        SYS_CONSOLE_PRINT("blink: stopped\r\n");
        return;
    }

    uint32_t hz = 1000u;
    if (argc >= 2) {
        char *end = NULL;
        unsigned long parsed = strtoul(argv[1], &end, 0);
        if (end == argv[1] || *end != '\0') {
            SYS_CONSOLE_PRINT("Usage: blink [hz | stop]    "
                              "(no arg → 1000 Hz, 0 or stop → halt)\r\n");
            return;
        }
        hz = (uint32_t)parsed;
    }

    if (hz == 0u) {
        (void)pd10_blink_set_hz(0u);
        SYS_CONSOLE_PRINT("blink: stopped\r\n");
        return;
    }

    if (!pd10_blink_set_hz(hz)) {
        SYS_CONSOLE_PRINT("blink FAIL: %lu Hz too high for SYS_TIME resolution\r\n",
                          (unsigned long)hz);
        return;
    }
    SYS_CONSOLE_PRINT("blink: running on PD10 at %lu Hz\r\n",
                      (unsigned long)hz);
}

static const SYS_CMD_DESCRIPTOR blink_cmd_tbl[] = {
    {"blink", (SYS_CMD_FNC) blink_cmd,
     ": PD10 rectangle (blink [hz|stop]; no arg → 1000 Hz; 0 or stop halts)"},
};

void PD10_BLINK_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(blink_cmd_tbl,
                         (int)(sizeof(blink_cmd_tbl) / sizeof(*blink_cmd_tbl)),
                         "blink", ": PD10 rectangle generator");
}
