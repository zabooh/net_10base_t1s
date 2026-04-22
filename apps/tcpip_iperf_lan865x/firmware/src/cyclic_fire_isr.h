#ifndef CYCLIC_FIRE_ISR_H
#define CYCLIC_FIRE_ISR_H

#include <stdint.h>
#include <stdbool.h>

/*
 * cyclic_fire_isr — high-precision fire path using TC1 compare-match ISR.
 *
 * An alternative to the main-loop-polled tfuture path: instead of
 * busy-waiting in the main loop until the target tick, we program
 * TC1's CC0 compare register and the NVIC does the wake-up at exact
 * hardware compare-match time.  Expected jitter floor ~150-200 ns
 * (NVIC IRQ entry + ISR prologue) vs ~3-100 µs for the polled path
 * whose spin window competes with other ISRs and main-loop services.
 *
 * TC1 is a 16-bit standalone counter at 60 MHz (shares GCLK9 with TC0).
 * This wraps every 65536/60 µs ≈ 1.09 ms — sufficient for cyclic_fire's
 * use pattern (always armed <1 ms into the future) but not for the
 * sparse tfuture_at arming used by the smoke test.  That's fine — this
 * module is cyclic_fire-specific.
 *
 * Callback signature matches tfuture for drop-in compatibility. */

typedef void (*cyclic_fire_isr_cb_t)(uint64_t target_ns, uint64_t actual_ns);

void cyclic_fire_isr_init(void);

/* Arm the next fire at target_wc_ns.  PTP_CLOCK must be valid.  Must
 * arm for a target less than ~1 ms in the future (TC1 is 16-bit,
 * 60 MHz, wraps at 65536 ticks = 1.092 ms).  Returns false if already
 * pending, PTP_CLOCK not valid, target in the past, or target > 1 ms
 * in the future. */
bool cyclic_fire_isr_arm_at_ns(uint64_t target_wc_ns);

/* Cancel any pending fire.  Safe to call when idle. */
void cyclic_fire_isr_cancel(void);

/* Register the fire callback.  Called from ISR context — the callback
 * must NOT block, must NOT do console prints longer than a few bytes,
 * and must NOT take locks that the main loop holds. */
void cyclic_fire_isr_set_callback(cyclic_fire_isr_cb_t cb);

#endif /* CYCLIC_FIRE_ISR_H */
