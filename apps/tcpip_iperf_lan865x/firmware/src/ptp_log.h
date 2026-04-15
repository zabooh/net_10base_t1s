#ifndef PTP_LOG_H
#define PTP_LOG_H

/*
 * ptp_log.h — Deferred log queue for PTP tasks.
 *
 * PTP_LOG(fmt, ...) enqueues a formatted message into a ring buffer.
 * ptp_log_flush()   must be called once per SYS_Tasks() iteration to drain
 *                   that buffer via SYS_CONSOLE_PRINT.  Because all prints
 *                   happen from a single call site they can never interleave.
 */

void ptp_log_enqueue(const char *fmt, ...);
void ptp_log_flush(void);

#define PTP_LOG ptp_log_enqueue

#endif /* PTP_LOG_H */
