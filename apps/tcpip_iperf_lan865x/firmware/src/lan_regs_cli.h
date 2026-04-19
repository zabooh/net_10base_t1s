#ifndef LAN_REGS_CLI_H
#define LAN_REGS_CLI_H

#include <stdint.h>

/* Register the lan_read / lan_write CLI commands with SYS_CMD. */
void LAN_REGS_CLI_Register(void);

/* Service the async read/write state machine.  Call from the application
 * main loop once per iteration with the current SYS_TIME tick value and
 * the tick-per-ms conversion factor. */
void LAN_REGS_CLI_Service(uint64_t current_tick, uint64_t ticks_per_ms);

#endif /* LAN_REGS_CLI_H */
