/*
 * ptp_drv_ext.c — PTP driver extension for LAN865x
 *
 * See ptp_drv_ext.h for the wiring overview.  This translation unit owns
 * everything that would otherwise live in drv_lan865x_api.c just for the
 * sake of PTP, so the upstream driver source stays close to the Harmony
 * trunk.
 */

#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#include "definitions.h"
#include "driver/lan865x/drv_lan865x.h"
#include "driver/lan865x/src/dynamic/tc6/tc6.h"

#include "ptp_drv_ext.h"
#include "ptp_ts_ipc.h"

/* ---------------------------------------------------------------------------
 * RX timestamp / raw-frame IPC slots
 * ---------------------------------------------------------------------------
 * Filled by DRV_LAN865X_OnPtpFrame_Hook() below (called from the driver's
 * TC6_CB_OnRxEthernetPacket via a __weak override).
 * Externed in ptp_ts_ipc.h. */
volatile PTP_RxTimestampEntry_t g_ptp_rx_ts  = {0u, false};
volatile PTP_RxFrameEntry_t     g_ptp_raw_rx = {{0u}, 0u, 0u, 0u, false};

/* ---------------------------------------------------------------------------
 * EIC EXTINT-14 — change-notification interrupt for the LAN865x nIRQ pin
 * ---------------------------------------------------------------------------
 * The handler latches a TC0 tick at the earliest possible moment so that
 * sysTickAtRx is accurate to the nIRQ assertion rather than to the later
 * SPI-callback moment (improvement from ~200 µs error to < 5 µs).
 *
 * EIC_EXTINT_14_Handler is a weak symbol in the Harmony startup file;
 * defining it here causes the linker to bind it to this implementation. */
static volatile uint64_t s_nirq_tick = 0u;

void EIC_EXTINT_14_Handler(void)
{
    s_nirq_tick = SYS_TIME_Counter64Get();
    EIC_REGS->EIC_INTFLAG = (1u << 14u);   /* W1C — clear EXTINT14 flag */
}

static void _InitNIrqEIC(void)
{
    /* Enable EIC APB-A clock */
    MCLK_REGS->MCLK_APBAMASK |= MCLK_APBAMASK_EIC_Msk;

    /* Use OSCULP32K (CKSEL=1) — no GCLK peripheral clock needed */
    EIC_REGS->EIC_CTRLA = EIC_CTRLA_CKSEL_Msk;

    /* CONFIG[1] covers EXTINT8..15.  EXTINT14 sits at bits 27:24.
     * SENSE=0x2 (FALL) — nIRQ is active-low; trigger on falling edge. */
    EIC_REGS->EIC_CONFIG[1] = (EIC_REGS->EIC_CONFIG[1] & ~(0xFu << 24u))
                             | (0x2u << 24u);

    /* Enable EXTINT14 interrupt */
    EIC_REGS->EIC_INTENSET = (1u << 14u);

    /* Enable EIC and wait for sync */
    EIC_REGS->EIC_CTRLA |= EIC_CTRLA_ENABLE_Msk;
    while ((EIC_REGS->EIC_SYNCBUSY & EIC_SYNCBUSY_ENABLE_Msk) != 0u) {}

    /* Unmask in NVIC */
    NVIC_EnableIRQ(EIC_EXTINT_14_IRQn);
}

void PTP_DRV_EXT_Init(void)
{
    _InitNIrqEIC();
}

/* ---------------------------------------------------------------------------
 * TX Timestamp Capture state
 * ---------------------------------------------------------------------------
 * STATUS0 bits 8-10 (TTSCAA / TTSCAB / TTSCAC) and the EXTINT-14 tick that
 * coincided with the OnStatus0 callback.  Saved here by the OnStatus0 hook
 * before the driver's W1C clear, so the GM state machine can read them
 * without racing the driver. */
static volatile uint32_t drvTsCaptureStatus0  [DRV_LAN865X_INSTANCES_NUMBER];
static volatile uint64_t drvTsCaptureNirqTick [DRV_LAN865X_INSTANCES_NUMBER];

uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx)
{
    if (idx >= DRV_LAN865X_INSTANCES_NUMBER) { return 0u; }
    uint32_t val = drvTsCaptureStatus0[idx];
    drvTsCaptureStatus0[idx] = 0u;
    return val;
}

uint64_t DRV_LAN865X_GetTsCaptureNirqTick(uint8_t idx)
{
    if (idx >= DRV_LAN865X_INSTANCES_NUMBER) { return 0u; }
    return drvTsCaptureNirqTick[idx];
}

/* ---------------------------------------------------------------------------
 * Driver weak-callback overrides
 * ---------------------------------------------------------------------------
 * The driver declares these hooks as __attribute__((weak)) no-ops; the
 * strong definitions below take effect because this TU is linked into the
 * application image. */

/* Called from drv_lan865x_api.c::_OnStatus0() BEFORE the W1C clear of
 * STATUS0.  Saves the TTSCAA/B/C bits and the latest nIRQ tick so the GM
 * state machine can retrieve them via the public getters above. */
void DRV_LAN865X_OnStatus0_Hook(uint8_t idx, uint32_t status0)
{
    if (idx >= DRV_LAN865X_INSTANCES_NUMBER) { return; }
    if (0u != (status0 & 0x0700u)) {
        drvTsCaptureStatus0[idx]  |= (status0 & 0x0700u);
        drvTsCaptureNirqTick[idx]  = s_nirq_tick;
    }
}

/* Called from drv_lan865x_api.c::TC6_CB_OnRxEthernetPacket() once the SPI
 * payload is in segLoad and we know the frame was received successfully.
 * If it's a PTP frame (EtherType 0x88F7) we copy it for APP_Tasks to
 * process via ptp_rx.c.  All other frames are ignored here and continue
 * up the normal TCP/IP stack path. */
void DRV_LAN865X_OnPtpFrame_Hook(uint8_t idx, const uint8_t *frame,
                                  uint16_t len, const uint64_t *rxTimestamp)
{
    (void)idx;

    /* Track the latest RX timestamp for back-compat consumers. */
    if (rxTimestamp != NULL) {
        g_ptp_rx_ts.rxTimestamp = *rxTimestamp;
        g_ptp_rx_ts.valid       = true;
    }

    /* PTP EtherType 0x88F7 ? */
    if (frame == NULL || len < 14u) { return; }
    if (frame[12] != 0x88u || frame[13] != 0xF7u) { return; }

    uint16_t copyLen = (len > (uint16_t)PTP_RAW_BUF_SIZE) ?
                       (uint16_t)PTP_RAW_BUF_SIZE : len;
    (void)memcpy((uint8_t *)g_ptp_raw_rx.data, frame, copyLen);
    g_ptp_raw_rx.length      = copyLen;
    g_ptp_raw_rx.rxTimestamp = (rxTimestamp != NULL) ? *rxTimestamp : 0u;

    /* Only update sysTickAtRx for frames that carry a hardware RX
     * timestamp (= SYNC).  FollowUp frames have rxTimestamp==NULL and
     * arrive ~1-4 ms later — overwriting sysTickAtRx with the FollowUp
     * tick would create a systematic offset in PTP_CLOCK_GetTime_ns()
     * because the anchor pair (wallclock=t2_SYNC, tick=tick_FollowUp)
     * would be inconsistent.
     *
     * The tick comes from the EIC ISR at nIRQ assertion, not from
     * SYS_TIME_Counter64Get() at this callback.  That cuts the
     * sysTickAtRx error from ~200 µs to < 5 µs.  The remaining ~10 ms
     * SFD-to-nIRQ delay is compensated by PTP_FOL_ANCHOR_OFFSET_NS in
     * ptp_fol_task.c. */
    if (rxTimestamp != NULL) {
        g_ptp_raw_rx.sysTickAtRx = (s_nirq_tick != 0u)
                                 ? s_nirq_tick
                                 : SYS_TIME_Counter64Get();
    }
    g_ptp_raw_rx.pending = true;
}

/* ---------------------------------------------------------------------------
 * Public-API wrappers — call into TC6 via the driver-exposed accessor
 * --------------------------------------------------------------------------- */

bool DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf, uint16_t len,
                                  uint8_t tsc, DRV_LAN865X_RawTxCallback_t cb,
                                  void *pTag)
{
    TC6_t *tc = (TC6_t *)DRV_LAN865X_GetTc6Inst(idx);
    if (tc == NULL) { return false; }
    return TC6_SendRawEthernetPacket(tc, pBuf, len, tsc,
                                     (TC6_RawTxCallback_t)(void *)cb, pTag);
}

bool DRV_LAN865X_IsReady(uint8_t idx)
{
    return DRV_LAN865X_GetTc6Inst(idx) != NULL;
}
