#include "button_led.h"

#include <stdint.h>
#include <stdbool.h>

#include "system/ports/sys_ports.h"
#include "system/time/sys_time.h"
#include "peripheral/port/plib_port.h"   /* PORT_REGS for PINCFG PULLEN */

/* SAM E54 Curiosity Ultra pin assignments (see button_led.h) */
#define SW1_PIN   SYS_PORT_PIN_PD00
#define SW2_PIN   SYS_PORT_PIN_PD01
#define LED1_PIN  SYS_PORT_PIN_PC21
#define LED2_PIN  SYS_PORT_PIN_PA16

/* Group/pin numbers for direct PINCFG register access to enable the
 * pull-up resistor.  PORT_REGS->GROUP index: PA=0, PB=1, PC=2, PD=3. */
#define SW1_GROUP   3u
#define SW1_PINNUM  0u
#define SW2_GROUP   3u
#define SW2_PINNUM  1u

/* LED polarity: the user-LED drive transistors invert, so pin HIGH =
 * LED off and pin LOW = LED on.  Hide this from callers. */
#define LED_WRITE(pin, on)  do { if (on) SYS_PORT_PinClear(pin); else SYS_PORT_PinSet(pin); } while (0)
#define LED_READ(pin)       (!SYS_PORT_PinLatchRead(pin))

/* Debounce: a button must report the same raw level for this many
 * consecutive ms before a transition counts.  20 ms is the standard
 * bench value and plenty for the switches on this board. */
#define DEBOUNCE_MS  20u

typedef struct {
    SYS_PORT_PIN pin;
    bool         stable_level;      /* last accepted (debounced) level  */
    bool         last_raw_level;    /* last sample                      */
    uint64_t     raw_change_tick;   /* tick when last_raw_level changed */
} button_state_t;

static button_state_t s_sw1 = {SW1_PIN, true, true, 0u};
static button_state_t s_sw2 = {SW2_PIN, true, true, 0u};
static uint64_t       s_ticks_per_ms = 0u;

static void configure_button_pullup(uint8_t group, uint8_t pin_num)
{
    /* On SAM E54 the PORT pull resistor is enabled via PINCFG.PULLEN,
     * and its direction is chosen by the OUT register (OUT=1 → pull-up,
     * OUT=0 → pull-down).  INEN also needs to be set so the input
     * synchroniser actually delivers a value to PIN register reads. */
    PORT_REGS->GROUP[group].PORT_DIRCLR  = (1u << pin_num);      /* input */
    PORT_REGS->GROUP[group].PORT_OUTSET  = (1u << pin_num);      /* pull up */
    PORT_REGS->GROUP[group].PORT_PINCFG[pin_num] = 0x06u;        /* INEN | PULLEN */
}

static bool debounce_step(button_state_t *b, uint64_t now_tick)
{
    /* Returns true on a HIGH→LOW transition (button pressed), once the
     * new level has been stable for DEBOUNCE_MS.  Active-low switches,
     * so "pressed" corresponds to the low level. */
    bool raw = SYS_PORT_PinRead(b->pin);

    if (raw != b->last_raw_level) {
        b->last_raw_level  = raw;
        b->raw_change_tick = now_tick;
        return false;   /* wait for the level to settle */
    }

    uint64_t settled_ticks = now_tick - b->raw_change_tick;
    if (settled_ticks < (DEBOUNCE_MS * s_ticks_per_ms)) {
        return false;
    }

    if (raw == b->stable_level) {
        return false;   /* no new transition yet */
    }

    b->stable_level = raw;
    /* Press event is the HIGH→LOW edge (raw == false). */
    return (raw == false);
}

void button_led_init(void)
{
    /* Button pins: input with internal pull-up (idle HIGH). */
    configure_button_pullup(SW1_GROUP, SW1_PINNUM);
    configure_button_pullup(SW2_GROUP, SW2_PINNUM);

    /* LED pins: output, start OFF (HIGH = off for active-low drive). */
    SYS_PORT_PinOutputEnable(LED1_PIN);
    SYS_PORT_PinSet(LED1_PIN);
    SYS_PORT_PinOutputEnable(LED2_PIN);
    SYS_PORT_PinSet(LED2_PIN);

    s_ticks_per_ms = (uint64_t)SYS_TIME_FrequencyGet() / 1000u;
    if (s_ticks_per_ms == 0u) s_ticks_per_ms = 60000u;   /* safety: 60 MHz */
}

void button_led_service(uint64_t current_tick)
{
    if (debounce_step(&s_sw1, current_tick)) {
        SYS_PORT_PinToggle(LED1_PIN);
    }
    if (debounce_step(&s_sw2, current_tick)) {
        SYS_PORT_PinToggle(LED2_PIN);
    }
}

void button_led_set_led1(bool on) { LED_WRITE(LED1_PIN, on); }
void button_led_set_led2(bool on) { LED_WRITE(LED2_PIN, on); }
bool button_led_get_led1(void)    { return LED_READ(LED1_PIN); }
bool button_led_get_led2(void)    { return LED_READ(LED2_PIN); }
