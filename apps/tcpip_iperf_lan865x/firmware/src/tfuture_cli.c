#include "tfuture_cli.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "tfuture.h"
#include "ptp_clock.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

static void tfuture_at_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("Usage: tfuture_at <absolute_ns>\r\n");
        return;
    }
    uint64_t target_ns = (uint64_t)strtoull(argv[1], NULL, 0);
    if (!tfuture_arm_at_ns(target_ns)) {
        SYS_CONSOLE_PRINT("tfuture_at FAIL  (target not in future, PTP_CLOCK invalid, or already pending)\r\n");
        return;
    }
    SYS_CONSOLE_PRINT("tfuture_at OK  target=%llu ns\r\n",
                      (unsigned long long)target_ns);
}

static void tfuture_in_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("Usage: tfuture_in <ms_from_now>\r\n");
        return;
    }
    uint32_t ms = (uint32_t)strtoul(argv[1], NULL, 0);
    if (!tfuture_arm_in_ms(ms)) {
        SYS_CONSOLE_PRINT("tfuture_in FAIL  (PTP_CLOCK invalid, or already pending)\r\n");
        return;
    }
    SYS_CONSOLE_PRINT("tfuture_in OK  delay=%lu ms\r\n", (unsigned long)ms);
}

static void tfuture_cancel_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    tfuture_cancel();
    SYS_CONSOLE_PRINT("tfuture cancelled\r\n");
}

static void tfuture_status_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    tfuture_state_t st = tfuture_get_state();
    const char *ss = (st == TFUTURE_PENDING) ? "pending" :
                     (st == TFUTURE_FIRED)   ? "fired"   : "idle";
    SYS_CONSOLE_PRINT("tfuture state     : %s\r\n", ss);
    SYS_CONSOLE_PRINT("tfuture fires     : %lu\r\n", (unsigned long)tfuture_get_fire_count());
    SYS_CONSOLE_PRINT("drift correction  : %s\r\n",
                      tfuture_get_drift_correction() ? "ON" : "OFF");
    SYS_CONSOLE_PRINT("PTP_CLOCK drift   : %+ld ppb\r\n", (long)PTP_CLOCK_GetDriftPPB());
    uint64_t last_t = 0u, last_a = 0u;
    tfuture_get_last(&last_t, &last_a);
    if (last_t != 0u) {
        int64_t delta = (int64_t)(last_a - last_t);
        SYS_CONSOLE_PRINT("last target ns    : %llu\r\n", (unsigned long long)last_t);
        SYS_CONSOLE_PRINT("last actual ns    : %llu\r\n", (unsigned long long)last_a);
        SYS_CONSOLE_PRINT("last delta ns     : %+lld\r\n", (long long)delta);
    }
}

static void tfuture_drift_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("tfuture drift correction: %s\r\n",
                          tfuture_get_drift_correction() ? "ON" : "OFF");
        return;
    }
    bool enable;
    if (strcmp(argv[1], "on") == 0)        enable = true;
    else if (strcmp(argv[1], "off") == 0)  enable = false;
    else {
        SYS_CONSOLE_PRINT("Usage: tfuture_drift [on|off]\r\n");
        return;
    }
    tfuture_set_drift_correction(enable);
    SYS_CONSOLE_PRINT("tfuture drift correction: %s\r\n", enable ? "ON" : "OFF");
}

static void tfuture_reset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    tfuture_trace_reset();
    SYS_CONSOLE_PRINT("tfuture: trace reset\r\n");
}

static void tfuture_dump_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    tfuture_trace_dump();
}

static const SYS_CMD_DESCRIPTOR tfuture_cmd_tbl[] = {
    {"tfuture_at",     (SYS_CMD_FNC) tfuture_at_cmd,     ": arm firing at absolute PTP_CLOCK ns (tfuture_at <ns>)"},
    {"tfuture_in",     (SYS_CMD_FNC) tfuture_in_cmd,     ": arm firing at now + <ms> (tfuture_in <ms>)"},
    {"tfuture_cancel", (SYS_CMD_FNC) tfuture_cancel_cmd, ": cancel any pending tfuture"},
    {"tfuture_status", (SYS_CMD_FNC) tfuture_status_cmd, ": show tfuture state, fire count, last target/actual"},
    {"tfuture_reset",  (SYS_CMD_FNC) tfuture_reset_cmd,  ": clear tfuture ring buffer"},
    {"tfuture_dump",   (SYS_CMD_FNC) tfuture_dump_cmd,   ": dump all recorded fires (<target_ns> <actual_ns> <delta>)"},
    {"tfuture_drift",  (SYS_CMD_FNC) tfuture_drift_cmd,  ": enable/disable drift correction in tfuture (tfuture_drift [on|off])"},
};

void TFUTURE_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(tfuture_cmd_tbl,
                         (int)(sizeof(tfuture_cmd_tbl) / sizeof(*tfuture_cmd_tbl)),
                         "tfuture", ": tfuture commands");
}
