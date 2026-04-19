#include "loop_stats_cli.h"

#include <string.h>

#include "loop_stats.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

static void loop_stats_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc >= 2 && strcmp(argv[1], "reset") == 0) {
        LOOP_STATS_Reset();
        SYS_CONSOLE_PRINT("loop_stats: reset\r\n");
        return;
    }
    LOOP_STATS_Print();
}

static const SYS_CMD_DESCRIPTOR loop_stats_cmd_tbl[] = {
    {"loop_stats", (SYS_CMD_FNC) loop_stats_cmd, ": main-loop per-subsystem timing (loop_stats [reset])"},
};

void LOOP_STATS_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(loop_stats_cmd_tbl,
                         (int)(sizeof(loop_stats_cmd_tbl) / sizeof(*loop_stats_cmd_tbl)),
                         "loop-stats", ": loop stats commands");
}
