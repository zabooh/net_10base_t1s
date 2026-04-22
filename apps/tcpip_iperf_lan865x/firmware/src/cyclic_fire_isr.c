#include "cyclic_fire_isr.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "device.h"                         /* TC1_REGS, MCLK, GCLK, NVIC */
#include "ptp_clock.h"
#include "system/time/sys_time.h"

#define TC1_NOMINAL_HZ              60000000ULL
#define TC1_MAX_COUNT               0xFFFFu           /* 16-bit counter */

/* The 1 ms arming ceiling leaves a small safety margin below the
 * hardware wrap (1.092 ms).  cyclic_fire's typical re-arm is
 * half_period = 250 µs so we're well inside. */
#define TC1_MAX_ARM_TICKS           (TC1_NOMINAL_HZ / 1000u)

static cyclic_fire_isr_cb_t s_fire_cb      = NULL;
static volatile bool        s_pending      = false;
static volatile uint64_t    s_target_ns    = 0u;
static volatile uint64_t    s_target_tick  = 0u;   /* TC0-64 tick view  */

/* -------------------------------------------------------------------------
 * TC0 ↔ PTP_CLOCK math — identical to tfuture.c's compute_target_tick.
 * Duplicated here on purpose so this module can be dropped in without
 * linking against tfuture.c's statics.
 * ------------------------------------------------------------------------ */

static bool compute_target_tick(uint64_t target_wc_ns, uint64_t *target_tick_out)
{
    if (!PTP_CLOCK_IsValid()) {
        return false;
    }
    uint64_t now_wc_ns = PTP_CLOCK_GetTime_ns();
    uint64_t now_tick  = SYS_TIME_Counter64Get();
    if (target_wc_ns <= now_wc_ns) {
        return false;
    }
    uint64_t delta_wc_ns = target_wc_ns - now_wc_ns;
    int32_t drift_ppb = PTP_CLOCK_GetDriftPPB();
    uint64_t freq_hz = SYS_TIME_FrequencyGet();
    if (freq_hz == 0u) freq_hz = TC1_NOMINAL_HZ;

    uint64_t base_ticks = (delta_wc_ns / 1000000000ULL) * freq_hz +
                          ((delta_wc_ns % 1000000000ULL) * freq_hz) / 1000000000ULL;
    if (drift_ppb != 0) {
        uint64_t abs_ppb = (uint64_t)((drift_ppb < 0) ? -drift_ppb : drift_ppb);
        uint64_t adj     = (base_ticks * abs_ppb) / 1000000000ULL;
        if (drift_ppb > 0) base_ticks += adj;
        else               base_ticks = (base_ticks > adj) ? (base_ticks - adj) : 0u;
    }
    *target_tick_out = now_tick + base_ticks;
    return true;
}

/* -------------------------------------------------------------------------
 * TC1 hardware bring-up
 * ------------------------------------------------------------------------ */

static void tc1_bring_up(void)
{
    /* 1. Enable TC1 APBA clock gate (MCLK). */
    MCLK_REGS->MCLK_APBAMASK |= MCLK_APBAMASK_TC1_Msk;

    /* 2. Route GCLK9 (= GCLK1, 60 MHz, the same one TC0 already uses per
     *    plib_clock.c) to TC1.  TC0_GCLK_ID == TC1_GCLK_ID == 9. */
    if ((GCLK_REGS->GCLK_PCHCTRL[9] & GCLK_PCHCTRL_CHEN_Msk) == 0u) {
        GCLK_REGS->GCLK_PCHCTRL[9] =
            GCLK_PCHCTRL_GEN(0x1u) | GCLK_PCHCTRL_CHEN_Msk;
        while ((GCLK_REGS->GCLK_PCHCTRL[9] & GCLK_PCHCTRL_CHEN_Msk) == 0u) { }
    }

    /* 3. Software reset. */
    TC1_REGS->COUNT16.TC_CTRLA = TC_CTRLA_SWRST_Msk;
    while ((TC1_REGS->COUNT16.TC_SYNCBUSY & TC_SYNCBUSY_SWRST_Msk) != 0u) { }

    /* 4. 16-bit, prescaler /1 → 60 MHz, "NFRQ" waveform (no compare-driven
     *    auto-reset — we only want the MC0 interrupt, not PWM output). */
    TC1_REGS->COUNT16.TC_CTRLA = TC_CTRLA_MODE_COUNT16 | TC_CTRLA_PRESCALER_DIV1;
    TC1_REGS->COUNT16.TC_WAVE  = (uint8_t)TC_WAVE_WAVEGEN_NFRQ;

    /* 5. Clear any stale interrupt flags; MC0 interrupt is NOT enabled
     *    until arm() programs a valid target. */
    TC1_REGS->COUNT16.TC_INTFLAG = (uint8_t)TC_INTFLAG_Msk;

    /* 6. NVIC: priority equal to TC0/SYS_TIME (3) — the fire callback is
     *    short and we don't want to starve critical ISRs. */
    NVIC_SetPriority(TC1_IRQn, 3);
    NVIC_EnableIRQ(TC1_IRQn);

    /* 7. Enable the counter.  Keep it running continuously; arming just
     *    updates CC0 + enables the MC0 interrupt. */
    TC1_REGS->COUNT16.TC_CTRLA |= TC_CTRLA_ENABLE_Msk;
    while ((TC1_REGS->COUNT16.TC_SYNCBUSY & TC_SYNCBUSY_ENABLE_Msk) != 0u) { }
}

/* -------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------ */

void cyclic_fire_isr_init(void)
{
    s_fire_cb     = NULL;
    s_pending     = false;
    s_target_ns   = 0u;
    s_target_tick = 0u;
    tc1_bring_up();
}

bool cyclic_fire_isr_arm_at_ns(uint64_t target_wc_ns)
{
    if (s_pending) {
        return false;
    }
    uint64_t target_tick = 0u;
    if (!compute_target_tick(target_wc_ns, &target_tick)) {
        return false;
    }

    uint64_t now_tick = SYS_TIME_Counter64Get();
    if (target_tick <= now_tick) {
        return false;
    }
    uint64_t ticks_until = target_tick - now_tick;
    if (ticks_until > TC1_MAX_ARM_TICKS) {
        /* Outside TC1's 16-bit window — caller needs to split OR use
         * the main-loop-polled tfuture path. */
        return false;
    }

    /* Read TC1's current count (needs READSYNC in non-buffered modes —
     * easiest path is to request a read sync and wait).  Actually the
     * COUNT register in NFRQ mode is directly readable without READSYNC
     * when CTRLBSET.CMD = READSYNC — but the simpler approach is: just
     * read COUNT which reflects the last sync'd value (up to 1 GCLK
     * cycle stale, negligible). */
    TC1_REGS->COUNT16.TC_CTRLBSET = TC_CTRLBSET_CMD_READSYNC;
    while ((TC1_REGS->COUNT16.TC_SYNCBUSY & TC_SYNCBUSY_CTRLB_Msk) != 0u) { }
    uint16_t tc1_now = TC1_REGS->COUNT16.TC_COUNT;

    uint16_t cc0 = (uint16_t)((tc1_now + (uint16_t)ticks_until) & TC1_MAX_COUNT);

    /* Store target info BEFORE enabling the interrupt so the ISR sees
     * a consistent view. */
    s_target_ns   = target_wc_ns;
    s_target_tick = target_tick;
    s_pending     = true;

    TC1_REGS->COUNT16.TC_CC[0]   = cc0;
    TC1_REGS->COUNT16.TC_INTFLAG = (uint8_t)TC_INTFLAG_MC0_Msk;  /* W1C */
    TC1_REGS->COUNT16.TC_INTENSET = (uint8_t)TC_INTENSET_MC0_Msk;
    return true;
}

void cyclic_fire_isr_cancel(void)
{
    TC1_REGS->COUNT16.TC_INTENCLR = (uint8_t)TC_INTENCLR_MC0_Msk;
    TC1_REGS->COUNT16.TC_INTFLAG  = (uint8_t)TC_INTFLAG_MC0_Msk;
    s_pending = false;
}

void cyclic_fire_isr_set_callback(cyclic_fire_isr_cb_t cb)
{
    s_fire_cb = cb;
}

/* -------------------------------------------------------------------------
 * TC1 ISR — single entry point, MC0 compare match.
 * Keep it SHORT: pin ops + user callback + re-arm only.
 * ------------------------------------------------------------------------ */

void TC1_Handler(void)
{
    if ((TC1_REGS->COUNT16.TC_INTFLAG & TC_INTFLAG_MC0_Msk) == 0u) {
        return;
    }
    /* Disable the compare interrupt until the re-arm in fire_cb enables
     * it again — otherwise a re-fire could happen on TC1 wrap-around
     * before we've updated CC0. */
    TC1_REGS->COUNT16.TC_INTENCLR = (uint8_t)TC_INTENCLR_MC0_Msk;
    TC1_REGS->COUNT16.TC_INTFLAG  = (uint8_t)TC_INTFLAG_MC0_Msk;    /* W1C */

    uint64_t target_ns = s_target_ns;
    s_pending          = false;

    cyclic_fire_isr_cb_t cb = s_fire_cb;
    if (cb != NULL) {
        /* actual_ns: read PTP_CLOCK as early as possible to minimise
         * ISR-entry-to-timestamp delay. */
        uint64_t actual_ns = PTP_CLOCK_GetTime_ns();
        cb(target_ns, actual_ns);
    }
}
