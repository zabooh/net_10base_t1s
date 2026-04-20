#ifndef PD10_BLINK_H
#define PD10_BLINK_H

#include <stdint.h>
#include <stdbool.h>

/*
 * pd10_blink — simple main-loop-driven rectangle generator on PD10.
 *
 * Independent of PTP / cyclic_fire.  Toggles PD10 at a configurable
 * frequency using only SYS_TIME_Counter64Get(); no hardware timers,
 * no interrupts, no busy-wait.  Intended for wiring / scope-probe
 * verification and general GPIO diagnostics.
 *
 * Frequency semantics:  `hz` is the RECTANGLE frequency, i.e. one
 * full high + low cycle per 1/hz seconds.  The toggle rate is 2 × hz.
 * Frequency is off by default; enable via the CLI `blink` command.
 */

void pd10_blink_init(void);

/* Service the toggle — call from the main loop every iteration.
 * current_tick should be SYS_TIME_Counter64Get() (already captured
 * by the caller for reuse). */
void pd10_blink_service(uint64_t current_tick);

/* Start the rectangle at `hz` Hz.  `hz == 0` stops the blink.
 * Returns false if hz is too high to resolve (half-period < 1 tick). */
bool pd10_blink_set_hz(uint32_t hz);

bool     pd10_blink_is_running(void);
uint32_t pd10_blink_get_hz(void);

#endif /* PD10_BLINK_H */
