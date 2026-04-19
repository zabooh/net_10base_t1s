# tfuture Bias Investigation — Session Handoff

**Status:** Primary fix applied and committed. GM bias resolved
(−1600 µs → −34 µs median, 47× improvement). **A smaller residual
FOL-side bias of −322 µs remains** — different mechanism, see §12.
**Last updated:** 2026-04-19 (after primary fix verified)

---

## 1. Next-session prompt — read this first

Paste this into the next Claude session verbatim:

> Read `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_tfuture_bias_open.md`.
> The primary bias (GM-side, caused by DRIFT_SANITY_PPB_ABS clamping out
> every sample) is already fixed and committed.  A residual FOL-side
> bias of ~−322 µs median remains (see §12), systematic and tight
> (MAD 37 µs) — suggests a lead-time-independent fixed offset in the
> FOL anchor path, not a drift-filter issue.  Next step is to confirm
> lead-time independence: rerun `tfuture_diagnose_test.py` on the
> currently-flashed firmware and look at Phase A/B/C (lead=1/2/4 s,
> drift ON).  If the FOL median stays near −322 µs across all three
> leads, the cause is a fixed offset (likely a GM-vs-FOL
> anchor-capture asymmetry, e.g. the +575983 ns PTP_GM_ANCHOR_OFFSET_NS
> constant that has no FOL counterpart).  If FOL median scales with
> lead_ms, the cause is still rate-related and the drift filter on
> FOL needs more attention.

---

## 2. One-sentence summary

The tfuture module fires with a systematic -1.3 ms bias at lead=2 s
because GM's PTP_CLOCK drift filter never converges — the real
LAN865x-crystal vs. SAME54-crystal mismatch (~1200 ppm) exceeds the
filter's sanity clamp of ±200 ppm (`DRIFT_SANITY_PPB_ABS` in
`ptp_clock.c`), so every incoming sample is silently skipped and
`drift_ppb` stays at 0.

---

## 3. What was built (before the bug)

New module **tfuture** — schedules a single-shot firing event at an
absolute PTP_CLOCK wallclock time, intended to demonstrate coordinated
actions across two HW-PTP-synchronised boards.

Files added:
- `src/tfuture.c`, `src/tfuture.h` — module (~250 lines)
- `tcpip_iperf_lan865x.X/tfuture_sync_test.py` — two-board sync test
- `tcpip_iperf_lan865x.X/README_tfuture.md` — user documentation

See `README_tfuture.md` for the happy-path design documentation; this
document covers the failure mode and diagnosis.

Firmware CLI added (still available after the fix):
- `tfuture_at <ns>`, `tfuture_in <ms>`, `tfuture_cancel`
- `tfuture_status`, `tfuture_reset`, `tfuture_dump`
- **Diagnostic (added during investigation, keep):**
  - `tfuture_drift [on|off]` — disable drift correction in tfuture's
    `compute_target_tick`
  - `ptp_gm_delay [<ns>]` — add signed ns to GM anchor_wc
  - `clk_set_drift [<ppb>]` — manually force PTP_CLOCK drift_ppb

---

## 4. What broke

First run of `tfuture_sync_test.py` (lead=2000 ms, 20 rounds) showed:

| Metric | Observed | Expected |
|---|---|---|
| GM self_jitter median | **−1.6 ms** | ~0 ± few hundred ns |
| FOL self_jitter median | **−566 µs** | ~0 ± few hundred ns |
| Inter-board median | **−1.0 ms** | ~0 ± tens of ns |
| Robust stdev | 40–200 µs | 100–500 ns |

Log: `tfuture_sync_test_20260418_235350.log` (original observation).

Both boards fire systematically EARLIER than their own declared target.
GM is about 3× worse than FOL, consistent across all 20 rounds (tight
distribution: min/max range only ±200 µs around the mean).

---

## 5. Investigation log — what was tried

Three diagnostic scripts were built and run. Each confirmed or
eliminated a hypothesis.

### 5.1 `tfuture_diagnose_test.py` — lead scan + drift toggle

4 phases: lead_ms = 1000 / 2000 / 4000 with drift ON, plus lead_ms =
2000 with drift OFF. Log: `tfuture_diagnose_test_20260419_002836.log`.

Findings:
- Bias **scales with lead_ms** but not strictly linearly (C/B ratio
  2.75 instead of ideal 2.0, FOL shows 5.0×).
- Turning off drift correction in `compute_target_tick` **makes bias
  worse by ~150 µs**, not better. So the drift correction is helping,
  not hurting — not the cause.
- GM drift_ppb reading: **+0 in every phase** until Phase D ended (when
  it suddenly jumped to +137683 ppb). Filter is not converging.

Fit over the lead scan: `bias ≈ +1027 − 1201 × lead_s` (µs) for GM.
Two components: a +1 ms fixed term and a −1.2 ms/s proportional term.

### 5.2 `tfuture_anchor_delay_test.py` — Hypothesis 1 (anchor_wc vs. anchor_tick gap)

Theory: GM's `anchor_wc` is captured at the Sync-SFD moment (LAN865x
TTSCA read), while `anchor_tick` is captured ~6 ms later when the
firmware reads `SYS_TIME_Counter64Get()`. Swept an extra offset added
to `anchor_wc` through 0 / 3 / 6 / 9 / 12 ms. Log:
`tfuture_anchor_delay_test_20260419_005148.log`.

Findings:
- GM bias stayed in −1.41 to −1.65 ms range across all 5 delay values —
  essentially flat.
- **Hypothesis 1 REJECTED.** The anchor_wc/tick gap is not the bug.

### 5.3 `tfuture_drift_forced_test.py` — Hypothesis 2 (drift filter not converging)

Theory: Real TC0-vs-LAN865x rate mismatch is large; filter is stuck at
0; so `compute_target_tick` under-corrects. Manually forced drift_ppb
via new `clk_set_drift` CLI through {−500, 0, 200, 500, 700, 900, 1200}
kppb. Log: `tfuture_drift_forced_test_20260419_011431.log`.

Findings: monotonic with a clear minimum —

| forced drift_ppb | GM median bias |
|---|---|
| −500 000 | −2115 µs |
| 0 | −1290 µs |
| +200 000 | −1100 µs |
| +500 000 | −714 µs |
| +700 000 | −440 µs |
| +900 000 | −426 µs |
| **+1 200 000** | **+58 µs** ✓ |

**Hypothesis 2 CONFIRMED.** The real GM TC0-vs-LAN865x rate mismatch is
approximately **+1200 ppm** (LAN865x crystal runs that much slower than
SAME54 crystal × PLL).

---

## 6. Root cause + fix plan

### 6.1 Root cause

In [src/ptp_clock.c](../../src/ptp_clock.c), line ~36:

```c
#define DRIFT_SANITY_PPB_ABS  200000
```

Only samples with `|inst_ppb| < 200 kppb` are accepted into the IIR
filter. Samples outside this range are silently dropped (filter not
updated). On this specific hardware, the real rate mismatch is ~1200
kppb, so **every** sample is dropped and `drift_ppb` stays at 0 forever.

Consequence: `PTP_CLOCK_GetTime_ns()` applies no drift correction,
accumulating ~1.2 ns of error per 1 ms of elapsed time since the last
anchor update. Over a 2-second lead this adds up to ~1.5–2 ms worth of
mismatch between tfuture's target_tick computation and PTP_CLOCK output
at the firing moment.

### 6.2 Fix

**Single-line change.** In [src/ptp_clock.c](../../src/ptp_clock.c):

```c
// Was:  #define DRIFT_SANITY_PPB_ABS  200000
#define DRIFT_SANITY_PPB_ABS  5000000   // ±5000 ppm, covers worst-case crystal mismatch
```

5000 kppb = 5000 ppm = 0.5 %. Comfortably covers the observed 1200 ppm
and any plausible worst-case pair of cheap crystals, while still
rejecting obviously-bad samples from anchor-update glitches (which
typically produce values in the tens of thousands of ppm).

Also update the adjacent comment block (lines ~32-36) to explain the
new limit:

```c
/* Sanity window for the per-sample instantaneous ppb estimate.
 * Accepts up to ±5000 ppm (0.5 %).  Must cover the combined
 * crystal mismatch between the SAME54 PLL source and the LAN865x
 * internal oscillator (two independent crystals); measured on this
 * board pair at approximately +1200 ppm, so the old ±200 ppm limit
 * silently dropped every sample and the filter never converged. */
#define DRIFT_SANITY_PPB_ABS  5000000
```

### 6.3 Verification

After rebuild + flash, run:

```bash
python tfuture_sync_test.py --gm-port COM8 --fol-port COM10
```

**Pass criteria:**
- GM self_jitter median magnitude < 200 µs (was −1.6 ms).
- FOL self_jitter median magnitude < 200 µs.
- Inter-board median magnitude < 500 µs.
- Robust stdev below 300 µs on each (stays similar to before).

If pass: the fix is the single-line change. No further work needed on
the bias front.

If fail (or only partial improvement): proceed to section 8.

**Additional verification** (optional): run
`tfuture_diagnose_test.py` and check that drift_ppb reading on GM is
now near +1 200 000 (not 0) at all phases. The `tfuture_status` CLI
also shows the live drift_ppb; manual `tfuture_status` on the GM board
after a few seconds of PTP FINE should show it converged.

---

## 7. Files and line numbers

Touch these files during the next session:

| File | Purpose | Line/region |
|---|---|---|
| `src/ptp_clock.c` | the fix (widen DRIFT_SANITY_PPB_ABS) | line ~36 + adjacent comment block |
| `tfuture_sync_test.py` | verification run | no changes |

Leave these alone (diagnostics, already committed behavior, working):
- `src/tfuture.c`, `src/tfuture.h`
- `src/app.c` (CLI wiring for the three diagnostic commands)
- `src/ptp_gm_task.c/h` (extra_anchor_delay mechanism — keep as
  diagnostic, not used by default)

All four diagnostic scripts (`tfuture_diagnose_test.py`,
`tfuture_anchor_delay_test.py`, `tfuture_drift_forced_test.py`, plus
`tfuture_sync_test.py`) should stay in the repo as regression tools.

---

## 8. If the fix doesn't work — secondary hypotheses

If widening DRIFT_SANITY_PPB_ABS doesn't drop the bias below 200 µs,
the filter is converging but to the wrong value. Possible causes:

### 8.1 Read-delay-variance noise dominates the filter

The ~6 ms delay between SFD (LAN865x capture) and
`SYS_TIME_Counter64Get()` read in GM has jitter on the order of
milliseconds (FreeRTOS scheduling, SPI contention). This variance
shows up as ±8000 ppm per-sample noise in `inst_ppb`, and even after
IIR smoothing can leave a biased residual.

**Diagnostic:** add `SYS_CONSOLE_PRINT` of `inst_ppb` before the sanity
check, run PTP for 60 s, check the distribution. If mean is far from
1200 ppm and stdev > 2000 ppm, the filter is noise-dominated.

**Fix options (pick one):**
- Reduce IIR time constant to suppress noise (change
  `DRIFT_IIR_N` from 32 to 128).
- Gate filter updates to only use samples where `ε_arm ≈ 0` (just
  after an anchor capture, known-fresh); skip mid-interval samples.
- Capture anchor_tick at the SFD moment via hardware (EIC ISR on a
  LAN865x PPS output, or TC input capture tied to the SPI transaction).

### 8.2 LAN865x TSU increment miscalibrated

If the LAN865x's `CLOCK_INCREMENT` register was set assuming a
different crystal frequency than what's actually populated on the
board, LAN865x TSU itself doesn't tick at 1 ns-per-ns. The drift
filter would capture the combined TC0+TSU mismatch, but tfuture's
formula assumes PTP_CLOCK output tracks real wallclock.

**Diagnostic:** measure LAN865x TSU rate against an external 10 MHz
reference (if available), or cross-check with GM→FOL PTP
`meanPathDelay` reading — if it drifts over minutes, TSU rate is
mis-set.

### 8.3 Something else entirely

Check git log for recent changes that might have regressed behavior:
```bash
git log --oneline apps/tcpip_iperf_lan865x/firmware/src/ptp_clock.c
git log --oneline apps/tcpip_iperf_lan865x/firmware/src/ptp_gm_task.c
```

Compare against the state when `ptp_offset_capture.py` produced the
"~50 ns at SFD" result documented in `README_PTP.md` §11.

---

## 9. What I already know from diagnosis (don't redo)

| Finding | Confirmed by |
|---|---|
| CLI round-trip is ~300 ms (long) | diagnose test Phase A all-FOL-fail at 1s lead |
| PTP itself (HW timestamping at SFD) is sub-100-ns accurate | `ptp_offset_capture.py` history |
| FOL drift filter DOES converge (values in ±60 ppb seen) | diagnose test drift_ppb readings |
| GM drift filter DOES NOT converge (stays at 0) | diagnose test drift_ppb readings |
| Real GM rate mismatch is ~1200 ppm | drift-forced test minimum at +1200 kppb |
| anchor_wc/tick gap is NOT the cause | anchor-delay-sweep showed flat bias |
| tfuture's drift-correction term is working correctly | drift-off made bias slightly worse, not better |

---

## 10. Files relevant to this investigation

All under `apps/tcpip_iperf_lan865x/firmware/`:

### Source (committed or uncommitted, check git status)
- `src/tfuture.c`, `src/tfuture.h` — module
- `src/ptp_clock.c` — **the file to edit** (line ~36)
- `src/ptp_gm_task.c`, `src/ptp_gm_task.h` — has anchor-delay diagnostic knob
- `src/app.c` — CLI wiring for diagnostic commands

### Test scripts (all uncommitted at time of writing)
- `tcpip_iperf_lan865x.X/tfuture_sync_test.py` — main test
- `tcpip_iperf_lan865x.X/tfuture_diagnose_test.py` — lead scan + drift toggle
- `tcpip_iperf_lan865x.X/tfuture_anchor_delay_test.py` — anchor delay sweep
- `tcpip_iperf_lan865x.X/tfuture_drift_forced_test.py` — drift force sweep

### Logs (all uncommitted, keep as evidence)
- `tfuture_sync_test_20260418_235350.log` — original failure observation
- `tfuture_diagnose_test_20260418_225101.log` — broken (crashed on None)
- `tfuture_diagnose_test_20260419_002836.log` — first clean diagnose run
- `tfuture_anchor_delay_test_20260419_005148.log` — Hypothesis 1 rejected
- `tfuture_drift_forced_test_20260419_011431.log` — Hypothesis 2 confirmed

---

## 11. Primary fix (committed)

The DRIFT_SANITY_PPB_ABS widening landed.  GM self_jitter dropped
from −1.6 ms median to −34 µs median.  Commit message and
verification log referenced in the git history of this repo.

---

## 12. Remaining FOL-side residual bias (open)

**Observed after the primary fix is applied:**

| Metric (at lead=2000 ms)   | Value        |
|----------------------------|--------------|
| GM  self_jitter median     | **−34 µs**   |
| FOL self_jitter median     | **−322 µs**  |
| Inter-board median         | +287 µs      |
| FOL robust stdev (MAD·1.4826) | 55 µs    |

FOL's bias is **tight** (MAD 37 µs = tight distribution around its median),
so the −322 µs is **systematic**, not random scheduling noise.  It is
~5× smaller than the original GM problem, so much less severe, but
still over the 200 µs target.

### 12.1 Next diagnostic step

Rerun `tfuture_diagnose_test.py` on the currently-flashed firmware
(with the primary fix active) and compare FOL self_jitter median
across lead_ms = 1000 / 2000 / 4000:

- If FOL median stays near −322 µs regardless of lead_ms
  → **fixed offset**, lead-time-independent.  Investigate asymmetries
  between GM and FOL anchor-capture paths (most likely suspect:
  `PTP_GM_ANCHOR_OFFSET_NS = 575983` has no FOL counterpart; the
  anchor points might be semantically offset by that amount).
- If FOL median scales with lead_ms (doubles from 2000→4000)
  → proportional effect.  Look at FOL's drift_ppb filter behavior
  post-fix; the filter might still be clamping samples on FOL despite
  the wider limit.

### 12.2 Possible fix candidates (not yet tried)

1. Apply a similar constant offset to FOL's anchor_wc, equal to
   `PTP_GM_ANCHOR_OFFSET_NS` — so both boards have the same semantic
   anchor position relative to the wire SFD.
2. Capture FOL's anchor_tick via the same EIC-ISR path already used
   for `g_ptp_raw_rx.sysTickAtRx`, and add an equivalent low-jitter
   anchor-tick path on GM (removes the ~6 ms variable read delay).
3. Use the `clk_set_drift` CLI on FOL during the run to probe whether
   forcing FOL's drift_ppb to values around ±200 kppb changes the
   residual (similar to the drift-forced-sweep we did for GM).

### 12.3 Pragmatic take

The −322 µs remaining bias is small enough that many use-cases
(coordinated firing within ±1 ms inter-board) will tolerate it.
The original ~1.3 ms catastrophic bias is gone.  This can remain
open until a concrete application demands sub-200 µs inter-board
coordination.

---

## 13. Cleanup guidance

Delete this README from the repo once both §10 (primary fix) and
§12 (FOL residual) are fully resolved, or spin section §12 off into
a dedicated short note (e.g. `README_tfuture.md` §8 "Limitations").
