#ifndef IPERF_CONTROL_H
#define IPERF_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

/*
 * iperf_control — thin programmatic interface to Harmony's iperf module.
 *
 * The stock Harmony iperf exposes itself only through the SYS_CMD
 * console ("iperf -s", "iperf -c <ip>", "iperfk").  The two relevant
 * handlers (CommandIperfStart, CommandIperfStop) were made non-static
 * in iperf.c so this module can dispatch them directly, feeding a stub
 * SYS_CMD_DEVICE_NODE whose output API routes to SYS_CONSOLE_PRINT.
 *
 * All three entry points are non-blocking — iperf itself runs as a
 * TCPIP_IPERF_Task() state machine driven by the stack's main loop.
 */

/* Set the IPv4 address + netmask on the default Ethernet interface.
 * ip_str and mask_str must be dotted-quad ASCII.  Returns true on
 * success. */
bool iperf_control_set_ip(const char *ip_str, const char *mask_str);

/* Start an iperf TCP server on default port 5001.  Idempotent. */
void iperf_control_server_start(void);

/* Start an iperf TCP client streaming to server_ip:5001.
 * server_ip is a dotted-quad ASCII string. */
void iperf_control_client_start(const char *server_ip);

/* Stop any running iperf session (server or client). */
void iperf_control_stop(void);

#endif /* IPERF_CONTROL_H */
