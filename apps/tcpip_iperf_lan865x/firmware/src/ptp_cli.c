#include "ptp_cli.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "ptp_fol_task.h"
#include "ptp_gm_task.h"
#include "ptp_clock.h"
#include "ptp_offset_trace.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

static void ptp_mode_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    if (argc < 2) {
        ptpMode_t mode = PTP_FOL_GetMode();
        const char *modeStr = (mode == PTP_MASTER) ? "master" :
                              (mode == PTP_SLAVE)  ? "follower" : "off";
        SYS_CONSOLE_PRINT("PTP mode: %s\r\n", modeStr);
        return;
    }
    if (strcmp(argv[1], "off") == 0) {
        PTP_FOL_SetMode(PTP_DISABLED);
        PTP_GM_Deinit();
        SYS_CONSOLE_PRINT("PTP disabled\r\n");
    } else if (strcmp(argv[1], "master") == 0) {
        bool verbose = (argc >= 3) && (strcmp(argv[2], "v") == 0);
        PTP_FOL_SetMode(PTP_MASTER);
        PTP_GM_SetVerbose(verbose);
        PTP_GM_Init();
        SYS_CONSOLE_PRINT("PTP Grandmaster enabled%s\r\n", verbose ? " (verbose)" : "");
    } else if ((strcmp(argv[1], "follower") == 0) || (strcmp(argv[1], "slave") == 0)) {
        PTP_FOL_SetMode(PTP_SLAVE);
        bool verbose = (argc >= 3) && (strcmp(argv[2], "v") == 0);
        PTP_FOL_SetVerbose(verbose);
        SYS_CONSOLE_PRINT("PTP Follower enabled%s\r\n", verbose ? " (verbose)" : "");
    } else {
        SYS_CONSOLE_PRINT("Usage: ptp_mode [off|master [v]|follower [v]]\r\n");
    }
}

static void ptp_time_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    if (!PTP_CLOCK_IsValid()) {
        SYS_CONSOLE_PRINT("ptp_time: not valid (no PTP sync yet)\r\n");
        return;
    }
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();
    uint32_t sec    = (uint32_t)(now_ns / 1000000000ULL);
    uint32_t ns     = (uint32_t)(now_ns % 1000000000ULL);
    uint32_t h      = sec / 3600u;
    uint32_t m      = (sec % 3600u) / 60u;
    uint32_t s      = sec % 60u;
    SYS_CONSOLE_PRINT("ptp_time: %02lu:%02lu:%02lu.%09lu  drift=%+ldppb\r\n",
                      (unsigned long)h, (unsigned long)m,
                      (unsigned long)s, (unsigned long)ns,
                      (long)PTP_CLOCK_GetDriftPPB());
}

static void ptp_status_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    ptpMode_t mode = PTP_FOL_GetMode();
    const char *modeStr = (mode == PTP_MASTER) ? "master" :
                          (mode == PTP_SLAVE)  ? "follower" : "off";
    SYS_CONSOLE_PRINT("PTP mode   : %s\r\n", modeStr);

    if (mode == PTP_MASTER) {
        uint32_t syncCount = 0u, gmState = 0u;
        PTP_GM_GetStatus(&syncCount, &gmState);
        SYS_CONSOLE_PRINT("GM syncs   : %lu\r\n", (unsigned long)syncCount);
        SYS_CONSOLE_PRINT("GM state   : %lu\r\n", (unsigned long)gmState);
        ptp_gm_dst_mode_t dst = PTP_GM_GetDstMode();
        SYS_CONSOLE_PRINT("Dst mode   : %s\r\n", (dst == PTP_GM_DST_BROADCAST) ? "broadcast" : "multicast");
    } else if (mode == PTP_SLAVE) {
        int64_t  offset    = 0;
        uint64_t absOffset = 0u;
        PTP_FOL_GetOffset(&offset, &absOffset);
        SYS_CONSOLE_PRINT("Offset ns  : %ld\r\n", (long)offset);
        SYS_CONSOLE_PRINT("Abs off ns : %lu\r\n", (unsigned long)absOffset);
        SYS_CONSOLE_PRINT("Mean delay : %ld ns\r\n", (long)PTP_FOL_GetMeanPathDelay());
    }
}

static void ptp_interval_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    if (argc != 2) {
        SYS_CONSOLE_PRINT("Usage: ptp_interval <ms>  (range: 10..10000)\r\n");
        return;
    }
    uint32_t ms = (uint32_t)strtoul(argv[1], NULL, 0);
    PTP_GM_SetSyncInterval(ms);
    SYS_CONSOLE_PRINT("Sync interval set to %lu ms\r\n", (unsigned long)ms);
}

static void ptp_offset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    int64_t  offset    = 0;
    uint64_t absOffset = 0u;
    PTP_FOL_GetOffset(&offset, &absOffset);
    SYS_CONSOLE_PRINT("Offset: %ld ns  (abs: %lu ns)\r\n",
                      (long)offset, (unsigned long)absOffset);
}

static void ptp_reset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    PTP_FOL_Reset();
    SYS_CONSOLE_PRINT("PTP follower servo reset\r\n");
}

static void ptp_trace_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("Usage: ptp_trace [on|off]\r\n");
        return;
    }
    bool enable = (strcmp(argv[1], "on") == 0);
    PTP_FOL_SetTrace(enable);
    PTP_GM_SetTrace(enable);
    SYS_CONSOLE_PRINT("PTP trace %s\r\n", enable ? "enabled" : "disabled");
}

static void ptp_dst_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    if (argc < 2) {
        ptp_gm_dst_mode_t dst = PTP_GM_GetDstMode();
        SYS_CONSOLE_PRINT("PTP dst: %s\r\n", (dst == PTP_GM_DST_BROADCAST) ? "broadcast" : "multicast");
        return;
    }
    if (strcmp(argv[1], "broadcast") == 0) {
        PTP_GM_SetDstMode(PTP_GM_DST_BROADCAST);
        SYS_CONSOLE_PRINT("PTP dst set to broadcast\r\n");
    } else if (strcmp(argv[1], "multicast") == 0) {
        PTP_GM_SetDstMode(PTP_GM_DST_MULTICAST);
        SYS_CONSOLE_PRINT("PTP dst set to multicast\r\n");
    } else {
        SYS_CONSOLE_PRINT("Usage: ptp_dst [multicast|broadcast]\r\n");
    }
}

static void clk_set_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc != 2) {
        SYS_CONSOLE_PRINT("Usage: clk_set <ns>\r\n");
        return;
    }
    uint64_t ns = (uint64_t)strtoull(argv[1], NULL, 0);
    PTP_CLOCK_ForceSet(ns);
    SYS_CONSOLE_PRINT("clk_set ok: %llu ns\r\n", (unsigned long long)ns);
}

static void clk_get_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    if (!PTP_CLOCK_IsValid()) {
        SYS_CONSOLE_PRINT("clk_get: not valid\r\n");
        return;
    }
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();
    SYS_CONSOLE_PRINT("clk_get: %llu ns  drift=%+ldppb\r\n",
                      (unsigned long long)now_ns, (long)PTP_CLOCK_GetDriftPPB());
}

static void ptp_offset_reset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    ptp_offset_trace_reset();
    SYS_CONSOLE_PRINT("ptp_offset: reset\r\n");
}

static void ptp_offset_dump_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    ptp_offset_trace_dump();
}

static void clk_set_drift_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("clk_set_drift: current drift_ppb = %+ld\r\n",
                          (long)PTP_CLOCK_GetDriftPPB());
        return;
    }
    int32_t ppb = (int32_t)strtol(argv[1], NULL, 0);
    PTP_CLOCK_SetDriftPPB(ppb);
    SYS_CONSOLE_PRINT("clk_set_drift: drift_ppb forced to %+ld\r\n", (long)ppb);
}

static void drift_iir_n_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("drift_iir_n: current = %ld  (default 128, range 8..4096)\r\n",
                          (long)PTP_CLOCK_GetDriftIIRN());
        SYS_CONSOLE_PRINT("  larger N -> lower jitter floor (1/sqrt(N)) but slower\r\n"
                          "  convergence (half-life ~ 0.7*N samples * 125 ms Sync interval)\r\n");
        return;
    }
    int32_t n = (int32_t)strtol(argv[1], NULL, 0);
    PTP_CLOCK_SetDriftIIRN(n);
    SYS_CONSOLE_PRINT("drift_iir_n set to %ld\r\n", (long)PTP_CLOCK_GetDriftIIRN());
}

static void drift_iir_reset_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO; (void)argc; (void)argv;
    PTP_CLOCK_ResetDriftFilter();
    SYS_CONSOLE_PRINT("drift_iir_reset: warm-up ramp re-armed (samples=0)\r\n");
}

static void ptp_gm_delay_cmd(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    (void)pCmdIO;
    if (argc < 2) {
        SYS_CONSOLE_PRINT("ptp_gm_delay: %lld ns\r\n",
                          (long long)PTP_GM_GetExtraAnchorDelay());
        return;
    }
    int64_t ns = (int64_t)strtoll(argv[1], NULL, 0);
    PTP_GM_SetExtraAnchorDelay(ns);
    SYS_CONSOLE_PRINT("ptp_gm_delay set to %lld ns\r\n", (long long)ns);
}

static const SYS_CMD_DESCRIPTOR ptp_cmd_tbl[] = {
    {"ptp_mode",         (SYS_CMD_FNC) ptp_mode_cmd,         ": set/get PTP mode (ptp_mode [off|master|follower])"},
    {"ptp_status",       (SYS_CMD_FNC) ptp_status_cmd,       ": show PTP status"},
    {"ptp_time",         (SYS_CMD_FNC) ptp_time_cmd,         ": show software PTP wallclock time"},
    {"ptp_interval",     (SYS_CMD_FNC) ptp_interval_cmd,     ": set GM Sync interval (ptp_interval <ms>)"},
    {"ptp_offset",       (SYS_CMD_FNC) ptp_offset_cmd,       ": show follower clock offset in ns"},
    {"ptp_reset",        (SYS_CMD_FNC) ptp_reset_cmd,        ": reset follower servo to UNINIT"},
    {"ptp_trace",        (SYS_CMD_FNC) ptp_trace_cmd,        ": enable/disable PTP Delay trace (ptp_trace [on|off])"},
    {"ptp_dst",          (SYS_CMD_FNC) ptp_dst_cmd,          ": set/get PTP destination MAC (ptp_dst [multicast|broadcast])"},
    {"clk_set",          (SYS_CMD_FNC) clk_set_cmd,          ": set software clock to <ns>, reset drift (clk_set <ns>)"},
    {"clk_get",          (SYS_CMD_FNC) clk_get_cmd,          ": read current software clock value in ns"},
    {"ptp_offset_reset", (SYS_CMD_FNC) ptp_offset_reset_cmd, ": clear PTP offset ring buffer"},
    {"ptp_offset_dump",  (SYS_CMD_FNC) ptp_offset_dump_cmd,  ": dump all recorded offsets (one per line, <ns> <status>)"},
    {"ptp_gm_delay",     (SYS_CMD_FNC) ptp_gm_delay_cmd,     ": diagnostic: set extra ns added to GM anchor_wc (ptp_gm_delay [<ns>])"},
    {"clk_set_drift",    (SYS_CMD_FNC) clk_set_drift_cmd,    ": diagnostic: manually set PTP_CLOCK drift_ppb (clk_set_drift [<ppb>])"},
    {"drift_iir_n",      (SYS_CMD_FNC) drift_iir_n_cmd,      ": get/set PTP_CLOCK drift IIR window N (drift_iir_n [<8..4096>])"},
    {"drift_iir_reset",  (SYS_CMD_FNC) drift_iir_reset_cmd,  ": re-arm adaptive IIR warm-up (fast convergence with low jitter floor)"},
};

void PTP_CLI_Register(void) {
    (void)SYS_CMD_ADDGRP(ptp_cmd_tbl,
                         (int)(sizeof(ptp_cmd_tbl) / sizeof(*ptp_cmd_tbl)),
                         "PTP", ": PTP commands");
}
