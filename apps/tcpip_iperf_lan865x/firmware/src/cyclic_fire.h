#ifndef CYCLIC_FIRE_H
#define CYCLIC_FIRE_H

#include <stdint.h>
#include <stdbool.h>

/*
 * cyclic_fire — periodic GPIO toggle synchronised across PTP-locked boards.
 *
 * Uses tfuture as the single-shot time source and re-arms it from the
 * post-fire callback, producing a periodic call at a configurable
 * period_us.  Each call toggles a GPIO pin (default PB22).  Given two
 * boards armed with the same phase_anchor_ns + period_us, both boards
 * toggle at identical PTP-wallclock moments — producing visually
 * synchronous rectangle signals on their PB22 pins that a scope can
 * compare directly.
 *
 * Default period: 500 µs  →  1 kHz rectangle (500 µs high, 500 µs low).
 *
 * Trade-off: shorter period = higher CPU share spent in tfuture's
 * busy-wait.  cyclic_fire_start lowers tfuture's spin threshold to
 * ~100 µs so other main-loop services (PTP, TCP/IP) still get CPU
 * every cycle.  Periods below ~200 µs are not recommended.
 */

#define CYCLIC_FIRE_DEFAULT_PERIOD_US  500u

bool     cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns);
void     cyclic_fire_stop(void);
bool     cyclic_fire_is_running(void);

uint32_t cyclic_fire_get_period_us(void);
uint64_t cyclic_fire_get_cycle_count(void);
uint64_t cyclic_fire_get_missed_count(void);

#endif /* CYCLIC_FIRE_H */
