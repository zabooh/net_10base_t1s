# PROMPT: 4-Board Test Rig for Autonomous AI-Driven Annex H Development

> **Audience:** an AI coding agent (or a human engineer briefing the
> agent) tasked with implementing the Annex H roadmap from
> `documentation/ptp/plca_ptp_asymmetrie.md` §12.  Read this file
> before any code edit — it describes the **automated build /
> flash / measure / verify loop** the agent uses to iterate.
>
> The whole point of this rig: the agent receives a goal
> (e.g. *"implement Phase 2 — Pdelay protocol"*), runs an inner
> loop of `edit → build → flash → run → measure → analyse`, and
> stops only when quantitative acceptance gates are met.  No
> human in the loop for the iteration.

---

## 1. Goal

Enable an AI agent to execute the multi-week Annex H implementation
roadmap (see `documentation/ptp/plca_ptp_asymmetrie.md` §12)
autonomously, by providing:

1. A 4-board hardware test bed where 3+ nodes share a real PLCA bus
2. A logic-analyzer-based ground-truth measurement of PTP
   synchronicity (PPS edges, MCU-GPIO time toggles)
3. A Python orchestrator that drives boards + Saleae and computes
   pass/fail metrics
4. A CLI surface on each board that the orchestrator can script
5. Quantitative acceptance gates per implementation phase

Result: the agent can answer *"is my implementation correct?"*
empirically — within the same iteration loop as `cargo test` /
`pytest`, not via a human-in-the-loop scope visit.

---

## 2. Hardware setup

### 2.1 Bill of materials

| Qty | Item | Notes |
|---|---|---|
| 4 | SAM E54 Curiosity Ultra board with LAN8651 click adapter | identical hardware so firmware is reproducible |
| 1 | Saleae Logic Pro 8 (or Logic 16 / Logic Pro 16) | 8+ digital channels needed |
| 1 | Host PC (Windows or Linux) | runs MPLAB X, mdb, Saleae Logic 2, Python orchestrator |
| 4 | USB-A → USB micro cables | one per board (CLI + flash) |
| 1 | 5-way wire harness for the T1S bus | star or daisy-chain shared twisted pair |
| 8 | 6" jumper wires | PPS + GPIO routing to Saleae |
| 4 | 5 V power supply (or USB) | per board |

### 2.2 Wiring

Per board, route two signals to the Saleae:

```
LAN8651 PPS pin ─────► MCU GPIO input (capture for runtime use)
                  │
                  └──► Saleae channel 2*N      (ground truth: chip TSU)

MCU GPIO output ─────► Saleae channel 2*N + 1  (software-side timing,
   (1 ms square wave                            shows PTP_CLOCK quality
   driven from the                              after sync convergence)
   PTP-disciplined
   software clock)
```

So with N=4 boards, the Saleae sees 8 channels:

```
Ch 0:  Board 0 PPS (LAN8651 hardware)
Ch 1:  Board 0 MCU GPIO (software-disciplined toggle)
Ch 2:  Board 1 PPS
Ch 3:  Board 1 MCU GPIO
Ch 4:  Board 2 PPS
Ch 5:  Board 2 MCU GPIO
Ch 6:  Board 3 PPS
Ch 7:  Board 3 MCU GPIO
```

### 2.3 PLCA bus topology

```
                    shared twisted pair (10BASE-T1S)
   ●─────●─────●─────●─────●
   │     │     │     │
  B0    B1    B2    B3
 ID=0  ID=1  ID=2  ID=3

 (NodeID 0 is by convention the PLCA coordinator
  → also the PTP grandmaster in test scenarios that need one)
```

Star topology with up to 25 cm stubs is acceptable for this length —
matches automotive T1S Eth0/Eth1 use-cases.

### 2.4 Board-to-host enumeration

Each board's USB CDC serial is identified by its **EDBG serial
number** (visible in `mdb` and `lsusb`).  The orchestrator reads a
config file `tools/test_rig/boards.yaml`:

```yaml
boards:
  - id: 0       # also PLCA NodeID
    edbg_serial: ATML2123040200001234
    com_port: COM7        # Windows; on Linux: /dev/ttyACM0
  - id: 1
    edbg_serial: ATML2123040200001235
    com_port: COM8
  - id: 2
    edbg_serial: ATML2123040200001236
    com_port: COM9
  - id: 3
    edbg_serial: ATML2123040200001237
    com_port: COM10

saleae:
  api_endpoint: tcp://127.0.0.1:10429
```

---

## 3. Firmware extensions required

The firmware needs four small CLI / behaviour additions on top of
the existing `cross-driverless` codebase.  These are part of the
test-rig setup, not part of Annex H itself.

### 3.1 Boot-time PLCA NodeID assignment

Currently NodeID is hardcoded.  Add a way to override it from a
build flag or NVM setting.  Minimum viable: 4 build configurations
`default_id0`, `default_id1`, `default_id2`, `default_id3` that
differ only in the PLCA NodeID compile-time constant.

### 3.2 PPS pin observability

The LAN8651 PPS output is already driven by the chip TSU.  Route
it to a free GPIO on the SAM-E54 (board-dependent — pick a pin
that is also accessible on a header so the wire to the Saleae is
short).  Document the chosen pin in the board pinmux.

### 3.3 MCU-GPIO software-toggle pin

Add a 1 ms square wave on a chosen GPIO, driven by a periodic
callback on `PTP_CLOCK_GetTime_ns()` modulo 1 ms == 0.  This
exposes the *software* time alignment (whereas PPS shows the
*hardware* time alignment).  When PTP is locked, both should toggle
simultaneously across all 4 boards within < 1 µs.

### 3.4 CLI commands the orchestrator scripts

Each board must accept these commands over its USB-CDC console:

```
ptp role <gm|slave>          # which role this node plays
ptp pdelay enable [period_ms]
ptp pdelay disable
ptp annexh enable
ptp annexh disable
ptp start                    # begin PTP after configuration
ptp stop
ptp status                   # human-readable; prints offset/drift
ptp metrics                  # machine-readable: comma-separated
                             #   offset_ns,drift_ppm,sync_count
ptp plca status              # PLCA NodeID, slot, beacon-tick, etc.
ptp pdelay show              # current Pdelay measurements
mcu_toggle period <us>       # set software-side GPIO period
mcu_toggle enable
mcu_toggle disable
reset                        # software reset (jump to reset vector)
```

Output format must be:
- One line per command response
- Prefix `[OK]` for success, `[ERR ...]` for failure
- Machine-readable values comma-separated (`offset_ns=123,drift_ppm=0.7`)
- Always end with a newline

This makes the CLI scriptable from Python without ambiguity.

---

## 4. Python orchestrator (`tools/test_rig/`)

### 4.1 Layout

```
tools/test_rig/
  ├── boards.yaml              (config — board serials + COM ports)
  ├── test_rig.py              (Python module, the orchestrator)
  ├── board_cli.py             (per-board CLI client wrapper)
  ├── saleae_client.py         (Saleae Logic 2 automation wrapper)
  ├── analyse_pps.py           (post-capture analysis)
  ├── flash_all.py             (parallel mdb-based flash)
  ├── scenarios/
  │     ├── s01_2node_basic.py        Phase 0 baseline
  │     ├── s02_3node_no_compen.py    show asymmetry without Annex H
  │     ├── s03_3node_static_id.py    static NodeID compensation
  │     ├── s04_4node_full_annexh.py  full Annex H
  │     ├── s05_burst_load.py         worst-case bus utilisation
  │     └── s06_skipping_slots.py     empty-slot handling
  └── README.md                (usage instructions)
```

### 4.2 Top-level API

```python
class TestRig:
    def __init__(self, config_path="boards.yaml"):
        ...
    
    def build(self, target="default"):
        """Run cmake + ninja from repo root.  Returns the .hex path."""
    
    def flash_all(self, hex_path):
        """Flash all boards in parallel via mdb.  Returns dict of
        {board_id: success_bool}."""
    
    def reset_all(self):
        """Software-reset every board, wait for boot banner."""
    
    def configure(self, role_map):
        """role_map = {0: 'gm', 1: 'slave', 2: 'slave', 3: 'slave'}.
        Sends `ptp role <r>` to each board."""
    
    def start_ptp(self, annexh=False):
        """Sends `ptp annexh enable/disable` and `ptp start`."""
    
    def run_capture(self, duration_s, sample_rate_mhz=100):
        """Saleae capture during which PTP runs.  Returns the
        path to the .sal file."""
    
    def analyse(self, sal_file):
        """Returns a dict of metrics:
            max_offset_ns, mean_offset_ns, std_offset_ns,
            drift_ppm, sync_loss_count, pdelay_anomalies."""
    
    def teardown(self):
        """Stop PTP, close serial ports, save logs."""
```

### 4.3 Saleae integration

Use the official `logic2-automation` Python package:

```python
from saleae import automation

with automation.Manager.connect() as mgr:
    cfg = automation.LogicDeviceConfiguration(
        enabled_digital_channels=[0,1,2,3,4,5,6,7],
        digital_sample_rate=100_000_000,  # 100 MHz, 10 ns resolution
        digital_threshold_volts=1.65,
    )
    cap_cfg = automation.CaptureConfiguration(
        capture_mode=automation.TimedCaptureMode(duration_seconds=60.0)
    )
    capture = mgr.start_capture(
        device_id="...",
        device_configuration=cfg,
        capture_configuration=cap_cfg,
    )
    capture.wait()
    capture.save_capture(filepath="/tmp/test_run.sal")
```

After capture, export digital edges to CSV and run `analyse_pps.py`.

### 4.4 PPS analysis (the heart of the verification)

`analyse_pps.py` does:

1. Parse all 8 channels' edge timestamps from the .sal export
2. For each second of capture, find the rising edge on each PPS
   channel
3. Compute pairwise offsets between boards
4. Output metrics:

```python
{
  "duration_s": 60.0,
  "boards_seen": [0, 1, 2, 3],
  "ref_board": 0,
  "max_offset_ns": {1: 312, 2: 845, 3: 1240},   # per slave vs GM
  "mean_offset_ns": {1: -12, 2: 4, 3: -8},
  "std_offset_ns": {1: 95, 2: 187, 3: 213},
  "drift_ppm": {1: 0.07, 2: 0.12, 3: 0.18},
  "sync_loss_count": {1: 0, 2: 0, 3: 1},
  "asymmetry_signature": "monotonic-with-nodeId"  # detects Annex-H absence
}
```

The `asymmetry_signature` field is the smoking gun for whether
Annex H is working: without compensation, offset increases
monotonically with NodeID distance to the GM.  After Annex H,
the per-NodeID bias should disappear.

---

## 5. Test scenarios

Each scenario is a Python script `scenarios/sNN_*.py` that:

1. Asserts firmware build identity (which feature flags)
2. Configures roles
3. Starts capture
4. Starts PTP
5. Runs for N seconds
6. Stops
7. Computes metrics
8. Returns `(passed: bool, metrics: dict, message: str)`

### 5.1 Scenario list

| ID | Scenario | Purpose | Pass gate |
|---|---|---|---|
| `s01` | 2 nodes, basic gPTP | smoke test, regression guard | max_offset < 1 µs over 60 s |
| `s02` | 3 nodes, no Annex H | shows the asymmetry baseline | max_offset > 5 µs (PROVES the problem) |
| `s03` | 3 nodes, static NodeID compensation | Phase 4 milestone | max_offset < 20 µs |
| `s04` | 4 nodes, full Annex H | final goal | max_offset < 1 µs over 5 min |
| `s05` | 4 nodes, full burst load | robustness | max_offset < 2 µs under load |
| `s06` | 3 nodes, empty-slot skipping | skip-handling correct | drift_ppm < 0.5 with slot skips |

### 5.2 Quality gates (the AI agent's stop criteria)

The agent's iteration loop continues until **all relevant scenarios
for its current phase pass**.  Phase-by-phase mapping:

| Annex H phase | Scenarios that must pass |
|---|---|
| Phase 1 (HW plumbing) | s01 + `ptp plca status` returns valid data on all 4 boards |
| Phase 2 (Pdelay protocol) | s01 + `ptp pdelay show` returns t1..t4 on all neighbour pairs |
| Phase 3 (Cycle observer) | s01 + cycle-duration jitter < 5 µs over 1 min |
| Phase 4 (Bias compensator) | s03 |
| Phase 5 (Variance filter) | s05 (under load) |
| Phase 6 (Stack integration) | s04 + s05 + s06 |
| Phase 7 (Config + CLI) | All `ptp ...` CLI commands work scripted |

---

## 6. AI agent integration

### 6.1 The inner loop

```python
def agent_inner_loop(goal_description, max_iterations=20):
    rig = TestRig()
    
    for iteration in range(max_iterations):
        # 1. Edit code (the agent does this part)
        agent_edit_step(goal_description, last_metrics)
        
        # 2. Build
        hex_path = rig.build()
        
        # 3. Flash all boards
        flash_results = rig.flash_all(hex_path)
        if not all(flash_results.values()):
            agent_log(f"flash failed: {flash_results}")
            continue  # try editing again
        
        # 4. Run scenarios for current phase
        last_metrics = {}
        all_passed = True
        for scenario in current_phase_scenarios(goal_description):
            passed, metrics, msg = scenario.run(rig)
            last_metrics[scenario.id] = metrics
            if not passed:
                all_passed = False
                agent_log(f"FAIL {scenario.id}: {msg}")
        
        # 5. Stop?
        if all_passed:
            return "DONE"
    
    return "MAX_ITERATIONS_REACHED"
```

### 6.2 What the agent edits

- Source files: `apps/tcpip_iperf_lan865x/firmware/src/*.c/*.h`
- Build files: `cmake/.../user.cmake` if a new file is added
- Test scenarios: only when adding new metrics or scenarios
- **Never** edits MCC-managed files (per `cross-driverless` rules)

### 6.3 Failure injection / debug aids

When a scenario fails, the agent has access to:

- Full `.sal` capture file (Saleae) — can re-analyse with different metrics
- Per-board UART log files (saved during the scenario run)
- `ptp metrics` snapshots taken every second
- `ptp plca status` and `ptp pdelay show` snapshots

Agent can write a debug-only scenario `sDD_<topic>.py` for itself.

---

## 7. Bootstrap / one-time setup

### 7.1 Pre-flight checklist

- [ ] All 4 boards build firmware identically (CMake checksum the
      same except for the NodeID flag)
- [ ] All 4 boards enumerate as separate USB-CDC devices with
      stable EDBG serials
- [ ] `mdb` can flash each board individually (test once manually)
- [ ] PPS pins from each LAN8651 are routed to the Saleae and
      visible on Logic 2
- [ ] MCU GPIO toggle pins are routed to the Saleae and visible
- [ ] The PLCA bus is correctly wired (verify ARP / ICMP between
      two boards before Annex H work)
- [ ] Saleae Logic 2 is running with automation server enabled
- [ ] `tools/test_rig/boards.yaml` is filled in with real EDBG
      serials and COM ports
- [ ] `tools/test_rig/test_rig.py:s01_2node_basic` passes manually
      before handing the rig to the agent

### 7.2 First successful run

The agent's *very first* step on a fresh rig should be to run
`s01` (the 2-node baseline) without modifying any code.  If `s01`
fails, the rig is broken — fix the rig before proceeding to any
implementation work.

### 7.3 Documentation deliverables

After the rig is built, generate (or have the agent generate):

- `documentation/testing/annex_h_test_rig.md` — operator's manual
- A photo of the wired test rig (for the reference hardware section)
- A ScreenShot of a `s01` Saleae capture annotated with the 4 PPS
  edges aligning at sub-µs

---

## 8. Effort estimate

| Phase | Effort | Done by |
|---|---|---|
| Hardware: solder PPS leads, harness | 0.5 day | human |
| Saleae setup + Logic 2 automation install | 0.5 day | human |
| Firmware: PPS GPIO route + mcu_toggle | 0.5 day | human or agent |
| Firmware: CLI extensions per §3.4 | 1 day | agent |
| Python orchestrator + scenarios | 2 days | agent |
| End-to-end smoke (s01 passing) | 0.5 day | both |
| **Total — rig ready** | **5 days** | |
| Annex H Phase 1–7 (using the rig) | ~3 weeks | agent |
| **Total — Annex H done** | **~4 weeks** | |

The human part (hardware soldering, Saleae purchase) is the hard
prerequisite.  Once the rig works, the agent runs the implementation
phase autonomously.

---

## 9. Risks and unknowns

| Risk | Mitigation |
|---|---|
| LAN8651 PPS output is not stable enough for sub-µs ground truth | Validate on 2-board setup first; use PHY's TSU-PPS mode (verify in datasheet) |
| Saleae 100 MHz sampling has 10 ns resolution; might not be tight enough for sub-100-ns offsets | Use Saleae Pro at 500 MHz if 100 MHz turns out marginal |
| 4 boards may have crystal frequency variation > 50 ppm | Specify boards with same crystal source, characterise free-running drift before PTP test |
| `mdb` parallel flash sometimes locks up | Add retry logic in `flash_all.py`; serial-fallback if parallel fails |
| MCU GPIO toggle precision (jitter from interrupt latency) | Document the floor; treat as an upper bound on observable software-time accuracy, not a target |
| PLCA bus reflections at 4 nodes with star topology | Use proper terminations (54 Ω) per IEEE 802.3cg; verify eye diagram once |
| Agent-induced firmware instability bricks a board | EDBG can always re-flash; document the recovery procedure |

---

## 10. When done

Update:

- `documentation/ptp/plca_ptp_asymmetrie.md` §12.7 — mark "strategic
  recommendation" as obsolete: the Annex H is now implemented and
  verified.
- `documentation/testing/annex_h_test_rig.md` — operator's manual
  for the rig.
- This `PROMPT_annex_h_test_rig.md` — mark all `[ ]` boxes complete,
  add "Outcome" section with the actual achieved offset numbers.

Then close the loop by submitting:

- A Microchip-Harmony PR with the complete Annex H implementation
  (using the cross-driverless minimization pattern as starting point)
- A Zephyr PR with the same logic ported to the standard
  `eth_driver_api.get_ptp_clock` + `ptp_clock_lan865x.c` pattern

Both PRs reference this test rig as the verification harness.

---

## 11. Why this approach is uniquely suited to AI execution

1. **Tight feedback loop**: edit → build → flash → measure → score
   in < 5 minutes.  Equivalent to `cargo test`, but for embedded.
2. **Quantitative gates**: every phase has a number that must drop
   below a threshold.  No ambiguous "looks right" judgement calls.
3. **Hardware ground truth**: the Saleae sees the LAN8651's hardware
   PPS — independent of any software bug.  The agent cannot
   accidentally satisfy a metric by miscomputing.
4. **Scoped action surface**: agent edits only `firmware/src/*.c/*.h`
   and `cmake/.../user.cmake`.  Cannot accidentally break MCC, the
   build system, or the test rig itself.
5. **Reproducible**: the rig state is fully defined by `boards.yaml`,
   the firmware hex, the scenario, and the PLCA configuration.  Any
   fail can be reproduced for debugging.

This is the difference between *"AI helps a human implement Annex H"*
and *"AI implements Annex H, human reviews the PR"*.  The rig is
what makes the second mode possible.
