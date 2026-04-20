#include "pd10_blink.h"

#include "system/time/sys_time.h"
#include "system/ports/sys_ports.h"

/* PD10 is EXT1 pin 5 ("GPIO1") on the SAM E54 Curiosity Ultra —
 * scope-clippable on the 2.54 mm header.  Marked "Available" in the
 * default pin_configurations.csv, so we configure it as output at
 * runtime without MCC changes. */
#define PD10_BLINK_PIN       SYS_PORT_PIN_PD10

static bool     s_running            = false;
static uint32_t s_hz                 = 0u;
static uint64_t s_half_period_ticks  = 0u;
static uint64_t s_next_toggle_tick   = 0u;

void pd10_blink_init(void)
{
    SYS_PORT_PinOutputEnable(PD10_BLINK_PIN);
    SYS_PORT_PinClear(PD10_BLINK_PIN);
    s_running           = false;
    s_hz                = 0u;
    s_half_period_ticks = 0u;
    s_next_toggle_tick  = 0u;
}

bool pd10_blink_set_hz(uint32_t hz)
{
    if (hz == 0u) {
        s_running = false;
        s_hz      = 0u;
        SYS_PORT_PinClear(PD10_BLINK_PIN);
        return true;
    }

    uint64_t ticks_per_sec  = (uint64_t)SYS_TIME_FrequencyGet();
    uint64_t half_period_ts = ticks_per_sec / (2ULL * (uint64_t)hz);
    if (half_period_ts == 0u) {
        /* Requested frequency higher than the tick resolution allows. */
        return false;
    }

    s_hz                = hz;
    s_half_period_ticks = half_period_ts;
    s_next_toggle_tick  = SYS_TIME_Counter64Get() + half_period_ts;
    SYS_PORT_PinClear(PD10_BLINK_PIN);
    s_running = true;
    return true;
}

void pd10_blink_service(uint64_t current_tick)
{
    if (!s_running) {
        return;
    }
    /* Signed compare so a wrapped s_next_toggle_tick (unlikely at 64-bit
     * but correct either way) still triggers. */
    if ((int64_t)(current_tick - s_next_toggle_tick) >= 0) {
        SYS_PORT_PinToggle(PD10_BLINK_PIN);
        /* Advance the scheduled tick by one half-period so the average
         * period stays drift-free even if service() is called slightly
         * late on some iterations. */
        s_next_toggle_tick += s_half_period_ticks;
    }
}

bool     pd10_blink_is_running(void) { return s_running; }
uint32_t pd10_blink_get_hz(void)     { return s_hz;      }
