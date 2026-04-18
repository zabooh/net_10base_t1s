#ifndef PTP_OFFSET_TRACE_H
#define PTP_OFFSET_TRACE_H

#include <stdint.h>

/*
 * ptp_offset_trace -- high-resolution capture of PTP follower offset values.
 *
 * Each call to ptp_offset_trace_record() stores one hardware-derived
 * `offset = (t2-t1) - mean_path_delay` value into a ring buffer so the CLI
 * can dump a complete time-series AFTER the measurement is done -- without
 * UART traffic distorting the samples themselves (unlike ptp_trace).
 *
 * Typical usage:
 *   > ptp_offset_reset      (start fresh)
 *   ... let PTP run for N seconds ...
 *   > ptp_offset_dump       (prints all samples, one per line)
 *
 * A Python post-processor parses the output and computes mean / stdev /
 * histogram / Allan deviation over the real sub-microsecond offset data.
 */

#define PTP_OFFSET_TRACE_SIZE  1024u

void     ptp_offset_trace_record(int32_t offset_ns, uint8_t sync_status);
void     ptp_offset_trace_reset(void);
void     ptp_offset_trace_dump(void);
uint32_t ptp_offset_trace_count(void);

#endif /* PTP_OFFSET_TRACE_H */
