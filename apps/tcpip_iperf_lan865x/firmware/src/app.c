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
#include <stdlib.h>
#include <string.h>
#include "ptp_ts_ipc.h"
#include "PTP_FOL_task.h"
#include "ptp_gm_task.h"
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

/* Maximum expected PTP frame size on wire.
 * Sync/FollowUp messages are <= 76 bytes; Announce up to ~90 bytes.
 * 128 bytes gives comfortable headroom for all standard PTP message types. */
#define PTP_MAX_FRAME_SIZE  128u

/* Buffer for a single pending PTP frame received in pktEth0Handler */
typedef struct {
    uint8_t  data[PTP_MAX_FRAME_SIZE];
    uint16_t length;
    uint64_t rxTimestamp;
    bool     pending;
} PTP_FRAME_BUFFER;

static PTP_FRAME_BUFFER ptp_rx_buffer = {0};

/* Track LAN865x driver ready state to detect reinit-complete while in GM mode */
static bool lan865x_prev_ready = false;

// *****************************************************************************
// Globals: LAN865X register access state
// *****************************************************************************

#define APP_LAN_TIMEOUT_MS  200u

typedef enum {
    APP_LAN_IDLE,
    APP_LAN_WAIT_READ,
    APP_LAN_WAIT_WRITE
} app_lan_state_t;

static app_lan_state_t  app_lan_state          = APP_LAN_IDLE;
static uint32_t         app_lan_addr           = 0u;
static uint32_t         app_lan_value          = 0u;
static uint64_t         app_lan_expire_tick    = 0u;
static bool             app_lan_op_initiated   = false;

volatile bool     app_lan_reg_operation_complete = false;
volatile bool     app_lan_reg_operation_success  = false;
volatile uint32_t app_lan_reg_read_value         = 0u;

// *****************************************************************************
// Section: Application Callback Functions
// *****************************************************************************

static void lan_read_callback(void *reserved1, bool success, uint32_t addr, uint32_t value, void *pTag, void *reserved2) {
    app_lan_reg_operation_success = success;
    app_lan_reg_read_value        = value;
    app_lan_reg_operation_complete = true;
}

static void lan_write_callback(void *reserved1, bool success, uint32_t addr, uint32_t value, void *pTag, void *reserved2) {
    app_lan_reg_operation_success  = success;
    app_lan_reg_operation_complete = true;
}

// *****************************************************************************
// Section: Application Local Functions
// *****************************************************************************

static void lan_read(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    if (argc != 2) {
        SYS_CONSOLE_PRINT("Usage: lan_read <address_hex>\n\r");
        SYS_CONSOLE_PRINT("Example: lan_read 0x00040000\n\r");
        return;
    }
    if (app_lan_state != APP_LAN_IDLE) {
        SYS_CONSOLE_PRINT("ERROR: Previous LAN operation still in progress\n\r");
        return;
    }
    app_lan_addr                   = strtoul(argv[1], NULL, 0);
    app_lan_reg_operation_complete = false;
    app_lan_op_initiated           = false;
    app_lan_state                  = APP_LAN_WAIT_READ;
}

static void lan_write(SYS_CMD_DEVICE_NODE *pCmdIO, int argc, char **argv) {
    if (argc != 3) {
        SYS_CONSOLE_PRINT("Usage: lan_write <address_hex> <value_hex>\n\r");
        SYS_CONSOLE_PRINT("Example: lan_write 0x00040000 0x12345678\n\r");
        return;
    }
    if (app_lan_state != APP_LAN_IDLE) {
        SYS_CONSOLE_PRINT("ERROR: Previous LAN operation still in progress\n\r");
        return;
    }
    app_lan_addr                   = strtoul(argv[1], NULL, 0);
    app_lan_value                  = strtoul(argv[2], NULL, 0);
    app_lan_reg_operation_complete = false;
    app_lan_op_initiated           = false;
    app_lan_state                  = APP_LAN_WAIT_WRITE;
}

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
        PTP_FOL_SetMode(PTP_MASTER);
        PTP_GM_Init();
        SYS_CONSOLE_PRINT("PTP Grandmaster enabled\r\n");
    } else if ((strcmp(argv[1], "follower") == 0) || (strcmp(argv[1], "slave") == 0)) {
        PTP_FOL_SetMode(PTP_SLAVE);
        bool verbose = (argc >= 3) && (strcmp(argv[2], "v") == 0);
        PTP_FOL_SetVerbose(verbose);
        SYS_CONSOLE_PRINT("PTP Follower enabled%s\r\n", verbose ? " (verbose)" : "");
    } else {
        SYS_CONSOLE_PRINT("Usage: ptp_mode [off|master|follower]\r\n");
    }
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

static const SYS_CMD_DESCRIPTOR lan_cmd_tbl[] = {
    {"lan_read",    (SYS_CMD_FNC) lan_read,        ": read LAN865X register (lan_read <addr_hex>)"},
    {"lan_write",   (SYS_CMD_FNC) lan_write,       ": write LAN865X register (lan_write <addr_hex> <value_hex>)"},
    {"ptp_mode",    (SYS_CMD_FNC) ptp_mode_cmd,    ": set/get PTP mode (ptp_mode [off|master|follower])"},
    {"ptp_status",  (SYS_CMD_FNC) ptp_status_cmd,  ": show PTP status"},
    {"ptp_interval",(SYS_CMD_FNC) ptp_interval_cmd,": set GM Sync interval (ptp_interval <ms>)"},
    {"ptp_offset",  (SYS_CMD_FNC) ptp_offset_cmd,  ": show follower clock offset in ns"},
    {"ptp_reset",   (SYS_CMD_FNC) ptp_reset_cmd,   ": reset follower servo to UNINIT"},
    {"ptp_dst",     (SYS_CMD_FNC) ptp_dst_cmd,     ": set/get PTP destination MAC (ptp_dst [multicast|broadcast])"},
};

static bool Command_Init(void) {
    return SYS_CMD_ADDGRP(lan_cmd_tbl, (int)(sizeof(lan_cmd_tbl) / sizeof(*lan_cmd_tbl)), "Test", ": Test Commands");
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

    /* PTP frame (EtherType 0x88F7): buffer for processing in APP_Tasks, do not forward to IP stack */
    if (frameType == 0x88F7u) {
        uint64_t rxTs = 0u;
        if (g_ptp_rx_ts.valid) {
            rxTs = g_ptp_rx_ts.rxTimestamp;
            g_ptp_rx_ts.valid = false;
        }
        /* Store the frame for later processing in APP_Tasks().
         * If a frame is already pending, the older (stale) frame is overwritten
         * because PTP Sync frames that are not processed promptly become irrelevant. */
        uint16_t copyLen = rxPkt->pDSeg->segLen;
        if (copyLen > (uint16_t)sizeof(ptp_rx_buffer.data)) {
            copyLen = (uint16_t)sizeof(ptp_rx_buffer.data);
        }
        memcpy(ptp_rx_buffer.data, rxPkt->pMacLayer, copyLen);
        ptp_rx_buffer.length      = copyLen;
        ptp_rx_buffer.rxTimestamp = rxTs;
        ptp_rx_buffer.pending     = true;
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
            /* Try to register the PTP packet handler with the TCPIP stack.
             * PacketHandlerRegister() needs the interface to be "up" (bInterfaceEnabled).
             * We log the result but do NOT block here — move to IDLE regardless so that
             * the direct driver-level PTP capture (g_ptp_raw_rx) can work even if the
             * stack-level handler registration fails. */
            TCPIP_NET_HANDLE eth0_net_hd = TCPIP_STACK_IndexToNet(0);

            /* Wait until network handle is valid (stack initialized) */
            if (eth0_net_hd == NULL) {
                break;
            }

            /* Attempt handler registration */
            TCPIP_STACK_PROCESS_HANDLE hPktHnd =
                TCPIP_STACK_PacketHandlerRegister(eth0_net_hd, pktEth0Handler, MyEth0HandlerParam);
            SYS_CONSOLE_PRINT("[APP] PacketHandlerRegister: %s\r\n",
                              (hPktHnd != NULL) ? "OK" : "FAIL");

            /* Advance to IDLE regardless — g_ptp_raw_rx driver path always works */
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
                ptp_fol_initialized = true;
            }
            uint64_t current_tick = SYS_TIME_Counter64Get();

            /* === Manual LAN865x register access service === */
            switch (app_lan_state) {
                case APP_LAN_IDLE:
                    break;

                case APP_LAN_WAIT_READ:
                    if (!app_lan_reg_operation_complete) {
                        if (!app_lan_op_initiated) {
                            TCPIP_MAC_RES res = DRV_LAN865X_ReadRegister(0, app_lan_addr, true, lan_read_callback, NULL);
                            if (res != TCPIP_MAC_RES_OK) {
                                SYS_CONSOLE_PRINT("LAN865X Read failed to start: result=%d\n\r", (int)res);
                                app_lan_state = APP_LAN_IDLE;
                            } else {
                                app_lan_expire_tick  = current_tick + (uint64_t)APP_LAN_TIMEOUT_MS * ticks_per_ms;
                                app_lan_op_initiated = true;
                            }
                        } else {
                            if ((int64_t)(current_tick - app_lan_expire_tick) >= 0) {
                                SYS_CONSOLE_PRINT("LAN865X Read timeout for addr=0x%08X\n\r", (unsigned int)app_lan_addr);
                                app_lan_state        = APP_LAN_IDLE;
                                app_lan_op_initiated = false;
                            }
                        }
                    } else {
                        if (app_lan_reg_operation_success) {
                            SYS_CONSOLE_PRINT("LAN865X Read OK: Addr=0x%08X Value=0x%08X\n\r",
                                              (unsigned int)app_lan_addr, (unsigned int)app_lan_reg_read_value);
                        } else {
                            SYS_CONSOLE_PRINT("LAN865X Read failed for addr=0x%08X\n\r", (unsigned int)app_lan_addr);
                        }
                        app_lan_state        = APP_LAN_IDLE;
                        app_lan_op_initiated = false;
                    }
                    break;

                case APP_LAN_WAIT_WRITE:
                    if (!app_lan_reg_operation_complete) {
                        if (!app_lan_op_initiated) {
                            TCPIP_MAC_RES res = DRV_LAN865X_WriteRegister(0, app_lan_addr, app_lan_value, true, lan_write_callback, NULL);
                            if (res != TCPIP_MAC_RES_OK) {
                                SYS_CONSOLE_PRINT("LAN865X Write failed to start: result=%d\n\r", (int)res);
                                app_lan_state = APP_LAN_IDLE;
                            } else {
                                app_lan_expire_tick  = current_tick + (uint64_t)APP_LAN_TIMEOUT_MS * ticks_per_ms;
                                app_lan_op_initiated = true;
                            }
                        } else {
                            if ((int64_t)(current_tick - app_lan_expire_tick) >= 0) {
                                SYS_CONSOLE_PRINT("LAN865X Write timeout for addr=0x%08X\n\r", (unsigned int)app_lan_addr);
                                app_lan_state        = APP_LAN_IDLE;
                                app_lan_op_initiated = false;
                            }
                        }
                    } else {
                        if (app_lan_reg_operation_success) {
                            SYS_CONSOLE_PRINT("LAN865X Write OK: Addr=0x%08X Value=0x%08X\n\r",
                                              (unsigned int)app_lan_addr, (unsigned int)app_lan_value);
                        } else {
                            SYS_CONSOLE_PRINT("LAN865X Write failed for addr=0x%08X\n\r", (unsigned int)app_lan_addr);
                        }
                        app_lan_state        = APP_LAN_IDLE;
                        app_lan_op_initiated = false;
                    }
                    break;

                default:
                    break;
            }

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

            /* === FOL: process a buffered PTP frame ===
             * Primary path: g_ptp_raw_rx filled directly by TC6_CB_OnRxEthernetPacket
             * (driver level, independent of PacketHandlerRegister success).
             * Fallback:     ptp_rx_buffer filled by pktEth0Handler (stack level). */
            if (g_ptp_raw_rx.pending) {
                g_ptp_raw_rx.pending = false;   /* clear first to avoid re-entry */
                ptpMode_t curMode = PTP_FOL_GetMode();
                if (curMode == PTP_SLAVE) {
                    PTP_FOL_OnFrame((const uint8_t *)g_ptp_raw_rx.data,
                                   g_ptp_raw_rx.length,
                                   g_ptp_raw_rx.rxTimestamp);
                }
            } else if (ptp_rx_buffer.pending) {
                ptpMode_t curMode = PTP_FOL_GetMode();
                if (curMode == PTP_SLAVE) {
                    ptp_rx_buffer.pending = false;
                    PTP_FOL_OnFrame(ptp_rx_buffer.data,
                                    ptp_rx_buffer.length,
                                    ptp_rx_buffer.rxTimestamp);
                } else {
                    ptp_rx_buffer.pending = false;
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
