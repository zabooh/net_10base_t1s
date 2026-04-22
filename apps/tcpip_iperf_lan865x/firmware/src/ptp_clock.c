/*
 * ptp_clock.c — Software PTP Clock implementation
 *
 * See ptp_clock.h for API documentation.
 *
 * TC0 runs at 60 MHz (GCLK0/2, no prescaler).
 * Tick → nanoseconds: ticks * 1e9 / 60e6 = ticks * 50 / 3 (exact integer ratio).
 * Division is avoided by the decomposition: (t/3)*50 + ((t%3)*50)/3
 *
 * Continuous rate correction (enabled since the EIC EXTINT14 ISR anchor
 * capture reduced sysTickAtRx jitter from ~200 µs to <5 µs):
 *   At each PTP_CLOCK_Update() the ratio of (new_anchor_wc − prev_anchor_wc)
 *   to (new_anchor_tick − prev_anchor_tick) gives the current TC0 rate in
 *   GM ns-per-tick.  The deviation from the nominal 50/3 ns/tick is stored
 *   as a signed ppb correction and applied inside PTP_CLOCK_GetTime_ns().
 *   An IIR filter (α = 1/32) smooths out per-sample noise.
 */

#include "ptp_clock.h"
#include "system/time/sys_time.h"

/* TC0 frequency after GCLK0/2 prescaler (60 MHz) */
#define PTP_CLOCK_TC_FREQ_HZ  60000000ULL

/* IIR smoothing window for the ppb rate estimate.
 * α = 1/N, half-life ≈ 0.7 × N samples.
 * N = 128 → half-life ~89 samples ≈ 11 s at 125 ms Sync interval.
 *
 * History: N was 32 until 2026-04-20.  Characterisation with
 * drift_filter_analysis.py revealed filter stddev of ~47 ppm over 60 s
 * with strong lag-1 autocorrelation (0.91, random walk), leading to
 * ~65 µs/s cross-board phase drift in short capture windows.
 * Quadrupling N reduces stddev by √4 ≈ 2× to ~24 ppm at the cost of
 * slower response to crystal-rate changes (e.g. thermal drift).  For
 * a static indoor bench the slower response is a good trade-off.
 *
 * Per-sample noise from 5 µs sysTickAtRx jitter over a 125 ms interval
 * is ~40 ppm; √N averaging brings the steady-state floor to ~4 ppm
 * within one time constant. */
#define DRIFT_IIR_N_DEFAULT  128

/* Adaptive single-pole IIR with exponential α schedule.
 *
 * s_drift_iir_n is the steady-state filter window (the maximum N that
 * the recurrence ever uses).  At each sample the EFFECTIVE N is
 *
 *   N_eff = min(samples_since_reset, s_drift_iir_n)
 *
 * so the very first samples after a fresh PTP_CLOCK lock use a small
 * N (fast convergence: α≈1 → α=1/8 → α=1/64 → ...), then the filter
 * settles into the configured steady-state α=1/N_max.  This breaks the
 * usual single-pole trade-off — settle is now bounded by the warm-up
 * ramp (a few samples = sub-second), while the long-term jitter floor
 * stays at the 1/√N_max value.
 *
 * `drift_iir_n` CLI sets the steady-state ceiling.  `drift_iir_reset`
 * CLI rewinds the sample counter so the warm-up ramp re-runs (used by
 * the test scripts to make measurements reproducible). */
static int32_t  s_drift_iir_n        = DRIFT_IIR_N_DEFAULT;
static uint32_t s_drift_samples      = 0u;

/* Sanity window for the per-sample instantaneous ppb estimate.
 * Accepts up to ±5000 ppm.  Must cover the combined crystal mismatch
 * between the SAME54 PLL source and the LAN865x internal oscillator
 * (two independent crystals); measured on this board pair at
 * approximately +1200 ppm.  The previous ±200 ppm limit silently
 * dropped every GM sample (crystal mismatch > clamp), so the filter
 * never converged and tfuture self_jitter showed ~1.3 ms of bias at
 * lead=2 s.  Obviously-bad samples from anchor-update glitches
 * typically produce values in tens of thousands of ppm and are still
 * rejected. */
#define DRIFT_SANITY_PPB_ABS  5000000

/* Minimum elapsed-tick gap required before computing a rate estimate.
 * 10 ms = 600,000 TC0 ticks → per-sample noise ~50 ppm from 5 µs jitter,
 * still useful once filtered.  Shorter gaps produce too much noise. */
#define DRIFT_MIN_GAP_TICKS   600000u

/* -------------------------------------------------------------------------
 * Module state
 * ---------------------------------------------------------------------- */

static uint64_t s_anchor_wc_ns = 0u;
static uint64_t s_anchor_tick  = 0u;
static int32_t  s_drift_ppb    = 0;      /* filtered TC0 rate offset (signed ppb) */
static bool     s_valid        = false;
static bool     s_drift_valid  = false;  /* becomes true after 1st rate sample */

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/*
 * Convert TC0 tick count to nanoseconds at nominal 60 MHz rate.
 * 1e9 / 60e6 = 50/3 exactly, so no rounding error.
 */
static uint64_t ticks_to_ns(uint64_t ticks)
{
    return (ticks / 3ULL) * 50ULL + ((ticks % 3ULL) * 50ULL) / 3ULL;
}

/*
 * Convert TC0 tick count to nanoseconds with ppb rate correction.
 * Positive drift_ppb means TC0 runs FASTER than nominal (more ticks per
 * real GM nanosecond), so each tick represents LESS ns than 50/3 —
 * subtract the adjustment.  Negative ppb adds it.
 *   corrected = base − base × drift_ppb / 1e9
 */
static uint64_t ticks_to_ns_corrected(uint64_t ticks, int32_t drift_ppb)
{
    uint64_t base = ticks_to_ns(ticks);
    if (drift_ppb == 0) {
        return base;
    }
    uint64_t abs_ppb = (uint64_t)((drift_ppb < 0) ? -drift_ppb : drift_ppb);
    /* adjustment magnitude = base × |ppb| / 1e9; base < 2^54 and |ppb| ≤ 2e5
     * keeps the product below 2^64 comfortably. */
    uint64_t adj = (base * abs_ppb) / 1000000000ULL;
    if (drift_ppb > 0) {
        return (base > adj) ? (base - adj) : 0u;
    } else {
        return base + adj;
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick)
{
    /* Measure instantaneous TC0 rate vs GM by comparing the delta of both
     * since the previous anchor.  Feed through IIR to suppress per-sample
     * noise, then store as the ppb correction used in GetTime_ns(). */
    if (s_valid) {
        uint64_t dwc_ns = wallclock_ns - s_anchor_wc_ns;
        uint64_t dtick  = sys_tick - s_anchor_tick;

        if (dtick >= (uint64_t)DRIFT_MIN_GAP_TICKS && dwc_ns > 0u) {
            uint64_t expected_ns = ticks_to_ns(dtick);
            int64_t  residual_ns = (int64_t)dwc_ns - (int64_t)expected_ns;
            /* Instantaneous ppb: residual × 1e9 / expected.
             * Positive residual means TC0 underestimates time → TC0 is slow
             * → drift_ppb must be NEGATIVE so corrected = base + adj (more ns). */
            int64_t inst_ppb = -(residual_ns * 1000000000LL) / (int64_t)expected_ns;

            if (inst_ppb > -DRIFT_SANITY_PPB_ABS && inst_ppb < DRIFT_SANITY_PPB_ABS) {
                if (s_drift_valid) {
                    /* Adaptive N: ramp from 1 → s_drift_iir_n.  Effective N
                     * starts small so the first samples after a fresh lock
                     * pull the estimate hard toward inst (α≈1), then settles
                     * into the configured steady-state α=1/N_max once
                     * s_drift_samples has caught up. */
                    int32_t n_eff = ((uint32_t)s_drift_iir_n > s_drift_samples)
                                  ? (int32_t)s_drift_samples
                                  : s_drift_iir_n;
                    if (n_eff < 1) n_eff = 1;
                    /* s_drift_ppb = ((N-1)*old + inst) / N   (IIR) */
                    int64_t blended = ((int64_t)s_drift_ppb * (n_eff - 1)
                                       + inst_ppb) / n_eff;
                    s_drift_ppb = (int32_t)blended;
                } else {
                    s_drift_ppb   = (int32_t)inst_ppb;
                    s_drift_valid = true;
                }
                if (s_drift_samples < 0xFFFFFFFFu) {
                    s_drift_samples++;
                }
            }
            /* Out-of-range sample: silently skip, keep previous estimate. */
        }
    }
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
    uint64_t delta_ns   = s_drift_valid
                        ? ticks_to_ns_corrected(delta_tick, s_drift_ppb)
                        : ticks_to_ns(delta_tick);

    return s_anchor_wc_ns + delta_ns;
}

int32_t PTP_CLOCK_GetDriftPPB(void)
{
    return s_drift_valid ? s_drift_ppb : 0;
}

void PTP_CLOCK_SetDriftPPB(int32_t drift_ppb)
{
    /* Kept for backward compatibility with callers that pushed a rate
     * estimate from the FOL servo.  Overwrites the IIR state; the next
     * PTP_CLOCK_Update() will resume filtering from this new seed. */
    s_drift_ppb     = drift_ppb;
    s_drift_valid   = true;
    s_drift_samples = 0u;     /* re-arm warm-up ramp from this seed */
}

bool PTP_CLOCK_IsValid(void)
{
    return s_valid;
}

void PTP_CLOCK_ForceSet(uint64_t wallclock_ns)
{
    /* Capture tick as close as possible to the moment the caller issues the set */
    uint64_t tick = SYS_TIME_Counter64Get();
    s_anchor_wc_ns  = wallclock_ns;
    s_anchor_tick   = tick;
    s_drift_ppb     = 0;
    s_drift_valid   = false;   /* re-learn rate after a manual set */
    s_drift_samples = 0u;
    s_valid         = true;
}

void PTP_CLOCK_ResetDriftFilter(void)
{
    /* Re-arm the warm-up ramp without disturbing the current rate
     * estimate seed.  The next handful of samples will use small
     * N (large α) and pull the filter quickly toward the live
     * crystal drift, then settle into the configured steady state.
     * Used by test scripts to make settle-time measurements
     * reproducible across runs. */
    s_drift_samples = 0u;
    s_drift_valid   = false;
    s_drift_ppb     = 0;
}

int32_t PTP_CLOCK_GetDriftIIRN(void)
{
    return s_drift_iir_n;
}

void PTP_CLOCK_SetDriftIIRN(int32_t n)
{
    if (n < 8)    n = 8;
    if (n > 4096) n = 4096;
    s_drift_iir_n = n;
}
