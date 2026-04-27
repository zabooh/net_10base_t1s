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
#include "ptp_fol_task.h"
#include "ptp_gm_task.h"
#include "sw_ntp.h"
#include "tfuture.h"
#include "lan_regs_cli.h"
#include "ptp_cli.h"
#include "sw_ntp_cli.h"
#include "tfuture_cli.h"
#include "loop_stats_cli.h"
#include "cyclic_fire_cli.h"
#include "pd10_blink.h"
#include "pd10_blink_cli.h"
#include "standalone_demo.h"
#include "demo_cli.h"
#include "test_exception_cli.h"
#include "watchdog.h"
#include "ptp_log.h"
#include "ptp_rx.h"
#include "driver/lan865x/drv_lan865x.h"
#include "ptp_drv_ext.h"
#include "system/time/sys_time.h"
#include "system/console/sys_console.h"
#define TCPIP_THIS_MODULE_ID    TCPIP_MODULE_MANAGER
#include "library/tcpip/tcpip.h"

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
    CYCLIC_FIRE_CLI_Register();
    PD10_BLINK_CLI_Register();
    DEMO_CLI_Register();
    TEST_EXCEPTION_CLI_Register();
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

    PTP_DRV_EXT_Init();   /* EIC EXTINT-14 (LAN865x nIRQ tick latch) */
    Command_Init();
    sw_ntp_init();
    tfuture_init();
    pd10_blink_init();
    standalone_demo_init();
    /* watchdog_init() is intentionally NOT called here — the TCP/IP
     * stack + LAN865x + PTP follower bring-up takes a few seconds and
     * during that window the main loop never runs, so an active WDT
     * would reset us into a boot-loop.  We bring it up at first entry
     * to APP_STATE_IDLE (see APP_Tasks below), once everything is
     * settled and the main loop is actually iterating. */
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

            /* Decode RSTC.RCAUSE so spontaneous resets are no longer
             * silent.  Exactly one bit is normally set indicating which
             * source triggered the most recent reset.  The register
             * survives the reset itself and is read-only.  Combined into
             * a single SYS_CONSOLE_PRINT() with the Build banner so
             * Harmony's console-queue can't drop the second line during
             * the early-boot back-pressure window. */
            {
                uint8_t rc = RSTC_REGS->RSTC_RCAUSE;
                const char *cause = "UNKNOWN";
                if      ((rc & RSTC_RCAUSE_POR_Msk)     != 0u) cause = "POR";
                else if ((rc & RSTC_RCAUSE_BODCORE_Msk) != 0u) cause = "BODCORE";
                else if ((rc & RSTC_RCAUSE_BODVDD_Msk)  != 0u) cause = "BODVDD";
                else if ((rc & RSTC_RCAUSE_EXT_Msk)     != 0u) cause = "EXT";
                else if ((rc & RSTC_RCAUSE_WDT_Msk)     != 0u) cause = "WDT";
                else if ((rc & RSTC_RCAUSE_SYST_Msk)    != 0u) cause = "SYST";
                /* Boot banner goes through SYS_CONSOLE_PRINT directly
                 * because PTP_LOG_Flush is not yet being called from
                 * SYS_Tasks at this point — ring-buffered messages
                 * would sit unfIlushed through the rest of Harmony init.
                 * The separator is a single-call string so the console
                 * queue can't split it; the [APP] tag here is the
                 * pre-timestamp format (PTP_CLOCK isn't valid yet). */
                SYS_CONSOLE_PRINT("\r\n"
                                  "==================================\r\n"
                                  "[APP] Build: " __DATE__ " " __TIME__
                                  "  RCAUSE=0x%02x (%s)\r\n",
                                  (unsigned int)rc, cause);
            }

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
            bool ptpRxOk = PTP_RX_Register(eth0_net_hd);
            PTP_LOG("[APP] PacketHandlerRegister: %s\r\n",
                    ptpRxOk ? "OK" : "FAIL");

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
                PTP_LOG("[APP] STATE_IDLE entered — calling PTP_FOL_Init\r\n");
                PTP_FOL_Init();
                /* Provide follower with local MAC so it can build Delay_Req frames */
                TCPIP_NET_HANDLE netH = TCPIP_STACK_IndexToNet(0);
                if (netH != NULL) {
                    const uint8_t *pMac = TCPIP_STACK_NetAddressMac(netH);
                    if (pMac != NULL) {
                        PTP_FOL_SetMac(pMac);
                    }
                }
#if PTP_AN1847_STYLE
                /* AN1847 / mult-sync convention: PLCA Coordinator (node 0)
                 * is the Grandmaster, all other nodes are Followers.  Auto-
                 * select mode based on the configured PLCA local node ID.
                 * Can still be overridden later via the `ptp_mode` CLI. */
                PTP_FOL_AutoSelectMode((uint8_t)DRV_LAN865X_PLCA_NODE_ID_IDX0);
#endif
                ptp_fol_initialized = true;
                /* Bring up the WDT only AFTER the slow boot-time
                 * subsystems have completed (TCP/IP stack, LAN865x,
                 * PTP follower init) so the WDT doesn't trip during
                 * boot. */
                watchdog_init();
            }
            uint64_t current_tick = SYS_TIME_Counter64Get();

            /* === Watchdog: keep alive while the main loop is healthy === */
            watchdog_kick();

            /* === PD10 rectangle generator — enabled via `blink` CLI === */
            pd10_blink_service(current_tick);

            /* === PTP-standalone demo: SW1/SW2 select role, LEDs visualise === */
            standalone_demo_service(current_tick);

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

            /* Drive the PTP-specific register-init state machine.  Becomes
             * a no-op once PTP_DRV_EXT_RegisterInitDone() returns true.
             * Replaces the inline driver patches (TC6_MEMMAP / case 46/47
             * tweaks) that previously lived in drv_lan865x_api.c. */
            PTP_DRV_EXT_Tasks(0u);

            /* === FOL/GM: process a buffered PTP frame ===
             * Filled by TC6_CB_OnRxEthernetPacket at driver level. */
            PTP_RX_Poll();

            /* Re-run PTP_GM_Init() if the LAN865x driver recovers from a
             * reinit (Loss-of-Framing-Error) while in GM mode. The reinit
             * clears TX-Match registers written by PTP_GM_Init(). */
            bool lan865x_ready = DRV_LAN865X_IsReady(0u);
            if (!lan865x_prev_ready && lan865x_ready &&
                (PTP_FOL_GetMode() == PTP_MASTER))
            {
                PTP_LOG("[PTP-GM] driver ready after reinit - re-applying TX-Match config\r\n");
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
