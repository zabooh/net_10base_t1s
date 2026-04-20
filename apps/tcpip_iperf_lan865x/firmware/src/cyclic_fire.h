#ifndef CYCLIC_FIRE_H
#define CYCLIC_FIRE_H

#include <stdint.h>
#include <stdbool.h>

/*
 * cyclic_fire — periodic GPIO toggle synchronised across PTP-locked boards.
 *
 * Uses tfuture as the single-shot time source and re-arms it from the
 * post-fire callback, producing a periodic call at a configurable
 * period_us.  Each call toggles a GPIO pin (PD10 = EXT1 pin 5, the
 * "GPIO1" position on the Xplained-Pro header — directly scope-clippable
 * on the 2.54 mm header).  Given two boards armed with the same
 * phase_anchor_ns + period_us, both boards toggle at identical
 * PTP-wallclock moments — producing visually synchronous rectangle
 * signals on their PD10 pins that a scope can compare directly.
 *
 * `period_us` is the FULL rectangle period (one high + one low phase).
 * The internal callback fires twice per period (every period_us/2) and
 * toggles the pin on each fire.  Default 1000 µs → 1 kHz rectangle
 * (500 µs high, 500 µs low).
 *
 * Trade-off: shorter period = higher CPU share spent in tfuture's
 * busy-wait.  cyclic_fire_start lowers tfuture's spin threshold to
 * ~100 µs so other main-loop services (PTP, TCP/IP) still get CPU
 * every half-cycle.  Periods below ~400 µs are not recommended
 * (half-period ≈ spin threshold).
 */

#define CYCLIC_FIRE_DEFAULT_PERIOD_US  1000u

/* Output pattern per fire callback.
 *   SQUARE: 50/50 rectangle, one toggle per half-period callback.  Default.
 *   MARKER: 5-period cycle (= 10 half-period callbacks):
 *     half-period 0 → set HIGH
 *     half-period 2 → set LOW
 *     half-periods 1, 3-9 → no change (stays LOW for 4 full periods)
 *   The MARKER pattern makes the rising edge of each cycle visually
 *   isolated, so when two boards fire together the question
 *   "which board's edge comes first?" is unambiguous on a scope —
 *   useful when the cross-board offset is smaller than one period
 *   but non-zero and possibly of varying sign. */
typedef enum {
    CYCLIC_FIRE_PATTERN_SQUARE = 0,
    CYCLIC_FIRE_PATTERN_MARKER = 1,
} cyclic_fire_pattern_t;

bool     cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns);
bool     cyclic_fire_start_ex(uint32_t period_us, uint64_t phase_anchor_ns,
                              cyclic_fire_pattern_t pattern);
void     cyclic_fire_stop(void);
bool     cyclic_fire_is_running(void);

uint32_t cyclic_fire_get_period_us(void);
uint64_t cyclic_fire_get_cycle_count(void);
uint64_t cyclic_fire_get_missed_count(void);

#endif /* CYCLIC_FIRE_H */
