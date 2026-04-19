#include "cyclic_fire_cli.h"

#include <stdint.h>
#include <stdlib.h>

#include "cyclic_fire.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

static void cyclic_start_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    uint32_t period_us = CYCLIC_FIRE_DEFAULT_PERIOD_US;
    uint64_t anchor_ns = 0u;
    if (argc >= 2) {
        period_us = (uint32_t)strtoul(argv[1], NULL, 0);
    }
    if (argc >= 3) {
        anchor_ns = (uint64_t)strtoull(argv[2], NULL, 0);
    }
    if (!cyclic_fire_start(period_us, anchor_ns)) {
        SYS_CONSOLE_PRINT("cyclic_start FAIL  (already running or PTP_CLOCK not valid)\r\n");
        return;
    }
    SYS_CONSOLE_PRINT("cyclic_start OK  period=%lu us  anchor=%llu ns\r\n",
                      (unsigned long)period_us,
                      (unsigned long long)anchor_ns);
}

static void cyclic_stop_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    cyclic_fire_stop();
    SYS_CONSOLE_PRINT("cyclic stopped\r\n");
}

static void cyclic_status_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    SYS_CONSOLE_PRINT("cyclic running : %s\r\n",
                      cyclic_fire_is_running() ? "yes" : "no");
    SYS_CONSOLE_PRINT("period         : %lu us\r\n",
                      (unsigned long)cyclic_fire_get_period_us());
    SYS_CONSOLE_PRINT("cycles         : %llu\r\n",
                      (unsigned long long)cyclic_fire_get_cycle_count());
    SYS_CONSOLE_PRINT("misses         : %llu\r\n",
                      (unsigned long long)cyclic_fire_get_missed_count());
}

static const SYS_CMD_DESCRIPTOR cyclic_cmd_tbl[] = {
    {"cyclic_start",  (SYS_CMD_FNC) cyclic_start_cmd,  ": start cyclic GPIO toggle (cyclic_start [period_us [anchor_ns]])"},
    {"cyclic_stop",   (SYS_CMD_FNC) cyclic_stop_cmd,   ": stop cyclic GPIO toggle"},
    {"cyclic_status", (SYS_CMD_FNC) cyclic_status_cmd, ": show running state, period, cycles, misses"},
};

void CYCLIC_FIRE_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(cyclic_cmd_tbl,
                         (int)(sizeof(cyclic_cmd_tbl) / sizeof(*cyclic_cmd_tbl)),
                         "cyclic", ": cyclic-fire commands");
}
