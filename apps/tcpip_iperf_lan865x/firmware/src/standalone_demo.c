#include "standalone_demo.h"

#include <stdint.h>
#include <stdbool.h>

#include "system/ports/sys_ports.h"
#include "system/time/sys_time.h"
#include "peripheral/port/plib_port.h"

#include "cyclic_fire.h"
#include "ptp_clock.h"
#include "ptp_fol_task.h"
#include "ptp_gm_task.h"

/* ---------------------------------------------------------------------------
 * Pin assignments — SAM E54 Curiosity Ultra (see User's Guide DS70005405A
 * §Hardware Features).  Kept local to the demo; button_led.h is not
 * included on this branch so the two modules don't race for the GPIOs.
 * ------------------------------------------------------------------------ */
#define SW1_PIN         SYS_PORT_PIN_PD00
#define SW2_PIN         SYS_PORT_PIN_PD01
#define LED1_PIN        SYS_PORT_PIN_PC21   /* active-low: LOW = LED on   */
#define LED2_PIN        SYS_PORT_PIN_PA16
/* PD10 mirrors LED1's visible state (active-HIGH rectangle) so Saleae
 * can measure cross-board synchronicity of the 1 Hz visible blink. */
#define PD10_MIRROR_PIN SYS_PORT_PIN_PD10

#define SW1_GROUP       3u
#define SW1_PINNUM      0u
#define SW2_GROUP       3u
#define SW2_PINNUM      1u

#define LED_ON(pin)     SYS_PORT_PinClear(pin)
#define LED_OFF(pin)    SYS_PORT_PinSet(pin)
#define LED_TOGGLE(pin) SYS_PORT_PinToggle(pin)

/* ---------------------------------------------------------------------------
 * Timing constants
 * ------------------------------------------------------------------------ */

#define CYCLIC_PERIOD_US            500u

/* LED toggle rates encoded as PTP-wallclock half-period slot widths.
 * The decimator derives the LED state directly from the scheduled
 * target_ns via `(target_ns / SLOT_NS) & 1` — this way the LED phase
 * is a pure function of the absolute PTP-wallclock and does not depend
 * on a per-board fire-callback counter.  Once both boards are PTP-locked
 * they read identical target_ns values and therefore land on identical
 * LED phases; a per-board counter, by contrast, could diverge when one
 * board's cyclic_fire catches up over a PTP_CLOCK jump while the other
 * doesn't, producing a 180° LED mismatch that was occasionally seen on
 * the bench. */
#define LED1_SLOT_NS                500000000ULL   /* 500 ms half-period → 1 Hz */
#define LED2_SLOT_NS                250000000ULL   /* 250 ms half-period → 2 Hz */

#define DEBOUNCE_MS                 20u

/* Master-side "fake sync" time before LED2 goes solid.  The master's
 * software PTP_CLOCK gets updated on its own TX timestamps immediately,
 * so there's no real "waiting for lock" state — but visually we want the
 * same blink-then-solid sequence as the follower for the demo. */
#define MASTER_BLINK_DURATION_MS    2000u

/* ---------------------------------------------------------------------------
 * Demo state machine
 * ------------------------------------------------------------------------ */

typedef enum {
    DEMO_FREE         = 0,   /* boot state: no PTP, LED2 off, LED1 free-run */
    DEMO_SYNCING_FOL  = 1,   /* SW1 pressed: becoming follower, LED2 blink */
    DEMO_SYNCING_GM   = 2,   /* SW2 pressed: becoming master,   LED2 blink */
    DEMO_SYNCED       = 3,   /* lock achieved: LED2 solid on                */
} demo_state_t;

static demo_state_t     s_state = DEMO_FREE;
static uint64_t         s_state_enter_tick = 0u;
static uint64_t         s_ticks_per_ms     = 0u;

/* No per-LED counters any more — LED phase is computed stateless from
 * target_ns inside the decimator (see LED1_SLOT_NS / LED2_SLOT_NS). */

/* ---------------------------------------------------------------------------
 * Button debounce (active-low inputs with internal pull-up)
 * ------------------------------------------------------------------------ */

typedef struct {
    SYS_PORT_PIN pin;
    bool         stable_level;
    bool         last_raw_level;
    uint64_t     raw_change_tick;
} debounce_t;

static debounce_t s_sw1 = {SW1_PIN, true, true, 0u};
static debounce_t s_sw2 = {SW2_PIN, true, true, 0u};

static void configure_button_pullup(uint8_t group, uint8_t pin_num)
{
    PORT_REGS->GROUP[group].PORT_DIRCLR  = (1u << pin_num);
    PORT_REGS->GROUP[group].PORT_OUTSET  = (1u << pin_num);   /* pull direction = up */
    PORT_REGS->GROUP[group].PORT_PINCFG[pin_num] = 0x06u;     /* INEN | PULLEN */
}

static bool debounce_press_edge(debounce_t *d, uint64_t now_tick)
{
    bool raw = SYS_PORT_PinRead(d->pin);
    if (raw != d->last_raw_level) {
        d->last_raw_level  = raw;
        d->raw_change_tick = now_tick;
        return false;
    }
    if ((now_tick - d->raw_change_tick) < (DEBOUNCE_MS * s_ticks_per_ms)) {
        return false;
    }
    if (raw == d->stable_level) {
        return false;
    }
    d->stable_level = raw;
    return (raw == false);   /* HIGH→LOW = press */
}

/* ---------------------------------------------------------------------------
 * cyclic_fire user-callback hook — runs every half-period (250 µs).
 * Keeps this path short; no printf, no blocking calls.
 * ------------------------------------------------------------------------ */

static void demo_decimator(uint64_t target_ns)
{
    /* LED1 state is a pure function of the scheduled target_ns — after
     * PTP lock both boards see identical target_ns values and therefore
     * drive LED1 to the same polarity.  Also drive PD10 active-HIGH so
     * Saleae sees a clean 1 Hz rectangle tracking LED1's visible state. */
    bool led1_on = (((target_ns / LED1_SLOT_NS) & 1ULL) != 0ULL);
    if (led1_on) {
        LED_ON(LED1_PIN);
        SYS_PORT_PinSet(PD10_MIRROR_PIN);
    } else {
        LED_OFF(LED1_PIN);
        SYS_PORT_PinClear(PD10_MIRROR_PIN);
    }

    /* LED2 blinks at 2 Hz while in either SYNCING state; otherwise its
     * state is controlled by enter_state() (OFF in FREE, solid ON in
     * SYNCED) and we leave it alone. */
    if (s_state == DEMO_SYNCING_FOL || s_state == DEMO_SYNCING_GM) {
        bool led2_on = (((target_ns / LED2_SLOT_NS) & 1ULL) != 0ULL);
        if (led2_on) LED_ON(LED2_PIN);
        else         LED_OFF(LED2_PIN);
    }
}

/* ---------------------------------------------------------------------------
 * State transitions
 * ------------------------------------------------------------------------ */

static void enter_state(demo_state_t new_state, uint64_t now_tick)
{
    s_state            = new_state;
    s_state_enter_tick = now_tick;

    switch (new_state) {
    case DEMO_FREE:
        LED_OFF(LED2_PIN);
        break;
    case DEMO_SYNCING_FOL:
    case DEMO_SYNCING_GM:
        /* Start blinking from OFF so the first visible edge is a turn-on. */
        LED_OFF(LED2_PIN);
        break;
    case DEMO_SYNCED:
        LED_ON(LED2_PIN);
        break;
    }
}

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------ */

void standalone_demo_init(void)
{
    /* Buttons: input with internal pull-up. */
    configure_button_pullup(SW1_GROUP, SW1_PINNUM);
    configure_button_pullup(SW2_GROUP, SW2_PINNUM);

    /* LEDs: output, both OFF at boot (active-low drive, HIGH = off). */
    SYS_PORT_PinOutputEnable(LED1_PIN);
    LED_OFF(LED1_PIN);
    SYS_PORT_PinOutputEnable(LED2_PIN);
    LED_OFF(LED2_PIN);

    s_ticks_per_ms = (uint64_t)SYS_TIME_FrequencyGet() / 1000u;
    if (s_ticks_per_ms == 0u) s_ticks_per_ms = 60000u;

    /* Hook the decimator before we start cyclic_fire so the first
     * callback already counts. */
    cyclic_fire_set_user_callback(demo_decimator);

    /* Start cyclic_fire in free-run + SILENT mode.  SILENT skips the
     * native 250 µs PD10 toggle inside cyclic_fire — the decimator below
     * is the only thing that writes PD10, so the 1 MS/s Saleae capture
     * sees a clean 1 Hz rectangle rather than a 4 kHz glitch pattern.
     * PTP_CLOCK_ForceSet(0) satisfies cyclic_fire's "PTP valid"
     * precondition before any PTP role has been selected. */
    PTP_CLOCK_ForceSet(0u);
    (void)cyclic_fire_start_ex(CYCLIC_PERIOD_US, 0u,
                               CYCLIC_FIRE_PATTERN_SILENT);

    s_state = DEMO_FREE;
    s_state_enter_tick = SYS_TIME_Counter64Get();
}

void standalone_demo_service(uint64_t current_tick)
{
    bool sw1_pressed = debounce_press_edge(&s_sw1, current_tick);
    bool sw2_pressed = debounce_press_edge(&s_sw2, current_tick);

    /* Role selection is only accepted from the initial FREE state.  Once
     * a role has been chosen, the buttons are ignored — this keeps the
     * demo deterministic and avoids half-ways states the user has to
     * re-power-cycle out of. */
    if (s_state == DEMO_FREE) {
        if (sw1_pressed) {
            PTP_FOL_SetMode(PTP_SLAVE);
            enter_state(DEMO_SYNCING_FOL, current_tick);
        } else if (sw2_pressed) {
            /* Mirror what `ptp_mode master` does in ptp_cli.c: the FOL
             * mode flag has to flip to PTP_MASTER AND PTP_GM_Init() must
             * be called for the GM state machine to start sending Sync
             * frames.  Forgetting GM_Init() leaves the master silent —
             * the follower then never converges and its LED2 keeps
             * blinking forever. */
            PTP_FOL_SetMode(PTP_MASTER);
            PTP_GM_Init();
            enter_state(DEMO_SYNCING_GM, current_tick);
        }
    }

    /* Progress check: move SYNCING → SYNCED when the local sync criterion
     * is satisfied.  Different criterion per role. */
    if (s_state == DEMO_SYNCING_FOL) {
        if (PTP_FOL_GetServoState() == FINE) {
            enter_state(DEMO_SYNCED, current_tick);
        }
    } else if (s_state == DEMO_SYNCING_GM) {
        uint64_t elapsed_ticks = current_tick - s_state_enter_tick;
        if (elapsed_ticks >= (MASTER_BLINK_DURATION_MS * s_ticks_per_ms)) {
            enter_state(DEMO_SYNCED, current_tick);
        }
    }
}
