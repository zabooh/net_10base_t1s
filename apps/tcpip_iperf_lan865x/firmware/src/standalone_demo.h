#ifndef STANDALONE_DEMO_H
#define STANDALONE_DEMO_H

#include <stdint.h>
#include <stdbool.h>

/*
 * standalone_demo — self-contained PTP synchronisation demonstration.
 *
 * Boot behaviour (no PTP yet, both boards independent):
 *   - cyclic_fire starts automatically with period_us=500 (= 2 kHz toggle
 *     rate on PD10, useful for scope cross-checking).
 *   - LED1 (PC21) toggles every 500 ms via a decimator hooked into the
 *     cyclic_fire callback (every 2000 half-periods).
 *   - LED2 (PA16) is OFF.
 *   - Because the two boards' TC0 crystals differ by ~100 ppm and PTP is
 *     not active, LED1 visibly drifts apart between boards.
 *
 * Demo mode selection (one button per board):
 *   - SW1 press → this board becomes PTP follower → LED2 starts blinking
 *     at 250 ms while the servo converges.  When syncStatus reaches FINE,
 *     LED2 goes solid on and stays on.
 *   - SW2 press → this board becomes PTP master → LED2 blinks 250 ms for
 *     2 s (matching typical follower lock time, for visual symmetry) and
 *     then stays solid on.
 *
 * Once both boards have transitioned (one to follower, one to master),
 * PTP_CLOCK on the follower realigns to the master's wallclock and the
 * cyclic_fire callbacks happen at synchronised PTP-wallclock instants —
 * LED1 visibly stops drifting and toggles in lock-step on both boards.
 *
 * Replaces button_led on this branch — the buttons no longer toggle the
 * LEDs directly, they trigger PTP role selection instead.
 */

void standalone_demo_init(void);

/* Call from the main loop once per iteration; current_tick is the
 * caller's already-captured SYS_TIME_Counter64Get() value. */
void standalone_demo_service(uint64_t current_tick);

#endif /* STANDALONE_DEMO_H */
