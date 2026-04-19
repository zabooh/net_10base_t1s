#ifndef PTP_RX_H
#define PTP_RX_H

#include <stdbool.h>

#define TCPIP_THIS_MODULE_ID    TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"

/* Register the PTP (EtherType 0x88F7) packet handler with the TCP/IP stack.
 * Returns true on success, false otherwise. */
bool PTP_RX_Register(TCPIP_NET_HANDLE hNet);

/* Poll and dispatch any buffered PTP frame captured by the driver-level
 * callback (g_ptp_raw_rx).  Call once per main-loop iteration. */
void PTP_RX_Poll(void);

#endif /* PTP_RX_H */
