#include "sw_ntp_offset_trace.h"
#include "system/console/sys_console.h"
#include "system/time/sys_time.h"

static int64_t  s_offset[SW_NTP_OFFSET_TRACE_SIZE];
static uint8_t  s_valid [SW_NTP_OFFSET_TRACE_SIZE];
static uint32_t s_head       = 0u;
static uint32_t s_total      = 0u;
static uint32_t s_overwrites = 0u;

void sw_ntp_offset_trace_record(int64_t offset_ns, uint8_t valid)
{
    s_offset[s_head] = offset_ns;
    s_valid [s_head] = valid;
    s_head = (s_head + 1u) % SW_NTP_OFFSET_TRACE_SIZE;
    s_total++;
    if (s_total > SW_NTP_OFFSET_TRACE_SIZE) {
        s_overwrites++;
    }
}

void sw_ntp_offset_trace_reset(void)
{
    s_head       = 0u;
    s_total      = 0u;
    s_overwrites = 0u;
}

uint32_t sw_ntp_offset_trace_count(void)
{
    return s_total;
}

static uint32_t trace_live_count(void)
{
    return (s_total < SW_NTP_OFFSET_TRACE_SIZE) ? s_total : SW_NTP_OFFSET_TRACE_SIZE;
}

static uint32_t trace_first_index(void)
{
    return (s_total < SW_NTP_OFFSET_TRACE_SIZE) ? 0u : s_head;
}

/* Same rate-limiting strategy as ptp_offset_trace: 4 lines per batch,
 * ~20 ms pause between batches; SW-NTP samples are fewer (e.g. 60 at 1 Hz
 * over a minute), so dump completes in well under a second. */
static void busy_wait_us(uint32_t microseconds)
{
    uint64_t start = SYS_TIME_Counter64Get();
    uint64_t ticks = (uint64_t)microseconds * 60ULL;
    while ((SYS_TIME_Counter64Get() - start) < ticks) {
        /* spin */
    }
}

#define DUMP_BATCH_LINES     4u
#define DUMP_BATCH_PAUSE_US  20000u

void sw_ntp_offset_trace_dump(void)
{
    uint32_t live = trace_live_count();
    uint32_t idx  = trace_first_index();

    SYS_CONSOLE_PRINT(
        "sw_ntp_offset_dump: start count=%lu overwrites=%lu capacity=%lu\r\n",
        (unsigned long)live,
        (unsigned long)s_overwrites,
        (unsigned long)SW_NTP_OFFSET_TRACE_SIZE);
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    for (uint32_t i = 0u; i < live; i++) {
        SYS_CONSOLE_PRINT("%lld %u\r\n",
                          (long long)s_offset[idx],
                          (unsigned)s_valid[idx]);
        idx = (idx + 1u) % SW_NTP_OFFSET_TRACE_SIZE;
        if (((i + 1u) % DUMP_BATCH_LINES) == 0u) {
            busy_wait_us(DUMP_BATCH_PAUSE_US);
        }
    }
    busy_wait_us(DUMP_BATCH_PAUSE_US);

    SYS_CONSOLE_PRINT("sw_ntp_offset_dump: end\r\n");
}
