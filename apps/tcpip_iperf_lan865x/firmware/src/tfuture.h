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

/* Ring-buffer trace */
void            tfuture_trace_reset(void);
void            tfuture_trace_dump(void);
uint32_t        tfuture_trace_count(void);

#endif /* TFUTURE_H */
