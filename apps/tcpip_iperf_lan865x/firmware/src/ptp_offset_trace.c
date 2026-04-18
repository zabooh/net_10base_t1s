#include "ptp_offset_trace.h"
#include "system/console/sys_console.h"
#include "system/time/sys_time.h"

/* Ring-buffer storage.
 * Each entry packs:
 *   offset_ns   : signed 32-bit nanoseconds
 *   sync_status : 8-bit (UNINIT=0, MATCHFREQ=1, HARDSYNC=2, COARSE=3, FINE=4)
 * Kept in two parallel arrays rather than a packed struct so alignment is
 * trivial and the compiler emits simple word-sized stores on Cortex-M4. */
static int32_t  s_offset[PTP_OFFSET_TRACE_SIZE];
static uint8_t  s_status[PTP_OFFSET_TRACE_SIZE];

static uint32_t s_head       = 0u;   /* next write position (0 .. SIZE-1) */
static uint32_t s_total      = 0u;   /* total records since last reset   */
static uint32_t s_overwrites = 0u;   /* how many oldest entries got overwritten */

void ptp_offset_trace_record(int32_t offset_ns, uint8_t sync_status)
{
    s_offset[s_head] = offset_ns;
    s_status[s_head] = sync_status;
    s_head = (s_head + 1u) % PTP_OFFSET_TRACE_SIZE;
    s_total++;
    if (s_total > PTP_OFFSET_TRACE_SIZE) {
        s_overwrites++;
    }
}

void ptp_offset_trace_reset(void)
{
    s_head       = 0u;
    s_total      = 0u;
    s_overwrites = 0u;
}

uint32_t ptp_offset_trace_count(void)
{
    return s_total;
}

/* Number of entries currently in the ring (at most SIZE). */
static uint32_t trace_live_count(void)
{
    return (s_total < PTP_OFFSET_TRACE_SIZE) ? s_total : PTP_OFFSET_TRACE_SIZE;
}

/* First index in chronological order.  Before wrap: always 0.
 * After wrap: head (oldest entry is at head, head-1 is newest). */
static uint32_t trace_first_index(void)
{
    return (s_total < PTP_OFFSET_TRACE_SIZE) ? 0u : s_head;
}

/* -------------------------------------------------------------------------
 * Dump format -- one sample per line, "<offset_ns> <status>".
 * Machine-readable by a trivial Python parser.
 *
 * Header/footer wrap the payload so the receiver can delimit reliably even
 * when other trace messages are interleaved.
 * ---------------------------------------------------------------------- */
/* Busy-wait N ticks of the system counter.  TC0 @ 60 MHz: 60 ticks/us. */
static void busy_wait_us(uint32_t microseconds)
{
    uint64_t start = SYS_TIME_Counter64Get();
    uint64_t ticks = (uint64_t)microseconds * 60ULL;
    while ((SYS_TIME_Counter64Get() - start) < ticks) {
        /* spin */
    }
}

/* Rate-limit the dump.  SYS_CONSOLE_PRINT is non-blocking and silently
 * drops data when its internal write buffer is full.  Each line is ~20-30
 * chars = 1.7-2.6 ms @ 115200 baud.
 *
 * Drain budget:  4 lines per batch = ~120 chars = ~10 ms TX time.
 * Pause 20 ms per batch — in which the UART drains ~230 chars = ~8 lines.
 * Net: buffer occupancy trends downward every batch.
 * Total for 1024 lines: 256 batches × 20 ms + TX time ≈ 7-8 s.
 * Acceptable for a diagnostic command; Python dump_offsets has a 60 s window. */
#define DUMP_BATCH_LINES    4u
#define DUMP_BATCH_PAUSE_US  20000u

void ptp_offset_trace_dump(void)
{
    uint32_t live = trace_live_count();
    uint32_t idx  = trace_first_index();

    SYS_CONSOLE_PRINT(
        "ptp_offset_dump: start count=%lu overwrites=%lu capacity=%lu\r\n",
        (unsigned long)live,
        (unsigned long)s_overwrites,
        (unsigned long)PTP_OFFSET_TRACE_SIZE);
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    for (uint32_t i = 0u; i < live; i++) {
        SYS_CONSOLE_PRINT("%ld %u\r\n",
                          (long)s_offset[idx],
                          (unsigned)s_status[idx]);
        idx = (idx + 1u) % PTP_OFFSET_TRACE_SIZE;
        if (((i + 1u) % DUMP_BATCH_LINES) == 0u) {
            busy_wait_us(DUMP_BATCH_PAUSE_US);
        }
    }
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    SYS_CONSOLE_PRINT("ptp_offset_dump: end\r\n");
}
