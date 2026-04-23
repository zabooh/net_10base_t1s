#include "ptp_log.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include "config/default/system/console/sys_console.h"
#include "ptp_clock.h"
#include "system/time/sys_time.h"

/* -------------------------------------------------------------------------
 * Tag prefix rewrite — turn "[APP]..." / "[FOL]..." / "[PTP-GM]..." /
 * "[PTP]..." / "[IPERF]..." / "[DEMO]..." into
 *   [hh:mm:ss.nnnnnnnnn APP  ] ...
 *   [hh:mm:ss.nnnnnnnnn FOL  ] ...
 *   [hh:mm:ss.nnnnnnnnn GM   ] ...
 *   [hh:mm:ss.nnnnnnnnn PTP  ] ...
 *   [hh:mm:ss.nnnnnnnnn IPERF] ...
 *   [hh:mm:ss.nnnnnnnnn DEMO ] ...
 * Before PTP_CLOCK is valid an uptime-based form is used instead.
 *
 * All known tags are left-aligned and padded to 5-char width so the
 * closing ']' lands at a fixed column across Master + Follower logs.
 *
 * If the message doesn't start with a recognised tag it is passed
 * through unchanged — that lets ad-hoc printouts still flow without
 * needing to opt in.
 * ---------------------------------------------------------------------- */
struct tag_match { const char *prefix; int plen; const char *tag5; };

static const struct tag_match k_tags[] = {
    /* Longer prefixes MUST come first so "[PTP-GM]" matches before "[PTP]". */
    { "[PTP-GM]", 8, "GM   " },
    { "[IPERF]",  7, "IPERF" },
    { "[DEMO]",   6, "DEMO " },
    { "[APP]",    5, "APP  " },
    { "[FOL]",    5, "FOL  " },
    { "[PTP]",    5, "PTP  " },
    { "[CLK]",    5, "CLK  " },
};
#define K_TAGS_COUNT (sizeof(k_tags)/sizeof(k_tags[0]))

static int format_ts(char *buf, size_t bufsz)
{
    if (PTP_CLOCK_IsValid()) {
        uint64_t ns = PTP_CLOCK_GetTime_ns();
        uint32_t s  = (uint32_t)(ns / 1000000000ULL);
        uint32_t nsec = (uint32_t)(ns % 1000000000ULL);
        uint32_t h = s / 3600u;
        uint32_t m = (s % 3600u) / 60u;
        uint32_t ss = s % 60u;
        return snprintf(buf, bufsz, "%02lu:%02lu:%02lu.%09lu",
                        (unsigned long)h, (unsigned long)m,
                        (unsigned long)ss, (unsigned long)nsec);
    }
    uint64_t ms = SYS_TIME_Counter64Get() / 60000ULL;
    return snprintf(buf, bufsz, "uptime=%llums",
                    (unsigned long long)ms);
}

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

    /* Format the caller's payload into a scratch buffer first so we can
     * inspect / rewrite the leading "[TAG]..." prefix before committing
     * the final line to the ring buffer. */
    char body[PTP_LOG_MSG_LEN];
    va_list ap;
    va_start(ap, fmt);
    int body_len = vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);
    if (body_len < 0) {
        body[0] = '\0';
        body_len = 0;
    }

    /* Detect a recognised tag prefix and splice in the timestamp. */
    const char *tag5 = NULL;
    const char *rest = body;
    for (size_t i = 0; i < K_TAGS_COUNT; i++) {
        if (strncmp(body, k_tags[i].prefix, (size_t)k_tags[i].plen) == 0) {
            tag5 = k_tags[i].tag5;
            rest = body + k_tags[i].plen;
            break;
        }
    }

    char *dst = ptp_log_buf[ptp_log_head];
    if (tag5 != NULL) {
        char ts[40];
        (void)format_ts(ts, sizeof(ts));
        /* Format: [<timestamp> <tag5>]<rest>
         * tag5 is already padded to 5 chars so the ']' lands in a fixed
         * column regardless of which subsystem emitted the line. */
        (void)snprintf(dst, PTP_LOG_MSG_LEN, "[%s %s]%s", ts, tag5, rest);
    } else {
        /* No tag → pass through unchanged. */
        (void)snprintf(dst, PTP_LOG_MSG_LEN, "%s", body);
    }

    ptp_log_head = next;
}

/* -------------------------------------------------------------------------
 * ptp_log_flush — called once per SYS_Tasks() iteration.
 * Drains the ring buffer through SYS_CONSOLE_PRINT so that all output
 * originates from a single serialised call site — no interleaving possible.
 *
 * Rate-limited to PTP_LOG_FLUSH_PER_TICK messages per call so a burst of
 * queued trace output (e.g. Sync/FollowUp mismatch + Delay_Req timeout +
 * retry + DELAY_CALC all queued inside one FollowUp processing pass) cannot
 * starve SYS_CMD_Tasks of main-loop time — otherwise incoming CLI commands
 * (like clk_get) get delayed, producing multi-ms measurement outliers.
 * ---------------------------------------------------------------------- */
#define PTP_LOG_FLUSH_PER_TICK  2u

void ptp_log_flush(void)
{
    uint8_t drained = 0u;
    while (ptp_log_tail != ptp_log_head && drained < PTP_LOG_FLUSH_PER_TICK)
    {
        SYS_CONSOLE_PRINT("%s", ptp_log_buf[ptp_log_tail]);
        ptp_log_tail = (uint8_t)((ptp_log_tail + 1u) % PTP_LOG_QUEUE_SIZE);
        drained++;
    }
}
