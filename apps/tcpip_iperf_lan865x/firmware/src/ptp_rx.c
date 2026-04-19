#include "ptp_rx.h"

#include <stdbool.h>
#include <stdint.h>

#include "ptp_ts_ipc.h"
#include "PTP_FOL_task.h"
#include "ptp_gm_task.h"

#define TCPIP_THIS_MODULE_ID    TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"
#include "library/tcpip/src/tcpip_packet.h"

static const void *MyEth0HandlerParam = NULL;

/* Harmony TCP/IP packet handler for eth0 (LAN865x).
 * Called by the TCP/IP stack in interrupt/task context for every received
 * frame.  Returns true if the packet was consumed (caller must NOT free it);
 * returns false to let the stack process the frame normally. */
static bool pktEth0Handler(TCPIP_NET_HANDLE hNet, TCPIP_MAC_PACKET* rxPkt,
                           uint16_t frameType, const void* hParam)
{
    (void)hNet;
    (void)hParam;

    /* PTP frame (EtherType 0x88F7): consumed here so the IP stack does not see it.
     * Frame data is already captured by the primary path (TC6_CB_OnRxEthernetPacket
     * → g_ptp_raw_rx) at driver level before this handler is called. */
    if (frameType == 0x88F7u) {
        TCPIP_PKT_PacketAcknowledge(rxPkt, TCPIP_MAC_PKT_ACK_RX_OK);
        return true;
    }
    return false;
}

bool PTP_RX_Register(TCPIP_NET_HANDLE hNet)
{
    TCPIP_STACK_PROCESS_HANDLE h =
        TCPIP_STACK_PacketHandlerRegister(hNet, pktEth0Handler, MyEth0HandlerParam);
    return (h != NULL);
}

void PTP_RX_Poll(void)
{
    if (!g_ptp_raw_rx.pending) {
        return;
    }
    g_ptp_raw_rx.pending = false;   /* clear first to avoid re-entry */
    if (PTP_FOL_GetMode() == PTP_SLAVE) {
        PTP_FOL_OnFrame((const uint8_t *)g_ptp_raw_rx.data,
                        g_ptp_raw_rx.length,
                        g_ptp_raw_rx.rxTimestamp);
    } else if (PTP_FOL_GetMode() == PTP_MASTER) {
        /* GM: respond to Delay_Req frames from followers */
        PTP_GM_OnDelayReq((const uint8_t *)g_ptp_raw_rx.data,
                          g_ptp_raw_rx.length,
                          g_ptp_raw_rx.rxTimestamp);
    }
}
