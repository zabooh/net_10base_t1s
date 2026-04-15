#include "ptp_log.h"
#include <stdio.h>
#include <stdarg.h>
#include "config/default/system/console/sys_console.h"

/* -------------------------------------------------------------------------
 * Ring buffer configuration
 *   32 slots × 256 bytes = 8 KB RAM — well within ATSAME54P20A (256 KB)
 * ---------------------------------------------------------------------- */
#define PTP_LOG_QUEUE_SIZE  32u
#define PTP_LOG_MSG_LEN     256u

static char    ptp_log_buf[PTP_LOG_QUEUE_SIZE][PTP_LOG_MSG_LEN];
static uint8_t ptp_log_head = 0u;
static uint8_t ptp_log_tail = 0u;

/* -------------------------------------------------------------------------
 * ptp_log_enqueue — called from PTP task context instead of SYS_CONSOLE_PRINT.
 * If the ring buffer is full the message is silently dropped rather than
 * blocking or corrupting the queue.
 * ---------------------------------------------------------------------- */
void ptp_log_enqueue(const char *fmt, ...)
{
    uint8_t next = (uint8_t)((ptp_log_head + 1u) % PTP_LOG_QUEUE_SIZE);

    if (next == ptp_log_tail)
    {
        return; /* queue full — drop */
    }

    va_list ap;
    va_start(ap, fmt);
    vsnprintf(ptp_log_buf[ptp_log_head], PTP_LOG_MSG_LEN, fmt, ap);
    va_end(ap);

    ptp_log_head = next;
}

/* -------------------------------------------------------------------------
 * ptp_log_flush — called once per SYS_Tasks() iteration.
 * Drains the ring buffer through SYS_CONSOLE_PRINT so that all output
 * originates from a single serialised call site — no interleaving possible.
 * ---------------------------------------------------------------------- */
void ptp_log_flush(void)
{
    while (ptp_log_tail != ptp_log_head)
    {
        SYS_CONSOLE_PRINT("%s", ptp_log_buf[ptp_log_tail]);
        ptp_log_tail = (uint8_t)((ptp_log_tail + 1u) % PTP_LOG_QUEUE_SIZE);
    }
}
