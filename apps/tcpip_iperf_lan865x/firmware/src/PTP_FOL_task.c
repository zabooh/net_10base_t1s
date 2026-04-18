//DOM-IGNORE-BEGIN
/*
Copyright (C) 2025, Microchip Technology Inc., and its subsidiaries. All rights reserved.
Adapted from noIP-SAM-E54-Curiosity-PTP-Follower/ptp_task.c for use with the
Harmony TCP/IP stack (T1S 100BaseT Bridge project).

Key differences vs. the noIP version:
  - TC6_WriteRegister() replaced by DRV_LAN865X_WriteRegister() (fire-and-forget).
  - TC6_Service() blocking loops removed (the Harmony driver handles service internally).
  - get_macPhy_inst() / TC6_t* macPhy removed (use driver index 0 instead).
  - printf replaced with SYS_CONSOLE_PRINT.
  - ptpTask() renamed to PTP_FOL_Init().
  - PTP_FOL_OnFrame() added as the entry point from pktEth0Handler().
*/
//DOM-IGNORE-END

#include <stdio.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>
#include "PTP_FOL_task.h"
#include "filters.h"
#include "ptp_clock.h"
#include "ptp_ts_ipc.h"
#include "config/default/driver/lan865x/drv_lan865x.h"
#include "config/default/system/time/sys_time.h"
#include "ptp_log.h"

/* -------------------------------------------------------------------------
 * State machine for sequential register writes
 * ---------------------------------------------------------------------- */

typedef enum {
    FOL_REG_IDLE,
    FOL_REG_WRITE_TSL,
    FOL_REG_WAIT_TSL,
    FOL_REG_WRITE_TN,
    FOL_REG_WAIT_TN,
    FOL_REG_WRITE_PPSCTL,
    FOL_REG_WAIT_PPSCTL,
    FOL_REG_WRITE_TISUBN,
    FOL_REG_WAIT_TISUBN,
    FOL_REG_WRITE_TI,
    FOL_REG_WAIT_TI,
    FOL_REG_WRITE_TA,
    FOL_REG_WAIT_TA,
    FOL_REG_DONE
} fol_reg_state_t;

typedef enum {
    FOL_ACTION_NONE,
    FOL_ACTION_HARD_SYNC,
    FOL_ACTION_ENABLE_PPS,
    FOL_ACTION_SET_CLOCK_INC,
    FOL_ACTION_ADJUST_OFFSET
} fol_action_t;

typedef struct {
    uint32_t tsl_value;
    uint32_t tn_value;
    uint32_t tisubn_value;
    uint32_t ti_value;
    uint32_t ta_value;
} fol_reg_values_t;

static fol_reg_state_t  fol_reg_state         = FOL_REG_IDLE;
static fol_action_t     fol_pending_action    = FOL_ACTION_NONE;
static fol_reg_values_t fol_reg_values        = {0u, 0u, 0u, 0u, 0u};
static volatile bool    fol_reg_write_complete = false;
static uint32_t         fol_reg_timeout       = 0u;

/* -------------------------------------------------------------------------
 * Delay Request / Response state
 * ---------------------------------------------------------------------- */

/* Frame buffer for outgoing Delay_Req: 14 (eth) + 44 (delayReqMsg_t) */
#define FOL_DELAY_REQ_BUF_SIZE  58u
static uint8_t  fol_delay_req_buf[FOL_DELAY_REQ_BUF_SIZE];

static uint8_t  fol_src_mac[6]           = {0u};
static uint16_t fol_delay_req_seq_id     = 0u;
static uint16_t fol_delay_req_sent_seq_id = 0u;   /* seq_id of the currently pending Delay_Req */
static bool     fol_delay_req_pending    = false;  /* awaiting Delay_Resp */
static uint32_t fol_delay_req_sent_tick  = 0u;     /* SYS_TIME tick when Delay_Req was sent */
static volatile bool fol_tx_busy         = false;

/* t3: follower Delay_Req send time (ns, from software clock) */
static int64_t  fol_t3_ns               = 0;
/* t4: GM receive time of Delay_Req (ns, from Delay_Resp receiveTimestamp) */
static int64_t  fol_t4_ns               = 0;

/* Mean path delay and corrected offset (both in ns) */
static int64_t  fol_mean_path_delay     = 0;
static bool     fol_delay_valid         = false;  /* at least one Delay_Resp received */

/* -------------------------------------------------------------------------
 * Delay_Req TX hardware timestamp capture (t3) — mirrors GM TTSCA read
 * ---------------------------------------------------------------------- */
typedef enum {
    FOL_TTSCA_IDLE,
    FOL_TTSCA_CONFIG0_READ,       /* one-shot: read CONFIG0 to preserve other bits */
    FOL_TTSCA_CONFIG0_WAIT_READ,  /* one-shot: wait for read cb                    */
    FOL_TTSCA_CONFIG0_WAIT_WRITE, /* one-shot: wait for write cb (FTSE set)        */
    FOL_TTSCA_WRITE_TXMPATL, /* per-frame: write TXMPATL=0xF701 (Delay_Req pattern) */
    FOL_TTSCA_WAIT_TXMPATL,  /* per-frame: wait for TXMPATL write cb               */
    FOL_TTSCA_WRITE_TXMCTL,  /* per-frame: write TXMCTL=0x0002 to arm TX-Match     */
    FOL_TTSCA_WAIT_TXMCTL,   /* per-frame: wait for TXMCTL write cb; then send     */
    FOL_TTSCA_WAIT_TX_CB,    /* wait for TX-done callback (fol_tx_busy=false)      */
    FOL_TTSCA_READ_STATUS0,  /* issue ReadRegister(FOL_OA_STATUS0)                 */
    FOL_TTSCA_WAIT_STATUS0,  /* wait cb; check TTSCAA; retry up to N times         */
    FOL_TTSCA_WAIT_H,        /* DRV_LAN865X_ReadRegister(FOL_OA_TTSCAH) issued     */
    FOL_TTSCA_WAIT_L,        /* DRV_LAN865X_ReadRegister(FOL_OA_TTSCAL) issued     */
    FOL_TTSCA_WAIT_CLR       /* W1C STATUS0 write issued                           */
} fol_ttsca_state_t;

static fol_ttsca_state_t    fol_ttsca_state     = FOL_TTSCA_IDLE;
static volatile bool        fol_ttsca_op_done   = false;
static volatile uint32_t    fol_ttsca_op_val    = 0u;
static uint32_t             fol_ttsca_status0   = 0u;   /* STATUS0 snapshot for W1C */
static uint32_t             fol_ttsca_t3_sec    = 0u;   /* intermediate seconds     */
static uint32_t             fol_ttsca_retry     = 0u;   /* STATUS0 retry counter    */
static uint64_t             fol_t3_hw_ns        = 0u;   /* HW-captured t3           */
static bool                 fol_t3_hw_valid     = false; /* set when capture is done */
static bool                 fol_config0_set     = false; /* true after CONFIG0 FTSE armed */

/* Deferred Delay_Resp: when Delay_Resp arrives before TTSCA capture completes,
 * save t1/t2/t4 and finalize the calculation once TTSCA reaches IDLE. */
static bool    fol_delay_resp_deferred = false;
static int64_t fol_deferred_t1         = 0;
static int64_t fol_deferred_t2         = 0;
static int64_t fol_deferred_t4         = 0;

#define FOL_TTSCA_MAX_RETRIES   50u  /* max STATUS0 re-reads waiting for TTSCAA */

/* Forward declaration — defined after fol_ttsca_service() */
static void complete_delay_calc(int64_t t1, int64_t t2, int64_t t3, bool hw, int64_t t4);
/* Forward declaration — defined near sendDelayReq() */
static void fol_delay_req_timeout_service(void);

/* -------------------------------------------------------------------------
 * Globals (mirror of ptp_task.c)
 * ---------------------------------------------------------------------- */

ptpSync_ct      TS_SYNC;

static ptpMode_t ptpMode          = PTP_DISABLED;
static int32_t   ptp_sync_sequenceId = -1;
static uint8_t   syncReceived     = 0;
static bool      wallClockSet     = false;

volatile double rateRatio         = 1.0;
volatile double rateRatioIIR      = 1.0;
volatile double rateRatioFIR      = 1.0;
volatile double offsetIIR         = 0;
volatile double offsetFIR         = 0;

static uint8_t  ptpSynced         = 0;
static uint8_t  syncStatus        = UNINIT;
static uint8_t  prevSyncStatus    = UNINIT;
static bool     ptp_fol_verbose   = false;
static bool     ptp_trace_enabled = false;  /* activated by ptp_trace CLI command */
static uint32_t runs              = 0;
static uint64_t diffLocal         = 0;
static uint64_t diffRemote        = 0;

volatile int64_t  offset          = 0;
volatile uint64_t offset_abs      = 0;
volatile int      hardResync      = 0;

static double   rateRatioValue[FIR_FILER_SIZE]         = {0};
static lpfStateF rateRatiolpfState;

/* Calibrated TI/TISUBN saved at first UNINIT->MATCHFREQ; reused after GM restart
 * so that the 16-frame UNINIT re-measurement (which gives rateRatioFIR~1.0 because
 * the LAN865x clock is already at the calibrated rate) does not overwrite the
 * correct crystal-compensated increment value. */
static uint32_t calibratedTI_value     = 0u;
static uint32_t calibratedTISUBN_value = 0u;

static int32_t  offsetValue[FIR_FILER_SIZE_FINE]       = {0};
static lpfState offsetState;

static int32_t  offsetCoarseValue[FIR_FILER_SIZE_FINE] = {0};
static lpfState offsetCoarseState;

long double continiousratio = 1.0;
static int32_t  __attribute__((unused)) diff        = 0;
static int32_t  __attribute__((unused)) filteredDiff= 0;
long double corrNs          = 0.0;
long double corrNsFlt       = 0.0;

/* -------------------------------------------------------------------------
 * Register-write callback and service function
 * ---------------------------------------------------------------------- */

static void fol_reg_write_callback(void *reserved1, bool success, uint32_t addr,
                                   uint32_t value, void *pTag, void *reserved2)
{
    (void)reserved1; (void)addr; (void)value; (void)pTag; (void)reserved2;
    if (success) {
        fol_reg_write_complete = true;
    } else {
        PTP_LOG("[FOL] Register write failed: addr=0x%08X\r\n", (unsigned int)addr);
        fol_reg_state      = FOL_REG_IDLE;
        fol_pending_action = FOL_ACTION_NONE;
    }
}

/* -------------------------------------------------------------------------
 * Delay_Req TX timestamp (t3) capture via LAN865x TTSCA register pair
 * ---------------------------------------------------------------------- */
/* Forward declaration — defined after sendDelayReq */
static void fol_tx_callback(void *pInst, const uint8_t *pTx, uint16_t len,
                             void *pTag, void *pGlobalTag);

static void fol_ttsca_op_cb(void *r1, bool ok, uint32_t addr,
                             uint32_t value, void *tag, void *r2)
{
    (void)r1; (void)ok; (void)addr; (void)tag; (void)r2;
    fol_ttsca_op_val  = value;
    fol_ttsca_op_done = true;
}

static void fol_ttsca_service(void)
{
    switch (fol_ttsca_state) {
        case FOL_TTSCA_IDLE:
            break;

        /* ---- One-shot CONFIG0 RMW: set FTSE (bit 7) + bit 6 to enable TX TS capture ---- */
        case FOL_TTSCA_CONFIG0_READ:
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_CONFIG0,
                                                              true,
                                                              fol_ttsca_op_cb, NULL)) {
                PTP_LOG("[FOL] TTSCA: ReadRegister(CONFIG0) failed\r\n");
                fol_tx_busy     = false;
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_CONFIG0_WAIT_READ;
            break;

        case FOL_TTSCA_CONFIG0_WAIT_READ:
            if (!fol_ttsca_op_done) { break; }
            {
                /* Set bits 7 (FTSE) and 6 — same as GM_STATE_RMW_CONFIG0_WAIT_READ */
                uint32_t v = fol_ttsca_op_val | 0x000000C0u;
                fol_ttsca_op_done = false;
                if (TCPIP_MAC_RES_OK != DRV_LAN865X_WriteRegister(0u, FOL_OA_CONFIG0, v,
                                                                   true,
                                                                   fol_ttsca_op_cb, NULL)) {
                    PTP_LOG("[FOL] TTSCA: WriteRegister(CONFIG0) failed\r\n");
                    fol_tx_busy     = false;
                    fol_ttsca_state = FOL_TTSCA_IDLE;
                    break;
                }
                fol_ttsca_state = FOL_TTSCA_CONFIG0_WAIT_WRITE;
            }
            break;

        case FOL_TTSCA_CONFIG0_WAIT_WRITE:
            if (!fol_ttsca_op_done) { break; }
            fol_config0_set   = true;
            fol_ttsca_op_done = false;
            fol_ttsca_state   = FOL_TTSCA_WRITE_TXMPATL;
            break;

        /* ---- Per-frame: write TXMPATL=0xF701 (Delay_Req pattern — overrides GM's 0xF700 for Sync) ---- */
        case FOL_TTSCA_WRITE_TXMPATL:
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMPATL,
                                                               FOL_TXMPATL_DELAY_REQ,
                                                               true,
                                                               fol_ttsca_op_cb, NULL)) {
                PTP_LOG("[FOL] TTSCA: WriteRegister(TXMPATL) failed\r\n");
                fol_tx_busy     = false;
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_TXMPATL;
            break;

        case FOL_TTSCA_WAIT_TXMPATL:
            if (!fol_ttsca_op_done) { break; }
            /* TXMPATL=0xF701 confirmed — now arm TX-Match-Detector */
            fol_ttsca_op_done = false;
            fol_ttsca_state   = FOL_TTSCA_WRITE_TXMCTL;
            break;

        /* ---- Arm TX-Match-Detector before Delay_Req TX (same as GM Sync path) ---- */
        case FOL_TTSCA_WRITE_TXMCTL:
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMCTL, 0x0002u,
                                                               true,
                                                               fol_ttsca_op_cb, NULL)) {
                PTP_LOG("[FOL] TTSCA: WriteRegister(TXMCTL) failed\r\n");
                fol_tx_busy     = false;
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_TXMCTL;
            break;

        case FOL_TTSCA_WAIT_TXMCTL:
            if (!fol_ttsca_op_done) { break; }
            /* TXMCTL armed — capture SW t3 as close to TX as possible, then send */
            fol_t3_ns = (int64_t)PTP_CLOCK_GetTime_ns();
            if (!DRV_LAN865X_SendRawEthFrame(0u, fol_delay_req_buf,
                                             FOL_DELAY_REQ_BUF_SIZE,
                                             0x01u, /* tsc=1: TTSCA capture */
                                             fol_tx_callback, NULL)) {
                fol_tx_busy     = false;
                PTP_LOG("[FOL] Delay_Req send failed\r\n");
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            if (ptp_trace_enabled) {
                PTP_LOG("[TRACE] DELAY_REQ_SENT seq=%u t3_sw=%lld\r\n",
                        (unsigned)fol_delay_req_seq_id, (long long)fol_t3_ns);
            }
            fol_delay_req_pending     = true;
            fol_delay_req_sent_tick  = SYS_TIME_CounterGet();
            fol_delay_req_sent_seq_id = fol_delay_req_seq_id;  /* save before increment — IEEE 1588 §11.3.3 */
            fol_delay_req_seq_id++;
            fol_ttsca_state = FOL_TTSCA_WAIT_TX_CB;
            break;

        /* ---- Wait for TX-done callback, then explicitly read STATUS0 (mirrors GM) ---- */
        case FOL_TTSCA_WAIT_TX_CB:
            /* fol_tx_callback() sets fol_tx_busy=false when SPI TX is confirmed */
            if (fol_tx_busy) { break; }
            /* TX complete — now explicitly read STATUS0 for TTSCAA, same as GM */
            fol_ttsca_retry   = 0u;
            fol_ttsca_state   = FOL_TTSCA_READ_STATUS0;
            break;

        case FOL_TTSCA_READ_STATUS0:
        {
            /* The TC6 library's _OnStatus0 handler may have already read and W1C-cleared
             * STATUS0 (preserving TTSCAA in drvTsCaptureStatus0) via the EXST path.
             * Check the callback-captured value first — avoids reading 0 from hardware
             * (mirrors GM_STATE_READ_STATUS0). */
            uint32_t cbCapture = DRV_LAN865X_GetAndClearTsCapture(0u);
            if (0u != cbCapture) {
                fol_ttsca_status0 = cbCapture;
                fol_ttsca_op_done = false;
                if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_TTSCAH,
                                                                  true,
                                                                  fol_ttsca_op_cb, NULL)) {
                    PTP_LOG("[FOL] TTSCA: ReadRegister(TTSCAH) failed\r\n");
                    fol_ttsca_state = FOL_TTSCA_IDLE;
                    break;
                }
                fol_ttsca_state = FOL_TTSCA_WAIT_H;
                break;
            }
            /* CB not yet available — issue explicit STATUS0 SPI read */
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_STATUS0,
                                                              true,
                                                              fol_ttsca_op_cb, NULL)) {
                PTP_LOG("[FOL] TTSCA: ReadRegister(STATUS0) failed\r\n");
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_STATUS0;
            break;
        }

        case FOL_TTSCA_WAIT_STATUS0:
        {
            /* During the SPI round-trip the TC6 EXST handler may have captured
             * TTSCAA and W1C-cleared STATUS0.  Check the CB path first
             * (mirrors GM_STATE_WAIT_STATUS0). */
            uint32_t cbCapture = DRV_LAN865X_GetAndClearTsCapture(0u);
            if (0u != cbCapture) {
                fol_ttsca_status0 = cbCapture;
                fol_ttsca_op_done = false;
                if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_TTSCAH,
                                                                  true,
                                                                  fol_ttsca_op_cb, NULL)) {
                    PTP_LOG("[FOL] TTSCA: ReadRegister(TTSCAH) failed\r\n");
                    fol_ttsca_state = FOL_TTSCA_IDLE;
                    break;
                }
                fol_ttsca_state = FOL_TTSCA_WAIT_H;
                break;
            }
            if (!fol_ttsca_op_done) { break; }
            if (0u == (fol_ttsca_op_val & (uint32_t)FOL_STS0_TTSCAA)) {
                /* TTSCAA not yet set — retry up to FOL_TTSCA_MAX_RETRIES times */
                if (++fol_ttsca_retry > FOL_TTSCA_MAX_RETRIES) {
                    PTP_LOG("[FOL] t3 HW capture timeout STATUS0=0x%08X — SW fallback\r\n",
                            (unsigned int)fol_ttsca_op_val);
                    fol_ttsca_state = FOL_TTSCA_IDLE;
                    /* fol_t3_hw_valid remains false — finalize deferred calc with SW t3 */
                    if (fol_delay_resp_deferred) {
                        fol_delay_resp_deferred = false;
                        PTP_LOG("[FOL] t3 HW deferred timeout — deferred calc with SW t3\r\n");
                        complete_delay_calc(fol_deferred_t1, fol_deferred_t2,
                                            fol_t3_ns, false, fol_deferred_t4);
                    }
                    break;
                }
                fol_ttsca_state = FOL_TTSCA_READ_STATUS0; /* re-read STATUS0 */
                break;
            }
            /* TTSCAA is set via direct read — save STATUS0 for later W1C and read timestamps */
            fol_ttsca_status0 = fol_ttsca_op_val;
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_TTSCAH,
                                                              true,
                                                              fol_ttsca_op_cb,
                                                              NULL)) {
                PTP_LOG("[FOL] TTSCA: ReadRegister(TTSCAH) failed\r\n");
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_H;
            break;
        }

        case FOL_TTSCA_WAIT_H:
            if (!fol_ttsca_op_done) { break; }
            fol_ttsca_t3_sec  = fol_ttsca_op_val;
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_ReadRegister(0u, FOL_OA_TTSCAL,
                                                              true,
                                                              fol_ttsca_op_cb,
                                                              NULL)) {
                PTP_LOG("[FOL] TTSCA: ReadRegister(TTSCAL) failed\r\n");
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_L;
            break;

        case FOL_TTSCA_WAIT_L:
            if (!fol_ttsca_op_done) { break; }
            fol_t3_hw_ns    = (uint64_t)fol_ttsca_t3_sec * 1000000000ULL
                            + (uint64_t)fol_ttsca_op_val;
            fol_t3_hw_valid = true;
            if (ptp_trace_enabled) {
                PTP_LOG("[TRACE] T3_HW sec=%u ns=%u total=%llu\r\n",
                        (unsigned int)fol_ttsca_t3_sec,
                        (unsigned int)fol_ttsca_op_val,
                        (unsigned long long)fol_t3_hw_ns);
            }
            /* W1C: clear the TTSCAA bit in STATUS0 */
            fol_ttsca_op_done = false;
            if (TCPIP_MAC_RES_OK != DRV_LAN865X_WriteRegister(0u, FOL_OA_STATUS0,
                                                               fol_ttsca_status0,
                                                               true,
                                                               fol_ttsca_op_cb,
                                                               NULL)) {
                /* W1C failure is non-critical; t3 is already captured */
                fol_ttsca_state = FOL_TTSCA_IDLE;
                break;
            }
            fol_ttsca_state = FOL_TTSCA_WAIT_CLR;
            break;

        case FOL_TTSCA_WAIT_CLR:
            if (!fol_ttsca_op_done) { break; }
            fol_ttsca_state = FOL_TTSCA_IDLE;
            /* If processDelayResp was deferred while waiting for T3_HW, finalize now */
            if (fol_delay_resp_deferred) {
                fol_delay_resp_deferred = false;
                complete_delay_calc(fol_deferred_t1, fol_deferred_t2,
                                    (int64_t)fol_t3_hw_ns, true, fol_deferred_t4);
            }
            break;
    }
}

#define FOL_REG_TIMEOUT_MS 100u

void PTP_FOL_Service(void)
{
    if (ptpMode != PTP_SLAVE) {
        return;
    }

    fol_delay_req_timeout_service();
    fol_ttsca_service();

    switch (fol_reg_state) {
        case FOL_REG_IDLE:
            if (fol_pending_action != FOL_ACTION_NONE) {
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                fol_reg_state   = FOL_REG_WRITE_TSL;
            }
            break;

        case FOL_REG_WRITE_TSL:
            if (fol_pending_action == FOL_ACTION_HARD_SYNC) {
                fol_reg_write_complete = false;
                (void)DRV_LAN865X_WriteRegister(0u, MAC_TSL, fol_reg_values.tsl_value,
                                                true, fol_reg_write_callback, NULL);
                fol_reg_state = FOL_REG_WAIT_TSL;
            } else {
                fol_reg_state = FOL_REG_WRITE_PPSCTL;
            }
            break;

        case FOL_REG_WAIT_TSL:
            if (fol_reg_write_complete) {
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                fol_reg_state   = FOL_REG_WRITE_TN;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for MAC_TSL write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_WRITE_TN:
            fol_reg_write_complete = false;
            fol_reg_timeout = FOL_REG_TIMEOUT_MS;
            (void)DRV_LAN865X_WriteRegister(0u, MAC_TN, fol_reg_values.tn_value,
                                            true, fol_reg_write_callback, NULL);
            fol_reg_state = FOL_REG_WAIT_TN;
            break;

        case FOL_REG_WAIT_TN:
            if (fol_reg_write_complete) {
                PTP_LOG("[FOL] Hard sync completed\r\n");
                fol_reg_state = FOL_REG_DONE;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for MAC_TN write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_WRITE_PPSCTL:
            if (fol_pending_action == FOL_ACTION_ENABLE_PPS) {
                fol_reg_write_complete = false;
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                (void)DRV_LAN865X_WriteRegister(0u, PPSCTL, 0x000007Du,
                                                true, fol_reg_write_callback, NULL);
                fol_reg_state = FOL_REG_WAIT_PPSCTL;
            } else {
                fol_reg_state = FOL_REG_WRITE_TISUBN;
            }
            break;

        case FOL_REG_WAIT_PPSCTL:
            if (fol_reg_write_complete) {
                PTP_LOG("[FOL] 1PPS output enabled\r\n");
                fol_reg_state = FOL_REG_DONE;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for PPSCTL write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_WRITE_TISUBN:
            if (fol_pending_action == FOL_ACTION_SET_CLOCK_INC) {
                fol_reg_write_complete = false;
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                (void)DRV_LAN865X_WriteRegister(0u, MAC_TISUBN, fol_reg_values.tisubn_value,
                                                true, fol_reg_write_callback, NULL);
                fol_reg_state = FOL_REG_WAIT_TISUBN;
            } else {
                fol_reg_state = FOL_REG_WRITE_TA;
            }
            break;

        case FOL_REG_WAIT_TISUBN:
            if (fol_reg_write_complete) {
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                fol_reg_state   = FOL_REG_WRITE_TI;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for MAC_TISUBN write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_WRITE_TI:
            fol_reg_write_complete = false;
            fol_reg_timeout = FOL_REG_TIMEOUT_MS;
            (void)DRV_LAN865X_WriteRegister(0u, MAC_TI, fol_reg_values.ti_value,
                                            true, fol_reg_write_callback, NULL);
            fol_reg_state = FOL_REG_WAIT_TI;
            break;

        case FOL_REG_WAIT_TI:
            if (fol_reg_write_complete) {
                PTP_LOG("[FOL] Clock increment set: TI=%u TISUBN=0x%08X\r\n",
                        (unsigned int)fol_reg_values.ti_value,
                        (unsigned int)fol_reg_values.tisubn_value);
                fol_reg_state = FOL_REG_DONE;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for MAC_TI write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_WRITE_TA:
            if (fol_pending_action == FOL_ACTION_ADJUST_OFFSET) {
                fol_reg_write_complete = false;
                fol_reg_timeout = FOL_REG_TIMEOUT_MS;
                (void)DRV_LAN865X_WriteRegister(0u, MAC_TA, fol_reg_values.ta_value,
                                                true, fol_reg_write_callback, NULL);
                fol_reg_state = FOL_REG_WAIT_TA;
            } else {
                fol_reg_state = FOL_REG_DONE;
            }
            break;

        case FOL_REG_WAIT_TA:
            if (fol_reg_write_complete) {
                fol_reg_state = FOL_REG_DONE;
            } else if (--fol_reg_timeout == 0u) {
                PTP_LOG("[FOL] Timeout waiting for MAC_TA write\r\n");
                fol_reg_state      = FOL_REG_IDLE;
                fol_pending_action = FOL_ACTION_NONE;
            }
            break;

        case FOL_REG_DONE:
            fol_reg_state      = FOL_REG_IDLE;
            fol_pending_action = FOL_ACTION_NONE;
            break;
    }
}

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/* Full 64-bit byte swap using GCC builtin (XC32 / ARM Cortex-M4 supported) */
static uint64_t BSWAP64(uint64_t rawValue)
{
    uint32_t high = (uint32_t)((rawValue >> 32u) & 0xFFFFFFFFu);
    uint32_t low  = (uint32_t)((rawValue >>  0u) & 0xFFFFFFFFu);
    return (((uint64_t)__builtin_bswap32(low)) << 32u) |
            ((uint64_t)__builtin_bswap32(high));
}

static int64_t getCorrectionField(ptpHeader_t *hdr)
{
    return (int64_t)(BSWAP64((uint64_t)hdr->correctionField) >> 16);
}

uint64_t tsToInternal(const timeStamp_t *ts)
{
    uint64_t seconds = ((uint64_t)ts->secondsMsb << 32u) | ts->secondsLsb;
    return (seconds * SEC_IN_NS) + ts->nanoseconds;
}

/* -------------------------------------------------------------------------
 * Delay_Req TX callback
 * ---------------------------------------------------------------------- */

static void fol_tx_callback(void *pInst, const uint8_t *pTx, uint16_t len,
                             void *pTag, void *pGlobalTag)
{
    (void)pInst; (void)pTx; (void)len; (void)pTag; (void)pGlobalTag;
    fol_tx_busy = false;
}

/* Async timeout service: detects Delay_Req timeout at 1 ms granularity.
 * Called from PTP_FOL_Service() so the timeout fires independently of the
 * next Sync/FollowUp arrival — the old design piggy-backed the check on
 * sendDelayReq() which only runs every ~125 ms, delaying retries (and
 * delaying CLI responses when the burst of timeout handling + retry happens
 * inside a single FollowUp processing pass). */
static void fol_delay_req_timeout_service(void)
{
    if (!fol_delay_req_pending) {
        return;
    }
    uint32_t elapsed = SYS_TIME_CounterGet() - fol_delay_req_sent_tick;
    if (elapsed >= SYS_TIME_MSToCount(500u)) {
        if (ptp_trace_enabled) {
            PTP_LOG("[FOL] Delay_Req timeout -- retrying\r\n");
        }
        fol_delay_req_pending = false;
    }
}

/* Build and send a Delay_Req frame.
 * t1, t2 are the timestamps from the latest processed FollowUp so that the
 * (t2-t1) term used for delay calculation matches the same Sync cycle.
 * Timeout handling now lives in fol_delay_req_timeout_service() (1 ms tick). */
static void sendDelayReq(uint64_t t1_ns, uint64_t t2_ns)
{
    if (fol_delay_req_pending) {
        return;  /* still waiting for Delay_Resp (async timeout will clear) */
    }
    if (fol_tx_busy || fol_ttsca_state != FOL_TTSCA_IDLE) {
        return;  /* TX path or TTSCA capture still busy */
    }

    /* Build Ethernet header */
    /* Destination: broadcast FF:FF:FF:FF:FF:FF.
     * The LAN865x MAC address filter is not configured for PTP multicast
     * (01:1B:19:00:00:00), so multicast Delay_Req frames are silently
     * dropped at the GM's hardware RX filter.  Using broadcast ensures
     * all nodes on the T1S bus receive the frame. */
    fol_delay_req_buf[0] = 0xFFu; fol_delay_req_buf[1] = 0xFFu;
    fol_delay_req_buf[2] = 0xFFu; fol_delay_req_buf[3] = 0xFFu;
    fol_delay_req_buf[4] = 0xFFu; fol_delay_req_buf[5] = 0xFFu;
    /* Source MAC */
    fol_delay_req_buf[6]  = fol_src_mac[0]; fol_delay_req_buf[7]  = fol_src_mac[1];
    fol_delay_req_buf[8]  = fol_src_mac[2]; fol_delay_req_buf[9]  = fol_src_mac[3];
    fol_delay_req_buf[10] = fol_src_mac[4]; fol_delay_req_buf[11] = fol_src_mac[5];
    /* EtherType 0x88F7 */
    fol_delay_req_buf[12] = PTP_ETHER_TYPE_H;
    fol_delay_req_buf[13] = PTP_ETHER_TYPE_L;

    /* Build Delay_Req message body */
    delayReqMsg_t *msg = (delayReqMsg_t *)(&fol_delay_req_buf[14]);
    memset(msg, 0, sizeof(delayReqMsg_t));
    msg->header.tsmt          = (uint8_t)MSG_DELAY_REQ;  /* messageType=0x01 */
    msg->header.version       = 0x02u;                    /* PTPv2 */
    msg->header.messageLength = htons((uint16_t)sizeof(delayReqMsg_t));
    /* clockIdentity from MAC (EUI-64) */
    msg->header.sourcePortIdentity.clockIdentity[0] = fol_src_mac[0];
    msg->header.sourcePortIdentity.clockIdentity[1] = fol_src_mac[1];
    msg->header.sourcePortIdentity.clockIdentity[2] = fol_src_mac[2];
    msg->header.sourcePortIdentity.clockIdentity[3] = 0xFFu;
    msg->header.sourcePortIdentity.clockIdentity[4] = 0xFEu;
    msg->header.sourcePortIdentity.clockIdentity[5] = fol_src_mac[3];
    msg->header.sourcePortIdentity.clockIdentity[6] = fol_src_mac[4];
    msg->header.sourcePortIdentity.clockIdentity[7] = fol_src_mac[5];
    msg->header.sourcePortIdentity.portNumber = htons(1u);
    msg->header.sequenceID    = htons(fol_delay_req_seq_id);
    msg->header.controlField  = 0x01u;  /* Delay_Req control value */
    msg->header.logMessageInterval = 0x7Fu;
    /* originTimestamp = 0 (as per IEEE 1588 §11.3.2) */

    /* SW t3 pre-init; refreshed to actual send time in FOL_TTSCA_WAIT_TXMCTL */
    fol_t3_ns       = (int64_t)PTP_CLOCK_GetTime_ns();
    fol_t3_hw_valid = false;

    /* Store in global TS_SYNC which already holds these values. */
    (void)t1_ns; (void)t2_ns;  /* delay calculation uses TS_SYNC directly   */

    /* Mark TX busy now to block re-entry; actual send happens in state machine.
     * On first invocation (fol_config0_set==false), do CONFIG0 RMW first to set
     * FTSE (bit 7) which enables LAN865x TX timestamp capture. */
    fol_tx_busy       = true;
    fol_ttsca_op_done = false;
    /* Always write TXMPATL=0xF701 callback-confirmed per Delay_Req (driver _InitMemMap
     * sets TXMPATL=0xF700 for Sync; fire-and-forget is not reliable). Skip the
     * one-shot CONFIG0 RMW only when it has already run. */
    fol_ttsca_state   = fol_config0_set ? FOL_TTSCA_WRITE_TXMPATL
                                        : FOL_TTSCA_CONFIG0_READ;
}

/* Parse a received Delay_Resp and update the mean path delay. */
/* Finalize a Delay_Req/Resp calculation.  Called either directly from
 * processDelayResp (when t3_hw already valid or TTSCA already idle) or
 * deferred from fol_ttsca_service once the TTSCA state machine reaches IDLE. */
static void complete_delay_calc(int64_t t1, int64_t t2, int64_t t3, bool hw, int64_t t4)
{
    int64_t forward  = t2 - t1;    /* path: GM → follower  */
    int64_t backward = t4 - t3;    /* path: follower → GM  */
    fol_mean_path_delay = (forward + backward) / 2;
    fol_delay_valid     = true;

    if (ptp_trace_enabled) {
        PTP_LOG("[TRACE] DELAY_CALC t1=%lld t2=%lld t3=%lld(hw=%d) t4=%lld fwd=%lld bwd=%lld delay=%lld\r\n",
                (long long)t1, (long long)t2,
                (long long)t3, (int)hw, (long long)t4,
                (long long)forward, (long long)backward,
                (long long)fol_mean_path_delay);
    }
}

static void processDelayResp(delayRespMsg_t *ptpPkt, uint64_t rxTimestamp)
{
    (void)rxTimestamp;

    if (!fol_delay_req_pending) {
        if (ptp_trace_enabled) {
            PTP_LOG("[TRACE] DELAY_RESP_UNSOLICITED seq=%u\r\n",
                    (unsigned)htons(ptpPkt->header.sequenceID));
        }
        return;  /* unsolicited Delay_Resp — ignore */
    }

    if (ptp_trace_enabled) {
        PTP_LOG("[TRACE] DELAY_RESP_RECEIVED seq=%u\r\n",
                (unsigned)htons(ptpPkt->header.sequenceID));
    }

    /* Verify sequenceId matches our pending Delay_Req (IEEE 1588 §11.3.3) */
    if (htons(ptpPkt->header.sequenceID) != fol_delay_req_sent_seq_id) {
        if (ptp_trace_enabled) {
            PTP_LOG("[TRACE] DELAY_RESP_WRONG_SEQ got=%u expected=%u\r\n",
                    (unsigned)htons(ptpPkt->header.sequenceID),
                    (unsigned)fol_delay_req_sent_seq_id);
        }
        PTP_LOG("[FOL] Delay_Resp seq mismatch — ignored\r\n");
        return;
    }

    /* Verify that the Delay_Resp is addressed to us (requestingPortIdentity) */
    if (memcmp(ptpPkt->requestingPortIdentity.clockIdentity,
               ((delayReqMsg_t *)&fol_delay_req_buf[14])->header.sourcePortIdentity.clockIdentity,
               sizeof(clockIdentity_t)) != 0) {
        if (ptp_trace_enabled) {
            PTP_LOG("[TRACE] DELAY_RESP_WRONG_CLOCK seq=%u\r\n",
                    (unsigned)htons(ptpPkt->header.sequenceID));
        }
        PTP_LOG("[FOL] Delay_Resp for different clock — ignored\r\n");
        return;
    }

    /* Extract t4: GM's receive time of our Delay_Req (receiveTimestamp field) */
    uint32_t t4_sec  = htonl(ptpPkt->receiveTimestamp.secondsLsb);
    uint32_t t4_nsec = htonl(ptpPkt->receiveTimestamp.nanoseconds);
    fol_t4_ns = (int64_t)((uint64_t)t4_sec * SEC_IN_NS + (uint64_t)t4_nsec);

    /* Retrieve t1 and t2 from the latest processed FollowUp cycle */
    int64_t t1_ns = (int64_t)tsToInternal(&TS_SYNC.origin);
    int64_t t2_ns = (int64_t)tsToInternal(&TS_SYNC.receipt);

    /* If the TTSCA capture is still in progress, defer the calculation.
     * The state machine will call complete_delay_calc() once it reaches IDLE
     * (either with a valid T3_HW or with the SW fallback on timeout).
     * Block any new Delay_Req by keeping the TTSCA cycle running; unblock
     * sendDelayReq by clearing fol_delay_req_pending so the timeout guard
     * does not wrongly retransmit while we wait. */
    if (fol_ttsca_state != FOL_TTSCA_IDLE) {
        fol_delay_resp_deferred = true;
        fol_deferred_t1         = t1_ns;
        fol_deferred_t2         = t2_ns;
        fol_deferred_t4         = fol_t4_ns;
        fol_delay_req_pending   = false;  /* prevent false-timeout retransmit */
        return;
    }

    /* TTSCA already idle — use whichever t3 is available */
    int64_t t3_used = fol_t3_hw_valid ? (int64_t)fol_t3_hw_ns : fol_t3_ns;
    if (!fol_t3_hw_valid) {
        PTP_LOG("[FOL] t3 HW not ready — SW fallback used\r\n");
    }
    fol_delay_req_pending = false;
    complete_delay_calc(t1_ns, t2_ns, t3_used, fol_t3_hw_valid, fol_t4_ns);
}

/* -------------------------------------------------------------------------
 * Slave-node reset
 * ---------------------------------------------------------------------- */

static void resetSlaveNode(void)
{
    PTP_LOG("GM_RESET -> Slave node reset initiated due to sequence ID mismatch\r\n");

    ptp_sync_sequenceId = -1;
    syncReceived        = 0;
    wallClockSet        = false;
    ptpSynced           = 0;
    runs                = 0;
    hardResync          = 1;    /* force hard-sync on first FollowUp after reset */
    diffLocal           = 0;
    diffRemote          = 0;

    /* Reset Delay_Req / Delay_Resp state */
    fol_delay_req_pending    = false;
    fol_delay_req_sent_tick  = 0u;
    fol_delay_valid          = false;
    fol_mean_path_delay      = 0;
    fol_t3_ns                = 0;
    fol_t4_ns                = 0;
    fol_delay_req_seq_id      = 0u;
    fol_delay_req_sent_seq_id = 0u;

    /* Reset HW t3 capture state machine */
    fol_t3_hw_valid        = false;
    fol_t3_hw_ns           = 0u;
    fol_ttsca_state        = FOL_TTSCA_IDLE;
    fol_ttsca_op_done      = false;
    fol_ttsca_retry        = 0u;
    fol_tx_busy            = false;
    fol_delay_resp_deferred = false;

    memset(&TS_SYNC, 0, sizeof(ptpSync_ct));

    for (uint32_t x = 0; x < FIR_FILER_SIZE_FINE; x++) {
        firLowPassFilter(0, &offsetCoarseState);
        firLowPassFilter(0, &offsetState);
    }
    for (uint32_t x = 0; x < FIR_FILER_SIZE; x++) {
        firLowPassFilterF(1.0, &rateRatiolpfState);
    }

    if (calibratedTI_value != 0u) {
        /* Fast-reset path: LAN865x TI/TISUBN registers already hold the
         * calibrated crystal-compensation value — re-apply them and skip
         * the 16-frame UNINIT re-measurement that would produce
         * rateRatioFIR~1.0 (= uncompensated, 5ppm drift per frame). */
        fol_reg_values.ti_value     = calibratedTI_value;
        fol_reg_values.tisubn_value = calibratedTISUBN_value;
        fol_pending_action          = FOL_ACTION_SET_CLOCK_INC;
        syncStatus                  = MATCHFREQ;   /* jump straight to MATCHFREQ */
        PTP_LOG("GM_RESET: reusing calibrated TI=%u TISUBN=0x%08X\r\n",
                (unsigned int)calibratedTI_value,
                (unsigned int)calibratedTISUBN_value);
    } else {
        syncStatus = UNINIT;   /* first boot: normal calibration needed */
    }
    prevSyncStatus = syncStatus;

    PTP_FOL_Init();
}

/* -------------------------------------------------------------------------
 * PTP message processors
 * ---------------------------------------------------------------------- */

static void processSync(syncMsg_t *ptpPkt)
{
    uint16_t seqId = htons(ptpPkt->header.sequenceID);
    if (ptp_sync_sequenceId < 0) {
        ptp_sync_sequenceId = seqId;
        syncReceived        = 0;
    } else {
        int sequenceDifference = abs((int)seqId - (int)ptp_sync_sequenceId);
        if (sequenceDifference > 10) {
            PTP_LOG("Large sequence mismatch: %u vs %d. Resetting...\r\n",
                    (unsigned int)seqId, (int)ptp_sync_sequenceId);
            resetSlaveNode();
        } else if ((int)ptp_sync_sequenceId == (int)seqId) {
            syncReceived = 1;
        } else {
            syncReceived = 0;
            if (ptp_trace_enabled) {
                PTP_LOG("Sync seqId mismatch. Is: %u - %d\r\n",
                        (unsigned int)seqId, (int)ptp_sync_sequenceId);
            }
            ptp_sync_sequenceId = -1;
        }
    }
}

static void processFollowUp(followUpMsg_t *ptpPkt)
{
    uint16_t seqId = htons(ptpPkt->header.sequenceID);
    if (ptp_sync_sequenceId >= 0 && syncReceived) {
        if (ptp_sync_sequenceId == (int)seqId) {
            ptp_sync_sequenceId = (ptp_sync_sequenceId + 1) & 0xFFFF;
            syncReceived = 0;
        } else {
            if (ptp_trace_enabled) {
                PTP_LOG("FollowUp seqId mismatch. Is: %u - %d\r\n",
                        (unsigned int)seqId, (int)ptp_sync_sequenceId);
            }
            ptp_sync_sequenceId = -1;
            memset(&TS_SYNC.receipt,      0, sizeof(ptpTimeStamp_t));
            memset(&TS_SYNC.receipt_prev, 0, sizeof(ptpTimeStamp_t));
            return;
        }
    } else {
        ptp_sync_sequenceId = ((int)seqId + 1) & 0xFFFF;
        if (ptp_trace_enabled) {
            PTP_LOG("FollowUp seqId out of sync. Is: %u - %d\r\n",
                    (unsigned int)seqId, (int)ptp_sync_sequenceId);
        }
        return;
    }

    /* Extract t1 from PTP frame */
    TS_SYNC.origin.secondsMsb  = htons(ptpPkt->preciseOriginTimestamp.secondsMsb);
    TS_SYNC.origin.secondsLsb  = htonl(ptpPkt->preciseOriginTimestamp.secondsLsb);
    TS_SYNC.origin.nanoseconds = htonl(ptpPkt->preciseOriginTimestamp.nanoseconds);
    TS_SYNC.origin.correctionField = (uint64_t)getCorrectionField(&ptpPkt->header);

    /* Hard sync: set the local wall clock directly to the GM timestamp */
    if (hardResync) {
        fol_reg_values.tsl_value = TS_SYNC.origin.secondsLsb;
        fol_reg_values.tn_value  = TS_SYNC.origin.nanoseconds;
        fol_pending_action = FOL_ACTION_HARD_SYNC;
        PTP_LOG("Large offset, scheduling hard sync\r\n");
        hardResync = 0;
    }

    /* Enable 1PPS output once the clock is synced */
    if (ptpSynced && !wallClockSet) {
        fol_pending_action = FOL_ACTION_ENABLE_PPS;
        wallClockSet = true;
    }

    /* Convert to internal ns representation */
    uint64_t t1 = tsToInternal(&TS_SYNC.origin);
    uint64_t t2 = tsToInternal(&TS_SYNC.receipt);

    /* Discard this Sync if the local receive timestamp is not yet available.
     * This happens on the very first Sync after a reset when the MAC RX
     * timestamp has not been captured yet.  Using t2=0 would produce a
     * multi-second offset spike that corrupts the rateRatio filter. */
    if (t2 == 0u) {
        return;
    }

    /* Update software PTP clock anchor: t2 is the RTSA wallclock, sysTickAtRx
     * was captured atomically alongside it in TC6_CB_OnRxEthernetPacket(). */
    if (g_ptp_raw_rx.sysTickAtRx != 0u)
    {
        PTP_CLOCK_Update(t2, g_ptp_raw_rx.sysTickAtRx);
    }

    if (TS_SYNC.receipt_prev.secondsLsb != 0u) {
        uint64_t curr = t2;
        uint64_t prev = tsToInternal(&TS_SYNC.receipt_prev);
        diffLocal = curr - prev;
    }

    if (TS_SYNC.origin_prev.secondsLsb != 0u) {
        uint64_t curr = t1;
        uint64_t prev = tsToInternal(&TS_SYNC.origin_prev);
        diffRemote = curr - prev;
    }

    TS_SYNC.receipt_prev = TS_SYNC.receipt;
    TS_SYNC.origin_prev  = TS_SYNC.origin;

    /* Rate-ratio estimation */
    if (diffLocal && diffRemote) {
        if (syncStatus == UNINIT || syncStatus > HARDSYNC) {
            rateRatio = (double)diffRemote / (double)diffLocal;
            if (rateRatio > 0.998 && rateRatio < 1.002) {
                rateRatioIIR = lowPassExponential((double)diffRemote / (double)diffLocal,
                                                  rateRatio, 0.5);
                rateRatioFIR = firLowPassFilterF((double)diffRemote / (double)diffLocal,
                                                 &rateRatiolpfState);
                PTP_CLOCK_SetDriftPPB((int32_t)((rateRatioFIR - 1.0) * 1e9));
            } else {
                if (ptp_trace_enabled) {
                    PTP_LOG("Filtered rateRatio outlier\r\n");
                }
            }
        }
        runs++;
    } else {
        return;
    }

    offset     = (int64_t)t2 - (int64_t)t1;

    /* Apply IEEE 1588 mean path delay correction when a Delay_Resp has been
     * received.  The corrected offset is:
     *   offset_from_master = ((t2-t1) - (t4-t3)) / 2
     *                      = (t2-t1) - mean_path_delay               */
    if (fol_delay_valid) {
        offset -= fol_mean_path_delay;
    }

    uint8_t neg = (offset < 0) ? 0u : 1u;
    offset_abs  = (uint64_t)llabs(offset);

    /* ---- Clock servo state machine ---- */
    if (syncStatus == UNINIT) {
        if (runs >= (FIR_FILER_SIZE * 1u)) {
            double calcInc = CLOCK_CYCLE_NS * rateRatioFIR;
            uint8_t mac_ti = (uint8_t)calcInc;
            double calcSubInc = calcInc - (double)mac_ti;
            calcSubInc *= 16777216.0;
            uint32_t calcSubInc_uint = (uint32_t)calcSubInc;
            calcSubInc_uint = ((calcSubInc_uint >> 8) & 0xFFFFu)
                            | ((calcSubInc_uint & 0xFFu) << 24);

            fol_reg_values.tisubn_value = calcSubInc_uint;
            fol_reg_values.ti_value     = (uint32_t)mac_ti;
            fol_pending_action = FOL_ACTION_SET_CLOCK_INC;
            PTP_LOG("PTP UNINIT->MATCHFREQ  scheduling TI=%u TISUBN=0x%08X\r\n",
                    (unsigned int)mac_ti, (unsigned int)calcSubInc_uint);

            /* Save calibrated values for reuse after any subsequent GM restart */
            calibratedTI_value     = (uint32_t)mac_ti;
            calibratedTISUBN_value = calcSubInc_uint;

            syncStatus = MATCHFREQ;
            ptpSynced  = 1;
            runs       = 0;
        }

    } else if (syncStatus == MATCHFREQ) {
        if (offset_abs > MATCHFREQ_RESET_THRESHOLD) {
            hardResync = 1;
        } else {
            PTP_LOG("[PTP] MATCHFREQ->HARDSYNC offset=%d\r\n", (int)offset);
            syncStatus = HARDSYNC;
            ptpSynced  = 1;   /* ensure PPS is re-enabled on next frame (fast-reset path) */
        }

    } else if (syncStatus >= HARDSYNC) {
        if (offset_abs > HARDSYNC_RESET_THRESHOLD) {
            syncStatus = UNINIT;
            for (uint32_t x = 0; x < FIR_FILER_SIZE_FINE; x++) {
                (void)firLowPassFilter(0, &offsetCoarseState);
                (void)firLowPassFilter(0, &offsetState);
            }
            for (uint32_t x = 0; x < FIR_FILER_SIZE; x++) {
                (void)firLowPassFilterF(1.0, &rateRatiolpfState);
            }
            runs = 0;

        } else if (offset_abs > HARDSYNC_THRESHOLD) {
            offset_abs = HARDSYNC_THRESHOLD;
            fol_reg_values.ta_value = ((neg & 1u) << 31) | (uint32_t)offset_abs;
            fol_pending_action = FOL_ACTION_ADJUST_OFFSET;
            syncStatus = HARDSYNC;

        } else if (offset_abs > HARDSYNC_COARSE_THRESHOLD) {
            for (uint32_t x = 0; x < FIR_FILER_SIZE_FINE; x++) {
                (void)firLowPassFilter(0, &offsetCoarseState);
                (void)firLowPassFilter(0, &offsetState);
                offsetCoarseState.filled = 0;
                offsetState.filled       = 0;
            }
            fol_reg_values.ta_value = ((neg & 1u) << 31) | (uint32_t)offset_abs;
            fol_pending_action = FOL_ACTION_ADJUST_OFFSET;
            syncStatus = HARDSYNC;

        } else if (offset_abs > HARDSYNC_FINE_THRESHOLD) {
            for (uint32_t x = 0; x < FIR_FILER_SIZE_FINE; x++) {
                (void)firLowPassFilter(0, &offsetState);
                offsetState.filled = 0;
            }
            offsetFIR = firLowPassFilter((int32_t)offset, &offsetCoarseState);
            neg = (offsetFIR < 0) ? 0u : 1u;
            int32_t write_val = (int32_t)offsetFIR;
            if (!neg) write_val = write_val * (-1);

            fol_reg_values.ta_value = ((neg & 1u) << 31) | (uint32_t)write_val;
            fol_pending_action = FOL_ACTION_ADJUST_OFFSET;
            syncStatus = COARSE;

        } else {
            offsetFIR = firLowPassFilter((int32_t)offset, &offsetState);
            neg = (offsetFIR < 0) ? 0u : 1u;
            int32_t write_val = (int32_t)offsetFIR;
            if (!neg) write_val = write_val * (-1);

            fol_reg_values.ta_value = ((neg & 1u) << 31) | (uint32_t)write_val;
            fol_pending_action = FOL_ACTION_ADJUST_OFFSET;
            syncStatus = FINE;
        }
    }

    /* Log state transitions once per change so the test script can detect them */
    if (syncStatus != prevSyncStatus) {
        if (syncStatus == COARSE) {
            PTP_LOG("\r\nPTP COARSE  offset=%d\r\n", (int)offset);
        } else if (syncStatus == FINE) {
            PTP_LOG("\r\nPTP FINE    offset=%d\r\n", (int)offset);
        }
        prevSyncStatus = syncStatus;
    }

    /* Verbose mode: overwrite same terminal line on every sync (\r, no \r\n) */
    if (ptp_fol_verbose) {
        uint32_t t1_sec = (uint32_t)(t1 / 1000000000ULL);
        uint32_t t1_ns  = (uint32_t)(t1 % 1000000000ULL);
        uint32_t t1_h   = t1_sec / 3600u;
        uint32_t t1_m   = (t1_sec % 3600u) / 60u;
        uint32_t t1_s   = t1_sec % 60u;
        uint32_t t2_sec = (uint32_t)(t2 / 1000000000ULL);
        uint32_t t2_ns  = (uint32_t)(t2 % 1000000000ULL);
        uint32_t t2_h   = t2_sec / 3600u;
        uint32_t t2_m   = (t2_sec % 3600u) / 60u;
        uint32_t t2_s   = t2_sec % 60u;
        static const char *stateNames[] = {"UNINIT   ", "MATCHFREQ", "HARDSYNC ", "COARSE   ", "FINE     "};
        uint8_t si = (syncStatus < 5u) ? syncStatus : 4u;
        PTP_LOG("[FOL] %s  t1=%02lu:%02lu:%02lu.%09lu  t2=%02lu:%02lu:%02lu.%09lu  off=%+10d ns  delay=%lld ns\r\n",
                stateNames[si],
                (unsigned long)t1_h, (unsigned long)t1_m, (unsigned long)t1_s, (unsigned long)t1_ns,
                (unsigned long)t2_h, (unsigned long)t2_m, (unsigned long)t2_s, (unsigned long)t2_ns,
                (int)offset, (long long)fol_mean_path_delay);
    }

    /* Initiate Delay_Req / Delay_Resp exchange for path delay measurement.
     * Only send when a valid MAC is available and the clock is at least
     * in MATCHFREQ state so the t3 software-clock reading is meaningful. */
    if (fol_src_mac[0] != 0u || fol_src_mac[1] != 0u || fol_src_mac[2] != 0u ||
        fol_src_mac[3] != 0u || fol_src_mac[4] != 0u || fol_src_mac[5] != 0u) {
        if (syncStatus >= MATCHFREQ) {
            sendDelayReq(t1, t2);
        }
    }
}

void PTP_FOL_SetVerbose(bool verbose) {
    ptp_fol_verbose = verbose;
}

void PTP_FOL_SetTrace(bool enable) {
    ptp_trace_enabled = enable;
    PTP_LOG("[TRACE] PTP trace %s\r\n", enable ? "enabled" : "disabled");
}

/* -------------------------------------------------------------------------
 * PTP frame dispatcher  (called internally & exposed via header)
 * ---------------------------------------------------------------------- */

void handlePtp(uint8_t *pData, uint32_t size, uint32_t sec, uint32_t nsec)
{
    (void)size;
    ptpHeader_t *ptpPkt = (ptpHeader_t *)(pData + sizeof(ethHeader_t));
    uint8_t messageType = ptpPkt->tsmt & 0x0Fu;

    if (messageType == (uint8_t)MSG_FOLLOW_UP) {
        processFollowUp((followUpMsg_t *)ptpPkt);
    } else if (messageType == (uint8_t)MSG_SYNC) {
        processSync((syncMsg_t *)ptpPkt);
        if (syncReceived) {
            TS_SYNC.receipt_prev        = TS_SYNC.receipt;
            TS_SYNC.receipt.secondsLsb  = sec;
            TS_SYNC.receipt.nanoseconds = nsec;
        }
    } else if (messageType == (uint8_t)MSG_DELAY_RESP) {
        uint64_t rxTs = ((uint64_t)sec << 32u) | (uint64_t)nsec;
        processDelayResp((delayRespMsg_t *)ptpPkt, rxTs);
    } else {
        PTP_LOG("[FOL] Unknown msgType=%u, ignored\r\n", (unsigned)messageType);
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void PTP_FOL_Init(void)
{
    /* Reset TTSCA state (including CONFIG0 one-shot flag so FTSE is re-applied
     * at the start of each FOL mode activation). */
    fol_config0_set        = false;
    fol_ttsca_state        = FOL_TTSCA_IDLE;
    fol_ttsca_op_done      = false;
    fol_ttsca_retry        = 0u;
    fol_t3_hw_valid        = false;
    fol_t3_hw_ns           = 0u;
    fol_tx_busy            = false;
    fol_delay_resp_deferred = false;

    /* Configure TX-Match base registers for Delay_Req TX timestamp capture (t3).
     * TXMPATL is intentionally NOT written here — it is written callback-confirmed
     * (FOL_TTSCA_WRITE_TXMPATL) before every Delay_Req TX to guarantee 0xF701 is
     * in effect (driver _InitMemMap sets 0xF700 for Sync; fire-and-forget is unreliable).
     * TXMCTL is left disarmed here; armed to 0x0002 per-Delay_Req in state machine. */
    DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMCTL,  0x00000000u,         true, NULL, NULL);
    DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMLOC,  30u,                 true, NULL, NULL);
    DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMPATH, PTP_ETHER_TYPE_H,    true, NULL, NULL);
    DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMMSKH, 0x00000000u,         true, NULL, NULL);
    DRV_LAN865X_WriteRegister(0u, FOL_OA_TXMMSKL, 0x00000000u,         true, NULL, NULL);

    /* Set up PPS output (stopped; PPSCTL=0x02 = pulse-width / period preset) */
    DRV_LAN865X_WriteRegister(0u, PPSCTL,   0x00000002u,       true, NULL, NULL);
    DRV_LAN865X_WriteRegister(0u, SEVINTEN, SEVINTEN_PPSDONE_Msk, true, NULL, NULL);

    memset(&TS_SYNC, 0, sizeof(TS_SYNC));

    rateRatiolpfState.buffer     = &rateRatioValue[0];
    rateRatiolpfState.filterSize = sizeof(rateRatioValue) / sizeof(rateRatioValue[0]);

    offsetState.buffer           = &offsetValue[0];
    offsetState.filterSize       = sizeof(offsetValue) / sizeof(offsetValue[0]);

    offsetCoarseState.buffer     = &offsetCoarseValue[0];
    offsetCoarseState.filterSize = sizeof(offsetCoarseValue) / sizeof(offsetCoarseValue[0]);

    PTP_LOG("PTP_FOL_Init: HW init done, PTP mode=%d (not activated)\r\n", (int)ptpMode);
}

ptpMode_t PTP_FOL_GetMode(void)
{
    return ptpMode;
}

void PTP_FOL_SetMode(ptpMode_t mode)
{
    ptpMode = mode;
    if (mode == PTP_SLAVE) {
        resetSlaveNode();
    }
}

void PTP_FOL_GetOffset(int64_t *pOffset, uint64_t *pOffsetAbs)
{
    if (pOffset)    *pOffset    = offset;
    if (pOffsetAbs) *pOffsetAbs = offset_abs;
}

void PTP_FOL_GetCalibratedClockInc(uint32_t *pTI, uint32_t *pTISUBN)
{
    if (pTI)     *pTI     = calibratedTI_value;
    if (pTISUBN) *pTISUBN = calibratedTISUBN_value;
}

void PTP_FOL_Reset(void)
{
    resetSlaveNode();
}

void PTP_FOL_SetMac(const uint8_t *pMac)
{
    if (pMac != NULL) {
        memcpy(fol_src_mac, pMac, 6u);
    }
}

int64_t PTP_FOL_GetMeanPathDelay(void)
{
    return fol_delay_valid ? fol_mean_path_delay : 0;
}

void PTP_FOL_OnFrame(const uint8_t *pData, uint16_t len, uint64_t rxTimestamp)
{
    if (ptpMode != PTP_SLAVE) {
        PTP_LOG("[FOL] OnFrame ignored: mode=%d (not SLAVE)\r\n", (int)ptpMode);
        return;
    }
    uint32_t sec  = (uint32_t)((rxTimestamp >> 32u) & 0xFFFFFFFFu);
    uint32_t nsec = (uint32_t)( rxTimestamp         & 0xFFFFFFFFu);
    handlePtp((uint8_t *)pData, (uint32_t)len, sec, nsec);
}
