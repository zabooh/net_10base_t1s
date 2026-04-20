#include "cyclic_fire_cli.h"

#include <stdint.h>
#include <stdlib.h>

#include "cyclic_fire.h"
#include "ptp_clock.h"
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

/* MARKER variant: 1-period high + 4-periods low pattern (see cyclic_fire.h
 * CYCLIC_FIRE_PATTERN_MARKER).  Used to make the cross-board "who fires
 * first?" question visually unambiguous on a scope, when the cross-board
 * offset is smaller than a full period and possibly of varying sign. */
static void cyclic_start_marker_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    uint32_t period_us = CYCLIC_FIRE_DEFAULT_PERIOD_US;
    uint64_t anchor_ns = 0u;
    if (argc >= 2) {
        period_us = (uint32_t)strtoul(argv[1], NULL, 0);
    }
    if (argc >= 3) {
        anchor_ns = (uint64_t)strtoull(argv[2], NULL, 0);
    }
    if (!cyclic_fire_start_ex(period_us, anchor_ns, CYCLIC_FIRE_PATTERN_MARKER)) {
        SYS_CONSOLE_PRINT("cyclic_start_marker FAIL  (already running or PTP_CLOCK not valid)\r\n");
        return;
    }
    SYS_CONSOLE_PRINT("cyclic_start_marker OK  period=%lu us  anchor=%llu ns  "
                      "(1-high + 4-low pattern)\r\n",
                      (unsigned long)period_us,
                      (unsigned long long)anchor_ns);
}

/* Free-running variant: bootstraps PTP_CLOCK to local TC0 (each board on its
 * own crystal, no GM sync required) and starts cyclic_fire.  Intended for the
 * "before sync" half of the synchronisation demo — two boards run on
 * independent timebases and their PD10 edges drift apart at the crystal
 * mismatch rate. */
static void cyclic_start_free_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    uint32_t period_us = CYCLIC_FIRE_DEFAULT_PERIOD_US;
    if (argc >= 2) {
        period_us = (uint32_t)strtoul(argv[1], NULL, 0);
    }
    PTP_CLOCK_ForceSet(0u);
    if (!cyclic_fire_start(period_us, 0u)) {
        SYS_CONSOLE_PRINT("cyclic_start_free FAIL  (already running)\r\n");
        return;
    }
    SYS_CONSOLE_PRINT("cyclic_start_free OK  period=%lu us  (free-run, no PTP sync)\r\n",
                      (unsigned long)period_us);
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
    {"cyclic_start",        (SYS_CMD_FNC) cyclic_start_cmd,        ": start cyclic GPIO toggle — square wave (cyclic_start [period_us [anchor_ns]])"},
    {"cyclic_start_marker", (SYS_CMD_FNC) cyclic_start_marker_cmd, ": start cyclic GPIO toggle — 1-high+4-low pulse (cyclic_start_marker [period_us [anchor_ns]])"},
    {"cyclic_start_free",   (SYS_CMD_FNC) cyclic_start_free_cmd,   ": start cyclic GPIO toggle without PTP sync (cyclic_start_free [period_us])"},
    {"cyclic_stop",         (SYS_CMD_FNC) cyclic_stop_cmd,         ": stop cyclic GPIO toggle"},
    {"cyclic_status",       (SYS_CMD_FNC) cyclic_status_cmd,       ": show running state, period, cycles, misses"},
};

void CYCLIC_FIRE_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(cyclic_cmd_tbl,
                         (int)(sizeof(cyclic_cmd_tbl) / sizeof(*cyclic_cmd_tbl)),
                         "cyclic", ": cyclic-fire commands");
}
