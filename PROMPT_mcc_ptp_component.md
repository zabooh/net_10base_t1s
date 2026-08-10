# PROMPT: Make PTP for LAN8651 selectable inside MCC

> **Audience:** an engineer or coding agent who picks up this task from
> the `cross-driverless` branch.  Treat this file as a self-contained
> brief — read it before doing anything else, then plan, research,
> implement, and verify in that order.

---

## Goal

End-state: a developer who opens the `tcpip_iperf_lan865x` (or any
other LAN8651-based) project in MPLAB X and runs MCC sees a checkbox
**"Enable PTP / IEEE 1588 hardware timestamping"** on the LAN865x
driver component (or as a separate sibling component).  Toggling that
checkbox should:

1. Copy the PTP driver-extension files (`ptp_drv_ext.c/.h` and the
   minimum subset of `ptp_*.c/.h` the user needs) into
   `firmware/src/`.
2. Add them to the build (CMake `file.cmake` and MPLAB X
   `configurations.xml`).
3. Apply the **12 irreducible inline patches** to
   `drv_lan865x_api.c` and `drv_lan865x.h` (see §2.8 in
   `README_cross.md`).
4. Wire `PTP_DRV_EXT_Init()` and `PTP_DRV_EXT_Tasks()` into the
   generated `app.c` (or document that the user must add them).

When the checkbox is **off**, the project regenerates exactly as the
upstream Microchip template: zero PTP files, zero `drv_lan865x_api.c`
patches.

This eliminates the entire "MCC overwrites our patches" dilemma
documented in §2 / §7 of `README_cross.md`.

---

## Why this is worth doing

| Today | After this task |
| --- | --- |
| Every MCC run threatens 12 inline-driver lines (§2.8) | MCC *generates* those 12 lines from the component template |
| `<stdarg.h>` template bug (§6) is permanent for users | Component-level workaround can include `<stdarg.h>` defensively |
| New users of LAN865x + PTP have to manually replicate the cross-driverless commits | One checkbox |
| Fork-vs-upstream drift only grows | Upstream-friendly — submittable as a PR to Microchip Harmony Net |
| README_cross.md §2.7's "Mitigation Strategy C" (Harmony Template Mechanism) | actually implemented |

This is the **proper** long-term solution that §2.7 discussed but
deferred.

---

## Required reading (in order)

1. `README_cross.md` §2.1–2.6 — the seven PTP patches and *why*
   each exists.
2. `README_cross.md` §2.8 — what the irreducible 12 lines are
   (`OnPtpFrame_Hook` + `GetTc6Inst()`) and what got moved out
   into `ptp_drv_ext.{c,h}`.
3. `README_cross.md` §6 + §7 — the MCC template bug and the
   real-world manual-merge experience.
4. `apps/tcpip_iperf_lan865x/firmware/src/ptp_drv_ext.c` and `.h`
   — the runtime payload this component needs to ship.
5. `apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c`
   — the file that gets the 12-line patch.

Then, **before any code changes**:

6. Reverse-engineer the existing `net_10base_t1s` MCC component:
   - Where does its component definition live?  Most Harmony
     packages have a `config/` and a `templates/` directory under
     `~/.mcc/harmony/content/net_10base_t1s/v1.4.2/`.
   - How does Microchip currently express the LAN865x driver as a
     selectable MCC component (Net package, `drvExtMacLan865x`)?
   - What file format are the component definitions in?  (Likely
     Python: `*.py` for logic, FreeMarker `*.ftl` for code
     templates.)
   - How does an MCC component inject `<itemPath>` entries into
     `configurations.xml`?  Is this automatic if the component
     declares its source files, or is there an explicit hook?

7. Read the official Microchip docs on **Harmony component
   development**:
   - https://onlinedocs.microchip.com/oxy/GUID-... (search for
     "Harmony Configurator Component Development Guide")
   - GitHub examples under
     https://github.com/Microchip-MPLAB-Harmony/csp/tree/master/peripheral
     are good real-world references for component structure.

---

## Phased implementation plan

### Phase 0 — Discovery (~1 day, no code)

- [ ] Locate and read the source of an existing simple Harmony
  component (e.g., a SERCOM peripheral wrapper in `csp`) to
  internalise the component-definition pattern.
- [ ] Locate the existing `drvExtMacLan865x` component definition
  under the Net Harmony package.  Identify which `.py` file
  declares the symbol set and which `.ftl` files emit the actual
  driver source code.
- [ ] Map MCC's "regenerate" step to specific file actions: which
  template emits `drv_lan865x_api.c`?  Is the file copied verbatim
  from the package cache, or is it the output of a `.ftl`?
  (Findings here decide §6's exact root cause too — bonus.)
- [ ] Decide whether to:
  - **Option A**: Add a checkbox to the existing
    `drvExtMacLan865x` component (intrusive, requires modifying
    Microchip's component).
  - **Option B**: Create a *new* component
    `drvExtMacLan865x_PTP` that depends on `drvExtMacLan865x` and
    adds the PTP capability (cleaner, more upstream-PR-able).

  Recommendation: **Option B** unless Microchip's component model
  doesn't support clean dependency chaining.

### Phase 1 — Component skeleton (~1 day)

- [ ] Create a minimal MCC component called `drv_lan865x_ptp` (or
  similar) that does *nothing* functional — just shows up as a
  selectable item in the MCC Resources tree.  Just the toggle, no
  file-emission yet.
- [ ] Verify it appears in MPLAB X MCC GUI when the
  `net_10base_t1s` package is loaded.
- [ ] Verify it can be enabled/disabled and the state persists
  across MCC restarts.

**Acceptance criterion**: a checkbox shows up.  Toggling it has no
visible effect yet.

### Phase 2 — File emission (~2 days)

- [ ] Add file-emission rules for `ptp_drv_ext.c` and
  `ptp_drv_ext.h`.  These are *static* files (no FreeMarker
  templating needed for v1) — the component just copies them
  into `firmware/src/`.
- [ ] Verify enabling the toggle results in those two files
  appearing under `firmware/src/`, and disabling removes them.
- [ ] Also add file-emission rules for the minimum useful PTP
  consumer subset: at least `ptp_clock.{c,h}`, `ptp_fol_task.{c,h}`,
  `ptp_gm_task.{c,h}`, `ptp_rx.{c,h}`, `ptp_log.{c,h}`,
  `ptp_ts_ipc.h`, `filters.{c,h}`.  (This makes the demonstrator
  buildable; the cyclic-fire / sw_ntp / standalone_demo CLI files
  are pure demo-shell and can be omitted from v1.)
- [ ] Verify the `<itemPath>` entries appear in
  `configurations.xml` when the toggle is on.

**Acceptance criterion**: `cd firmware/tcpip_iperf_lan865x.X &&
make` succeeds (or fails only on linker errors due to the missing
12 inline patches; those come in Phase 3).

### Phase 3 — Inline patch injection (~3 days, hardest phase)

This is the design-critical phase.  How does the component apply
the 12-line patch to `drv_lan865x_api.c` (which is owned by the
`drvExtMacLan865x` parent component)?

Three sub-options to evaluate, in order of preference:

**3.A — `.ftl`-template merge in the parent component**

If `drv_lan865x_api.c` is generated by a `.ftl` template owned by
`drvExtMacLan865x`, then the cleanest fix is:

- Submit upstream PR to Microchip that adds two `<#if PTP_ENABLED>`
  blocks in the `.ftl` template:
  - one around the `__attribute__((weak))` hook decl (Z. 51)
  - one around the hook call in `TC6_CB_OnRxEthernetPacket` (Z. 1383)
  - and one around the `DRV_LAN865X_GetTc6Inst()` accessor body
    (Z. 2446)
- Plus declare a `PTP_ENABLED` symbol in the parent component
  that the new `drv_lan865x_ptp` component flips to true.

This is the right answer if accepted upstream.  Document the PR
URL in this prompt for reference.

**3.B — Header-only injection via `definitions.h.ftl`**

If 3.A is not feasible (Microchip rejects the PR), use the
existing `definitions.h.ftl` template which IS already extended
by user components.  Idea: define the hook implementations
in `ptp_drv_ext.c` and rely on `__attribute__((weak))` semantics
already in the upstream driver — but that requires Microchip's
upstream `drv_lan865x_api.c` to have the hook calls.  If they
don't, this option degrades to Option 3.C.

**3.C — Post-generation patch script**

If 3.A and 3.B both fail, ship a small Python or shell script
inside the component that:

- Runs after MCC has finished regenerating files.
- Detects `drv_lan865x_api.c`, applies the 12-line patch via
  `patch` or in-place sed.
- Records the post-patch hash so subsequent MCC runs know the
  file is "patched" and don't re-prompt.
- Logs to console: *"PTP component: applied driver patch to
  drv_lan865x_api.c"*.

This is the least elegant but most robust option.

**Acceptance criterion**: enable the PTP toggle in MCC, click
"Generate", build the project — succeeds without manual
intervention.  Disable the toggle, regenerate — file goes back
to upstream-pristine.

### Phase 4 — `app.c` integration (~0.5 days)

- [ ] When PTP is enabled, the component must add 2 lines to
  `app.c`:
  - `#include "ptp_drv_ext.h"` near the top
  - `PTP_DRV_EXT_Init();` in `APP_Initialize()`
  - `PTP_DRV_EXT_Tasks(0u);` somewhere in `APP_Tasks()`'s
    `APP_STATE_SERVICE_TASKS` branch.

  Note: `app.c` is user-owned, not regenerated.  So the component
  cannot directly modify it.  Two sub-options:

  - **4.A**: Emit a snippet `ptp_app_init.c` that contains the
    necessary calls in a way the user can include from `app.c`.
  - **4.B**: Document in component help text:
    *"Add `PTP_DRV_EXT_Init()` to your `APP_Initialize()` and
    `PTP_DRV_EXT_Tasks(0u)` to your `APP_Tasks()`."*
  - **4.C**: Generate a complete `app.c.ftl` if the component is
    aggressive enough — but this risks overwriting user logic.

  Recommendation: **4.B** for v1 (minimum surprise), **4.A** for
  v2 (more automation).

### Phase 5 — `configurations.xml` itemPath integration (~0.5 days)

- [ ] Verify that the standard MCC source-emission also adds the
  PTP files to `configurations.xml` automatically.  If yes, done.
- [ ] If not, find the hook (likely a Python callback in the
  component definition) and add the `<itemPath>` entries.

### Phase 6 — Testing on the demonstrator project (~1 day)

- [ ] Use `c:/work/ptp/check4/net_10base_t1s/` as the test bed.
  Reset to `master` (clean upstream).
- [ ] Open in MPLAB X.  Open MCC.  Verify the new "Enable PTP"
  checkbox is visible on the LAN865x component.
- [ ] Toggle ON.  Click Generate.
- [ ] Verify: `firmware/src/ptp_drv_ext.c` exists,
  `configurations.xml` has the `<itemPath>` entry,
  `drv_lan865x_api.c` has the 12-line patch applied.
- [ ] Build.  Should succeed.  Flash.  Run PoR-to-Sync test.
  PTP slave should lock to a known master within a few seconds.
- [ ] Toggle OFF.  Click Generate.  Verify all PTP files
  disappear and `drv_lan865x_api.c` reverts to upstream-pristine.
- [ ] Toggle ON again, verify regeneration is idempotent.

### Phase 7 — Upstream PR (optional but recommended; ~ongoing)

- [ ] Open a PR to
  https://github.com/Microchip-MPLAB-Harmony/net_10base_t1s
  proposing the new component.
- [ ] Reference Issue #__ (the MCC template bug from §6) so
  Microchip sees the related defect at the same time.
- [ ] CC: Jing Richter-Xu
  (`Jing.Richter-Xu@microchip.com`) and Thorsten Kummermehr
  (`thorsten.kummermehr@microchip.com`) — the responsible
  maintainers per `git log`.

---

## Acceptance criteria for the whole task

- [ ] In a freshly cloned `net_10base_t1s` project, MCC shows a
  "PTP" toggle on the LAN865x configurator.
- [ ] Toggling it ON and clicking Generate produces a project
  that builds clean (XC32 v5.10, `-Werror -Wall`) and that
  PTP-slave-syncs on real hardware.
- [ ] Toggling it OFF and regenerating produces a project that
  is byte-identical to the pristine upstream `master` branch in
  the affected files.
- [ ] No manual edits to `drv_lan865x_api.c` are ever needed.
- [ ] `cross-driverless` branch becomes obsolete: its 12-line
  driver patch is now generated by MCC; its `ptp_drv_ext.c/.h`
  are now copied by MCC.

---

## Out of scope for this task

- Replacing or "fixing" the MCC `<stdarg.h>` template bug from
  §6.  That's a separate Microchip-internal fix; the new PTP
  component should defensively ensure `<stdarg.h>` is in the
  emitted file but should not touch the existing template.
- Hardware sign-off of the new register-init values
  (`0x000400F8: 0x9B00`, `DEEP_SLEEP_CTRL_1: 0xE0`) introduced
  in §7.2.  That's a Phase-6 prerequisite, not an
  implementation deliverable.
- Porting to other Harmony Net targets (LAN867x, LAN9303 etc.).
  Stick to LAN8651 for v1.

---

## Risks and unknowns

| Risk | Mitigation |
| --- | --- |
| MCC component model may not support cleanly-dependent extension components (Option B in Phase 0) | Fall back to Option A (modify `drvExtMacLan865x` directly), accept that this means upstream PR is mandatory |
| FreeMarker template injection (Phase 3.A) may not be possible without a Microchip-owned change | Fall back to Phase 3.C (post-gen patch script) |
| `configurations.xml` MCC-emitted entries may not survive across MCC versions (cosmetic reorderings, see §3) | Document that only `<itemPath>` *correctness* is guaranteed, not order |
| Component could collide with future Microchip official PTP support | Name it `drv_lan865x_ptp_community` or similar to namespace-isolate |
| Tooling docs for MCC component dev are sparse | Budget extra discovery time in Phase 0 |

---

## Estimated total effort

| Phase | Effort |
| --- | --- |
| 0 — Discovery | 1 day |
| 1 — Component skeleton | 1 day |
| 2 — File emission | 2 days |
| 3 — Inline patch injection | 3 days |
| 4 — `app.c` integration | 0.5 days |
| 5 — `configurations.xml` glue | 0.5 days |
| 6 — Testing | 1 day |
| 7 — Upstream PR | open-ended |
| **Total** | **~9 working days + review/PR cycle** |

---

## When you are done

Update `README_cross.md` with a new §8 ("MCC Component for PTP")
that:

- Marks §2–§7 as **historical**: documents the previous
  manual-patch workflow that this component replaces.
- Documents the new MCC-checkbox workflow.
- Notes that the `cross-driverless` branch is now obsolete
  (linked to the migration commit hash).

Then close this PROMPT_mcc_ptp_component.md by:

- Marking all `[ ]` checkboxes complete.
- Adding a final section *"Outcome"* with the PR URL, the
  upstream commit SHA where the PTP component was merged, and
  any caveats that emerged.
- Committing it on the branch where the implementation lives.

Good luck.
