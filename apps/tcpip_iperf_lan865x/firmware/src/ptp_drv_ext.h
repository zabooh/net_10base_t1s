/*
 * ptp_drv_ext.h — PTP driver extension for LAN865x
 *
 * Bundles all hardware-timestamping plumbing the PTP stack needs from the
 * LAN865x driver, so the upstream driver itself stays close to the Harmony
 * trunk.  The driver carries only TWO irreducible patches:
 *   - the OnPtpFrame_Hook decl + 1-line hook call (RX 64-bit hardware
 *     timestamp delivery)
 *   - the GetTc6Inst() accessor (private TC6_t* needed for TX-TSC arming)
 * Everything else (EIC EXTINT-14 ISR, raw-Ethernet TX wrapper, RX PTP-frame
 * sniff, PTP-specific register init state machine) lives here.
 *
 * Wiring:
 *   1. Call PTP_DRV_EXT_Init() once from APP_Initialize().
 *   2. Call PTP_DRV_EXT_Tasks(0u) periodically from APP_Tasks(); it is a
 *      no-op until DRV_LAN865X_IsReady() returns true, then runs the
 *      register-init state machine to one completion.
 *   3. Include this header from any TU that calls DRV_LAN865X_SendRawEthFrame
 *      / IsReady / GetAndClearTsCapture / GetTsCaptureNirqTick.
 *   4. The driver's TC6_CB_OnRxEthernetPacket calls OnPtpFrame_Hook here
 *      automatically (weak override).
 */

#ifndef PTP_DRV_EXT_H
#define PTP_DRV_EXT_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* TX completion callback for raw Ethernet frames sent via
 * DRV_LAN865X_SendRawEthFrame().  Signature is compatible with
 * TC6_RawTxCallback_t (void* used for TC6_t* to keep tc6.h out of the
 * public API). */
typedef void (*DRV_LAN865X_RawTxCallback_t)(void *pInst, const uint8_t *pTx,
                                             uint16_t len, void *pTag,
                                             void *pGlobalTag);

/* Initialise the PTP driver extension.  Sets up the EIC EXTINT-14
 * change-notification interrupt for the LAN865x nIRQ pin so that the
 * arrival moment can be latched at ISR-precision (~5 µs jitter) instead
 * of read at task level. */
void PTP_DRV_EXT_Init(void);

/* PTP register-init state machine.  Drives the post-IsReady writes that
 * configure CONFIG0/FTSE-FTSS, IMASK0, DEEP_SLEEP_CTRL_1, the TX-Match
 * filter for Sync detection, PADCTRL and PPSCTL.  Must be called
 * periodically from APP_Tasks() (e.g. once per task tick).  Returns
 * automatically once all writes have completed. */
void PTP_DRV_EXT_Tasks(uint8_t idx);

/* True when PTP_DRV_EXT_Tasks has finished writing all PTP-specific
 * registers.  Use to gate dependent state machines (e.g. ptp_fol_task,
 * ptp_gm_task) so they don't issue WriteRegister calls before this
 * sequence completes. */
bool PTP_DRV_EXT_RegisterInitDone(void);

/* Send a raw Ethernet frame via the LAN865x TC6 layer with a
 * caller-selected Transmit-Timestamp-Capture flag.
 *   tsc=0x01 → arm Capture A (use for PTP Sync)
 *   tsc=0x00 → no capture       (use for PTP FollowUp / Delay_Resp)
 * Returns true if the frame was accepted by TC6, false otherwise. */
bool DRV_LAN865X_SendRawEthFrame(uint8_t idx, const uint8_t *pBuf, uint16_t len,
                                  uint8_t tsc, DRV_LAN865X_RawTxCallback_t cb,
                                  void *pTag);

/* True when the LAN865x driver instance is fully initialised and ready
 * to accept register access and frame TX/RX requests. */
bool DRV_LAN865X_IsReady(uint8_t idx);

/* Always returns 0u in the driverless build — kept for back-compat with
 * gm_task callers, which transparently fall back to a direct STATUS0 SPI
 * read when this returns 0.  (Was previously populated by the OnStatus0
 * driver hook before W1C-clear; the hook has been removed and gm_task's
 * SPI fallback is now the *only* path.) */
uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx);

/* The TC0 tick (SYS_TIME_Counter64) that was latched by the EXTINT-14 ISR
 * at the moment the LAN865x asserted nIRQ.  Use as the anchor tick for
 * PTP_CLOCK_Update().  Persists across calls (no clear-on-read). */
uint64_t DRV_LAN865X_GetTsCaptureNirqTick(uint8_t idx);

#ifdef __cplusplus
}
#endif

#endif /* PTP_DRV_EXT_H */
