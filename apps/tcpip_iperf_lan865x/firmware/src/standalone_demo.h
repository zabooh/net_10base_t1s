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

/* Autopilot gate.  `false` disables the cyclic_fire watchdog AND
 * unhooks the demo's user-callback from cyclic_fire, handing PD10 and
 * cyclic_fire ownership over to an external controller (e.g. a bench
 * test script).  `true` restores the user-callback; the watchdog will
 * then re-arm cyclic_fire on its next tick if it finds it stopped.
 * Bound to the CLI via `demo_autopilot on|off` in demo_cli.c. */
void standalone_demo_set_enabled(bool enable);
bool standalone_demo_is_enabled(void);

/* LED1/PD10 half-period slot in nanoseconds (default 500_000_000 = 1 Hz
 * rectangle).  Lowering it raises the PD10 toggle rate — bench test
 * scripts set it to 500_000 ns (1 kHz) so a 10 s Saleae capture yields
 * enough transitions for meaningful interval-histogram statistics.
 * Because the decimator reads PTP_CLOCK on every fire, lower slot_ns
 * does not degrade sync quality — cross-board jitter stays at the PTP
 * servo residual (~100 ns) regardless of rate.
 * Values smaller than the fire_callback half-period are clamped up. */
void     standalone_demo_set_led1_slot_ns(uint64_t slot_ns);
uint64_t standalone_demo_get_led1_slot_ns(void);

/* cyclic_fire full rectangle period in µs (default 500 → fire_callback
 * runs every 250 µs).  Lowering it sharpens PD10 edge timing (each
 * edge lands within half a fire interval of the target wallclock
 * slot) at the cost of more ISR load.  Hot-applied — caller doesn't
 * need to stop/start cyclic_fire manually.  0 resets to the compile-
 * time default.  The LED1 slot is re-clamped to the new Nyquist
 * minimum if necessary. */
void     standalone_demo_set_cyclic_period_us(uint32_t period_us);
uint32_t standalone_demo_get_cyclic_period_us(void);

#endif /* STANDALONE_DEMO_H */
