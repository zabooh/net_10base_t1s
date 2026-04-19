# tfuture Bias Investigation — Session Handoff

**Status:** Primary fix applied and committed (GM bias resolved, 47×
improvement).  **FOL residual bias is now characterised** (2026-04-19):
it is **rate-proportional**, not a fixed offset — linear fit
`bias = +1535 − 1084 × lead_s` (µs).  FOL's drift filter reports ~0 ppb
despite the ~1084 ppm proportional effect, because the filter measures
the wrong quantity for tfuture's purposes (see §12.2).  Next session:
validate the sweet-spot hypothesis by sweeping FOL's drift_ppb
manually via `clk_set_drift`.
**Last updated:** 2026-04-19 (after FOL diagnose re-run)

---

## 1. Next-session prompt — read this first

Paste this into the next Claude session verbatim:

> Read `apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/README_tfuture_bias_open.md`.
> Primary GM fix is done (committed d18a102).  FOL residual bias is
> characterised in §12: it scales with lead_ms (−633 µs @ 2 s, −2801 µs
> @ 4 s), fit `bias = +1535 − 1084 × lead_s` µs, yet FOL's drift_ppb
> filter reports ~0 throughout.  Section §12.2 explains why
> (filter measures a matched-but-wrong quantity).
>
> Next concrete step: build a FOL-side version of
> `tfuture_drift_forced_test.py` that sweeps FOL's drift_ppb via
> `clk_set_drift` (the CLI is sent to the FOL serial port instead of GM).
> Expected sweet spot around +1 084 000 ppb where the proportional
> term cancels.  If a sweet spot exists and reduces the bias to within
> the constant +1535 µs (≈ ±200 µs achievable at lead=2 s once the
> rate term is zeroed), we have confirmation that the mechanism is
> rate-based and the proper fix is to improve FOL's drift filter input
> (see §12.4 fix candidates).  If no sweet spot exists, reconsider the
> model — something deeper than a rate mismatch.

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

### 12.1 Characterisation (post-fix, 2026-04-19)

First observation came from a single-lead sync test
(`tfuture_sync_test_20260419_113338.log`, lead=2000 ms):

| Metric               | Value      |
|----------------------|------------|
| GM  self_jitter med  | **−34 µs** |
| FOL self_jitter med  | **−322 µs**|
| Inter-board median   | +287 µs    |

Follow-up multi-lead diagnose run (`tfuture_diagnose_test_20260419_114553.log`)
reveals the scaling behaviour:

| Phase | lead_ms | drift | GM median | FOL median | GM drift_ppb | FOL drift_ppb |
|-------|--------:|:-----:|----------:|-----------:|-------------:|--------------:|
| A     | 1000    | ON    | (arm-fail — CLI RTT too long at this lead) |||
| B     | 2000    | ON    | +13 µs    | **−633 µs**  | +1 180 918   | −2            |
| C     | 4000    | ON    | −12 µs    | **−2801 µs** | +1 240 388   | −108          |
| D     | 2000    | OFF   | −1458 µs  | −664 µs     | +1 080 968   | −114          |

**GM is perfect in B and C.**  Phase D (drift OFF) reverts GM to its
pre-fix behaviour, reconfirming the drift filter is now doing its job
on GM.

**FOL bias scales super-linearly with lead_ms.**  Ratio C/B = 4.43
for a 2× lead increase; a pure-proportional effect would give 2.0.
Linear fit:

    bias = +1535 − 1084 × lead_s   (µs, lead_s in seconds)

Two components superimposed:
  - **Constant +1535 µs** (lead-time-independent offset)
  - **−1084 µs/s** (proportional, equivalent to ~1084 ppm rate error)

**FOL drift_ppb stays near 0** throughout all phases.  Phase B vs D
(both lead=2000 ms) shows only 31 µs difference — i.e. drift correction
has almost zero effect on FOL, because the filter value is essentially
zero.  The filter *is* converging, just not to a value that would help
tfuture compensate.

### 12.2 Why the filter "correctly" reports ~0

Each board has two independent clocks:
  - TC0 (SAME54 crystal × PLL, ~60 MHz nominal)
  - LAN865x TSU (LAN865x's own crystal, driven through CLOCK_INCREMENT)

After PTP PI-servo convergence, **FOL's LAN865x TSU rate is
regulated to match GM's LAN865x TSU rate** (via adjusting
CLOCK_INCREMENT).  The drift filter measures
`(anchor_wc rate) / (anchor_tick rate) − 1`:

  - GM:  rate_ratio = GM_LAN_rate / GM_TC0_rate − 1 = **+1200 ppm** (observed)
  - FOL: rate_ratio = GM_LAN_rate / FOL_TC0_rate − 1 ≈ **0** (observed)

The implication: **FOL's TC0 happens to run at approximately GM's
LAN865x rate** (coincidence of crystals).  Both are ~1200 ppm off from
"GM_TC0_rate" — but FOL's TC0 matches the regulated LAN865x timeline.

So the filter is correct in its own definition.  But tfuture's
compute_target_tick assumes `(50/3) ns per TC0 tick = 1 ns per real ns`,
which is a statement about TC0 rate vs **real wallclock**.  Neither
the filter on GM (with 1200 ppm compensation) nor the filter on FOL
(with 0 compensation) gives tfuture what it really needs: the rate
of TC0 against a truly-stable reference.

The remaining FOL bias — proportional at ~1084 ppm — reflects some
*residual* time-varying effect that neither board's filter captures.
Possibilities: the PI servo's ongoing adjustments to FOL's
CLOCK_INCREMENT create a time-varying LAN865x rate; anchor update
timing jitter; subtle asymmetry in how anchor_wc is derived from t2
vs. ptp_gm_anchor_offset on GM.

### 12.3 Next diagnostic step — FOL drift sweep

Build `tfuture_drift_forced_test_FOL.py` by cloning
`tfuture_drift_forced_test.py` and sending the `clk_set_drift` CLI
to the FOL serial port instead of GM.  Sweep values covering
±1 500 000 ppb.  Expected outcome:

- A sweet spot near **+1 084 000 ppb** (to cancel the rate term)
  produces bias close to the pure constant **+1535 µs**.
- If that matches, two findings fall out:
  1. The mechanism is rate-based (proportional term is real, not an
     artefact).
  2. The +1535 µs intercept is a separate mechanism (likely an
     anchor-capture asymmetry; see §12.4).

If no sweet spot exists (bias is insensitive to drift_ppb forcing),
the model is wrong and we need to dig into PTP_FOL_task.c to
understand exactly what anchor_wc and anchor_tick represent on FOL.

### 12.4 Possible fix candidates (not yet tried)

Once §12.3 confirms the model:

1. **For the +1535 µs constant term**: apply a similar constant offset
   to FOL's anchor_wc, equal to `PTP_GM_ANCHOR_OFFSET_NS` (575 983 ns)
   — so both boards have the same semantic anchor position relative
   to the wire SFD.  Note: the observed constant is +1535 µs, not
   +575 µs, so this might only partially account for it.
2. **For the −1084 ppm rate term**: the filter on FOL needs to measure
   a different quantity.  One option — capture FOL's anchor_tick via
   a truly low-jitter mechanism (the existing EIC ISR for
   `g_ptp_raw_rx.sysTickAtRx` is already low-jitter), and recompute
   the relationship between consecutive anchor-ticks and
   anchor-wc differently.  Another — hard-code a correction
   coefficient on FOL (simple but brittle, needs per-board
   calibration).
3. **Short-term workaround**: expose a `tfuture_fol_bias_ns <ns>` CLI
   that subtracts a constant from the fire time on FOL.  Works but is
   a per-board-pair hack.

### 12.5 Artefacts from this session

- `tfuture_sync_test_20260419_113338.log` — first post-fix verification
  (20 rounds, lead=2 s)
- `tfuture_diagnose_test_20260419_114553.log` — multi-lead characterisation

Both uncommitted (log files are evidence, can be kept locally or
attached to a GitHub issue for the FOL follow-up).

### 12.6 Pragmatic take

The −633 µs at lead=2 s and −2.8 ms at lead=4 s are consequential for
application use-cases.  The 2-second lead case is the most common in
tfuture usage and has ~1× inter-board error at the CLI-RTT-defined
minimum.  This is **not a show-stopper for many applications** but
should be solved before anyone depends on sub-ms inter-board
coordination.

---

## 13. Cleanup guidance

Delete this README from the repo once both §10 (primary fix) and
§12 (FOL residual) are fully resolved, or spin section §12 off into
a dedicated short note (e.g. `README_tfuture.md` §8 "Limitations").
