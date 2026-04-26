/*
 * ptp_drv_ext.c — PTP driver extension for LAN865x
 *
 * See ptp_drv_ext.h for the wiring overview.  This translation unit owns
 * everything that would otherwise live in drv_lan865x_api.c just for the
 * sake of PTP, so the upstream driver source stays close to the Harmony
 * trunk.
 *
 * What lives here (was previously inline-patched into drv_lan865x_api.c):
 *
 *   - Register-init state machine PTP_DRV_EXT_RegisterInit(): runs after
 *     DRV_LAN865X_IsReady() and overwrites the upstream MEMMAP defaults
 *     for IMASK0, DEEP_SLEEP_CTRL_1, TX-Match (TXMPATH/L, TXMMSKH/L,
 *     TXMLOC, TXMCTL), CONFIG0 (FTSE+FTSS) and adds PADCTRL+PPSCTL writes
 *     that the upstream driver does not perform.  See PTP_DRV_EXT_Tasks.
 *
 *   - EIC EXTINT-14 ISR for nIRQ pin: latches a TC0 tick at ISR-precision.
 *     Used by both the GM (TX timestamp anchor) and the slave (RX
 *     timestamp anchor).
 *
 *   - Strong override of DRV_LAN865X_OnPtpFrame_Hook (the *one* PTP hook
 *     that genuinely cannot be replaced by a Harmony-public API: the
 *     64-bit RX hardware timestamp is delivered ONLY via the rxTimestamp
 *     parameter of TC6_CB_OnRxEthernetPacket).
 *
 *   - Wrappers DRV_LAN865X_SendRawEthFrame() and DRV_LAN865X_IsReady()
 *     that hide the TC6_t accessor behind a clean header.
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
 * The OnStatus0 driver hook (which previously saved STATUS0 bits 8-10
 * before the W1C clear) is GONE — it has been replaced by app-side polling
 * in ptp_gm_task.c::GM_STATE_READ_STATUS0/WAIT_STATUS0, which reads
 * STATUS0 directly via DRV_LAN865X_ReadRegister().
 *
 * GetAndClearTsCapture() therefore always returns 0u — it remains in the
 * public API as a "callback-saved" optimization slot that is never
 * populated; gm_task transparently falls back to the SPI read.
 *
 * GetTsCaptureNirqTick() returns the latest EIC EXTINT-14 latched tick,
 * which is what the GM uses as the anchor for PTP_CLOCK_Update(). */
uint32_t DRV_LAN865X_GetAndClearTsCapture(uint8_t idx)
{
    (void)idx;
    return 0u;   /* OnStatus0_Hook was removed; gm_task uses SPI fallback */
}

uint64_t DRV_LAN865X_GetTsCaptureNirqTick(uint8_t idx)
{
    (void)idx;
    return s_nirq_tick;
}

/* ---------------------------------------------------------------------------
 * Driver weak-callback override — RX hardware timestamp capture
 * ---------------------------------------------------------------------------
 * The driver declares this hook as __attribute__((weak)) no-op; the strong
 * definition below takes effect because this TU is linked into the app.
 *
 * Why this hook is irreducible (cannot be replaced by Harmony API):
 *   The 64-bit chip RX timestamp arrives inline in the TC6 SPI footer
 *   (RTSA bit + 8-byte timestamp).  TC6 hands it to the driver via the
 *   rxTimestamp parameter of TC6_CB_OnRxEthernetPacket, which then
 *   discards it (the upstream driver does not propagate it onto the
 *   TCPIP_MAC_PACKET).  By the time TCPIP_STACK_PacketHandlerRegister's
 *   handler is called, the timestamp is no longer accessible from any
 *   register.  The 14-line hook in drv_lan865x_api.c is the only way to
 *   capture it before it is lost. */
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

/* ===========================================================================
 *  PTP_DRV_EXT_RegisterInit — post-IsReady register patch state machine
 * ===========================================================================
 * Replaces the inline driver patches that previously lived in TC6_MEMMAP[]
 * (init-state CONFIG / SETREGS) and in cases 46/47 of _InitMemMap.  Runs
 * after DRV_LAN865X_IsReady(idx) returns true.  Each step issues one
 * fire-and-forget DRV_LAN865X_WriteRegister / ReadModifyWriteRegister and
 * advances the state on the *next* PTP_DRV_EXT_Tasks() call after the
 * write callback fires (op_done).
 *
 * The driver MEMMAP table writes upstream defaults for these registers
 * first (IMASK0=0x100, DEEP_SLEEP_CTRL_1=0x80, TXM*=0xFF/0xFFFF/0/0/0/2,
 * CONFIG0=0x9026 with no FTSE/FTSS).  These app-side writes are the
 * last-write-wins overrides that bring the chip into the PTP-required
 * configuration.
 *
 * Caveat: PADCTRL (0x000A0088) and PPSCTL (0x000A0239) live in the
 * Extended-Block address space (0x000Axxxx).  Access requires that the
 * driver has reached SYS_STATUS_READY (which it has, since IsReady is
 * gated by that).  The TC6 layer routes these via TC6_ReadModifyWriteRegister
 * → TC6 control transactions; the driver's DRV_LAN865X_*Register wrappers
 * forward through pDrvInst->drvTc6, which is what GetTc6Inst returns.
 */

typedef enum {
    REG_INIT_IDLE = 0,
    REG_INIT_IMASK0,            /* 0x0000000C → 0x00000000 (unmask TTSCAA) */
    REG_INIT_IMASK0_WAIT,
    REG_INIT_DEEP_SLEEP,        /* 0x00040081 → 0x000000E0 */
    REG_INIT_DEEP_SLEEP_WAIT,
    REG_INIT_TXMPATH,           /* 0x00040041 → 0x00000088 */
    REG_INIT_TXMPATH_WAIT,
    REG_INIT_TXMPATL,           /* 0x00040042 → 0x0000F710 (Sync seqid) */
    REG_INIT_TXMPATL_WAIT,
    REG_INIT_TXMMSKH,           /* 0x00040043 → 0x00000000 (was upstream 0xFF) */
    REG_INIT_TXMMSKH_WAIT,
    REG_INIT_TXMMSKL,           /* 0x00040044 → 0x00000000 (was upstream 0xFFFF) */
    REG_INIT_TXMMSKL_WAIT,
    REG_INIT_TXMLOC,            /* 0x00040045 → 0x0000001E (was upstream 0x00) */
    REG_INIT_TXMLOC_WAIT,
    REG_INIT_TXMCTL,            /* 0x00040040 → 0x00000000 (was upstream 0x02) */
    REG_INIT_TXMCTL_WAIT,
    REG_INIT_CONFIG0,           /* 0x00000004 RMW: |=0xC0 (FTSE+FTSS) */
    REG_INIT_CONFIG0_WAIT,
    REG_INIT_PADCTRL,           /* 0x000A0088 RMW: value=0x100, mask=0x300 */
    REG_INIT_PADCTRL_WAIT,
    REG_INIT_PPSCTL,            /* 0x000A0239 → 0x0000007D */
    REG_INIT_PPSCTL_WAIT,
    REG_INIT_DONE
} reg_init_state_t;

static reg_init_state_t s_reg_state = REG_INIT_IDLE;
static volatile bool    s_reg_op_done = false;
static bool             s_reg_init_started = false;

static void _RegInitCb(void *r1, bool ok, uint32_t addr, uint32_t val, void *tag, void *r2)
{
    (void)r1; (void)ok; (void)addr; (void)val; (void)tag; (void)r2;
    s_reg_op_done = true;
}

/* Issue one register write/RMW; advance the state on success.  Returns
 * true if the call was issued (state machine should move to the WAIT
 * sub-state). */
static bool _RegInitWrite(uint8_t idx, uint32_t addr, uint32_t value)
{
    s_reg_op_done = false;
    return (TCPIP_MAC_RES_OK ==
        DRV_LAN865X_WriteRegister(idx, addr, value, true, _RegInitCb, NULL));
}

static bool _RegInitRMW(uint8_t idx, uint32_t addr, uint32_t value, uint32_t mask)
{
    s_reg_op_done = false;
    return (TCPIP_MAC_RES_OK ==
        DRV_LAN865X_ReadModifyWriteRegister(idx, addr, value, mask, true,
                                             _RegInitCb, NULL));
}

bool PTP_DRV_EXT_RegisterInitDone(void)
{
    return (s_reg_state == REG_INIT_DONE);
}

void PTP_DRV_EXT_Tasks(uint8_t idx)
{
    /* One-shot start: don't kick the state machine until the driver has
     * reached SYS_STATUS_READY.  After that, advance through the steps. */
    if (!s_reg_init_started) {
        if (!DRV_LAN865X_IsReady(idx)) {
            return;
        }
        s_reg_init_started = true;
        s_reg_state = REG_INIT_IMASK0;
    }

    switch (s_reg_state) {
        case REG_INIT_IMASK0:
            if (_RegInitWrite(idx, 0x0000000Cu, 0x00000000u)) {
                s_reg_state = REG_INIT_IMASK0_WAIT;
            }
            break;
        case REG_INIT_IMASK0_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_DEEP_SLEEP; }
            break;

        case REG_INIT_DEEP_SLEEP:
            if (_RegInitWrite(idx, 0x00040081u, 0x000000E0u)) {
                s_reg_state = REG_INIT_DEEP_SLEEP_WAIT;
            }
            break;
        case REG_INIT_DEEP_SLEEP_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMPATH; }
            break;

        case REG_INIT_TXMPATH:
            if (_RegInitWrite(idx, 0x00040041u, 0x00000088u)) {
                s_reg_state = REG_INIT_TXMPATH_WAIT;
            }
            break;
        case REG_INIT_TXMPATH_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMPATL; }
            break;

        case REG_INIT_TXMPATL:
            if (_RegInitWrite(idx, 0x00040042u, 0x0000F710u)) {
                s_reg_state = REG_INIT_TXMPATL_WAIT;
            }
            break;
        case REG_INIT_TXMPATL_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMMSKH; }
            break;

        case REG_INIT_TXMMSKH:
            if (_RegInitWrite(idx, 0x00040043u, 0x00000000u)) {
                s_reg_state = REG_INIT_TXMMSKH_WAIT;
            }
            break;
        case REG_INIT_TXMMSKH_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMMSKL; }
            break;

        case REG_INIT_TXMMSKL:
            if (_RegInitWrite(idx, 0x00040044u, 0x00000000u)) {
                s_reg_state = REG_INIT_TXMMSKL_WAIT;
            }
            break;
        case REG_INIT_TXMMSKL_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMLOC; }
            break;

        case REG_INIT_TXMLOC:
            if (_RegInitWrite(idx, 0x00040045u, 0x0000001Eu)) {
                s_reg_state = REG_INIT_TXMLOC_WAIT;
            }
            break;
        case REG_INIT_TXMLOC_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_TXMCTL; }
            break;

        case REG_INIT_TXMCTL:
            if (_RegInitWrite(idx, 0x00040040u, 0x00000000u)) {
                s_reg_state = REG_INIT_TXMCTL_WAIT;
            }
            break;
        case REG_INIT_TXMCTL_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_CONFIG0; }
            break;

        case REG_INIT_CONFIG0:
            /* CONFIG0: |=0xC0 = FTSE (0x80) | FTSS (0x40)
             * FTSE = Frame Timestamp Enable (required for TTSCAA capture)
             * FTSS = 64-bit timestamps (TC6 driver expects 64-bit) */
            if (_RegInitRMW(idx, 0x00000004u, 0x000000C0u, 0x000000C0u)) {
                s_reg_state = REG_INIT_CONFIG0_WAIT;
            }
            break;
        case REG_INIT_CONFIG0_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_PADCTRL; }
            break;

        case REG_INIT_PADCTRL:
            /* PADCTRL: enables TX timestamp output. */
            if (_RegInitRMW(idx, 0x000A0088u, 0x00000100u, 0x00000300u)) {
                s_reg_state = REG_INIT_PADCTRL_WAIT;
            }
            break;
        case REG_INIT_PADCTRL_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_PPSCTL; }
            break;

        case REG_INIT_PPSCTL:
            /* PPSCTL = 0x7D (=125): enables PPS clock for TSU counter. */
            if (_RegInitWrite(idx, 0x000A0239u, 0x0000007Du)) {
                s_reg_state = REG_INIT_PPSCTL_WAIT;
            }
            break;
        case REG_INIT_PPSCTL_WAIT:
            if (s_reg_op_done) { s_reg_state = REG_INIT_DONE; }
            break;

        case REG_INIT_IDLE:
        case REG_INIT_DONE:
        default:
            break;
    }
}
