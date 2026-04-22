/*
 * ptp_clock.h — Software PTP Clock (nanosecond resolution)
 *
 * Provides a nanosecond-resolution wallclock derived from the LAN865x PTP
 * hardware clock.  Works identically on both the Grandmaster (GM) and
 * Follower (FOL) boards after PTP convergence.
 *
 * Design:
 *   An anchor point (wallclock_ns, TC0_tick) is recorded at every PTP sync.
 *   Between anchors PTP_CLOCK_GetTime_ns() interpolates using TC0 (60 MHz)
 *   and compensates for MCU crystal drift with an IIR low-pass filter.
 *
 *   No additional SPI transfers or hardware timers are needed at query time.
 *
 * Anchor sources:
 *   FOL: TC6_CB_OnRxEthernetPacket()  → ptp_fol_task.c → PTP_CLOCK_Update()
 *   GM : GM_STATE_WAIT_TTSCA_L        → ptp_gm_task.c  → PTP_CLOCK_Update()
 */

#ifndef PTP_CLOCK_H
#define PTP_CLOCK_H

#include <stdint.h>
#include <stdbool.h>

/**
 * Set (or update) the anchor point.
 *
 * @param wallclock_ns  PTP wallclock value in nanoseconds (from RTSA or TTSCAL)
 * @param sys_tick      SYS_TIME_Counter64Get() captured at the same moment
 *
 * Called automatically by ptp_fol_task.c and ptp_gm_task.c — no manual call
 * required from application code.
 */
void     PTP_CLOCK_Update(uint64_t wallclock_ns, uint64_t sys_tick);

/**
 * Return the current wallclock-equivalent time in nanoseconds.
 *
 * Interpolates from the last anchor point using TC0 ticks.
 * Returns 0 until the first anchor point has been set.
 * Safe to call from any context (no SPI, no mutex, no blocking).
 */
uint64_t PTP_CLOCK_GetTime_ns(void);

/**
 * Return the measured MCU crystal drift relative to the PTP wallclock.
 * Positive = MCU runs faster than wallclock.
 * Unit: parts per billion (ppb).
 */
int32_t  PTP_CLOCK_GetDriftPPB(void);

/**
 * Update the reported crystal drift value.
 *
 * Called by ptp_fol_task.c whenever rateRatioFIR is recalculated.
 * @param drift_ppb  (rateRatioFIR - 1.0) * 1e9, rounded to int32_t
 */
void     PTP_CLOCK_SetDriftPPB(int32_t drift_ppb);

/**
 * Returns true once at least one anchor point has been recorded.
 */
bool     PTP_CLOCK_IsValid(void);

/**
 * Directly set the wallclock anchor to the given nanosecond value.
 *
 * Captures the current SYS_TIME tick internally (atomic with wallclock_ns).
 * Resets the drift IIR filter to zero so the clock starts fresh.
 * Use for standalone timer validation independent of PTP sync.
 *
 * @param wallclock_ns  The value to set the clock to, in nanoseconds.
 */
void     PTP_CLOCK_ForceSet(uint64_t wallclock_ns);

/**
 * Get / set the IIR drift filter window.  Larger N → lower
 * steady-state jitter floor (∝ 1/√N) but slower convergence
 * (half-life ≈ 0.7 × N samples × 125 ms Sync interval).
 * Default = 128 (≈ 11 s half-life, ~24 µs per-board servo MAD).
 * Acceptable range: 8 .. 4096.  Values outside are clamped.
 */
int32_t  PTP_CLOCK_GetDriftIIRN(void);
void     PTP_CLOCK_SetDriftIIRN(int32_t n);

/**
 * Re-arm the adaptive IIR warm-up ramp without changing the configured
 * steady-state N.  Use this from test scripts to make settle-time
 * measurements reproducible — after a reset the next ~N_max samples
 * will progressively tighten α from 1 down to 1/N_max.
 *
 * Has no effect on the current anchor (wallclock_ns, sys_tick), only on
 * the drift estimator: clears s_drift_valid and the sample counter so
 * the next PTP_CLOCK_Update() seeds the filter freshly.
 */
void     PTP_CLOCK_ResetDriftFilter(void);

#endif /* PTP_CLOCK_H */
