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
#include "driver/lan865x/drv_lan865x.h"
#include "system/time/sys_time.h"
#include "system/command/sys_command.h"
#include "system/console/sys_console.h"

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

static const SYS_CMD_DESCRIPTOR lan_cmd_tbl[] = {
    {"lan_read",  (SYS_CMD_FNC) lan_read,  ": read LAN865X register (lan_read <addr_hex>)"},
    {"lan_write", (SYS_CMD_FNC) lan_write, ": write LAN865X register (lan_write <addr_hex> <value_hex>)"},
};

static bool Command_Init(void) {
    return SYS_CMD_ADDGRP(lan_cmd_tbl, (int)(sizeof(lan_cmd_tbl) / sizeof(*lan_cmd_tbl)), "Test", ": Test Commands");
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


            if (appInitialized)
            {

                appData.state = APP_STATE_SERVICE_TASKS;
            }
            break;
        }

        case APP_STATE_SERVICE_TASKS:
        {
            static uint64_t ticks_per_ms = 0u;
            if (ticks_per_ms == 0u) {
                ticks_per_ms = (uint64_t)SYS_TIME_FrequencyGet() / 1000ULL;
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
