#include "cyclic_fire.h"

#include <stdbool.h>
#include <stdint.h>

#include "tfuture.h"
#include "ptp_clock.h"
#include "system/ports/sys_ports.h"

/* PB22 is pin 98 on the SAME54P20A — marked "Available" in the default
 * pin_configurations.csv, so we configure it as output at runtime. */
#define CYCLIC_FIRE_PIN            SYS_PORT_PIN_PB22

/* Lowered tfuture spin threshold while cyclic_fire is running.  100 µs
 * means the spin blocks the main loop for at most 100 µs per fire,
 * leaving (period_us − 100) µs for PTP / TCP-IP / etc. each cycle. */
#define CYCLIC_FIRE_SPIN_US        100u

static bool     s_running            = false;
static uint32_t s_period_us          = 0u;
static uint64_t s_last_target_ns     = 0u;
static uint64_t s_cycles             = 0u;
static uint64_t s_misses             = 0u;
static uint32_t s_saved_spin_us      = 0u;

static void fire_callback(uint64_t target_ns, uint64_t actual_ns)
{
    (void)actual_ns;
    if (!s_running) {
        return;
    }

    /* Toggle the output.  One toggle per callback ⇒ rectangle frequency
     * is half the callback rate (1/period_us). */
    SYS_PORT_PinToggle(CYCLIC_FIRE_PIN);

    /* Re-arm for the next slot at absolute PTP-wallclock time = target + period.
     * Using target_ns (not actual_ns) keeps the schedule free of firing-jitter
     * accumulation over time — jitter is bounded per cycle instead of drifting. */
    uint64_t next_target_ns = target_ns + (uint64_t)s_period_us * 1000ULL;

    /* If we slipped past the next target already (unlikely at period ≥ 200 µs
     * but possible if something stalled the main loop), catch up by rebasing
     * to a future slot and count a miss. */
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();
    while (next_target_ns <= now_ns) {
        next_target_ns += (uint64_t)s_period_us * 1000ULL;
        s_misses++;
    }

    s_last_target_ns = next_target_ns;
    (void)tfuture_arm_at_ns(next_target_ns);
    s_cycles++;
}

bool cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns)
{
    if (s_running) {
        return false;
    }
    if (!PTP_CLOCK_IsValid()) {
        return false;
    }
    if (period_us == 0u) {
        period_us = CYCLIC_FIRE_DEFAULT_PERIOD_US;
    }

    /* Configure GPIO as output (pin is "Available" in default config). */
    SYS_PORT_PinOutputEnable(CYCLIC_FIRE_PIN);
    SYS_PORT_PinClear(CYCLIC_FIRE_PIN);

    /* Compute the first target: align to phase_anchor + N × period so two
     * boards given the same anchor+period produce in-phase edges, regardless
     * of the delta between their two cyclic_fire_start() calls. */
    uint64_t now_ns    = PTP_CLOCK_GetTime_ns();
    uint64_t period_ns = (uint64_t)period_us * 1000ULL;
    uint64_t first_target_ns;
    if (phase_anchor_ns == 0u) {
        first_target_ns = now_ns + period_ns;
    } else {
        /* phase align: smallest anchor + N × period that is > now + margin */
        uint64_t margin_ns = period_ns;       /* at least one full period ahead */
        uint64_t ahead_ns  = now_ns + margin_ns;
        if (phase_anchor_ns >= ahead_ns) {
            first_target_ns = phase_anchor_ns;
        } else {
            uint64_t delta   = ahead_ns - phase_anchor_ns;
            uint64_t n_periods = (delta + period_ns - 1u) / period_ns;
            first_target_ns  = phase_anchor_ns + n_periods * period_ns;
        }
    }

    s_period_us       = period_us;
    s_cycles          = 0u;
    s_misses          = 0u;
    s_last_target_ns  = first_target_ns;
    s_saved_spin_us   = tfuture_get_spin_threshold_us();
    tfuture_set_spin_threshold_us(CYCLIC_FIRE_SPIN_US);
    tfuture_set_fire_callback(fire_callback);

    s_running = true;

    if (!tfuture_arm_at_ns(first_target_ns)) {
        /* Arm failed — roll everything back. */
        tfuture_set_fire_callback(NULL);
        tfuture_set_spin_threshold_us(s_saved_spin_us);
        SYS_PORT_PinClear(CYCLIC_FIRE_PIN);
        s_running = false;
        return false;
    }
    return true;
}

void cyclic_fire_stop(void)
{
    if (!s_running) {
        return;
    }
    s_running = false;             /* callback short-circuits before re-arming */
    tfuture_set_fire_callback(NULL);
    tfuture_cancel();
    tfuture_set_spin_threshold_us(s_saved_spin_us);
    SYS_PORT_PinClear(CYCLIC_FIRE_PIN);
}

bool     cyclic_fire_is_running(void)        { return s_running;         }
uint32_t cyclic_fire_get_period_us(void)     { return s_period_us;       }
uint64_t cyclic_fire_get_cycle_count(void)   { return s_cycles;          }
uint64_t cyclic_fire_get_missed_count(void)  { return s_misses;          }
