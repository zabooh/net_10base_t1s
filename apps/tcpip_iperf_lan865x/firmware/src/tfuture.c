/*
 * tfuture.c — see tfuture.h for the design overview.
 */

#include "tfuture.h"
#include "ptp_clock.h"
#include "system/time/sys_time.h"
#include "system/console/sys_console.h"

/* -------------------------------------------------------------------------
 * Tunables
 * ---------------------------------------------------------------------- */

/* TC0 runs at 60 MHz → 60 ticks per microsecond.
 * 1 ms = 60 000 ticks.  When the target is this close, switch from
 * "check and return" mode to a tight busy-wait until the target is hit.
 * 1 ms is also the PTP service gate period — this means at most one
 * PTP_FOL_Service invocation can be delayed by a single firing. */
#define TFUTURE_SPIN_THRESHOLD_TICKS  60000UL

/* -------------------------------------------------------------------------
 * Module state
 * ---------------------------------------------------------------------- */

static tfuture_state_t s_state         = TFUTURE_IDLE;
static uint64_t        s_target_ns     = 0u;   /* target in PTP_CLOCK ns      */
static uint64_t        s_target_tick   = 0u;   /* target in raw TC0 ticks     */
static uint64_t        s_last_target_ns = 0u;
static uint64_t        s_last_actual_ns = 0u;
static uint32_t        s_fires_count   = 0u;
static bool            s_drift_correction = true;   /* diagnostic toggle */

/* Ring buffer: (target_ns, actual_ns) pairs. */
static uint64_t s_trace_target[TFUTURE_TRACE_SIZE];
static uint64_t s_trace_actual[TFUTURE_TRACE_SIZE];
static uint32_t s_trace_head       = 0u;
static uint32_t s_trace_total      = 0u;
static uint32_t s_trace_overwrites = 0u;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/* Convert a PTP_CLOCK wallclock-ns value into a raw TC0 tick count, based
 * on the current anchor + drift.  The mapping is:
 *   wc_ns = anchor_wc_ns + ticks_to_ns_corrected(target_tick - anchor_tick, drift)
 * We obtain the anchor implicitly by reading (now_wc_ns, now_tick) close
 * to one another; the small window between the two reads only introduces
 * at most a microsecond of setup error, dwarfed by the firing tolerance.
 *
 * Returns false if target is not in the future, PTP_CLOCK not valid, or
 * the delta-ticks computation would overflow. */
static bool compute_target_tick(uint64_t target_wc_ns, uint64_t *target_tick_out)
{
    if (!PTP_CLOCK_IsValid()) {
        return false;
    }
    /* Capture (wc, tick) atomically in the sense that the TC0 read sits
     * immediately after GetTime_ns(); they reference the same anchor state. */
    uint64_t now_wc_ns = PTP_CLOCK_GetTime_ns();
    uint64_t now_tick  = SYS_TIME_Counter64Get();

    if (target_wc_ns <= now_wc_ns) {
        return false;
    }
    uint64_t delta_wc_ns = target_wc_ns - now_wc_ns;

    /* base_ticks = delta_wc_ns × 3 / 50  (inverse of the 50/3 ns/tick rate)
     * First-order drift correction: if TC0 runs drift_ppb faster than nominal,
     * each tick is shorter than 50/3 ns, so MORE ticks cover the same wc_ns.
     * i.e. ticks ≈ base × (1 + drift_ppb/1e9). */
    int32_t drift_ppb = s_drift_correction ? PTP_CLOCK_GetDriftPPB() : 0;

    /* Protect against overflow: delta_wc_ns up to ~2^63 → base_ticks up to
     * 2^63 × 3/50 ≈ 5.5 × 10^17, still within int64 range.  For sane values
     * (a few seconds in the future) this is many orders of magnitude away. */
    uint64_t base_ticks = (delta_wc_ns / 50ULL) * 3ULL +
                          ((delta_wc_ns % 50ULL) * 3ULL) / 50ULL;

    if (drift_ppb != 0) {
        /* adj magnitude = base × |ppb| / 1e9.  For ±200 ppm (|ppb|<2e5) and
         * base < 2^54, the product stays well within 2^64. */
        uint64_t abs_ppb = (uint64_t)((drift_ppb < 0) ? -drift_ppb : drift_ppb);
        uint64_t adj     = (base_ticks * abs_ppb) / 1000000000ULL;
        if (drift_ppb > 0) {
            base_ticks += adj;     /* faster TC0 ⇒ need more ticks */
        } else {
            base_ticks = (base_ticks > adj) ? (base_ticks - adj) : 0u;
        }
    }

    *target_tick_out = now_tick + base_ticks;
    return true;
}

static void trace_record(uint64_t target_ns, uint64_t actual_ns)
{
    s_trace_target[s_trace_head] = target_ns;
    s_trace_actual[s_trace_head] = actual_ns;
    s_trace_head = (s_trace_head + 1u) % TFUTURE_TRACE_SIZE;
    s_trace_total++;
    if (s_trace_total > TFUTURE_TRACE_SIZE) {
        s_trace_overwrites++;
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void tfuture_init(void)
{
    s_state         = TFUTURE_IDLE;
    s_target_ns     = 0u;
    s_target_tick   = 0u;
    s_last_target_ns = 0u;
    s_last_actual_ns = 0u;
    s_fires_count   = 0u;
    tfuture_trace_reset();
}

bool tfuture_arm_at_ns(uint64_t target_wc_ns)
{
    if (s_state == TFUTURE_PENDING) {
        /* Refuse silent replacement — caller must tfuture_cancel first. */
        return false;
    }
    uint64_t target_tick = 0u;
    if (!compute_target_tick(target_wc_ns, &target_tick)) {
        return false;
    }
    s_target_ns   = target_wc_ns;
    s_target_tick = target_tick;
    s_state       = TFUTURE_PENDING;
    return true;
}

bool tfuture_arm_in_ms(uint32_t ms_from_now)
{
    if (!PTP_CLOCK_IsValid()) {
        return false;
    }
    uint64_t now_wc_ns = PTP_CLOCK_GetTime_ns();
    uint64_t target_ns = now_wc_ns + (uint64_t)ms_from_now * 1000000ULL;
    return tfuture_arm_at_ns(target_ns);
}

void tfuture_cancel(void)
{
    if (s_state == TFUTURE_PENDING) {
        s_state = TFUTURE_IDLE;
    }
}

void tfuture_service(void)
{
    if (s_state != TFUTURE_PENDING) {
        return;
    }
    uint64_t now_tick = SYS_TIME_Counter64Get();

    /* int64 cast handles the rare case where current tick is ahead of target
     * (already past firing point) — treated as positive delta, fire immediately. */
    int64_t ticks_until_target = (int64_t)(s_target_tick - now_tick);

    if (ticks_until_target > (int64_t)TFUTURE_SPIN_THRESHOLD_TICKS) {
        /* Still far away, come back on next service call */
        return;
    }

    /* Within threshold — busy-wait to the exact target tick.
     * Worst-case spin = TFUTURE_SPIN_THRESHOLD_TICKS (1 ms).  During this
     * window, other main-loop services (PTP, SW-NTP, TCP/IP) do not run.
     * This is acceptable for a diagnostic module fired once every few
     * seconds; not suitable for high-frequency arming. */
    while ((int64_t)(s_target_tick - SYS_TIME_Counter64Get()) > 0) {
        /* spin */
    }

    /* Fire!  Capture the actual PTP_CLOCK time as close to the spin exit
     * as possible. */
    uint64_t actual_ns = PTP_CLOCK_GetTime_ns();

    s_last_target_ns = s_target_ns;
    s_last_actual_ns = actual_ns;
    s_fires_count++;
    trace_record(s_target_ns, actual_ns);
    s_state = TFUTURE_IDLE;    /* ready for next arm */
}

tfuture_state_t tfuture_get_state(void)
{
    return s_state;
}

void tfuture_get_last(uint64_t *target_ns_out, uint64_t *actual_ns_out)
{
    if (target_ns_out) *target_ns_out = s_last_target_ns;
    if (actual_ns_out) *actual_ns_out = s_last_actual_ns;
}

uint32_t tfuture_get_fire_count(void)
{
    return s_fires_count;
}

void tfuture_set_drift_correction(bool enable)
{
    s_drift_correction = enable;
}

bool tfuture_get_drift_correction(void)
{
    return s_drift_correction;
}

/* -------------------------------------------------------------------------
 * Ring-buffer trace
 * ---------------------------------------------------------------------- */

void tfuture_trace_reset(void)
{
    s_trace_head       = 0u;
    s_trace_total      = 0u;
    s_trace_overwrites = 0u;
}

uint32_t tfuture_trace_count(void)
{
    return s_trace_total;
}

static uint32_t trace_live_count(void)
{
    return (s_trace_total < TFUTURE_TRACE_SIZE) ? s_trace_total : TFUTURE_TRACE_SIZE;
}

static uint32_t trace_first_index(void)
{
    return (s_trace_total < TFUTURE_TRACE_SIZE) ? 0u : s_trace_head;
}

static void busy_wait_us(uint32_t microseconds)
{
    uint64_t start = SYS_TIME_Counter64Get();
    uint64_t ticks = (uint64_t)microseconds * 60ULL;   /* 60 MHz */
    while ((SYS_TIME_Counter64Get() - start) < ticks) {
        /* spin */
    }
}

#define DUMP_BATCH_LINES      4u
#define DUMP_BATCH_PAUSE_US   20000u

void tfuture_trace_dump(void)
{
    uint32_t live = trace_live_count();
    uint32_t idx  = trace_first_index();

    SYS_CONSOLE_PRINT(
        "tfuture_dump: start count=%lu overwrites=%lu capacity=%lu\r\n",
        (unsigned long)live,
        (unsigned long)s_trace_overwrites,
        (unsigned long)TFUTURE_TRACE_SIZE);
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    for (uint32_t i = 0u; i < live; i++) {
        int64_t delta = (int64_t)(s_trace_actual[idx] - s_trace_target[idx]);
        SYS_CONSOLE_PRINT("%llu %llu %lld\r\n",
                          (unsigned long long)s_trace_target[idx],
                          (unsigned long long)s_trace_actual[idx],
                          (long long)delta);
        idx = (idx + 1u) % TFUTURE_TRACE_SIZE;
        if (((i + 1u) % DUMP_BATCH_LINES) == 0u) {
            busy_wait_us(DUMP_BATCH_PAUSE_US);
        }
    }
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    SYS_CONSOLE_PRINT("tfuture_dump: end\r\n");
}
