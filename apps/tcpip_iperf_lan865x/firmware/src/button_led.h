#ifndef BUTTON_LED_H
#define BUTTON_LED_H

#include <stdint.h>
#include <stdbool.h>

/*
 * button_led — on-board user buttons SW1/SW2 toggle user LEDs LED1/LED2.
 *
 * Pin map (SAM E54 Curiosity Ultra DM320210, §Hardware Features of the
 * User's Guide DS70005405A):
 *   SW1 = PD00 (active-low, needs internal pull-up; no HW debounce)
 *   SW2 = PD01 (active-low, needs internal pull-up; no HW debounce)
 *   LED1 / USER_LED0 = PC21 (active-low: LOW = LED on)
 *   LED2 / USER_LED2 = PA16 (active-low: LOW = LED on)
 *
 * Behaviour: each press of SW1 toggles LED1; each press of SW2 toggles
 * LED2.  Edge is detected after a 20 ms debounce window.  Purely
 * main-loop polled; no interrupts.
 */

void button_led_init(void);

/* Call from the main loop every iteration.  current_tick is
 * SYS_TIME_Counter64Get() — passed in so the caller can reuse its
 * captured value without a second read. */
void button_led_service(uint64_t current_tick);

/* Force LED state (used e.g. for CLI diagnostics or startup self-test). */
void button_led_set_led1(bool on);
void button_led_set_led2(bool on);

bool button_led_get_led1(void);
bool button_led_get_led2(void);

#endif /* BUTTON_LED_H */
