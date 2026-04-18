#ifndef LOOP_STATS_H
#define LOOP_STATS_H

#include <stdint.h>
#include <stdbool.h>

/*
 * loop_stats — Per-subsystem main-loop timing instrumentation.
 *
 * Record_Start/Record_End bracket a call to a subsystem inside SYS_Tasks().
 * The module keeps max and avg elapsed time per subsystem since the last
 * LOOP_STATS_Reset() so a CLI command can query where the main loop is
 * actually spending time.
 *
 * Used to hunt ~9 ms main-loop stalls that cause clk_get measurement
 * outliers on the FOL.
 */

typedef enum {
    LOOP_STATS_SUBSYS_SYS_CMD = 0,
    LOOP_STATS_SUBSYS_TCPIP,
    LOOP_STATS_SUBSYS_LOG_FLUSH,
    LOOP_STATS_SUBSYS_APP,
    LOOP_STATS_SUBSYS_TOTAL,
    LOOP_STATS_SUBSYS_COUNT
} loop_stats_subsys_t;

void LOOP_STATS_RecordStart(loop_stats_subsys_t ss);
void LOOP_STATS_RecordEnd(loop_stats_subsys_t ss);
void LOOP_STATS_Reset(void);
void LOOP_STATS_Print(void);

#endif /* LOOP_STATS_H */
