# Timer Considerations — PTP_CLOCK Software Timestamp Analysis

Status: **FAIL — systematic offset ~−3.3 ms still present after FollowUp-guard fix**

---

## 1. Symptom

Test `ptp_time_test.py` repeatedly shows:

| Run | Firmware build | mean diff |
|-----|---------------|-----------|
| ptp_time_test_20260408_214047.log | (old, pre-fix) | −3306 µs |
| ptp_time_test_20260408_214716.log | (old, pre-fix) | −3348 µs |
| ptp_time_test_20260408_215224.log | Apr 8 2026 21:50:11 (post-fix) | −3338 µs |
| ptp_time_test_20260408_215920.log | Apr 8 2026 21:50:11 (post-fix) | −3336 µs |

The last two runs confirm the boards are running the latest firmware (`215011.hex`) —
the `[APP] Build:` timestamp in the log matches exactly.
Despite the FollowUp-guard fix the offset is **unchanged**.

---

## 2. First Hypothesis (refuted): FollowUp overwrote sysTickAtRx

**Original diagnosis:**
`TC6_CB_OnRxEthernetPacket()` fired for both SYNC and FollowUp frames (both EtherType 0x88F7).
FollowUp arrives ~3.3 ms after SYNC.  If `sysTickAtRx` was overwritten by the FollowUp
callback, the anchor pair `(wallclock=t2_SYNC, tick=tick_FollowUp)` would be inconsistent
and produce a systematic −3.3 ms offset.

**Fix applied:** Gate `sysTickAtRx = SYS_TIME_Counter64Get()` behind `if (rxTimestamp != NULL)`.
Only SYNC frames carry a hardware RX timestamp (RTSA); FollowUp always has `rxTimestamp == NULL`.

**Result:** Fix is correct but **does not eliminate the offset**.
The −3.3 ms offset is therefore caused by something else.

---

## 3. PTP_CLOCK anchor chain analysis

### 3.1 FOL anchor path

```
TC6_CB_OnRxEthernetPacket() [ISR/task context]
  → g_ptp_raw_rx.rxTimestamp = *rxTimestamp   (RTSA hardware timestamp = t2)
  → g_ptp_raw_rx.sysTickAtRx = SYS_TIME_Counter64Get()  (gated by rxTimestamp != NULL)
  → g_ptp_raw_rx.pending = true

APP_Tasks() [main loop, later]
  → if (g_ptp_raw_rx.pending) → PTP_FOL_OnFrame(...)
      → processFollowUp(...)
          → t2 = tsToInternal(&TS_SYNC.receipt)  ← RTSA hardware timestamp
          → PTP_CLOCK_Update(t2, g_ptp_raw_rx.sysTickAtRx)
```

**Key observation:**  
`sysTickAtRx` is captured at SYNC arrival time (correct, quasi-atomic with RTSA).  
`PTP_CLOCK_Update()` is called later — in `processFollowUp()`, not `processSync()`.  
`TS_SYNC.receipt` is set by `processSync()` when the SYNC frame arrives.  
The FollowUp frame arrives ~3.3 ms after SYNC and **triggers** the `PTP_CLOCK_Update()` call.  
But `sysTickAtRx` still holds the SYNC arrival tick (not overwritten).  
→ The anchor `(t2_SYNC, tick_SYNC)` is therefore **correct on FOL**.

### 3.2 GM anchor path

```
GM_STATE_SEND_SYNC          → SYNC TX frame sent with tsc=1
  ...SPI round-trips...
GM_STATE_READ_TXMCTL        → polls TXPMDET
GM_STATE_WAIT_TXMCTL        → waits for read callback
GM_STATE_READ_STATUS0       → checks TTSCAA/B/C available
GM_STATE_WAIT_STATUS0       → waits (or uses pre-cached cbCapture)
GM_STATE_READ_TTSCA_H       → reads seconds register
GM_STATE_WAIT_TTSCA_H       → waits for read callback
GM_STATE_READ_TTSCA_L       → reads nanoseconds register
GM_STATE_WAIT_TTSCA_L       →
    gm_ts_nsec = gm_op_val
    wc_ns = gm_ts_sec * 1e9 + gm_ts_nsec   ← LAN865x TTSCAL hardware TX timestamp (t1)
    PTP_CLOCK_Update(wc_ns, SYS_TIME_Counter64Get())   ← tick captured HERE
```

**Problem identified:**  
On GM: `SYS_TIME_Counter64Get()` is called in `GM_STATE_WAIT_TTSCA_L` — **many ms after**
the SYNC frame was actually transmitted.  The TX timestamp `t1` is correct (TTSCAL hardware
latch), but the `sys_tick` anchor is the tick at the moment the TTSCA_L register read callback
fires — **not** at the moment SYNC was sent.

The state machine traverses these states at ~1 ms per `PTP_GM_Service()` call:
- SEND_SYNC → WAIT_SYNC_TX_DONE: wait for TX callback
- READ_TXMCTL → WAIT_TXMCTL: ~1 ms
- READ_STATUS0 → WAIT_STATUS0: ~1 ms (or immediate via cbCapture)
- READ_TTSCA_H → WAIT_TTSCA_H: ~1 ms
- READ_TTSCA_L → WAIT_TTSCA_L: ~1 ms

Total delay from SYNC TX to `SYS_TIME_Counter64Get()`: approximately **3–4 ms**.

This means the GM anchor pair is `(t1_exact_hw, tick_3ms_later)`.
When `PTP_CLOCK_GetTime_ns()` is called:
```
result = t1 + ticks_to_ns(now_tick - tick_3ms_later)
       = t1 + (elapsed - 3ms_shift)
       = actual_wallclock - ~3ms
```
→ GM reports time that is ~3 ms **behind** the true wallclock.  
→ FOL (anchored correctly at SYNC arrival) reports time ~correct.  
→ `diff = WC_fol - WC_gm = ~+3.3 ms` → but measurements show **−3.3 ms**.

**Wait — signs need re-checking:**  
`diff_ns = (WC_fol - WC_gm) - (t_send_fol - t_send_gm)`  
If GM clock reads 3.3 ms **too high** (anchor tick is 3 ms late → interpolation goes back 3 ms
from a later reference → gives a larger value), then `WC_gm > WC_fol_true` → diff negative.

Alternatively: the FOL anchor may also be delayed. Let me reconsider:
- On FOL: `sysTickAtRx` captured at SYNC arrival. `PTP_CLOCK_Update()` called ~3.3 ms later in
  FollowUp processing. **The anchor tick is correct (SYNC arrival time).**
- On GM: `sys_tick` captured 3+ ms after SYNC TX. **The anchor tick is too late.**

When GM calls `PTP_CLOCK_GetTime_ns()` at time T:
```
delta_tick = T_tick - tick_TTSCA_L_late
           = (T - t_TX - 3ms) ticks     ← 3 ms too small
delta_ns   = ticks_to_ns(delta_tick)    ← 3 ms too small
result     = t1_hw + delta_ns           ← 3 ms too small
```
→ GM time is ~3 ms **behind** real wallclock.

When FOL calls `PTP_CLOCK_GetTime_ns()` at time T:
```
delta_tick = T_tick - tick_SYNC_arrival  ← correct
result     = t2_hw + delta_ns            ← correct
```
→ `diff = WC_fol - WC_gm ≈ +3 ms` (positive).

But test shows **−3.3 ms** consistently. This suggests either:
- FOL anchor is also shifted by ~6.6 ms, or
- The sign convention in `single_measurement()` is `(WC_fol - WC_gm)` with corrected latency.

**Most likely root cause:** Both boards have the same delayed-tick problem because
`PTP_CLOCK_Update()` on both boards is called several ms after the anchor hardware event.

---

## 4. The actual root cause

`PTP_CLOCK_Update(wallclock_ns, sys_tick)` must receive a `sys_tick` that was captured
**atomically with** `wallclock_ns` (i.e., at the same physical moment the LAN865x hardware
latched the timestamp).

| Board | Anchor wallclock source | sys_tick source | Delay |
|-------|------------------------|-----------------|-------|
| FOL | RTSA (rx footer, TC6 decode) | `TC6_CB_OnRxEthernetPacket()` | **< 1 µs** ✅ |
| GM | TTSCAL (SPI register read) | `WAIT_TTSCA_L` callback | **~3–4 ms** ❌ |

The correct fix for GM is to call `SYS_TIME_Counter64Get()` at the moment SYNC is sent
(or at the moment the TTSCAH register read is issued), not 3 ms later after the TTSCA_L
read completes.

---

## 5. Unrealistic drift values

`Drift GM = +1 026 654 ppb (+1026 ppm)` is ~5000x larger than a typical MEMS crystal error.

Root cause: the drift IIR in `ptp_clock.c` compares `delta_mcu_ns` (TC0 ticks → ns) with
`delta_wc` (PTP wallclock difference). If the anchor_tick lags by 3 ms, each `Update()`
call sees:
```
delta_tick ≈ (125 ms - 3 ms) = 122 ms   (too small by ~3 ms)
delta_wc   ≈ 125 ms
inst_ppb   ≈ (122ms - 125ms) / 125ms * 1e9 = -24000000 ppb  ← ~24000 ppm offset
```
And after IIR convergence the drift settles to this wrong value rather than to the true
crystal error (~50–300 ppb range).

---

## 6. Required fix

### Option A: Capture tick at SYNC TX time (GM)

In `ptp_gm_task.c`, capture `SYS_TIME_Counter64Get()` in `GM_STATE_SEND_SYNC` immediately
after `gm_send_raw_eth_frame(...)` succeeds, and pass it to `PTP_CLOCK_Update()` later
together with the TTSCAL value.

```c
// In GM_STATE_SEND_SYNC, after gm_send_raw_eth_frame() returns true:
gm_tick_at_sync_tx = SYS_TIME_Counter64Get();

// In GM_STATE_WAIT_TTSCA_L, replace:
PTP_CLOCK_Update(wc_ns, SYS_TIME_Counter64Get());
// with:
PTP_CLOCK_Update(wc_ns, gm_tick_at_sync_tx);
```

**Problem:** `gm_tick_at_sync_tx` is the tick at SPI frame submission, not at actual
wire transmission time. The SPI transfer itself takes ~1 frame × 64 bytes / (25 MHz SPI) ≈
20 µs, plus possible SPI-bus queuing. This error is ~20–50 µs, much better than 3 ms.

### Option B: Capture tick at TXPMDET detection (GM)

In `GM_STATE_WAIT_TXMCTL`, when `TXPMDET` bit is found set, also capture
`SYS_TIME_Counter64Get()`. TXPMDET fires when the LAN865x has matched and timestamped
the SYNC frame on the wire. This is the most accurate moment.

```c
case GM_STATE_WAIT_TXMCTL:
    if (gm_op_val & GM_TXMCTL_TXPMDET) {
        gm_tick_at_txpmdet = SYS_TIME_Counter64Get();
        // ← still ~1 ms before TTSCA_L read, but much closer to actual TX time
        ...
    }
```

**Problem:** TXPMDET callback fires 1–2 ms after SYNC is actually placed on the wire.
Better than 3 ms, but still not ideal.

### Option C: Capture tick atomically with TTSCAL (best)

Restructure: issue the TTSCA_H and TTSCA_L reads, and immediately after the TTSCA_L
callback fires (i.e., still inside `GM_STATE_WAIT_TTSCA_L`), call
`SYS_TIME_Counter64Get()` **before** doing any other work. This is what the current code
already does — but the captured tick is ~3 ms after SYNC TX.

**Key insight:** On GM, the anchor pair `(t1_hardware, tick_TTSCA_L)` has a fixed
systematic delay of ~3 ms. If this delay were **constant**, the drift IIR would converge
to a static offset and `GetTime_ns()` would still be accurate (the delta_tick computation
would compensate). The issue is that this delay varies slightly each cycle, introducing
noise, and the IIR learns the wrong "drift".

The correct approach is **Option A** with the addition of a constant-offset correction:
store the average SYNC-TX-to-TTSCA_L delay and subtract it. Or, more cleanly, use
Option A to get a tick very close to actual SYNC TX and drop the 3 ms systematic error.

---

## 7. Independent timer validation plan

Before fixing the GM anchor, validate the entire `PTP_CLOCK` mechanism
**independently of PTP Ethernet traffic** using a new CLI command pair:

- `clk_set <ns>` — calls `PTP_CLOCK_Update(ns, SYS_TIME_Counter64Get())` directly
- `clk_get` — calls `PTP_CLOCK_GetTime_ns()` and reports in ns

Test procedure (`hw_timer_sync_test.py`):
1. Set both boards to `clk_set 0` as near-simultaneously as possible
2. Poll `clk_get` N times on both boards in parallel (swap-symmetrised)
3. Analyse the difference — this measures **pure TC0 interpolation accuracy**
   (no PTP, no Ethernet, no RTSA, no TTSCAL involved)
4. If this test passes with low mean error: TC0 interpolation is correct;
   the −3.3 ms bug is confirmed to be in the anchor capture path (GM side)

---

## 8. Files changed so far

| File | Change |
|------|--------|
| `src/ptp_clock.h` | New file — API |
| `src/ptp_clock.c` | New file — implementation |
| `src/ptp_ts_ipc.h` | Added `sysTickAtRx` field |
| `src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c` | FollowUp-guard fix |
| `src/PTP_FOL_task.c` | `PTP_CLOCK_Update()` call |
| `src/ptp_gm_task.c` | `PTP_CLOCK_Update()` call + verbose mode |
| `src/ptp_gm_task.h` | `PTP_GM_SetVerbose()` declaration |
| `src/app.c` | `ptp_time` CLI command |
| `.generated/file.cmake` | `ptp_clock.c` added to build |
| `ptp_time_test.py` | New Python test |

---

*Last updated: 2026-04-08*
