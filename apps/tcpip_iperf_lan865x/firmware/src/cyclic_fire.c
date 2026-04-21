#include "cyclic_fire.h"

#include <stdbool.h>
#include <stdint.h>

#include "tfuture.h"
#include "ptp_clock.h"
#include "system/ports/sys_ports.h"

/* PD10 is the "GPIO1" position on the EXT1 Xplained-Pro header (pin 5)
 * on the SAM E54 Curiosity Ultra — a 2.54 mm header pin directly
 * accessible with a jumper / scope clip.  Marked "Available" in the
 * default pin_configurations.csv (chip pin 49), so we configure it
 * as output at runtime. */
#define CYCLIC_FIRE_PIN            SYS_PORT_PIN_PD10

/* Lowered tfuture spin threshold while cyclic_fire is running.  100 µs
 * means the spin blocks the main loop for at most 100 µs per fire,
 * leaving (period_us/2 − 100) µs for PTP / TCP-IP / etc. each half-cycle. */
#define CYCLIC_FIRE_SPIN_US        100u

static bool                  s_running            = false;
static uint32_t              s_period_us          = 0u;
static uint64_t              s_last_target_ns     = 0u;
static uint64_t              s_cycles             = 0u;
static uint64_t              s_misses             = 0u;
static uint32_t              s_saved_spin_us      = 0u;
static cyclic_fire_pattern_t s_pattern            = CYCLIC_FIRE_PATTERN_SQUARE;
static uint32_t              s_marker_phase       = 0u;   /* 0..9, MARKER pattern */

static void fire_callback(uint64_t target_ns, uint64_t actual_ns)
{
    (void)actual_ns;
    if (!s_running) {
        return;
    }

    /* Drive the pin per pattern.  Both patterns advance s_cycles, but the
     * MARKER pattern only toggles on specific phases within a 10-step
     * (= 5 full period) cycle, leaving the signal LOW for 4 out of 5
     * periods.  This produces isolated rising edges that are unambiguous
     * to compare across two boards on a scope. */
    if (s_pattern == CYCLIC_FIRE_PATTERN_SQUARE) {
        SYS_PORT_PinToggle(CYCLIC_FIRE_PIN);
    } else {
        /* MARKER:
         *   phase 0 → go HIGH  (rising edge starts the visible pulse)
         *   phase 2 → go LOW   (falling edge, 1 full period later)
         *   phases 1, 3..9    → no change (stay LOW for 4 more periods)
         * Cycle length = 10 half-period callbacks = 5 full periods. */
        if (s_marker_phase == 0u) {
            SYS_PORT_PinSet(CYCLIC_FIRE_PIN);
        } else if (s_marker_phase == 2u) {
            SYS_PORT_PinClear(CYCLIC_FIRE_PIN);
        }
        s_marker_phase = (s_marker_phase + 1u) % 10u;
    }

    /* Re-arm for the next half-period at absolute PTP-wallclock time =
     * target + period/2.  Using target_ns (not actual_ns) keeps the schedule
     * free of firing-jitter accumulation over time — jitter is bounded per
     * cycle instead of drifting. */
    uint64_t half_period_ns = (uint64_t)s_period_us * 500ULL;
    uint64_t next_target_ns = target_ns + half_period_ns;

    /* If we slipped past the next target already (unlikely at period ≥ 400 µs
     * but possible if something stalled the main loop), catch up by rebasing
     * to a future slot and count a miss. */
    uint64_t now_ns = PTP_CLOCK_GetTime_ns();
    while (next_target_ns <= now_ns) {
        next_target_ns += half_period_ns;
        s_misses++;
        /* Missed half-period still counts toward the MARKER phase; keep
         * the pattern aligned to absolute slots. */
        if (s_pattern == CYCLIC_FIRE_PATTERN_MARKER) {
            s_marker_phase = (s_marker_phase + 1u) % 10u;
        }
    }

    s_last_target_ns = next_target_ns;
    (void)tfuture_arm_at_ns(next_target_ns);
    s_cycles++;
}

bool cyclic_fire_start(uint32_t period_us, uint64_t phase_anchor_ns)
{
    return cyclic_fire_start_ex(period_us, phase_anchor_ns,
                                CYCLIC_FIRE_PATTERN_SQUARE);
}

bool cyclic_fire_start_ex(uint32_t period_us, uint64_t phase_anchor_ns,
                          cyclic_fire_pattern_t pattern)
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
    s_pattern         = pattern;
    /* Derive the MARKER phase counter from the absolute first_target tick
     * so all boards using the same anchor land on the same MARKER cycle
     * slot regardless of when their fire_callback first runs.  Without
     * this, a board armed N periods later (via the phase_align loop's
     * roll-forward above) would start s_marker_phase at 0 but be on a
     * different absolute MARKER slot — visible as a constant N×period_us
     * offset between two PTP-synced boards.  See R20 in readme_risks.md.
     * For SQUARE pattern the value is unused, so the calculation is
     * harmless either way. */
    {
        uint64_t half_period_ns = (uint64_t)period_us * 500ULL;
        s_marker_phase = (uint32_t)((first_target_ns / half_period_ns) % 10ULL);
    }
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
