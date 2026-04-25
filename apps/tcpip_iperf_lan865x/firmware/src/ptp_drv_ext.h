/*
 * ptp_drv_ext.h — PTP driver extension for LAN865x
 *
 * Bundles all hardware-timestamping plumbing the PTP stack needs from the
 * LAN865x driver, so the upstream driver itself stays close to the Harmony
 * trunk.  The driver carries only a handful of weak hook calls and one
 * accessor; everything else (EIC EXTINT-14 ISR, TTSCAA save-before-W1C,
 * raw-Ethernet TX wrapper, RX PTP-frame sniff) lives here.
 *
 * Wiring:
 *   1. Call PTP_DRV_EXT_Init() once from APP_Initialize().
 *   2. Include this header from any TU that calls DRV_LAN865X_SendRawEthFrame
 *      / IsReady / GetAndClearTsCapture / GetTsCaptureNirqTick.
 *   3. The driver's _OnStatus0 and TC6_CB_OnRxEthernetPacket call the
 *      OnStatus0_Hook / OnPtpFrame_Hook here automatically (weak override).
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

/* Atomic read-and-clear of the saved TX Timestamp Capture Available bits
 * (STATUS0 bits 8-10).  These are written by the driver's status interrupt
 * handler before the W1C clear (via the OnStatus0 hook), so the GM state
 * machine cannot lose them to the race against the driver's own clear.
 * Returns the bit mask, or 0 if nothing has been captured since the last
 * call. */
uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx);

/* The TC0 tick (SYS_TIME_Counter64) that was latched by the EXTINT-14 ISR
 * at the moment the LAN865x asserted nIRQ for the most recent timestamp
 * capture.  Pair with GetAndClearTsCapture() in quick succession.
 * Persists across calls (no clear-on-read). */
uint64_t DRV_LAN865X_GetTsCaptureNirqTick(uint8_t idx);

#ifdef __cplusplus
}
#endif

#endif /* PTP_DRV_EXT_H */
