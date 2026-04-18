#ifndef SW_NTP_OFFSET_TRACE_H
#define SW_NTP_OFFSET_TRACE_H

#include <stdint.h>

/*
 * sw_ntp_offset_trace — ring buffer for software-NTP offset samples.
 *
 * Parallel to ptp_offset_trace but records int64 (SW-NTP offsets can grow
 * large when HW-PTP is OFF, since the crystal drift is unbounded over time).
 * Each record carries offset_ns plus a status byte (0=timeout, 1=valid).
 *
 * Typical usage:
 *   > sw_ntp_offset_reset
 *   ... let SW-NTP run for N seconds ...
 *   > sw_ntp_offset_dump   (prints "<offset_ns> <status>" per line)
 */

#define SW_NTP_OFFSET_TRACE_SIZE  1024u

void     sw_ntp_offset_trace_record(int64_t offset_ns, uint8_t valid);
void     sw_ntp_offset_trace_reset(void);
void     sw_ntp_offset_trace_dump(void);
uint32_t sw_ntp_offset_trace_count(void);

#endif /* SW_NTP_OFFSET_TRACE_H */
