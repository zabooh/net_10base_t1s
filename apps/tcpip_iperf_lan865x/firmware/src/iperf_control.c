#include "iperf_control.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "system/console/sys_console.h"
#include "system/command/sys_command.h"
#include "config/default/library/tcpip/tcpip.h"
#include "config/default/library/tcpip/tcpip_manager.h"

/* Declared non-static in config/default/library/tcpip/src/iperf.c so we
 * can dispatch them without going through the SYS_CMD console parser. */
extern void CommandIperfStart(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv);
extern void CommandIperfStop (SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv);

/* -------------------------------------------------------------------------
 * Stub SYS_CMD_DEVICE_NODE routing iperf's output to the main console.
 * ---------------------------------------------------------------------- */

static void stub_msg(const void *p, const char *str)
{
    (void)p;
    if (str) SYS_CONSOLE_PRINT("%s", str);
}

static void stub_print(const void *p, const char *fmt, ...)
{
    (void)p;
    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    (void)vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    SYS_CONSOLE_PRINT("%s", buf);
}

static void stub_putc(const void *p, char c)
{
    (void)p;
    char s[2] = { c, '\0' };
    SYS_CONSOLE_PRINT("%s", s);
}

static int  stub_isrdy(const void *p) { (void)p; return 0; }
static char stub_getc (const void *p) { (void)p; return 0; }

static const SYS_CMD_API s_stub_api = {
    .msg    = stub_msg,
    .print  = stub_print,
    .putc_t = stub_putc,
    .isRdy  = stub_isrdy,
    .getc_t = stub_getc,
};

static SYS_CMD_DEVICE_NODE s_stub_cmd_io = {
    .pCmdApi    = &s_stub_api,
    .cmdIoParam = NULL,
};

/* -------------------------------------------------------------------------
 * IP configuration
 * ---------------------------------------------------------------------- */

bool iperf_control_set_ip(const char *ip_str, const char *mask_str)
{
    TCPIP_NET_HANDLE net = TCPIP_STACK_IndexToNet(0);
    if (net == NULL) {
        SYS_CONSOLE_PRINT("[IPERF] no network interface index 0\r\n");
        return false;
    }
    IPV4_ADDR ip;
    IPV4_ADDR mask;
    if (!TCPIP_Helper_StringToIPAddress(ip_str, &ip)) {
        SYS_CONSOLE_PRINT("[IPERF] bad IP '%s'\r\n", ip_str);
        return false;
    }
    if (!TCPIP_Helper_StringToIPAddress(mask_str, &mask)) {
        SYS_CONSOLE_PRINT("[IPERF] bad netmask '%s'\r\n", mask_str);
        return false;
    }
    if (!TCPIP_STACK_NetAddressSet(net, &ip, &mask, false)) {
        SYS_CONSOLE_PRINT("[IPERF] TCPIP_STACK_NetAddressSet failed\r\n");
        return false;
    }
    SYS_CONSOLE_PRINT("[IPERF] IP set to %s / %s\r\n", ip_str, mask_str);
    return true;
}

/* -------------------------------------------------------------------------
 * iperf dispatch
 * ---------------------------------------------------------------------- */

void iperf_control_server_start(void)
{
    static char a0[] = "iperf";
    static char a1[] = "-s";
    char *argv[] = { a0, a1, NULL };
    SYS_CONSOLE_PRINT("[IPERF] starting TCP server on :5001\r\n");
    CommandIperfStart(&s_stub_cmd_io, 2, argv);
}

void iperf_control_client_start(const char *server_ip)
{
    static char a0[] = "iperf";
    static char a1[] = "-c";
    static char a3[] = "-b";
    /* Rate-limit below 10BASE-T1S PLCA saturation so TCPIPStack
     * TX-backpressure asserts stop flooding the console.  4 Mbps is
     * well inside the observed ~4-6 Mbps achievable TCP goodput. */
    static char a4[] = "4000000";
    static char ip_buf[48];
    (void)strncpy(ip_buf, server_ip, sizeof(ip_buf) - 1u);
    ip_buf[sizeof(ip_buf) - 1u] = '\0';
    char *argv[] = { a0, a1, ip_buf, a3, a4, NULL };
    SYS_CONSOLE_PRINT("[IPERF] connecting TCP client to %s:5001 (cap 4 Mbps)\r\n",
                      server_ip);
    CommandIperfStart(&s_stub_cmd_io, 5, argv);
}

void iperf_control_stop(void)
{
    static char a0[] = "iperfk";
    char *argv[] = { a0, NULL };
    SYS_CONSOLE_PRINT("[IPERF] stop\r\n");
    CommandIperfStop(&s_stub_cmd_io, 1, argv);
}
