#include "sw_ntp_cli.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "sw_ntp.h"
#include "sw_ntp_offset_trace.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

/* Parse "a.b.c.d" into host-order uint32.  Returns true on success. */
static bool sw_ntp_parse_ip(const char *s, uint32_t *out)
{
    uint32_t a[4] = {0u, 0u, 0u, 0u};
    int      idx  = 0;
    char     c;
    while ((c = *s++) != '\0') {
        if (c == '.') {
            if (++idx > 3) return false;
        } else if ((c >= '0') && (c <= '9')) {
            a[idx] = a[idx] * 10u + (uint32_t)(c - '0');
            if (a[idx] > 255u) return false;
        } else {
            return false;
        }
    }
    if (idx != 3) return false;
    *out = (a[0] << 24) | (a[1] << 16) | (a[2] << 8) | a[3];
    return true;
}

static void sw_ntp_mode_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        sw_ntp_mode_t m = sw_ntp_get_mode();
        const char *ms = (m == SW_NTP_MASTER)   ? "master" :
                         (m == SW_NTP_FOLLOWER) ? "follower" : "off";
        SYS_CONSOLE_PRINT("SW-NTP mode: %s\r\n", ms);
        return;
    }
    if (strcmp(argv[1], "off") == 0) {
        sw_ntp_set_mode(SW_NTP_OFF);
        SYS_CONSOLE_PRINT("SW-NTP disabled\r\n");
    } else if (strcmp(argv[1], "master") == 0) {
        sw_ntp_set_mode(SW_NTP_MASTER);
        SYS_CONSOLE_PRINT("SW-NTP master enabled\r\n");
    } else if (strcmp(argv[1], "follower") == 0) {
        uint32_t ip = 0u;
        if (argc < 3 || !sw_ntp_parse_ip(argv[2], &ip)) {
            SYS_CONSOLE_PRINT("Usage: sw_ntp_mode follower <master_ip>\r\n");
            return;
        }
        sw_ntp_set_master_ip(ip);
        sw_ntp_set_mode(SW_NTP_FOLLOWER);
        SYS_CONSOLE_PRINT("SW-NTP follower enabled, master=%s\r\n", argv[2]);
    } else {
        SYS_CONSOLE_PRINT("Usage: sw_ntp_mode [off|master|follower <ip>]\r\n");
    }
}

static void sw_ntp_poll_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("SW-NTP poll interval: %lu ms\r\n",
                          (unsigned long)sw_ntp_get_poll_interval_ms());
        return;
    }
    uint32_t ms = (uint32_t)strtoul(argv[1], NULL, 0);
    sw_ntp_set_poll_interval_ms(ms);
    SYS_CONSOLE_PRINT("SW-NTP poll interval set to %lu ms\r\n",
                      (unsigned long)sw_ntp_get_poll_interval_ms());
}

static void sw_ntp_trace_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("Usage: sw_ntp_trace [on|off]\r\n");
        return;
    }
    bool enable = (strcmp(argv[1], "on") == 0);
    sw_ntp_set_verbose(enable);
    SYS_CONSOLE_PRINT("SW-NTP trace %s\r\n", enable ? "enabled" : "disabled");
}

static void sw_ntp_status_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    sw_ntp_mode_t m = sw_ntp_get_mode();
    const char *ms = (m == SW_NTP_MASTER)   ? "master" :
                     (m == SW_NTP_FOLLOWER) ? "follower" : "off";
    SYS_CONSOLE_PRINT("SW-NTP mode    : %s\r\n", ms);
    SYS_CONSOLE_PRINT("Poll interval  : %lu ms\r\n",
                      (unsigned long)sw_ntp_get_poll_interval_ms());
    if (m == SW_NTP_FOLLOWER) {
        uint32_t samples = 0u, timeouts = 0u;
        int64_t  last    = 0;
        sw_ntp_get_stats(&samples, &timeouts, &last);
        SYS_CONSOLE_PRINT("Samples        : %lu\r\n", (unsigned long)samples);
        SYS_CONSOLE_PRINT("Timeouts       : %lu\r\n", (unsigned long)timeouts);
        SYS_CONSOLE_PRINT("Last offset ns : %lld\r\n", (long long)last);
    }
}

static void sw_ntp_offset_reset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    sw_ntp_offset_trace_reset();
    SYS_CONSOLE_PRINT("sw_ntp_offset: reset\r\n");
}

static void sw_ntp_offset_dump_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    sw_ntp_offset_trace_dump();
}

static const SYS_CMD_DESCRIPTOR sw_ntp_cmd_tbl[] = {
    {"sw_ntp_mode",        (SYS_CMD_FNC) sw_ntp_mode_cmd,        ": set/get SW-NTP mode (sw_ntp_mode [off|master|follower <ip>])"},
    {"sw_ntp_poll",        (SYS_CMD_FNC) sw_ntp_poll_cmd,        ": set/get SW-NTP poll interval ms (sw_ntp_poll [<ms>])"},
    {"sw_ntp_status",      (SYS_CMD_FNC) sw_ntp_status_cmd,      ": show SW-NTP status"},
    {"sw_ntp_trace",       (SYS_CMD_FNC) sw_ntp_trace_cmd,       ": enable/disable SW-NTP trace (sw_ntp_trace [on|off])"},
    {"sw_ntp_offset_reset",(SYS_CMD_FNC) sw_ntp_offset_reset_cmd, ": clear SW-NTP offset ring buffer"},
    {"sw_ntp_offset_dump", (SYS_CMD_FNC) sw_ntp_offset_dump_cmd,  ": dump all recorded SW-NTP offsets (one per line, <ns> <valid>)"},
};

void SW_NTP_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(sw_ntp_cmd_tbl,
                         (int)(sizeof(sw_ntp_cmd_tbl) / sizeof(*sw_ntp_cmd_tbl)),
                         "SW-NTP", ": SW-NTP commands");
}
