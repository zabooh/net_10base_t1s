#include "loop_stats.h"
#include "system/time/sys_time.h"
#include "system/console/sys_console.h"

/* TC0 is at 60 MHz in this project (see ptp_clock.c); tick-to-us = tick / 60. */
#define LOOP_STATS_TC_FREQ_HZ  60000000ULL

static uint64_t s_start[LOOP_STATS_SUBSYS_COUNT];
static uint64_t s_max[LOOP_STATS_SUBSYS_COUNT];
static uint64_t s_sum[LOOP_STATS_SUBSYS_COUNT];
static uint32_t s_count[LOOP_STATS_SUBSYS_COUNT];

static const char *ss_names[LOOP_STATS_SUBSYS_COUNT] = {
    "SYS_CMD  ", "TCPIP    ", "LOG_FLUSH", "APP      ", "TOTAL    "
};

void LOOP_STATS_RecordStart(loop_stats_subsys_t ss)
{
    if ((unsigned)ss < (unsigned)LOOP_STATS_SUBSYS_COUNT) {
        s_start[ss] = SYS_TIME_Counter64Get();
    }
}

void LOOP_STATS_RecordEnd(loop_stats_subsys_t ss)
{
    if ((unsigned)ss < (unsigned)LOOP_STATS_SUBSYS_COUNT) {
        uint64_t elapsed = SYS_TIME_Counter64Get() - s_start[ss];
        if (elapsed > s_max[ss]) {
            s_max[ss] = elapsed;
        }
        s_sum[ss] += elapsed;
        s_count[ss]++;
    }
}

void LOOP_STATS_Reset(void)
{
    uint32_t i;
    for (i = 0u; i < (uint32_t)LOOP_STATS_SUBSYS_COUNT; i++) {
        s_max[i]   = 0u;
        s_sum[i]   = 0u;
        s_count[i] = 0u;
    }
}

static uint32_t ticks_to_us(uint64_t ticks)
{
    /* 60 MHz: us = ticks / 60  (avoid /1000000 divide on 64-bit result) */
    return (uint32_t)(ticks / (LOOP_STATS_TC_FREQ_HZ / 1000000ULL));
}

void LOOP_STATS_Print(void)
{
    uint32_t i;
    SYS_CONSOLE_PRINT("loop_stats: subsystem   max_us    avg_us   count\r\n");
    for (i = 0u; i < (uint32_t)LOOP_STATS_SUBSYS_COUNT; i++) {
        uint32_t max_us = ticks_to_us(s_max[i]);
        uint32_t avg_us = 0u;
        if (s_count[i] != 0u) {
            avg_us = ticks_to_us(s_sum[i] / (uint64_t)s_count[i]);
        }
        SYS_CONSOLE_PRINT("  %s  %7lu  %7lu  %7lu\r\n",
                          ss_names[i],
                          (unsigned long)max_us,
                          (unsigned long)avg_us,
                          (unsigned long)s_count[i]);
    }
}
