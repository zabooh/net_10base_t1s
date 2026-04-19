#ifndef TFUTURE_H
#define TFUTURE_H

#include <stdint.h>
#include <stdbool.h>

/*
 * tfuture — schedule a single-shot firing event at an absolute point in
 * the PTP_CLOCK wallclock timeline.  Used to demonstrate coordinated
 * actions across two PTP-synchronised boards: both arm the same
 * target_wc_ns; each fires when its own PTP_CLOCK reaches that value.
 *
 * Firing mechanism (hybrid precision):
 *   - Coarse: tfuture_service() is called every main-loop iteration and
 *     polls SYS_TIME_Counter64Get().
 *   - Tight spin: once the target is within ~1 ms, the service enters a
 *     busy-wait on SYS_TIME_Counter64Get() until the exact target tick
 *     is reached.  Gives near-tick (~17 ns) precision without requiring
 *     TC-compare-interrupt programming.
 *
 * No GPIO, no cross-board capture in this MVP — each board merely logs
 * its own firing timestamp into a ring buffer.  Python reads both ring
 * buffers and compares actual_GM vs actual_FOL to quantify how closely
 * the two boards physically fired at the same PTP_CLOCK moment.
 */

typedef enum {
    TFUTURE_IDLE    = 0,   /* ready to arm, no target pending           */
    TFUTURE_PENDING = 1,   /* target armed, waiting for tick to arrive  */
    TFUTURE_FIRED   = 2    /* most recent fire completed (diagnostic)   */
} tfuture_state_t;

#define TFUTURE_TRACE_SIZE  256u

/* Optional fire-callback hook.  Invoked from inside tfuture_service()
 * immediately after the spin-wait exits, before s_state is set back to
 * IDLE.  Runs in main-loop context (same stack as tfuture_service).
 * Keep the callback short — it directly extends the spin window. */
typedef void (*tfuture_fire_cb_t)(uint64_t target_ns, uint64_t actual_ns);
void            tfuture_set_fire_callback(tfuture_fire_cb_t cb);

/* Lifecycle */
void            tfuture_init(void);
void            tfuture_service(void);

/* Scheduling */
bool            tfuture_arm_at_ns(uint64_t target_wc_ns);
bool            tfuture_arm_in_ms(uint32_t ms_from_now);
void            tfuture_cancel(void);

/* Status */
tfuture_state_t tfuture_get_state(void);
void            tfuture_get_last(uint64_t *target_ns_out,
                                 uint64_t *actual_ns_out);
uint32_t        tfuture_get_fire_count(void);

/* Diagnostic: when false, compute_target_tick ignores drift_ppb (acts as if
 * drift = 0).  Useful to test whether the observed self_jitter bias comes
 * from the drift correction term. */
void            tfuture_set_drift_correction(bool enable);
bool            tfuture_get_drift_correction(void);

/* Configure the busy-wait threshold: when the remaining ticks-until-target
 * drops below this, the service enters the tight spin loop.  Larger =
 * tighter tick-level precision but blocks other services for longer.
 * Default: 1000 µs (matches PTP service cadence).  For cyclic sub-ms
 * firing use ~100 µs to leave room for PTP/TCP-IP per cycle. */
void            tfuture_set_spin_threshold_us(uint32_t us);
uint32_t        tfuture_get_spin_threshold_us(void);

/* Ring-buffer trace */
void            tfuture_trace_reset(void);
void            tfuture_trace_dump(void);
uint32_t        tfuture_trace_count(void);

#endif /* TFUTURE_H */
