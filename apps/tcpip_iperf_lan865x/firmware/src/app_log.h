#ifndef APP_LOG_H
#define APP_LOG_H

/*
 * app_log — high-level application-event logger.
 *
 * app_log_event("...") emits one timestamped, tagged log line through
 * the PTP_LOG ring buffer, so all console output goes through the
 * same serialising flush path and gets the common
 *
 *     [hh:mm:ss.nnnnnnnnn DEMO ] ...
 *
 * formatting (see ptp_log.c tag table for the other subsystem tags:
 * APP, FOL, GM, PTP, IPERF).  Pre-PTP-lock the timestamp falls back
 * to "uptime=<ms>ms".
 */

#include "ptp_log.h"

static inline void app_log_event(const char *event)
{
    /* The "[DEMO]" prefix is recognised by ptp_log_enqueue() and
     * rewritten to "[<ts> DEMO ]" before the message lands in the
     * ring buffer. */
    PTP_LOG("[DEMO] %s\r\n", event);
}

#endif /* APP_LOG_H */
