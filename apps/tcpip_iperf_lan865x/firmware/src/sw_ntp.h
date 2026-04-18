#ifndef SW_NTP_H
#define SW_NTP_H

#include <stdint.h>
#include <stdbool.h>

/*
 * sw_ntp — minimal software-NTP over UDP, modelled on how PTP is wired in
 * app.c but using PTP_CLOCK_GetTime_ns() for all four timestamps (T1..T4)
 * at the application layer, AFTER the TCP/IP stack.  Therefore it measures
 * the sync accuracy that a pure-software protocol would achieve on this
 * platform, with all SPI + FreeRTOS + stack latencies included in the jitter.
 *
 * One-byte opcode + three int64 timestamps per 32-byte UDP packet.
 * UDP port: 12345 (avoids collision with real NTP/123 and iperf/5001).
 *
 * The follower only MEASURES — it never corrects the PTP_CLOCK.  This
 * guarantees the PTP HW-servo (when enabled) remains the sole regulator, so
 * the same SW-NTP capture can run in both Phase A (HW-PTP off) and Phase B
 * (HW-PTP on) and produce directly-comparable statistics.
 */

typedef enum {
    SW_NTP_OFF = 0,
    SW_NTP_MASTER,
    SW_NTP_FOLLOWER
} sw_ntp_mode_t;

void          sw_ntp_init(void);
void          sw_ntp_service(void);      /* called from app.c main loop */

void          sw_ntp_set_mode(sw_ntp_mode_t mode);
sw_ntp_mode_t sw_ntp_get_mode(void);

/* Set master IPv4 address (follower only).  Argument in host byte order:
 * 192.168.0.30 -> (192<<24)|(168<<16)|(0<<8)|30. */
void          sw_ntp_set_master_ip(uint32_t ipv4_host_order);

void          sw_ntp_set_poll_interval_ms(uint32_t ms);
uint32_t      sw_ntp_get_poll_interval_ms(void);

void          sw_ntp_set_verbose(bool verbose);

void          sw_ntp_get_stats(uint32_t *samples_out,
                               uint32_t *timeouts_out,
                               int64_t  *last_offset_ns_out);

#endif /* SW_NTP_H */
