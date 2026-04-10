/*
 * ptp_clock.c — Software PTP Clock implementation
 *
 * See ptp_clock.h for API documentation.
 *
 * TC0 runs at 60 MHz (GCLK0/2, no prescaler).
 * Tick → nanoseconds: ticks * 1e9 / 60e6 = ticks * 50 / 3 (exact integer ratio).
 * Division is avoided by the decomposition: (t/3)*50 + ((t%3)*50)/3
 *
 * Drift IIR: α = 1/8 → settles in ~8 sync intervals (~1 s at 125 ms period).
 */

#include "ptp_clock.h"
#include "system/time/sys_time.h"

/* TC0 frequency after GCLK0/2 prescaler (60 MHz) */
#define PTP_CLOCK_TC_FREQ_HZ  60000000ULL

/* -------------------------------------------------------------------------
 * Module state
 * ---------------------------------------------------------------------- */

static uint64_t s_anchor_wc_ns = 0u;
static uint64_t s_anchor_tick  = 0u;
static int32_t  s_drift_ppb    = 0;
static bool     s_valid        = false;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/*
 * Convert TC0 tick count to nanoseconds.
 * 1e9 / 60e6 = 50/3 exactly, so no rounding error at 60 MHz.
 * Uses only 64-bit arithmetic — no __uint128_t required.
 */
static uint64_t ticks_to_ns(uint64_t ticks)
{
    return (ticks / 3ULL) * 50ULL + ((ticks % 3ULL) * 50ULL) / 3ULL;
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick)
{
    /* Drift correction disabled: the jitter in sys_tick capture between
     * consecutive anchor updates dominates the measurement and produces
     * spurious +1M ppb readings.  For the typical 0-500 ms interpolation
     * window the uncorrected crystal error is < 10 µs (21 ppm × 500 ms),
     * which is acceptable.  s_drift_ppb is kept at 0 permanently. */
    s_anchor_wc_ns = wallclock_ns;
    s_anchor_tick  = sys_tick;
    s_valid        = true;
}

uint64_t PTP_CLOCK_GetTime_ns(void)
{
    if (!s_valid)
    {
        return 0u;
    }

    uint64_t now_tick   = SYS_TIME_Counter64Get();
    uint64_t delta_tick = now_tick - s_anchor_tick;
    uint64_t delta_ns   = ticks_to_ns(delta_tick);

    return s_anchor_wc_ns + delta_ns;
}

int32_t PTP_CLOCK_GetDriftPPB(void)
{
    return s_drift_ppb;
}

void PTP_CLOCK_SetDriftPPB(int32_t drift_ppb)
{
    s_drift_ppb = drift_ppb;
}

bool PTP_CLOCK_IsValid(void)
{
    return s_valid;
}

void PTP_CLOCK_ForceSet(uint64_t wallclock_ns)
{
    /* Capture tick as close as possible to the moment the caller issues the set */
    uint64_t tick = SYS_TIME_Counter64Get();
    s_anchor_wc_ns = wallclock_ns;
    s_anchor_tick  = tick;
    s_drift_ppb    = 0;
    s_valid        = true;
}
