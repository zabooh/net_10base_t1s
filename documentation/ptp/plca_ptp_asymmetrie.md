# PLCA Slot Asymmetry and PTP Compensation on 10BASE-T1S

## Overview

This document explains why standard PTP (IEEE 1588) does not work directly on 10BASE-T1S multidrop buses with PLCA, and which additional mechanisms IEEE 802.1AS-2020 Annex H defines.

---

## 1. The Relevant Standard

**IEEE 802.1AS-2020, Annex H**

- **IEEE 802.1AS** = gPTP (generalized PTP) — profile of IEEE 1588 for time-critical bridged networks
- **Annex H** was added in 2020 to address T1S with PLCA
- Defines adaptations to the Pdelay mechanism and Sync logic for multidrop bus topologies

**Related standards:**

| Standard | Content |
|----------|--------|
| IEEE 802.3cg-2019 | 10BASE-T1S PHY layer + PLCA |
| IEEE 1588-2008/2019 | Original PTPv2 standard |
| IEEE 802.1AS-2020 | gPTP with Annex H for T1S |
| OPEN Alliance TC10/TC14 | Automotive-specific extensions |

---

## 2. The Underlying Problem

### Standard PTP Assumptions

Standard PTP assumes **point-to-point connections** with the following properties:

- Full-duplex (separate TX/RX lanes)
- Symmetric latency in both directions
- Constant, deterministic transmission time
- Switch/bridge controls medium access

### What T1S Does Differently

| Property | Standard Ethernet | 10BASE-T1S |
|-------------|------------------|------------|
| Topology | Point-to-point | Multidrop bus |
| Duplex | Full-duplex | Half-duplex |
| Medium access | Switch / CSMA/CD | PLCA token passing |
| Nodes per segment | 2 | up to 8 |

PLCA = **Physical Layer Collision Avoidance**: token-passing mechanism in the PHY that regulates slot-based access.

---

## 3. Where the Timestamp Is Taken

### Standard PTP Definition

The timestamp is taken at the **Start of Frame Delimiter (SFD)** — at the medium, not in software.

### On Classic Ethernet

```
Sender:    Application → MAC → PHY → SFD auf Draht → Timestamp t1
Empfänger: SFD auf Draht → Timestamp t2 → PHY → MAC → Application
```

Clearly defined point, no ambiguity.

### On T1S with PLCA

```
Sender:    MAC fertig → warten auf PLCA-Slot → SFD auf Draht
                       ↑
                       Hier kann beliebige Wartezeit liegen
```

**Critical question:** When is the TX timestamp taken?

- **Option A:** When the MAC has finished the frame (before the PLCA wait)
- **Option B:** When the SFD actually appears on the wire

**Standard's answer (Annex H):** Option B — timestamp at the actual SFD moment on the medium.

### Why This Matters So Much

```
MAC sagt "fertig" bei t = 100 ms
PLCA-Wartezeit: 5 ms (Slot kommt erst bei 105 ms)
SFD auf Draht:  t = 105 ms

Falsch (Option A): Timestamp = 100 ms
Richtig (Option B): Timestamp = 105 ms

Fehler bei Option A: 5 ms — komplett zerstört PTP-Genauigkeit
```

PTP targets sub-microsecond accuracy. A 5 ms error is 5000x too large.

### Solution in the LAN8651

The LAN8651 implements this **correctly in hardware**:

- The TSU (Timestamp Unit) sits directly at the PHY medium interface
- Captures the timestamp exactly at the SFD on the wire
- The PLCA wait is automatically included in the timestamp

The `MAC_TSH/TSL` registers deliver these correct timestamps.

---

## 4. The Additional Asymmetry With More Than 2 Nodes

Even with correct SFD timestamping, **3+ nodes** introduce additional problems.

### With 2 Nodes: Symmetric

```
Knoten A (PLCA ID 0)  ←→  Knoten B (PLCA ID 1)
```

In the PLCA cycle, both alternate. Wait times are on average symmetrically distributed. The standard Pdelay assumption `delay = round_trip / 2` works.

### With 3+ Nodes: Asymmetric

```
Knoten A (ID 0)  ←→  Knoten B (ID 1)  ←→  Knoten C (ID 2)
```

PLCA cycle:
- Slot 0: A is allowed to send
- Slot 1: B is allowed to send
- Slot 2: C is allowed to send
- Repeat

#### Example: A measures Pdelay to C

**Step 1:** A sends `Pdelay_Req` to C
- A waits on average half a cycle for slot 0
- A sends, C receives

**Step 2:** C replies with `Pdelay_Resp`
- C must wait from slot 2 to slot 0 — almost a full cycle
- C sends

**Result:**

```
A → C:  ~0,5 Slots Wartezeit + Übertragungszeit
C → A:  ~2 Slots Wartezeit + Übertragungszeit
```

Standard gPTP computes `path_delay = round_trip / 2`. This assumption is wrong because the directions have different wait times.

### The Asymmetry Scales

**With 8 nodes (T1S maximum):**

```
PLCA-Zyklus-Dauer:                30-50 µs (typisch)
Maximale Slot-Wartezeit:           ~7 × Slot-Zeit
Asymmetrie zwischen ID 0 und 7:   bis zu 50 µs
```

PTP target: < 1 µs accuracy. 50 µs of asymmetry would be 50x too large.

**Without Annex H compensation, PTP on multidrop T1S is unusable.**

---

## 5. Naive Compensation Is Not Enough

### The Simple Formula

If every node knows its own NodeID and the max NodeID, the expected slot wait time could in theory be computed:

```
slot_wartezeit(target, current) = 
    ((target - current + max_id + 1) MOD (max_id + 1)) × slot_dauer
```

**Example with 8 nodes, slot_dauer = 20 µs:**

A (ID=0) sends to C (ID=2):
- Wait time for A: ~0 (slot is right there)

C replies to A:
- Wait time for C: (0 - 2 + 8) mod 8 = 6 slots = 120 µs

Compensation:
```
echter_path_delay = (round_trip - bekannte_PLCA_wartezeiten) / 2
```

### Why the Simple Formula Is Insufficient

The static calculation is only an **upper bound**, not the true value.

#### Problem 1: Slot Skipping

PLCA skips empty slots:

```
Konfiguriert: A-B-C-D-E-F-G-H = 8 Slots × 20µs = 160µs Zyklus

Wenn B+D+F nichts senden:
Effektiv: A-C-E-G-H = 5 Slots × 20µs = 100µs Zyklus
```

From G's perspective, the effective slot position has shifted.

#### Problem 2: Burst Mode

PLCA allows multiple frames per slot (`max_burst_count`). Variable utilization changes the cycle duration.

#### Problem 3: Variable Bus Utilization

Sometimes all nodes are active, sometimes only two. The theoretical calculation then does not match exactly.

#### Problem 4: Beacon Timing

The PLCA beacon from the coordinator and the cycle start can differ. On beacon loss, the phase can shift.

---

## 6. What Annex H Actually Defines

Annex H is **more realistic** than the simple formula and uses several mechanisms in combination:

### 6.1 PLCA Beacon Timestamp

- The beacon time is used as an additional reference
- Pdelay is referenced to the beacon, not to the absolute SFD
- Reduces the dependency on slot skipping

### 6.2 Cycle Time Tracking

- Mechanisms to estimate the **current** cycle duration
- Takes active nodes and burst counts into account in real time

### 6.3 Conservative Filtering

- Pdelay values with high variance are discarded
- Statistical filtering across multiple measurements
- Converges on the stable component of the latency

### 6.4 Per-Hop Approach

- Pdelay is measured between direct neighbors
- Instead of end-to-end, which is unclearly defined on multidrop
- PLCA effects are easier to control per hop

### 6.5 NodeID-Dependent Bias Compensation

- Takes the PLCA NodeID position into account
- Estimates expected slot wait time based on observation

---

## 7. What the Hardware PHY Must Provide

For a true Annex H implementation, runtime information from the PHY is required.

### LAN8651 Registers (Example)

| Register Type | Information |
|--------------|-------------|
| PLCA status | Current slot, NodeID, burst count |
| Beacon detection | Timestamp of the last beacon detection |
| PLCA statistics | Counted slots, empty slots, burst events |
| TSU register | SFD timestamps (TX/RX) |

### What the Current Project Uses

In the 2-node setup, the standard TSU registers are used:
- `MAC_TSH` / `MAC_TSL` for SFD timestamps
- TTSCAA bit for TX capture confirmation

The PLCA status registers are not read because, with 2 nodes, no Annex H compensation is needed.

---

## 8. Practical Implementation Strategies

### Variant 1: Static Worst-Case Assumption

Use the simple formula:
```
echter_delay = round_trip / 2 - statische_PLCA_kompensation
```

**Applicable when:**
- Topology and traffic patterns are stable
- Requirement is "good enough" (10-50 µs accuracy)
- Implementation effort must remain minimal

**Not applicable when:**
- Sub-µs accuracy is required
- Bus utilization fluctuates strongly
- Nodes come and go

### Variant 2: Statistical Averaging

Average over many Pdelay measurements. Slot skipping averages out when traffic is stationary.

**Advantages:** Easy to implement, works without PLCA status registers

**Disadvantages:** Long convergence time (seconds to minutes), poor reaction to topology changes

### Variant 3: Full Annex H Implementation

Read PLCA status per frame, use beacon timestamps, implement cycle tracking.

**Advantages:** True sub-µs accuracy on multidrop is possible

**Disadvantages:** High implementation effort, dependent on hardware support

---

## 9. Application to the Current Project

### Current State: 2 Nodes

In the current `net_10base_t1s` project, only 2 nodes are involved. This means:

- Asymmetry is on average zero
- Standard `delay = round_trip / 2` works
- No Annex H compensation needed
- Implementation follows IEEE 1588-2008 (PTPv2)

Achieved accuracy: < 50 ns mean offset, < 200 ns worst case (measured with `ptp_offset_capture.py`).

### When Extending to 3+ Nodes

For a multidrop configuration with 3+ nodes, the following additions would be required:

#### Minimum Implementation

1. **Read PLCA status** from LAN8651 registers
   - Own NodeID
   - Max NodeID
   - Slot time (`to_timer`)

2. **Extension of the `processFollowUp()` function**
   ```c
   pdelay_komp = berechne_plca_wartezeit(meine_id, gm_id, max_id, slot_dauer);
   echter_delay = (round_trip - pdelay_komp) / 2;
   ```

3. **Best Master Clock Algorithm (BMCA)**
   With more than 2 nodes, it must be decided who is grandmaster.

#### Full Annex H Implementation

4. **Beacon tracking**
   Evaluate beacon timestamps from PLCA status

5. **Cycle duration tracking**
   Continuously measure the current cycle duration

6. **Burst count consideration**
   Factor variable slot occupancy into compensation

7. **Conservative filtering**
   Discard Pdelay measurements with high variance

---

## 10. Summary

### The Two Main Problems With T1S+PTP

**Problem 1: Timestamp position**
- Solution: Hardware timestamp at SFD (correctly implemented in the LAN8651)
- Already solved, no software adaptation needed

**Problem 2: PLCA slot asymmetry**
- Only occurs with 3+ nodes
- Cannot be solved by hardware alone
- Requires Annex H algorithms in software

### What Is Sufficient for 2 Nodes

- Standard PTPv2 (IEEE 1588-2008)
- Hardware timestamping at the SFD
- No slot asymmetry compensation

### What Is Additionally Required for 3+ Nodes

- IEEE 802.1AS-2020 Annex H mechanisms
- Runtime observation of PLCA dynamics
- Minimum: NodeID-based compensation
- Optimal: Beacon tracking + cycle tracking + filtering

### Static Information Is Not Enough

NodeID and max NodeID provide only a worst-case estimate. True compensation requires runtime observation of:

- Actual slot occupancy
- Empty-slot skipping
- Burst count variations
- Beacon phase shift

---

## 11. Accuracy Comparison

| Configuration | Achievable Accuracy |
|---------------|------------------------|
| 2 nodes, standard PTPv2 + HW timestamping | < 1 µs |
| 8 nodes, no compensation | 50-150 µs |
| 8 nodes, static ID compensation | 5-20 µs |
| 8 nodes, full Annex H implementation | < 1 µs |

---

## References

- IEEE 802.1AS-2020 (gPTP with Annex H)
- IEEE 802.3cg-2019 (10BASE-T1S + PLCA)
- IEEE 1588-2008/2019 (PTPv2)
- Microchip AN1847 (PTP over 10BASE-T1S)
- LAN8651 Datasheet (TSU + PLCA status registers)

---

**Created:** 2026-04-26
