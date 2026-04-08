/*
 * ptp_ts_ipc.h — PTP RX-Timestamp Inter-Process Communication
 *
 * The LAN865x TC6 driver extracts the hardware RX-timestamp from the SPI footer
 * and stores it here via TC6_CB_OnRxEthernetPacket() in drv_lan865x_api.c.
 * The application reads it in pktEth0Handler() (app.c) when a PTP frame arrives.
 *
 * Timestamp format:  Bit[63:32] = seconds low 32-bit
 *                    Bit[31: 0] = nanoseconds
 */

#ifndef PTP_TS_IPC_H
#define PTP_TS_IPC_H

#include <stdint.h>
#include <stdbool.h>

/* RX timestamp IPC (single entry, latest frame) */
typedef struct { uint64_t rxTimestamp; bool valid; } PTP_RxTimestampEntry_t;

/* Defined in drv_lan865x_api.c, written by the TC6 callback, read by pktEth0Handler */
extern volatile PTP_RxTimestampEntry_t g_ptp_rx_ts;

/* -------------------------------------------------------------------------
 * Direct PTP frame capture at driver level.
 * TC6_CB_OnRxEthernetPacket() fills this whenever a frame with
 * EtherType 0x88F7 arrives, BEFORE passing the macPkt to the TCPIP stack.
 * APP_Tasks() (APP_STATE_IDLE) reads and clears it.
 * This path works regardless of TCPIP_STACK_PacketHandlerRegister success.
 * ---------------------------------------------------------------------- */
#define PTP_RAW_BUF_SIZE  128u

typedef struct {
    uint8_t          data[PTP_RAW_BUF_SIZE];
    uint16_t         length;
    uint64_t         rxTimestamp;   /* LAN865x RTSA hardware timestamp in ns   */
    uint64_t         sysTickAtRx;   /* SYS_TIME_Counter64Get() at same moment  */
    volatile bool    pending;
} PTP_RxFrameEntry_t;

/* Defined in drv_lan865x_api.c */
extern volatile PTP_RxFrameEntry_t g_ptp_raw_rx;

#endif /* PTP_TS_IPC_H */
