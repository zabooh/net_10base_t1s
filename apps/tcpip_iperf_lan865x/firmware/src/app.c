/*******************************************************************************
  MPLAB Harmony Application Source File

  Company:
    Microchip Technology Inc.

  File Name:
    app.c

  Summary:
    This file contains the source code for the MPLAB Harmony application.

  Description:
    This file contains the source code for the MPLAB Harmony application.  It
    implements the logic of the application's state machine and it may call
    API routines of other MPLAB Harmony modules in the system, such as drivers,
    system services, and middleware.  However, it does not call any of the
    system interfaces (such as the "Initialize" and "Tasks" functions) of any of
    the modules in the system or make any assumptions about when those functions
    are called.  That is the responsibility of the configuration-specific system
    files.
 *******************************************************************************/

// *****************************************************************************
// *****************************************************************************
// Section: Included Files
// *****************************************************************************
// *****************************************************************************

#include "app.h"
#include "ptp_ts_ipc.h"
#include "PTP_FOL_task.h"
#include "ptp_gm_task.h"
#include "sw_ntp.h"
#include "tfuture.h"
#include "lan_regs_cli.h"
#include "ptp_cli.h"
#include "sw_ntp_cli.h"
#include "tfuture_cli.h"
#include "loop_stats_cli.h"
#include "driver/lan865x/drv_lan865x.h"
#include "system/time/sys_time.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"
#define TCPIP_THIS_MODULE_ID    TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"
#include "library/tcpip/src/tcpip_packet.h"

// *****************************************************************************
// *****************************************************************************
// Section: Global Data Definitions
// *****************************************************************************
// *****************************************************************************

// *****************************************************************************
/* Application Data

  Summary:
    Holds application data

  Description:
    This structure holds the application's data.

  Remarks:
    This structure should be initialized by the APP_Initialize function.

    Application strings and buffers are be defined outside this structure.
*/

APP_DATA appData;

// *****************************************************************************
// PTP packet handler forward declaration
// *****************************************************************************
bool pktEth0Handler(TCPIP_NET_HANDLE hNet, TCPIP_MAC_PACKET* rxPkt, uint16_t frameType, const void* hParam);
static const void *MyEth0HandlerParam = NULL;

/* Track LAN865x driver ready state to detect reinit-complete while in GM mode */
static bool lan865x_prev_ready = false;

// *****************************************************************************
// Section: Command Aggregation
// *****************************************************************************

static void Command_Init(void) {
    LAN_REGS_CLI_Register();
    PTP_CLI_Register();
    SW_NTP_CLI_Register();
    TFUTURE_CLI_Register();
    LOOP_STATS_CLI_Register();
}

/* --------------------------------------------------------------------------
 * pktEth0Handler — Harmony TCP/IP packet handler for eth0 (LAN865x).
 * Called by the TCP/IP stack in interrupt/task context for every received frame.
 * Returns true if the packet was consumed (caller must NOT free it);
 * returns false to let the stack process the frame normally.
 * -------------------------------------------------------------------------- */
bool pktEth0Handler(TCPIP_NET_HANDLE hNet, TCPIP_MAC_PACKET* rxPkt,
                    uint16_t frameType, const void* hParam)
{
    (void)hNet;
    (void)hParam;

    /* PTP frame (EtherType 0x88F7): consumed here so the IP stack does not see it.
     * Frame data is already captured by the primary path (TC6_CB_OnRxEthernetPacket
     * → g_ptp_raw_rx) at driver level before this handler is called.
     * No buffering needed here — ptp_rx_buffer fallback is retired. */
    if (frameType == 0x88F7u) {
        TCPIP_PKT_PacketAcknowledge(rxPkt, TCPIP_MAC_PKT_ACK_RX_OK);
        return true;
    }
    return false;
}

// *****************************************************************************
// *****************************************************************************
// Section: Application Initialization and State Machine Functions
// *****************************************************************************
// *****************************************************************************

/*******************************************************************************
  Function:
    void APP_Initialize ( void )

  Remarks:
    See prototype in app.h.
 */

void APP_Initialize ( void )
{
    /* Place the App state machine in its initial state. */
    appData.state = APP_STATE_INIT;

    Command_Init();
    sw_ntp_init();
    tfuture_init();
}


/******************************************************************************
  Function:
    void APP_Tasks ( void )

  Remarks:
    See prototype in app.h.
 */

void APP_Tasks ( void )
{

    /* Check the application's current state. */
    switch ( appData.state )
    {
        /* Application's initial state. */
        case APP_STATE_INIT:
        {
            bool appInitialized = true;

            SYS_CONSOLE_PRINT("[APP] Build: " __DATE__ " " __TIME__ "\r\n");

            if (appInitialized)
            {

                appData.state = APP_STATE_SERVICE_TASKS;
            }
            break;
        }

        case APP_STATE_SERVICE_TASKS:
        {
            /* Wait until the network handle is valid (stack initialized) */
            TCPIP_NET_HANDLE eth0_net_hd = TCPIP_STACK_IndexToNet(0);
            if (eth0_net_hd == NULL) {
                break;
            }

            /* Wait until the interface is up — PacketHandlerRegister() requires
             * bInterfaceEnabled to be set, which happens after link negotiation. */
            if (!TCPIP_STACK_NetIsUp(eth0_net_hd)) {
                break;
            }

            /* Register the PTP packet handler */
            TCPIP_STACK_PROCESS_HANDLE hPktHnd =
                TCPIP_STACK_PacketHandlerRegister(eth0_net_hd, pktEth0Handler, MyEth0HandlerParam);
            SYS_CONSOLE_PRINT("[APP] PacketHandlerRegister: %s\r\n",
                              (hPktHnd != NULL) ? "OK" : "FAIL");

            appData.state = APP_STATE_IDLE;
            break;
        }

        case APP_STATE_IDLE:
        {
            static uint64_t ticks_per_ms  = 0u;
            static uint64_t last_gm_tick  = 0u;
            static uint64_t last_fol_tick = 0u;
            static bool     ptp_fol_initialized = false;
            if (ticks_per_ms == 0u) {
                ticks_per_ms = (uint64_t)SYS_TIME_FrequencyGet() / 1000ULL;
            }
            /* === IDLE first-entry: init PTP follower HW === */
            if (!ptp_fol_initialized) {
                SYS_CONSOLE_PRINT("[APP] STATE_IDLE entered — calling PTP_FOL_Init\r\n");
                PTP_FOL_Init();
                /* Provide follower with local MAC so it can build Delay_Req frames */
                TCPIP_NET_HANDLE netH = TCPIP_STACK_IndexToNet(0);
                if (netH != NULL) {
                    const uint8_t *pMac = TCPIP_STACK_NetAddressMac(netH);
                    if (pMac != NULL) {
                        PTP_FOL_SetMac(pMac);
                    }
                }
                ptp_fol_initialized = true;
            }
            uint64_t current_tick = SYS_TIME_Counter64Get();

            /* === Manual LAN865x register access service === */
            LAN_REGS_CLI_Service(current_tick, ticks_per_ms);

            /* === GM Service: call PTP_GM_Service() every 1 ms === */
            if (PTP_FOL_GetMode() == PTP_MASTER) {
                if ((current_tick - last_gm_tick) >= ticks_per_ms) {
                    PTP_GM_Service();
                    last_gm_tick = current_tick;
                }
            }

            /* === FOL Service: call PTP_FOL_Service() every 1 ms === */
            if (PTP_FOL_GetMode() == PTP_SLAVE) {
                if ((current_tick - last_fol_tick) >= ticks_per_ms) {
                    PTP_FOL_Service();
                    last_fol_tick = current_tick;
                }
            }

            /* === SW-NTP Service: run every iteration (low RX-poll latency).
             * Internal poll-interval gate decides when to actually TX a request. */
            sw_ntp_service();

            /* === tfuture Service: runs every iteration so the 1-ms spin
             * threshold can fire with tick-level precision. */
            tfuture_service();

            /* === FOL: process a buffered PTP frame ===
             * Filled by TC6_CB_OnRxEthernetPacket at driver level.
             * pktEth0Handler only consumes the frame so the IP stack does not see it. */
            if (g_ptp_raw_rx.pending) {
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

            /* Re-run PTP_GM_Init() if the LAN865x driver recovers from a
             * reinit (Loss-of-Framing-Error) while in GM mode. The reinit
             * clears TX-Match registers written by PTP_GM_Init(). */
            bool lan865x_ready = DRV_LAN865X_IsReady(0u);
            if (!lan865x_prev_ready && lan865x_ready &&
                (PTP_FOL_GetMode() == PTP_MASTER))
            {
                SYS_CONSOLE_PRINT("[PTP-GM] driver ready after reinit - re-applying TX-Match config\r\n");
                PTP_GM_Init();
            }
            lan865x_prev_ready = lan865x_ready;

            break;
        }

        /* The default state should never be executed. */
        default:
        {
            /* TODO: Handle error in application's state machine. */
            break;
        }
    }
}


/*******************************************************************************
 End of File
 */
