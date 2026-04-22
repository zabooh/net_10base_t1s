#ifndef WATCHDOG_H
#define WATCHDOG_H

/*
 * watchdog — SAM E54 WDT with Early-Warning crash dump.
 *
 * Bring up the always-on Watchdog Timer with a 2-second total timeout
 * and an Early-Warning interrupt at 1 s elapsed.  The main loop calls
 * watchdog_kick() once per iteration (cheap — single register write).
 *
 * Healthy operation: kick keeps WDT happy, no interrupt, no reset.
 *
 * Silent hang (the failure mode that doesn't trigger a CPU fault and
 * therefore wouldn't fire exception_handler.c): main loop stops kicking
 * → after 1 s the WDT Early-Warning ISR fires.  That ISR re-uses the
 * same register-dump-and-reset path as the fault handlers, with a
 * label "WatchdogEW" — the dump shows where the CPU was stuck when
 * the EW fired (PC + stacked frame from whatever code the WDT ISR
 * preempted).  After the dump the WDT continues counting and resets
 * the chip at the 2-s mark; if the dump path itself takes longer than
 * the remaining 1 s for any reason, the hardware WDT reset still
 * hits — the controller WILL recover regardless. */

void watchdog_init(void);
void watchdog_kick(void);

#endif /* WATCHDOG_H */
