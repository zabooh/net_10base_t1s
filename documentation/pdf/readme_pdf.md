# PDF & Presentation Documentation Reference

Microchip PDFs and presentation decks collected for reference during
the T1S + PTP work on this fork.  They are *not* redistributable
verbatim — Microchip retains copyright on these documents.  This
index exists so a reader (human or LLM) can quickly know **which PDF,
which section, and approximately which page** contains a given piece
of information *before* opening the document.

When in doubt about a document's currency, search the document number
on [microchip.com](https://www.microchip.com) — the company
periodically issues newer revisions and the local copies in this
directory may lag.

---

## 1. Primary PTP / TSU references — LAN8650/1 (the project's MAC-PHY)

These are the canonical sources for the chip used in this project's
hardware (SAM E54 + LAN8651 Two-Wire ETH Click).

### LAN8650-1-Time-Synch-AN-60001847.pdf

| | |
|---|---|
| Document | AN60001847 — *LAN8650/1 Time Synchronization Application Note* |
| Length | 22 pages, revision DS60001847C, June 2025 |
| Use here | **The single most important document for PTP on T1S.**  Documents the Time Stamp Unit (TSU), the timestamp register pair (MAC_TSH / MAC_TSL), the timer increment register (MAC_TI), and the LAN8651's gPTP-relevant features.  Cross-referenced from `documentation/ptp/plca_ptp_asymmetrie.md`, `documentation/ptp/README_cross.md`, `documentation/ptp/readme_results.md`, `documentation/ptp/readme_upgrade.md`, and `documentation/ptp/readme_acma.md`. |
| Summary | Time-synchronization architecture for 10BASE-T1S multidrop using LAN8650/1.  **§1 Introduction (p.4-5)** — defines time-aware-bridge / time-aware-endpoint terminology, gives Figure 1-1 (full-802.1AS network with bridges) and Figure 1-2 (Microchip's "minimally time aware" mixing segment with one TSI-capable controller and several follower-only nodes).  **§2 Using PTP Messages for Time Synchronization (p.6-11)** — derives propagation-delay and clock-error equations (Equation 2-1 and 2-2 on p.7), explicitly calls out that current PTP standards do not yet cover multidrop / PLCA broadcast Pdelay (p.7) and recommends timestamping at the PHY (after the elastic buffer) rather than at the MAC; covers the wall-clock / oscillator block diagram (Figure 2-2, p.8); shows simulated clock-error profiles for time-direct update (Figure 2-3, p.9), one-time phase adjust (Figure 2-4, p.10), and increment-update (Figure 2-5, p.10); discusses PI/PID servo strategies (p.11).  **§3 LAN8650/1 Support for Time Synchronization (p.12-14)** — Figure 3-1 (p.12) shows the wall-clock + packet-timestamp + event-capture + event-generator + 1PPS feature block; describes the 94-bit wall clock layout (48 s + 30 ns + 16 sub-ns; MAC_TI nominally 0x28 = 40 ns at 25 MHz; p.12); the Packet Pattern Matcher (p.13) anchoring TX/RX timestamps at end-of-SFD; Event Capture (p.14) up to 4 events on DIOA0-3; Synchronized Event Generator (p.14) with 1PPS available on DIOA4 with pulse width 640 ns to 20.48 µs.  **§4 Implementing a Simple Clock Follower (p.15-18)** — Figure 4-1 (p.15) is the simple multidrop reference; Software Configuration (p.15-16) gives the exact register writes (RXMMSKH = 0xFFFFFF, RXMLOC = 0, MAC_TI = 0x28, FTSE bit + FTSS bit in OA_CONFIG0); Synchronizing the Clock describes the 4-state servo (Init / Unlocked / Locked-Coarse / Locked-Fine, p.16-17); Figure 4-2 (p.17) shows the clock-synchronization SW+HW block diagram; **Results (p.17-18)** — measured 100 ns peak-to-peak / 25 ns σ / 8 ns mean against a co-located grandmaster on 50 cm UTP using SAM D21 Curiosity Nano + LAN8651 Two-Wire ETH Click; Figure 4-3 (p.18) photo of the eval hardware.  Companion sample code at github.com/MicrochipTech/LAN865x-TimeSync. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN8650-1-Time-Synch-AN-60001847.pdf |
| Read first if | you are starting any PTP / Annex H / AN1847-style implementation work |

### LAN8650-1-Data-Sheet-60001734.pdf

| | |
|---|---|
| Document | DS60001734 — *LAN8650/1 Data Sheet* |
| Length | 370 pages, revision DS60001734F, 2025 |
| Use here | Authoritative reference for every register address used in `drv_lan865x_api.c::TC6_MEMMAP[]`, every PLCA control bit, every MAC layer behaviour.  The source for ACMA + EG0 + TSU mechanics that drive `documentation/ptp/readme_acma.md`. |
| Summary | Full datasheet for the LAN8650/1 10BASE-T1S MAC-PHY with SPI (32-pin VQFN, AEC-Q100, -40 to +125 °C).  Major sections — **§1 Preface (p.6-8)**: General Terms, Buffer Types, Register Bit Types, Reference Documents.  **§2 Introduction (p.9-12)**: feature matrix between LAN8650 (no LDO) vs LAN8651 (with internal 1.8 V LDO).  **§3 Pin Descriptions (p.13-20)**: pinouts, DIO function table.  **§4 Global Functional Descriptions (p.21-52)** — §4.1 Reset and Startup (p.21), §4.2 Clock Manager (p.24), §4.3 Interrupt Sources (p.24), §4.4 Sleep Mode (p.25), and the critical **§4.5 Synchronization Support (p.31-42)**: §4.5.1 Wall Clock 94-bit (48 s + 30 ns + 16 sub-ns; MAC_TSH/L/N + MAC_TI = 0x28; p.32); §4.5.2 Packet Timestamps with PTP message format and PHY pattern-matcher path (p.32-35; explicit warning on p.34 that delay through the PHY is variable when PLCA is enabled — must use MDI signal for timestamping); §4.5.3 Event Capture up to 4 timestamps (p.36-37, Figures 4-7 and 4-8 register allocation); §4.5.4 Synchronized Event Generator (p.38-39, EG0CTL with REP / ACTHI / START / ISREL bits; Figures 4-9 single-pulse and 4-10 repeating-pulse timing); §4.5.4.2 1PPS on DIOA4 via PPSCTL (p.40); §4.5.5 Phase Adjuster PACTRL + PACYC (p.40-41, three usage strategies for clock-update smoothing).  §4.6 Safety Features (p.42-52) — under-voltage detection, FMEDA support.  **§5 SPI (p.53-64)** — §5.1 SPI Format (p.53); §5.2 MAC Frame Data Transactions (p.53-61), including OA-TC6 chunked-frame format and the footer with RTSP (Receive Timestamp Parity) + SV (Start Valid) + RTSA bits, plus FTSE / FTSS bits in OA_CONFIG0; §5.3 Control Transactions (p.62-64).  **§6 Ethernet MAC (p.65-73)** — frame filtering, programming interface.  **§7 Integrated PHY (p.74-86)** — §7.1 Interrupts (p.74); **§7.2 PLCA (p.74-79)**: §7.2.1 Burst Mode with PLCA_BURST.MAXBC and BTMR (p.76); §7.2.2 Multiple TOs per Node via MULTID0-3 (p.77); §7.2.3 PLCA TO Skipping via PLCASKPCTL.TOSKPEN + PLCATOSKP (p.77); §7.2.4 PLCA Diagnostics with UNEXPB / RXINTO / TXCOL / BCNBFTO status bits (p.78-79); §7.2.5 Transmit Collisions (p.79).  **§7.3 ACMA (p.80-81)**: ACMACTL.ACMAEN, PADCTRL.ACMASEL = DIO2 or EG0; minimum 10 µs pulse width recommended; UNCRS bit in STS1 used as integrity indicator.  §7.4 Credit-Based Traffic Shaping (p.81-83) with full register list (CBSCTRL, CBSSTTH/L, CBSSPTH/L, CBSSLPCTL, CBSTPLMTH/L, CBSBTLMTH/L, CBSCRCTRH/L); restriction: not to be combined with PLCA burst mode or TO skipping.  §7.5 SQI (p.83-84).  §7.6 Cable Fault Diagnostics (p.85-86).  **§8 Application Information (p.87-98)** — schematic / EMC / crystal selection.  **§9 Operational Characteristics (p.99-112)** — DC and AC specs.  **§10 Packaging (p.113-116)**.  **§11 Register Descriptions (p.117-364, ~250 pages)** — split into MMS banks: §11.1 OA Standard Registers MMS 0 (p.118-156, includes OA_CONFIG0, OA_STATUS0/1, OA_IMASK0/1); §11.2 MAC Registers MMS 1 (p.157-200, includes MAC_TSH / MAC_TSL / MAC_TN / MAC_TI / MAC_TISUBN at 0x0070-0x006F); §11.3 PHY PCS Registers MMS 2-9 (p.201-205); §11.4 PHY PMA/PMD Registers (p.206-212); §11.5 PHY Vendor Specific Registers (p.213-285, includes PLCA_CTRL0 / PLCA_CTRL1 / PLCA_TOTMR / PLCA_BURST / PLCA_STS); §11.6 Miscellaneous Register Descriptions (p.286-364, includes TSU registers, Event Capture/Generator registers, DIO PADCTRL, ACMACTL, PPSCTL, plus DEVID at 0x0094 referenced by erratum s1).  **§12 Revision History (p.365)**. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/DataSheets/LAN8650-1-Data-Sheet-60001734.pdf |
| Read first if | you need to verify a specific register meaning, bit layout, electrical spec, or to look up ACMA / PLCA / TSU register addresses |

### LAN8650-1-Configuration-Appnote-60001760.pdf

| | |
|---|---|
| Document | AN60001760 — *LAN8650/1 Configuration Application Note* |
| Length | 10 pages, revision DS60001760G, June 2024 |
| Use here | Walks through the post-reset register-write recipe and PLCA configuration flow.  Background for understanding **why** `_InitMemMap` and `_InitUserSettings` write the values they do, and for designing the Annex H register-init state machine in `ptp_drv_ext.c::PTP_DRV_EXT_Tasks`. |
| Summary | Concise post-reset configuration recipe for LAN8650/1 silicon revisions B0 (PHY_ID 0x0001) and B1 (PHY_ID 0x0010).  **Page 1 — Introduction**: defines silicon revision table.  **Page 1-2 — Accessing Configuration Registers**: SPI primitives `read_register(mms, addr)`, `write_register(mms, addr, value)`, and the proprietary `indirect_read(addr, mask)` mechanism using register pair 0x04/0x0008 for trim values.  **Page 2 — Calculation of Configuration Parameters**: pulls value1 from MMS 4 / 0x0004 and value2 from MMS 4 / 0x0008 as signed-5-bit; sign-extends them to offset1 / offset2; then packs `cfgparam1 = ((9+offset1) & 0x3F) << 10 | ((14+offset1) & 0x3F) << 4 | 0x03` and `cfgparam2 = ((40+offset2) & 0x3F) << 10`.  **Page 2-3 — Table 1 Configuration Register Writes**: the canonical fixed sequence of ~20 register writes (mostly MMS 4, plus MMS 1) — values like 0x00D0=0x3F31, 0x00E0=0xC000, 0x00F4=0xC020, 0x00F8=0xB900, 0x00F9=0x4E53, 0x0081=0x0080, plus MMS 1 / 0x0077 = 0x0028 (sets MAC_TI to 40 ns at 25 MHz) and pattern-matcher / FTSE setup.  **Page 3 — Table 2 SQI Configuration Register Writes** (optional): cfgparam3-5 calculation and the SQI register block (MMS 4 / 0x00AD-0x00BB).  **Page 3-5 — PLCA Configuration Process**: §Enabling PLCA describes plcaparam1 = (Node_Count<<8) for coordinator (Node_ID=0) or plcaparam1 = Node_ID for follower; Table 3 (p.4) is the PLCA register-write sequence (PLCA_CTRL1 ← plcaparam1 at 0xCA02; PLCA_CTRL0 ← 0x8000 at 0xCA01; CDCTL0 read-modify-write at 0x0087 to clear bit 15 / CDEN); §Managing Collision Detection (p.4-5) with Figure 1 PLCA Collision Detect Flowchart; §Disabling PLCA via Table 4 (p.5).  Specific values 0xB900 and 0x80 in the project's `_InitMemMap` array trace back to this table directly. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN8650-1-Configuration-Appnote-60001760.pdf |
| Read first if | you are touching the chip's init sequence or PLCA setup |

### LAN8650-1-Errata-80001075.pdf

| | |
|---|---|
| Document | ER80001075 — *LAN8650/1 Errata and Data Sheet Clarifications* |
| Length | 5 pages, revision DS80001075F, 2025 |
| Use here | Documents known silicon issues for the B1 revision used in this project.  **Critical for the ACMA + EG0 architecture** in `documentation/ptp/readme_acma.md`: erratum s9 forces single-shot rather than periodic-mode Event Generators when the wall clock is PTP-controlled.  Erratum s7 dictates explicit PLCA-beacon disable when transitioning from PLCA to ACMA. |
| Summary | Combined errata for LAN8650/LAN8651 silicon revisions B0 (0x0001) and B1 (0x0010), nine items s1-s9 listed in Table 1-1 (p.2).  **§1.1 s1 — Determining Product ID and Hardware Revision (p.2)**: standard OA_PHYID register identifies only the integrated PHY block, not the MAC-PHY product; read DEVID at MMS 10 / 0x0094 instead.  **§1.2 s2 — Useless RX data block following early CS_N de-assertion (p.2)**: B0 only, resolved in B1.  **§1.3 s3 — SPI receive Ethernet frame transfer halt (p.2)**: B0 only, resolved in B1.  **§1.4 s4 — Packet TX halts on excessive collisions (p.2-3)**: TX may lock up if MAC must drop a frame due to excessive collisions; mitigated by proper PLCA configuration (no collisions) or by configuring SPI host to send only one frame per chunk in pure CSMA/CD mode.  **§1.5 s5 — Multi-coordinator PLCA action (p.3)**: when PHY receives an unexpected BEACON from a second coordinator, it sets UNEXPB in STS1 and enters a recovery state where it can RX but neither TX nor BEACON for 2 PLCA bus cycles; software must monitor UNEXPB and reconfigure the PHY as a follower per Clause 148.  **§1.6 s6 — SLPCAL field of SLPCTL0 may deliver invalid result on read (p.3)**: SLPCAL reads back as 0x0001 but must always be written as 0x0000 (read-modify-write must explicitly mask SLPCAL to 0).  **§1.7 s7 — PLCA coordinator does not stop transmitting beacons immediately on entering sleep (p.4)**: NODE_ID = 0 must never be configured to sleep on inactivity timers; before entering sleep the coordinator must clear PLCA_CTRL0.EN to disable the beacon, then set SLPEN/WKINEN/MDIWKEN appropriately, write SLPCAL = 0, and set SLPINHDLY ≥ 1.  **§1.8 s8 — Trade-off between noise immunity and carrier sense latency (p.4)**: TO_TMR (PLCA_TOTMR) must be configured equally across all nodes on the segment; default 32 (= 3.2 µs at 100 BT) is recommended; values < 29 risk reduced robustness; values > 32 may be needed in noisy environments with third-party devices on the bus.  **§1.9 s9 — Event Generator drifts relative to synchronized wall clock in periodic mode (p.5)**: in repeat mode (`EG0CTL.REP = 1`) the first pulse is wall-clock-aligned but subsequent pulses are tied to the local 25 MHz reference and ignore wall-clock corrections; workaround is to use single-shot (`REP = 0`) and re-arm in software for every pulse — this is the foundation of the ISR-driven re-arm pattern in `readme_acma.md` §7.6. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/Errata/LAN8650-1-Errata-80001075.pdf |
| Read first if | a register write does not behave as the datasheet suggests, or before validating new register-init values on real hardware, **and definitely before implementing ACMA + EG0** |

---

## 2. LAN8670/1/2 family references (separate-PHY siblings of LAN8650/1)

These documents cover the **standalone PHY** variants from Microchip
(LAN8670, LAN8671, LAN8672) which are *related to but distinct from*
the integrated MAC-PHY LAN8650/1 used in this project.  Useful as
secondary reference, especially for topology discovery (whose
hardware is identical between the families) and EVB/RMII reference
designs that some test setups use.

### LAN8670-1-2-Data-Sheet-60001573.pdf

| | |
|---|---|
| Document | DS60001573 — *LAN8670/1/2 10BASE-T1S Ethernet PHY Transceiver Data Sheet* |
| Length | 279 pages, revision DS60001573K, 2025 |
| Use here | Authoritative reference for the standalone PHY variants.  Useful when topology-discovery hardware is examined (it lives natively in this PHY family per AN6067) and when comparing the integrated-MAC LAN8650/1 against the standalone-PHY siblings.  Not directly used by this project's firmware — but the underlying 10BASE-T1S PHY core is shared with LAN8650/1. |
| Summary | Full datasheet for LAN8670 (32-VQFN), LAN8671 (24-VQFN), and LAN8672 (36-VQFN, C2 only) standalone 10BASE-T1S PHY transceivers with MII / RMII / SMI host-side interfaces.  Major sections — **§1 Preface (p.5-7)**: terms (BIN, SC-MII, SSD = Start-of-Stream Delimiter, etc.); buffer types; register-bit notations; reference documents (notably IEEE Std 802.3-2022, OPEN Alliance v1.4 Topology Discovery, OPEN Alliance v1.4 PLCA Management Registers, RMII v1.2).  **§2 Introduction (p.8-11)**: family overview, example systems.  **§3 Pin Description (p.12-25)**: per-package pinouts (LAN8670 §3.1, LAN8671 §3.2, LAN8672 §3.3); pin descriptions §3.4 (p.17); configuration straps §3.5 (p.23).  **§4 Functional Descriptions (p.26-78)** — §4.1 MII (p.26), §4.2 RMII (p.27), §4.3 SMI (p.28), §4.4 Interrupts (p.30), §4.5 Resets (p.31), §4.6 Initialization (p.31), §4.7 Clock Manager (p.32), **§4.8 PLCA (p.33-38)**, **§4.9 ACMA (p.39-40)**, §4.10 Credit Based Traffic Shaping (p.41-42), §4.11 Configuration Protection (p.43), **§4.12 Time Synchronization (p.44-47)** (analogous to LAN8650/1 TSU but on standalone PHY — note this PHY uses the host-MII for the upper-layer MAC), §4.13 Sleep Mode Rev C2 (p.48), §4.14 Sleep Mode Rev D0 (p.56), **§4.15 Topology Discovery Rev D0 (p.63-65)** — TD_CTRL / TD_STAT / TD_DLY_RES / TD_DIST_RES register definitions live here, §4.16 SQI Rev C2 (p.66), §4.17 DCQ SQI Rev D0 (p.68), §4.18 Cable Fault Diagnostics Rev C2 (p.72), §4.19 Harness Defect Detection Rev D0 (p.72), §4.20 Safety Notifications (p.74), §4.21 Link Status Overview Rev D0 (p.76).  **§5 Register Descriptions (p.79-225)** — §5.1 SMI Basic Control and Status (p.80); §5.2 PMA/PMD Registers (p.94); §5.3 PCS Registers (p.103); §5.4 Miscellaneous Registers (p.108-225, includes PLCA_CTRL0/1, PLCA_TOTMR, PLCA_BURST, ACMACTL, all TD_* registers).  **§6 Application Information (p.227-243)** — §6.1 MII Connectivity (p.227), §6.2 RMII Connectivity with Reference Clock (p.228), §6.3 System Configuration without Sleep Mode (p.228), §6.4 Power Connectivity (p.229), §6.5 EMC Considerations (p.232), §6.6 Crystal Oscillator Selection (p.233), §6.7 Reference Schematics (p.234).  **§7 Operational Characteristics (p.245-263)** — DC/AC specs.  **§8 Packaging (p.265-273)**.  Cross-reference pattern: when AN6067 says "TD_CTRL.REFN" without a register address, the address is in §5.4 of this datasheet. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/DataSheets/LAN8670-1-2-Data-Sheet-60001573.pdf |
| Read first if | you are designing a hardware variant with a separate MAC + LAN8670/1/2 PHY, or cross-referencing topology discovery details |

### LAN8670-1-2-Configuration-Appnote-60001699.pdf

| | |
|---|---|
| Document | AN60001699 — *LAN8670/1/2 Configuration Application Note* |
| Length | 6 pages, revision DS60001699G, 2025 |
| Use here | LAN8670/1/2-specific init recipe — analogous to AN60001760 for LAN8650/1.  Background reading for someone cross-comparing the two chip families. |
| Summary | Configuration recipe for LAN8670/1/2 silicon revisions C2 (0x0101) and D0 (0x0110).  **Page 1 — Introduction**: silicon revision table.  **Page 2 — Accessing Configuration Registers**: SMI primitives `read_register(mmd, addr)`, `write_register(mmd, addr, value)`, and the proprietary `indirect_read(addr, mask)` using register pair 0x1F / 0x00D8 / 0x00DA / 0x00D9 — a different MMD than LAN8650/1.  **§2 Revision D0 and later — Configuration Process (p.3-4)** — §2.1 Table 2-1 contains 9 register writes (e.g. MMD 0x1F / 0x0037 = 0x0800; 0x008A = 0xBFC0; 0x0118 = 0x029C; 0x00D6 = 0x1001; 0x0082 = 0x001C; the LSCTL register at 0x0012 introduced in D0); §2.2 PLCA Configuration with same logic as LAN8650/1 (Node_ID = 0 → coordinator with plcaparam1 = Node_Count << 8; Table 2-2 lists the PLCA register writes including CDCTL0 read-modify-write).  **§3 Revision C2 — Configuration Process (p.5-6)** — §3.1 Calculation of Configuration Parameters: identical sign-extend trick as LAN8650/1 AN1760; §3.2 Table 3-1 lists 11 register writes for C2 silicon (similar to LAN8650/1 Table 1 but different addresses — note 0x00D0 = 0x3F31 and 0x00E0 = 0xC000 are the same; 0x0084 = cfgparam1, 0x008A = cfgparam2); §3.3 Table 3-2 SQI Configuration (15 writes to MMD 0x1F / 0x00AD-0x00BB); §3.4 PLCA Configuration Process Rev C2.  Cross-reference: identical macro-pattern as AN1760 but different register addresses because the LAN8670/1/2 has a different MMD/MMS layout (uses MMD 0x1F instead of MMS 4). |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN8670-1-2-Configuration-Appnote-60001699.pdf |
| Read first if | you are setting up a LAN8670/1/2-based platform |

### LAN8670-1-2-Errata-80000962.pdf

| | |
|---|---|
| Document | ER80000962 — *LAN8670/1/2 Errata and Data Sheet Clarifications* |
| Length | 5 pages, revision DS80000962G, 2025 |
| Use here | Errata for the standalone PHY family.  The LAN8670/1/2 has **12 errata items s1-s12**, more than the LAN8650/1 (9 items).  Items differ between the families because the silicon designs differ; consult only when working with LAN8670/1/2 silicon. |
| Summary | Combined errata for LAN8670 / LAN8671 / LAN8672 across silicon revisions B1 / C1 / C2 / D0 (Table 1, p.1).  Items in Table 1-1 (p.3): **s1** Media Interface Mode (RMII) Identification — resolved C1 onwards.  **s2** Package Type Identification — LAN8671 only, resolved C1.  **s3 (p.3) RMII CSMA/CD operation in mixed PLCA segments**: LAN8670/1 RMII cannot be operated with PLCA disabled on a network with other PLCA-enabled nodes (BEACON/COMMIT symbols are improperly transferred via RMII causing dropped packets); LAN8672 doesn't support RMII at all; resolved with D0.  **s4** Incorrect reset indication on IRQ_N — resolved C1.  **s5 (p.4) Multi-coordinator PLCA action**: same mechanism as LAN8650/1 erratum s5 — UNEXPB bit in STS1, recovery state, software must monitor and reconfigure as follower.  **s6 (p.4) Transmission of collision fragments with PLCA and RMII**: applies to ALL revisions including D0; if MAC's IPG part 1 is shorter than 18 BT, MAC can transmit during carrier-asserted time and the PHY (not expecting it) will not detect the resulting collision and will forward the fragment to the network; workaround uses RMW write to MMD 0x1F / 0x008F = 0x00E0 with mask 0x07F0.  **s7** RMII incorrect carrier sense after collision — resolved C2.  **s8** Revert to CSMA/CD when PLCA Beacons missing — resolved C1.  **s9** Packet pattern matcher matches all message types — resolved C1.  **s10 (p.5) SLPCAL field of SLPCTL0 register may deliver invalid result on read**: same as LAN8650/1 erratum s6 — must always be written as 0; resolved D0.  **s11 (p.5) Coordinator does not stop transmitting beacons on entering sleep**: same mechanism as LAN8650/1 erratum s7 — NODE_ID = 0 must not sleep on inactivity timers; PLCA must be disabled before sleep entry.  **s12** Noise immunity / carrier sense latency trade-off: same as LAN8650/1 erratum s8 — TO_TMR ≥ 29 (default 32). |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/Errata/LAN8670-1-2-Errata-80000962.pdf |
| Read first if | you are validating register writes on LAN8670/1/2 hardware, or comparing errata between the two Microchip 10BASE-T1S families |

### LAN8670-1-2-Hardware-Design-Checklist-UG-60001745.pdf

| | |
|---|---|
| Document | UG60001745 — *LAN8670/1/2 Hardware Design Checklist* |
| Length | user guide, revision DS60001745D, 2024 |
| Use here | Hardware-design checklist for boards using the standalone PHY.  Most items (power decoupling, MDI termination, EMC, supply-sequencing) generalise to LAN8650/1 designs and to any T1S board layout. |
| Summary | Pin-by-pin and rail-by-rail checklist for designing a LAN8670/1/2 board.  Conformity table (p.2): C1 = silicon Rev 4 (0100b), C2 = silicon Rev 5 (0101b), checklist revision DS60001745D.  **§1 Introduction (p.1)** lists the 13 sections.  **§2 Conformity (p.2)**: Table 2-1 silicon-to-checklist mapping.  **§3 General Considerations (p.2)** — §3.1 Pin Check.  **§4 Power and Ground (p.2-5)** — §4.1 Power: VDDP / VDDA / VDDAU pinning per package (Table 4-1, p.2); decoupling rules (10 µF bulk + 0.1 µF + 0.01 µF per VDD pin; closest 0.01 µF first); ferrite-bead options (Figures 4-3 and 4-4 show isolated VDDP/VDDA/VDDAU power islands); supply-sequencing constraint (VDDA must never exceed VDDAU by more than 0.5 V; optional Schottky diode between 3.3Vsw and 3.3Vcont).  §4.2 Ground: ePAD via array.  **§5 Clock Circuit**, **§6 Ethernet PHY External BIN**, **§7 MII / SC-MII / RMII** with §7.1-7.3 per interface, **§8 SMI**, **§9 Start-Up** (§9.1 Reset, §9.2 Configuration Pins), **§10 Power Management Pins**, **§11 Application Pins**, **§12 Miscellaneous** (§12.1 RBIAS, §12.2 IRQ_N, §12.3 Do Not Connect Pins), **§13 Hardware Checklist Summary** at the end is a one-page sign-off list.  Note that LAN8650/1 has different pinout but identical decoupling and BIN principles, so this checklist's power/EMC items translate directly. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/UserGuides/LAN8670-1-2-Hardware-Design-Checklist-UG-60001745.pdf |
| Read first if | you are laying out a 10BASE-T1S board (either MAC-PHY or PHY-only) — the checklist works as a sign-off document at PCB review time |

---

## 3. Topology discovery (relevant to multi-node Annex H scenarios)

### LAN86xx-topology-discovery-AN-00006067.pdf

| | |
|---|---|
| Document | AN00006067 — *Topology Discovery for 10BASE-T1S Systems* |
| Length | 12 pages, revision DS00006067B, October 2025 (public release) |
| Use here | Describes how nodes discover each other on a shared T1S PLCA bus.  Directly relevant to per-pair path-delay calibration in `readme_acma.md` and to the Annex H Phase 6 work in `documentation/ptp/plca_ptp_asymmetrie.md` §12.2. |
| Summary | OPEN Alliance 10BASE-T1S Topology Discovery procedure (spec v1.4) implemented on LAN8670/1/2 D0+ hardware — note that the title says LAN86xx but the example nodes are LAN8670/1/2 PHYs, *not* the LAN8650/1 MAC-PHY used in this project.  **§1 References (p.3)** — IEEE 802.3-2022, OPEN Alliance Topology Discovery v1.4, LAN8670/1/2 datasheet DS60001573.  **§2 Introduction (p.4)** — distance is derived from time-of-flight measurement; the process requires coordination of all nodes on the segment with two designated "reference" and "measured" nodes; no other node may transmit during measurement.  **§3 Theory of Operation (p.5-7)** — System Configuration; **Hardware Block Diagram Figure 3-1 (p.5)** shows the alternate digital-PHY signal routing (scrambler / 1B2B encoder / serializer feeding the PMD transceiver, with a delay block guaranteeing ≥100 ns internal delay; receive-side has deserializer / decoder / descrambler / Measure FSM / Noise Detection); first 60 pulses train the descrambler; reference node transmits 40-85 ns pulses, measured node responds.  **Distance Measurement Equation 3-1 (p.6)**: `dist[ns] = (duration[ms] × 10⁶[ns/ms] / pulses − int_dly_ref[ns] − int_dly_meas[ns]) / 2`.  **Internal Delay Equation 3-2 (p.7)**: `int_dly[ns] = duration[ms] × 10⁶ / pulses` — measured by the node looping back its own pulse through TX → MDI → RX → delay block.  Internal delay is always > 100 ns by design (so 85 ns pulse-width does not affect leading-edge detection).  **§4 Topology Discovery Example (p.8-10)** — Figure 4-1 (p.8) the four-node automatic-mode flow with roles Requesting Node / PLCA Coordinator / Reference Node / Measured Node.  Detail flow (p.9-10): coordinator must first disable PLCA either via PRSCTL1.FBEN bit or by temporarily setting its own NODE_ID to 254 (delay must be ≥ 3× measurement duration); PRSCTL1.FBEN bit must be cleared by reference and measured nodes too so they remain in PLCA mode regardless of beacon presence; reference and measured nodes write `TD_CTRL.REFN = 0` (measured) or `1` (reference); duration field `TD_CTRL.DM_DUR[3:0]` = 0..15 corresponds to 1-16 ms; set `TD_CTRL.AUTO_START` and `TD_CTRL.TD_EN`; results in `TD_DLY_RES_LOW/HIGH` and `TD_DIST_RES_LOW/HIGH`; status in `TD_STAT.DLYM_DONE` and `TD_STAT.DM_DONE`.  Manual mode briefly described for segments with many nodes (faster because reference internal delay only measured once).  Cross-reference: register addresses for TD_* registers are in DS60001573 §5.4. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-topology-discovery-AN-00006067.pdf |
| Read first if | you are implementing neighbour-discovery, per-hop Pdelay, or per-follower path-delay calibration |

### lan8670-1-topology-discovery-application-note-00006067a.pdf

| | |
|---|---|
| Document | AN00006067a — *Topology Discovery for 10BASE-T1S Systems* |
| Length | 11 pages, revision DS00006067A, August 2025 (NDA-watermarked predecessor of AN00006067) |
| Use here | Same topic as the LAN86xx note above, NDA-confidential earlier draft.  Useful for cross-checking interpretation. |
| Summary | The earlier (Aug 2025) NDA-confidential issue of what later became the public AN00006067.  Content is essentially identical: same theory-of-operation block diagram, same equations for distance and internal delay, same automatic-mode flow with the four-node example.  References OPEN Alliance Topology Discovery v1.0 spec (the public revision cites v1.4), LAN8670/1/2 datasheet DS60001573, and uses the same TD_* register naming.  Differences from the public revision: wording polish, v1.0 vs v1.4 spec reference, and a "Microchip Confidential — NDA" banner with a per-download personalised watermark.  For day-to-day reference the public revision (AN00006067) is preferable. |
| URL | search "DS00006067A" on microchip.com — this revision is NDA-confidential and not publicly hosted |
| Read first if | you want a second voice on topology discovery semantics |

> **Note:** A duplicate copy of the public revision exists locally as
> `LAN86xx-topology-discovery-AN-00006067 (1).pdf` — identical content,
> same MD5 as the un-suffixed file.  Either copy is fine; consider
> deleting one to avoid confusion.

---

## 4. Hardware design — board, layout, PoDL, noise

### LAN86xx-BIN-Ref-Design-Application-Note-60001718.pdf

| | |
|---|---|
| Document | AN60001718 — *LAN86xx Bus Interface Network (BIN) Reference Design Application Note* |
| Length | application note, revision DS60001718C, April 2024 (~3 MB) |
| Use here | Reference design for the bus-side network (transformerless coupling, MDI termination, ESD protection) shared by all LAN86xx 10BASE-T1S parts including LAN8650/1.  Directly relevant when designing a custom board or matching an existing dev kit's BIN. |
| Summary | BIN circuit recommendations for nodes on a 10BASE-T1S mixing segment (10 Mbit/s, single twisted pair, end-terminated bus topology, 100 Ω nominal differential impedance).  **Introduction (p.1)** distinguishes Drop Nodes (middle of the network) from End Nodes (segment ends).  **Chapter 1 Application Circuit (p.2-5)**: Figure 1-1 (p.2) Node Topology shows the bus with End Nodes at both ends and Drop Nodes in between (< 10 cm stub length).  **§1.1 Minimal BIN (p.3-4)**: Figure 1-2 component placement; Table 1-1 component selection — C1 / C2 = 0.1 µF 50 V (AC coupling caps, mandatory on every node); R1 / R2 = 49.9 Ω 1% 1206 (End Node bus-edge termination) **or** 1.5 kΩ 1206 (Drop Node common-mode termination, optional); C3 = 0.1 µF 50 V 0805; R3 = 100 kΩ 0805; CN1 = 2.54 mm header (e.g. SL-70551-0036).  **§1.2 Optimized BIN (p.5)**: Figure 1-3 adds a Common-Mode Choke (L1 = 130 µH or 240 µH at 100 kHz, e.g. ACT1210D-131-2P-TL00, ACT1210E-241-2P-TL00, DLW32MH241MX2) and ESD elements (varistor AVRH10C221KT1R5YA8 25 kV or TVS diode EZA-EG3W11AV).  Component sizing (1206 metric for R1/R2) is required for EMC robustness — **do not deviate**.  **Chapter 2 PCB Layout Guidelines** and **Chapter 3 PCB Layout Example** follow (not extracted in detail). |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-BIN-Ref-Design-Application-Note-60001718.pdf |
| Read first if | you are laying out the MDI side of a T1S board |

### LAN86xx-10BASE-T1S-Layout-Guide-Appnote-00006174.pdf

| | |
|---|---|
| Document | AN00006174 — *10BASE-T1S Layout Recommendation Guide* |
| Length | application note, revision DS00006174A, 2025 |
| Use here | PCB-layout guidance specific to 10BASE-T1S: trace lengths, return paths, EMC.  Complements AN60001718 (BIN reference design). |
| Summary | Layout recommendations for 10BASE-T1S boards based on LAN8650/1 and LAN8670/1/2 reference designs (also applicable to other 10BASE-T1S devices).  **§1 References (p.2)** — AN60001718 (BIN), DS60001734 (LAN8650/1), DS60001573 (LAN8670/1/2).  **§2 Layout Recommendations (p.3-?)** organised by theme: **Trace Layout** with goals (consistent impedance, crosstalk reduction by ≥5× trace-width spacing, minimised loop area); decoupling capacitor placement directly below the device with own GND pad and via — Figure 2-1 (p.3) shows optimal placement; clock-trace routing for RMII applications: oscillator close to PHY and MCU with matching trace lengths within ±500 mil and series resistor (Figure 2-2, p.4 shows correct vs incorrect routing); reduce interconnect trace lengths; route MII/RMII/SPI lines over a continuous reference plane; 50 Ω controlled impedance on MII/RMII/SPI; series resistor before device; ≥5× trace-width separation between BIN and MII/RMII/SPI; 90° crossings only when needed; ground stitching around SPI lines.  **BIN section** (after Trace Layout): goals are minimised parasitic capacitance and 50 Ω trace impedance.  **Power and Ground** plus **Component Selection** sections complete the document.  Cross-reference: schematic symbols in DS60001734 §8 / DS60001573 §6 are the reference for application-side connectivity. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-10BASE-T1S-Layout-Guide-Appnote-00006174.pdf |
| Read first if | you are doing PCB layout for a T1S board (MII/RMII/SPI host-side as well as the BIN/MDI side) |

### LAN86xx-Intrinsic-Noise-AN-60001829.pdf

| | |
|---|---|
| Document | AN60001829 — *Understanding System Intrinsic Noise — Enabling Longer Reach and More Nodes on 10BASE-T1S Systems* |
| Length | 22 pages, revision DS60001829B, 2023 |
| Use here | Quantifies and explains the intrinsic noise floor of LAN86xx 10BASE-T1S systems on the wire.  Useful when chasing PTP timestamp jitter sources, extending bus length beyond IEEE Std 802.3cg's 25 m / 8 nodes baseline, or evaluating EMC margins. |
| Summary | Background and analysis showing how the IEEE 802.3cg-2019 baseline (8 nodes, 25 m total bus length) can be extended to **up to 50 nodes / 50 meters with significant noise margin, or 2 nodes / 100 meters in a low-noise environment** — provided cable, connectors, and topology are appropriate.  **§1 Introduction (p.3)** frames the problem: the IEEE standard does not specify cable or connectors; inherent noise of the mixing segment determines reach and node count.  **§2 Noise Budget (p.4)**: Figure 2-1 graphical noise budget for a point-to-point link — TX amplitude at PHY pin, BIN/DFE losses, system impedance mismatch, max port-to-port cable loss, EMC-induced (differential / alien) noise, RX tolerance at PHY pin.  **§3 Inherent Noise in Mixing Segments (p.5-6)**: Figure 3-1 extends the noise budget with an additional "Mixed Segment Inherent Noise (Reflections)" block; explains that each Drop Node introduces a slight impedance mismatch and contributes a reflection of every signal that arrives, attenuation reduces signal amplitude, and reflections from each node cause more reflections — every received signal sees a different combination depending on which node is transmitting.  **§4 Cable Characteristics (p.7-10)**: cable types, frequency-dependent attenuation curves.  **§5 Starting Point: 5-Meter System with 2 Nodes (p.11-13)**: simulation/measurement baseline.  **§6 Longer Reach: 35-Meter System with 8 Nodes (p.14)**: extending IEEE baseline.  **§7 Increasing Node Count, Extending Reach (p.15)**: design rules for larger segments.  **§8 Conclusions (p.16)**.  Useful for any design that pushes beyond the IEEE 8-node / 25 m envelope. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-Intrinsic-Noise-AN-60001829.pdf |
| Read first if | you are debugging timestamp jitter, SQI variability, or evaluating extended-reach / extended-node-count topologies |

### LAN86xx-Using-Power-over-Data-Line-in-10BASE-T1S-Application-Note-60001848.pdf

| | |
|---|---|
| Document | AN60001848 — *Using Power over Data Line functionality in 10BASE-T1S System* |
| Length | 24 pages, revision DS60001848B, 2025 |
| Use here | How to combine power and data on the same pair of wires for T1S nodes.  Relevant for industrial / sensor deployments where wiring is a cost factor. |
| Summary | PoDL implementation guidance for 10BASE-T1S multidrop networks — note that **the IEEE 802.3cg standard defines PoDL only for 10BASE-T1S point-to-point segments, not for multidrop**, so the solutions in this AN are necessarily engineered designs (not standard-compliant), drawing inspiration from PoE / 10BASE-T1L / RS-485 / USB.  **§1 Preface (p.3)** terms (BIN, CMC = Common Mode Choke, DMC = Differential Mode Choke, PD = Powered Device, PoDL = Power over Data Line, PSE = Power Sourcing Equipment).  **§2 An Introduction to Power over Data Line (p.4)**: motivates cable / weight reduction; contrasts with point-to-point PoE/PoDL standards (IEEE 802.3bu and 802.3cg); notes that 10BASE-T1S multidrop has no standard PoDL definition; tested chips for PoDL evaluation are LAN8651, LAN8670, and LAN8671.  **§3 Evaluation Setup (p.5-9)**: Figure 3-1 PSE block diagram with 12 V input, ATtiny 1616 + MCP2221 USB controller, OLED status display, PAC1720 power monitor, three regulator options (Straight-through 12 V, Buck 4-9 V, Boost 16-24 V), High-Side and Low-Side protection, and the swappable Filter Block Board (DMC / two coupled inductors / two separate inductors).  **§4 Evaluation Simulations (p.10-12)**: simulation models.  **§5 PoDL Evaluation Measurements (p.13-15)**: measured behaviour.  **§6 Example Applications (p.16-21)** — §6.1 Example with PoDL (p.19), §6.2 Example without PoDL (p.19), §6.3 Comparison Results (p.20-21).  **§7 Conclusions (p.22)**. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-Using-Power-over-Data-Line-in-10BASE-T1S-Application-Note-60001848.pdf |
| Read first if | your deployment uses PoDL or you are evaluating its viability for a multidrop sensor network |

---

## 5. Software ecosystem — Linux, Zephyr, EVB drivers

Microchip's documentation for the various software-platform paths.
Note that none of these currently include PTP support; they document
basic Ethernet driver setup only.

### LAN8651-Zephyr-Driver-Application-Note-00006170.pdf

| | |
|---|---|
| Document | AN00006170 — *LAN8651 Zephyr Driver Application Note* |
| Length | 21 pages, revision DS00006170A, 2025 |
| Use here | Microchip's own description of the Zephyr-side LAN8651 driver — discussed in `documentation/ptp/README_cross.md` §9 (Zephyr alternative platform).  Public Zephyr master tree as of 2026-04 has no PTP support; this AN documents the basic driver only. |
| Summary | Pure Ethernet-driver setup recipe; no PTP / time-synchronization content.  **§1 Host Setup**: step-by-step Ubuntu setup for Zephyr 4.1.0+; minimum tool versions (CMake 3.20.5, Python 3.10, devicetree compiler 1.4.6); apt-get dependencies; python venv + west bootstrap; Zephyr SDK 0.17.2.  **§2 SAM E54 Xplained Pro platform**: device-tree overlay snippets and west build commands for SAM-E54 + MikroE Two-Wire ETH Click (LAN8651 Rev B1).  **§3 STM32F413ZH Nucleo-144 platform**: same recipe for STM32F413ZH.  **§4 Flashing**: per-platform flashing instructions.  **§5 Throughput Test**: Zperf vs iperf 2.0.5 demonstrating roughly maximum 10BASE-T1S half-duplex bandwidth between two nodes.  Useful as a reference for what the upstream Zephyr LAN865x driver actually exposes today, and as a starting checklist for a Zephyr port of the Harmony work. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/LAN8651-Zephyr-Driver-Application-Note-00006170.pdf |
| Read first if | you are evaluating the Zephyr porting path for the PTP work |

### LAN865x-Linux-Driver-Install-Application-Note-00005990.pdf

| | |
|---|---|
| Document | AN00005990 — *LAN865x Linux Driver Installation* |
| Length | application note, revision DS00005990C, 2025 |
| Use here | Linux-side driver install instructions.  Background only.  Of interest because Microchip's Linux engineer Parthiban Veerasooran added LAN8651 TSU configuration to the upstream Linux driver in August 2025 (see `documentation/ptp/README_cross.md` §9.7) — the Linux driver is the closest equivalent of "what Annex H support could look like in a kernel-quality Open Source codebase". |
| Summary | Step-by-step build and install guide for the upstream Linux LAN865x MAC-PHY driver on a Raspberry Pi 4 Model B running kernels 6.6.51 / 6.12.25, paired with the MikroE Two-Wire ETH Click board (LAN8651 Rev B1).  Minimum supported kernels: 6.12 onwards for B0 silicon, 6.13 onwards for B1.  Two integration paths: built-in kernel support and Loadable Kernel Module (LKM).  Mechanical steps: apt-get prerequisites, git-clone Raspberry Pi kernel tree, overwrite four driver files (`drivers/net/ethernet/microchip/lan865x/lan865x.c`, `drivers/net/phy/microchip_t1s.c`, `drivers/net/ethernet/oa_tc6.c`, `include/linux/oa_tc6.h`) with the matching files from the rpi-6.13.y branch when running on a 6.12 source tree.  Build (`bcm2711_defconfig`, `KERNEL=kernel8`), install modules, edit `config.txt` overlays, then bring up the interface with `ip` / `iproute2`.  Like the Zephyr AN, this document is purely about getting an Ethernet link running — there is no PTP, TSU, or 1588 configuration discussion. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/LAN865x-Linux-Driver-Install-Application-Note-00005990.pdf |
| Read first if | you want to compare the Linux driver's PTP integration model with what we are designing for Harmony / Zephyr |

### EVB-LAN8670-RMII-Linux-Driver-Application-Note-00006120.pdf

| | |
|---|---|
| Document | AN00006120 — *EVB-LAN8670-RMII Linux Driver Application Note* |
| Length | application note (~635 KB) |
| Use here | Linux-driver setup specifically for the LAN8670 RMII evaluation board (separate-PHY variant).  Background reference if a test setup uses RMII rather than the MAC-PHY-SPI path. |
| Summary | Recipe-style application note paralleling AN00005990 but for the **standalone-PHY variant with RMII host-side interface** instead of the MAC-PHY SPI variant.  Covers Linux kernel selection, Raspberry Pi or similar SBC integration, RMII pin routing, and bring-up via `ip` / `iproute2`.  No PTP / TSU content (consistent with the rest of the Microchip Linux/Zephyr driver AN family).  Cross-reference: the LAN8670 datasheet's §6.2 "RMII Connectivity with Reference Clock" (DS60001573 p.228) is the hardware reference; this AN is the software bring-up. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/EVB-LAN8670-RMII-Linux-Driver-Application-Note-00006120.pdf |
| Read first if | you are bringing up an LAN8670 RMII evaluation board on Linux |

### EVB-LAN8670-RMII-Zephyr-Driver-Installation-Application-Note-00006247.pdf

| | |
|---|---|
| Document | AN00006247 — *EVB-LAN8670-RMII Zephyr Driver Installation Application Note* |
| Length | application note (~2 MB) |
| Use here | Zephyr-driver installation paralleling AN00006170 but for the LAN8670 standalone PHY with RMII host-side interface. |
| Summary | Zephyr integration recipe for the EVB-LAN8670-RMII evaluation board.  Likely follows the same structure as AN00006170: Ubuntu host setup → Zephyr SDK install → board device-tree overlays → west build / flash → throughput verification.  Differences vs LAN8651 path: uses RMII host-side interface (clock and TX/RX nibble streams) instead of OPEN-Alliance SPI; PHY driver is `microchip_t1s.c` rather than the `lan865x.c` MAC-PHY driver.  No PTP / TSU content (consistent with the rest of the family). |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/EVB-LAN8670-RMII-Zephyr-Driver-Installation-Application-Note-00006247.pdf |
| Read first if | you are bringing up an LAN8670 RMII evaluation board on Zephyr |

---

## 6. Microchip presentation decks (PPTX)

These are presentation files, typically used by Microchip
field-application engineers for customer training and product
overview.  They are **not** primary technical references — for
authoritative information consult the datasheets and application
notes — but they often summarise design intent and feature
positioning more concisely than the formal docs.

### 3_Special Features of LAN8670_1_2 LAN8650_1.pptx

| | |
|---|---|
| Document | Microchip presentation deck — *Special Features of LAN8670/1/2 and LAN8650/1* |
| Length | ~20 MB PPTX |
| Use here | Side-by-side comparison of the two Microchip 10BASE-T1S families — the standalone-PHY LAN8670/1/2 and the integrated-MAC LAN8650/1 — focused on differentiating features (PLCA, ACMA, TSU, sleep, safety, PoDL, etc.).  Useful for understanding which feature is in which silicon, and the design rationale behind the dual product line.  The "3_" filename prefix suggests it is part of a numbered Microchip training series. |
| Read first if | you need a quick orientation across the LAN86xx product family before diving into either datasheet |

### LAN867x_PHY_d47.pptx

| | |
|---|---|
| Document | Microchip presentation deck — *LAN867x PHY* (draft revision 47) |
| Length | ~8 MB PPTX |
| Use here | Product-overview presentation for the LAN8670/1/2 standalone PHY family.  Less detailed than the datasheet (DS60001573) but useful for high-level architecture and positioning context. |
| Read first if | you want a one-deck overview of the LAN8670/1/2 line without reading the 279-page datasheet |

> **Note:** PPTX content is not authoritative — slide decks are
> simplified and may lag the latest silicon/datasheet revisions.
> Always cross-check specific numerical claims against the
> corresponding datasheet, errata, or application note.

---

## 7. Recommended reading order

For someone starting on the PTP / Annex H / ACMA roadmap on this
project's specific hardware (SAM E54 + LAN8651):

1. **DS60001734** (datasheet) — sections 4.5 (Synchronization Support, p.31-42), 7.2 (PLCA, p.74-79), 7.3 (ACMA, p.80-81), 5.2 (SPI MAC frame data, p.53-61)
2. **AN60001847** (time-synch AN) — full read; canonical PTP-on-T1S reference (22 pages)
3. **AN60001760** (configuration AN) — the init flow context (Tables 1-4, p.2-5)
4. **ER80001075** (errata) — for the specific values you'll write to the chip, **especially before ACMA + EG0 implementation** (errata s9 and s7)
5. **AN00006067** (topology discovery) — when starting on multi-node work or per-pair path-delay calibration

If you also need to understand hardware design or board layout:

6. **AN60001718** (BIN reference design) — Figures 1-2 and 1-3 are the canonical schematics
7. **AN00006174** (layout guide) — Figures 2-1 and 2-2 for decoupling and clock routing
8. **UG60001745** (hardware design checklist) — sign-off list
9. **AN60001829** (intrinsic noise) — design rules for extending beyond 8 nodes / 25 m
10. **AN60001848** (PoDL — only if relevant)

If you need to compare against other Microchip silicon families:

11. **DS60001573** (LAN8670/1/2 datasheet)
12. **AN60001699** (LAN8670/1/2 configuration AN)
13. **ER80000962** (LAN8670/1/2 errata — has 12 items vs LAN8650/1's 9)
14. **PPTX deck "3_Special Features..."** (family comparison)

For software-ecosystem understanding (alternative platforms):

15. **AN00006170** (Zephyr LAN8651 driver)
16. **AN00005990** (Linux LAN865x driver)
17. **AN00006120** / **AN00006247** (EVB-LAN8670-RMII Linux/Zephyr)

Followed by the project-internal docs:

- `documentation/ptp/readme_results.md` — feasibility analysis,
  insel-vs-deployment assessment
- `documentation/ptp/readme_upgrade.md` — AN1847-style refactor on
  the `mult-sync` branch
- `documentation/ptp/readme_acma.md` — ACMA + EG0 + PTP architecture
  for deterministic TDMA slots
- `documentation/ptp/readme_802_1as_roadmap.md` — gap analysis to
  full 802.1AS conformance
- `documentation/ptp/plca_ptp_asymmetrie.md` — Annex H roadmap
- `documentation/ptp/README_cross.md` §2 — irreducible driver patches

---

## 8. Topic Quick Lookup

Direct mapping from common technical keywords to PDF + section + page,
so a reader can locate authoritative information without searching
across documents.  Page numbers are approximate ("~p.X") where the
content spans a range or where the entry is derived from the document's
TOC rather than the body.

### TSU, Wall Clock, and Timestamping

| Topic | Document | Section | Page |
|---|---|---|---|
| TSU 94-bit wall clock layout (48 s + 30 ns + 16 sub-ns) | DS60001734 | §4.5.1 | ~p.32 |
| TSU 94-bit wall clock layout | AN60001847 | §3 | p.12 |
| TSU 94-bit wall clock layout (LAN8670/1/2) | DS60001573 | §4.12 | ~p.44-47 |
| MAC_TSH / MAC_TSL / MAC_TN registers | DS60001734 | §11.2 (MMS 1) | ~p.157-200 |
| MAC_TI / MAC_TISUBN registers | DS60001734 | §11.2 (MMS 1) | ~p.157-200 |
| MAC_TI = 0x28 default for 25 MHz / 40 ns | AN60001760 | Table 1 (MMS 1 / 0x0077) | p.2 |
| MAC_TI = 0x28 default for 25 MHz / 40 ns | DS60001734 | §4.5.1 | ~p.32 |
| Packet pattern matcher (TXMLOC, TXMPATH/L, RXMLOC, etc.) | DS60001734 | §4.5.2 + §11.6 | p.34 + ~p.286-364 |
| Packet pattern matcher | AN60001847 | §3 | p.13 |
| End-of-SFD timestamping in PHY | DS60001734 | §4.5.2 (warning in §4.5.2.2) | p.34 |
| End-of-SFD timestamping rationale (multidrop) | AN60001847 | §2 + §3 | p.7, p.13 |
| FTSE / FTSS bits in OA_CONFIG0 | DS60001734 | §11.1 (MMS 0 / 0x0004) | ~p.118-156 |
| FTSE / FTSS bits | AN60001760 | Table 1 (MMS 4 / 0x0084 cfgparam1) | p.2 |
| Phase Adjuster (PACTRL, PACYC) | DS60001734 | §4.5.5 | p.40-41 |

### Event Capture and Generation

| Topic | Document | Section | Page |
|---|---|---|---|
| Event Capture (4 events on DIOA0-3) | DS60001734 | §4.5.3 | p.36-37 |
| Event Capture register allocation | DS60001734 | Figures 4-7 / 4-8 | p.37 |
| Event Generator periodic vs single-shot | DS60001734 | §4.5.4 | p.38-39 |
| Event Generator timing (Figures 4-9, 4-10) | DS60001734 | §4.5.4 | p.39 |
| EG0CTL bits (REP, ACTHI, START, ISREL) | DS60001734 | §11.6 | ~p.286-364 |
| Event Generator periodic-mode bug (s9) | ER80001075 | §1.9 | p.5 |
| 1PPS on DIOA4 (PPSCTL register, 640 ns to 20.48 µs) | DS60001734 | §4.5.4.2 | p.40 |
| 1PPS on DIOA4 | AN60001847 | §3 | p.14 |

### PLCA — Configuration and Behaviour

| Topic | Document | Section | Page |
|---|---|---|---|
| PLCA principle (BEACON, slot order, NODE_ID) | DS60001734 | §7.2 | p.74-79 |
| PLCA principle | DS60001573 | §4.8 | p.33-38 |
| PLCA_CTRL0 / PLCA_CTRL1 / PLCA_TOTMR / PLCA_BURST | DS60001734 | §11.5 (MMS 4) | ~p.213-285 |
| PLCA init sequence (LAN8650/1) | AN60001760 | Table 3 + flowchart | p.4-5 |
| PLCA init sequence (LAN8670/1/2 D0) | AN60001699 | Table 2-2 | p.4 |
| PLCA init sequence (LAN8670/1/2 C2) | AN60001699 | Table 3-1 + Table 3-2 | p.5-6 |
| PLCA Burst Mode (MAXBC, BTMR) | DS60001734 | §7.2.1 | p.76 |
| Multiple TOs per node (MULTID0-3) | DS60001734 | §7.2.2 | p.77 |
| PLCA TO Skipping (TOSKPEN, PLCATOSKP) | DS60001734 | §7.2.3 | p.77 |
| PLCA Diagnostics (UNEXPB, RXINTO, TXCOL, BCNBFTO) | DS60001734 | §7.2.4 | p.78-79 |
| PLCA Collision Detect flowchart | AN60001760 | Figure 1 | p.5 |
| Collision Detect Auto Disable (CDAD bit) | AN60001699 | §2.2 | p.4 |

### ACMA — Application Controlled Media Access

| Topic | Document | Section | Page |
|---|---|---|---|
| ACMA register block (ACMACTL / ACMASEL) | DS60001734 | §7.3 | p.80-81 |
| ACMA principle as TDMA | DS60001734 | §7.3 | p.80 |
| ACMA on standalone PHY family | DS60001573 | §4.9 | p.39-40 |
| ACMA register addresses (MMS 4 / 0xCA0E etc.) | DS60001734 | §11.5 / §11.6 | ~p.213-364 |
| Minimum ACMA pulse width (10 µs) | DS60001734 | §7.3 | p.80 |
| UNCRS bit as ACMA integrity indicator | DS60001734 | §7.3 + §11 | p.80 |

### OA-TC6 SPI Protocol (Open Alliance TC6)

| Topic | Document | Section | Page |
|---|---|---|---|
| SPI Format overview | DS60001734 | §5.1 | p.53 |
| MAC Frame Data Transactions | DS60001734 | §5.2 | p.53-61 |
| OA-TC6 chunked frame format | DS60001734 | §5.2 | p.53-61 |
| Footer bits (RTSP, SV, RTSA) | DS60001734 | §5.2 | ~p.53-61 |
| Control Transactions | DS60001734 | §5.3 | p.62-64 |
| OA_STATUS0 / OA_STATUS1 / OA_IMASK0 / OA_IMASK1 | DS60001734 | §11.1 (MMS 0) | ~p.118-156 |

### Sleep Mode

| Topic | Document | Section | Page |
|---|---|---|---|
| Sleep Mode overview | DS60001734 | §4.4 | p.25-30 |
| Sleep Mode Rev C2 (LAN8670/1/2) | DS60001573 | §4.13 | p.48-55 |
| Sleep Mode Rev D0 (LAN8670/1/2) | DS60001573 | §4.14 | p.56-62 |
| SLPCAL must always be written 0 (s6) | ER80001075 | §1.6 | p.3 |
| SLPCAL must always be written 0 (LAN8670/1/2 s10) | ER80000962 | §1.10 | p.5 |
| PLCA disable before sleep (s7) | ER80001075 | §1.7 | p.4 |
| PLCA disable before sleep (LAN8670/1/2 s11) | ER80000962 | §1.11 | p.5 |

### Topology Discovery

| Topic | Document | Section | Page |
|---|---|---|---|
| Topology Discovery hardware path | AN00006067 | §3 (Figure 3-1) | p.5 |
| Distance measurement equation | AN00006067 | Equation 3-1 | p.6 |
| Internal delay equation | AN00006067 | Equation 3-2 | p.7 |
| Topology Discovery automatic mode flow | AN00006067 | §4 (Figure 4-1) | p.8-10 |
| TD_CTRL / TD_STAT / TD_DLY_RES / TD_DIST_RES | DS60001573 | §4.15 + §5.4 | p.63-65, ~p.108-225 |
| PRSCTL1.FBEN bit (disable PLCA fallback) | AN00006067 | §4 | p.9 |
| Disable PLCA via NODE_ID = 254 trick | AN00006067 | §4 | p.9 |

### Hardware Design — BIN, Layout, Power

| Topic | Document | Section | Page |
|---|---|---|---|
| Mixing-segment topology (End vs Drop nodes) | AN60001718 | Figure 1-1 | p.2 |
| Minimal BIN circuit | AN60001718 | §1.1 (Figure 1-2 + Table 1-1) | p.3-4 |
| Optimized BIN with CMC and ESD | AN60001718 | §1.2 (Figure 1-3 + Table 1-2) | p.5 |
| End Node termination (49.9 Ω 1% 1206) | AN60001718 | Table 1-1 | p.3 |
| Drop Node common-mode termination (1.5 kΩ 1206) | AN60001718 | Table 1-1 | p.3 |
| AC coupling caps (0.1 µF 50 V on every node) | AN60001718 | §1.1 | p.4 |
| Optimal decoupling-cap placement | AN00006174 | §2 (Figure 2-1) | p.3 |
| Optimal clock-trace routing (RMII) | AN00006174 | §2 (Figure 2-2) | p.4 |
| MII / RMII / SPI 50 Ω controlled impedance | AN00006174 | §2 | p.4 |
| BIN trace impedance goal (50 Ω, low parasitic C) | AN00006174 | §2 | p.4 |
| Power and ground rules (LAN8670/1/2) | UG60001745 | §4 | p.2-5 |
| Power pinout per package (VDDP / VDDA / VDDAU) | UG60001745 | Table 4-1 | p.2 |
| Decoupling rules (10 µF + 0.1 µF + 0.01 µF) | UG60001745 | §4.1 | p.2 |
| Power-island isolation with ferrite beads | UG60001745 | Figures 4-3 / 4-4 | p.4-5 |
| VDDA must not exceed VDDAU by > 0.5 V | UG60001745 | §4.1 | p.3 |

### Noise, EMC, Reach Extension

| Topic | Document | Section | Page |
|---|---|---|---|
| Noise budget (point-to-point) | AN60001829 | §2 (Figure 2-1) | p.4 |
| Noise budget (multidrop, with reflections) | AN60001829 | §3 (Figure 3-1) | p.5 |
| Cable characteristics for T1S | AN60001829 | §4 | p.7-10 |
| 5 m / 2 nodes baseline | AN60001829 | §5 | p.11-13 |
| 35 m / 8 nodes extended | AN60001829 | §6 | p.14 |
| Up to 50 nodes / 50 m design rules | AN60001829 | §7 | p.15 |
| EMC considerations | DS60001734 | §8.4 | p.92 |
| EMC considerations (LAN8670/1/2) | DS60001573 | §6.5 | p.232 |

### Power over Data Line (PoDL)

| Topic | Document | Section | Page |
|---|---|---|---|
| PoDL principle and motivation | AN60001848 | §2 | p.4 |
| PoDL evaluation PSE block diagram | AN60001848 | §3 (Figure 3-1) | p.5 |
| PoDL evaluation simulations | AN60001848 | §4 | p.10-12 |
| PoDL evaluation measurements | AN60001848 | §5 | p.13-15 |
| Example application with PoDL | AN60001848 | §6.1 | p.19 |
| Example application without PoDL | AN60001848 | §6.2 | p.19 |
| With/without PoDL comparison | AN60001848 | §6.3 | p.20-21 |

### LAN8650/1 Errata Items (ER80001075)

| Item | Topic | Page |
|---|---|---|
| s1 | OA_PHYID does not identify MAC-PHY product; use DEVID at MMS 10 / 0x0094 | p.2 |
| s2 | RX block bug (B0 only, resolved B1) | p.2 |
| s3 | SPI RX halt (B0 only, resolved B1) | p.2 |
| s4 | TX halts on excessive collisions; mitigate with PLCA or one-frame-per-chunk in CSMA/CD | p.2-3 |
| s5 | Multi-coordinator BEACON sets UNEXPB; node enters recovery state | p.3 |
| s6 | SLPCAL field of SLPCTL0 must always be written 0 | p.3 |
| s7 | Coordinator does not stop transmitting beacons on sleep entry; disable PLCA first | p.4 |
| s8 | TO_TMR ≥ 29 (default 32) for noise immunity | p.4 |
| s9 | Event Generator periodic mode drifts vs synchronized wall clock; use single-shot | p.5 |

### LAN8670/1/2 Errata Items (ER80000962)

| Item | Topic | Page |
|---|---|---|
| s1 | RMII identification (resolved C1) | p.3 |
| s2 | Package type identification (LAN8671, resolved C1) | p.3 |
| s3 | RMII CSMA/CD operation in mixed PLCA segments | p.3 |
| s4 | Incorrect reset indication on IRQ_N (resolved C1) | p.4 |
| s5 | Multi-coordinator PLCA action (analogous to LAN8650/1 s5) | p.4 |
| s6 | Transmission of collision fragments with PLCA + RMII (all revisions) | p.4-5 |
| s7 | RMII incorrect carrier sense (resolved C2) | p.5 |
| s8 | Revert to CSMA/CD when BEACONs missing (resolved C1) | p.5 |
| s9 | Packet pattern matcher matches all message types (resolved C1) | p.5 |
| s10 | SLPCAL field of SLPCTL0 must always be 0 (analogous to LAN8650/1 s6, resolved D0) | p.5 |
| s11 | Coordinator beacon doesn't stop on sleep (analogous to LAN8650/1 s7) | p.5 |
| s12 | Noise immunity vs carrier sense latency trade-off (analogous to LAN8650/1 s8) | p.5 |

---

## 9. How to find newer revisions

Microchip uses stable document numbers for reference, but the
*revision letter* and minor errata changes happen frequently.  The
fastest way to find the current revision:

1. Go to `https://www.microchip.com`
2. Search for the document number (e.g. `60001734`)
3. Pick the *Documentation* tab on the resulting product page
4. Compare the "Last updated" date with the local PDF's date

For any PDF whose document number ends in a letter (like `00006067a`),
that letter is the revision indicator.  Letters increase
alphabetically: `a` < `b` < `c` < etc.  No letter = original release.

---

**Index last updated:** 2026-04-27 (expanded summaries for LAN8670/1/2
family + hardware ANs + EVB driver ANs + PPTX, added §8 Topic Quick
Lookup table, added page-number hints throughout)
