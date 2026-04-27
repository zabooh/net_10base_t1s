# PDF Documentation Reference

Microchip PDFs collected for reference during the T1S + PTP work on
this fork.  They are *not* redistributable verbatim — Microchip
retains copyright on these documents.  This index exists so you can
quickly find which PDF to consult for which engineering question.

When in doubt about a PDF's currency, search the document number on
[microchip.com](https://www.microchip.com) — the company periodically
issues newer revisions and the local copies in this directory may
lag.

---

## 1. Primary PTP / TSU references (most important for this project)

### LAN8650-1-Time-Synch-AN-60001847.pdf

| | |
|---|---|
| Document | AN60001847 — *LAN8650/1 Time Synchronization Application Note* |
| Length | application note (22 pages, revision DS60001847C, June 2025) |
| Use here | **The single most important document for PTP on T1S.**  Documents the Time Stamp Unit (TSU), the timestamp register pair (MAC_TSH / MAC_TSL), the timer increment register (MAC_TI), and the LAN8651's gPTP-relevant features.  Cross-referenced from `documentation/ptp/plca_ptp_asymmetrie.md` and `documentation/ptp/README_cross.md`. |
| Summary | Surveys how IEEE 1588 / 802.1AS time synchronization can be implemented on a 10BASE-T1S multidrop mixing segment using the LAN8650/1.  Section 2 walks through the Sync / Follow_up / Pdelay_Req / Pdelay_Resp message exchange, derives the propagation delay and clock error equations, and explicitly calls out that current PTP standards do not yet cover multidrop / PLCA broadcast Pdelay — it suggests either pre-measuring fixed peer delays or modifying software to demultiplex Pdelay responses, with timestamps captured at the PHY (after the elastic buffer) rather than at the MAC.  Section 3 describes the LAN8650/1 hardware: a 94-bit wall clock (48 s + 30 ns + 16 sub-ns), a per-tick increment register (nominally 0x28 for 40 ns at 25 MHz), one-time adjust, four event-capture timestamps, four event generators, a 1PPS output on DIOA4, and the packet pattern matcher that anchors timestamps to the end of the SFD on TX and RX.  Section 4 sketches a minimal clock-follower implementation that uses only Sync + Follow_up (no Pdelay), implements a software PLL with Init / Unlocked / Locked-Coarse / Locked-Fine states, and reports a measured ~100 ns peak-to-peak / 25 ns sigma 1PPS error against a co-located grandmaster on a 50 cm link using a SAM D21 Curiosity Nano board with the LAN8651 Two-Wire ETH Click.  Companion sample code lives at github.com/MicrochipTech/LAN865x-TimeSync. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN8650-1-Time-Synch-AN-60001847.pdf |
| Read first if | you are starting any PTP / Annex H implementation work |

### LAN8650-1-Data-Sheet-60001734.pdf

| | |
|---|---|
| Document | DS60001734 — *LAN8650/1 Data Sheet* |
| Length | full datasheet (370 pages, revision DS60001734F, 2025) |
| Use here | Authoritative reference for every register address used in `drv_lan865x_api.c::TC6_MEMMAP[]`, every PLCA control bit, every MAC layer behaviour.  Consult when an `_InitMemMap` register write is unclear or when adding new register access. |
| Summary | Full datasheet for the LAN8650 / LAN8651 10BASE-T1S MAC-PHY with SPI (32-pin VQFN, AEC-Q100, -40 to +125 C).  After pin-out and global function descriptions (reset/startup, clock manager, sleep mode, safety features), it dedicates section 4.5 to synchronization support — wall clock, packet timestamping, event capture/generation, 1PPS — which is the hardware basis for AN60001847.  Section 5 specifies the OPEN Alliance 10BASE-T1x MAC-PHY SPI protocol: framing, control transactions, MAC-frame chunked transfers, and footer fields including the receive timestamp parity bit (RTSP) and Start Valid (SV).  Sections 6 and 7 cover the integrated MAC and the integrated 10BASE-T1S PHY, including PLCA (Clause 148), application-controlled media access (ACMA), credit-based shaping, SQI, and cable fault diagnostics.  Section 11 is the bulk of the document — roughly 250 pages of register descriptions split across OA standard registers (MMS 0), MAC registers (MMS 1), PHY PCS / PMA / PMD / vendor-specific registers (MMS 2-9), and miscellaneous TSU / event / DIO registers (MMS 10).  This is the canonical address reference for any new register write the firmware needs to add. |
| URL (PDF) | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/DataSheets/LAN8650-1-Data-Sheet-60001734.pdf |
| URL (HTML) | https://onlinedocs.microchip.com/oxy/GUID-7A87AF7C-8456-416F-A89B-41F172C54117-en-US-10/index.html |
| Read first if | you need to verify a specific register meaning, bit layout, or electrical spec |

### LAN8650-1-Configuration-Appnote-60001760.pdf

| | |
|---|---|
| Document | AN60001760 — *LAN8650/1 Configuration Application Note* |
| Length | short application note (revision DS60001760G, June 2024) |
| Use here | Walks through the PLCA configuration flow and the recommended init sequence for the chip.  Useful background for understanding **why** `_InitMemMap` and `_InitUserSettings` write the values they do, and for designing the Annex H register-init state machine in `ptp_drv_ext.c::PTP_DRV_EXT_Tasks`. |
| Summary | Concise post-reset configuration recipe for LAN8650/1 silicon revisions B0 (0x0001) and B1 (0x0010).  Defines the SPI access primitives (read_register, write_register, indirect_read) and the proprietary indirect-read used to pull two factory trim values from addresses 0x04 and 0x08, which are then sign-extended and packed into cfgparam1 / cfgparam2.  The bulk of the note is Table 1 — a fixed sequence of about 20 register writes (mostly to MMS 4, plus one to MMS 1) that contains the values the project's TC6_MEMMAP array mirrors: 0x00D0=0x3F31, 0x00E0=0xC000, 0x00F4=0xC020, 0x00F8=0xB900, 0x00F9=0x4E53, 0x0081=0x0080, etc.  These writes also program the MAC to timestamp at the end of the SFD and set the timer increment to 40 ns for a 25 MHz clock.  The note then covers an optional SQI configuration block (Table 2), the PLCA enable sequence (PLCA_CTRL1 / PLCA_CTRL0 / CDCTL0), and a flowchart for re-enabling collision detect when PLCA falls back to CSMA/CD.  Specific values of B900 and 80 in `_InitMemMap` are sourced directly from this document. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN8650-1-Configuration-Appnote-60001760.pdf |
| Read first if | you are touching the chip's init sequence or PLCA setup |

### LAN8650-1-Errata-80001075.pdf

| | |
|---|---|
| Document | ER80001075 — *LAN8650/1 Errata and Data Sheet Clarifications* |
| Length | errata sheet (5 pages, revision DS80001075F, 2025) — shorter than expected |
| Use here | Documents known silicon issues for the B1 revision used in this project.  The register-value differences observed between the GitHub-HEAD and the MCC-regenerated TC6_MEMMAP (`0x000400F8` `0xB900`→`0x9B00` and `DEEP_SLEEP_CTRL_1` `0x80`→`0xE0`, see `documentation/ptp/README_cross.md` §7) are most likely B1-erratum workarounds documented here. |
| Summary | Short combined errata for LAN8650 / LAN8651 silicon revisions B0 (0x0001) and B1 (0x0010), listing nine items s1–s9.  s1: the OA_PHYID register only identifies the integrated PHY block, not the MAC-PHY product — read DEVID at MMS 10 / 0x0094 instead.  s2 (B0 only) and s3 (B0 only) are SPI receive-data-block bugs already fixed in B1.  s4: TX may halt on excessive collisions; mitigated by proper PLCA configuration or a one-frame-per-block transmit strategy in CSMA/CD mode.  s5: a duplicate-coordinator condition latches the PHY into a non-transmitting recovery state; software must monitor UNEXPB and reconfigure the PHY as a follower.  s6: the SLPCAL field of SLPCTL0 must always be written as 0.  s7: a coordinator does not stop transmitting beacons immediately on entering sleep — the workaround disables PLCA before sleep.  s8: noisy environments require TO_TMR ≥ 29 (default 32) to pass EMI/EMC tests.  s9: the Event Generator in periodic mode is locked to the local oscillator, not the synchronized wall clock, so 1PPS-style outputs must use single-shot mode for true synchronization.  Several of these (s5, s6, s7) explain specific bit patterns the project's MEMMAP currently writes. |
| URL (PDF) | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ProductDocuments/Errata/LAN8650-1-Errata-80001075.pdf |
| URL (HTML) | https://onlinedocs.microchip.com/oxy/GUID-CEB5226B-455F-4D06-B752-88F6BA400817-en-US-2/index.html |
| Read first if | a register write does not behave as the datasheet suggests, or before validating new register-init values on real hardware |

---

## 2. Topology discovery (relevant to multi-node Annex H scenarios)

### LAN86xx-topology-discovery-AN-00006067.pdf

| | |
|---|---|
| Document | AN00006067 — *Topology Discovery for 10BASE-T1S Systems* (revision DS00006067B, Oct 2025, public release) |
| Length | application note (12 pages) |
| Use here | Describes how nodes discover each other on a shared T1S PLCA bus.  Directly relevant to the Annex H Phase 6 (per-hop topology) work in `documentation/ptp/plca_ptp_asymmetrie.md` §12.2. |
| Summary | Explains the OPEN Alliance 10BASE-T1S Topology Discovery procedure (spec v1.4) and how to implement it in software using LAN8670/1/2 D0+ hardware — note that the title says LAN86xx but the example nodes are LAN8670/1/2 PHYs, *not* the LAN8650/1 MAC-PHY used in this project.  Section 3 describes the hardware path: in topology-discovery mode the digital PHY uses an alternate scrambler / 1B2B encoder / serializer routed to the PMD transceiver, with a receive-side delay block guaranteeing ≥100 ns internal delay.  The reference node transmits 40-85 ns pulses, the measured node responds, and distance is computed from pulse count, configurable measurement duration (1-16 ms), and pre-measured internal delays of both nodes.  Section 4 walks through an "Automatic mode" example with four roles (Requesting Node, PLCA Coordinator, Reference Node, Measured Node), showing how to disable PLCA before measurement (via PRSCTL1.FBEN or temporary node-ID 254), program TD_CTRL.REFN / DM_DUR / AUTO_START / TD_EN, read results from TD_DLY_RES_LOW/HIGH and TD_DIST_RES_LOW/HIGH, and check TD_STAT.DLYM_DONE / DM_DONE for completion.  A manual mode is also briefly described for segments with many nodes. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/AIS/ApplicationNotes/ApplicationNotes/LAN86xx-topology-discovery-AN-00006067.pdf |
| Read first if | you are implementing neighbour-discovery or per-hop Pdelay |

### lan8670-1-topology-discovery-application-note-00006067a.pdf

| | |
|---|---|
| Document | AN00006067a — *Topology Discovery for 10BASE-T1S Systems* (revision DS00006067A, Aug 2025, NDA-watermarked predecessor of AN00006067) |
| Length | application note (11 pages) |
| Use here | Same topic as the LAN86xx note above but specific to the LAN8670/1 PHY.  The mechanisms are very similar to the LAN8651; useful for cross-checking interpretation. |
| Summary | The earlier (revision A, August 2025) NDA-confidential issue of what later became the public AN00006067 above.  Content is essentially identical: same description, same theory-of-operation block diagram (digital PHY scrambler / 1B2B / serializer feeding the PMD transceiver, with a delay block ensuring ≥100 ns internal delay), same distance-measurement and internal-delay equations, and the same automatic-mode flow with the four-node example (Requesting / Coordinator / Reference / Measured).  References the OPEN Alliance Topology Discovery v1.0 spec (the public version cites v1.4) and the LAN8670/1/2 datasheet DS60001573.  The only meaningful differences from the public revision are wording polish, the v1.0 vs v1.4 spec reference, and a "Microchip Confidential — NDA" banner with a per-download personalised watermark.  For day-to-day reference the public revision is preferable. |
| URL | search "DS00006067A" on microchip.com — this revision is NDA-confidential and not publicly hosted; the equivalent public revision is the AN00006067 entry above |
| Read first if | you want a second voice on topology discovery semantics |

---

## 3. Software ecosystem context

### LAN8651-Zephyr-Driver-Application-Note-00006170.pdf

| | |
|---|---|
| Document | AN00006170 — *LAN8651 Zephyr Driver Application Note* (revision DS00006170A, 2025) |
| Length | application note (21 pages) |
| Use here | Microchip's own description of the Zephyr-side LAN8651 driver — the same driver discussed in `documentation/ptp/README_cross.md` §9 (Zephyr alternative platform).  Note that the public Zephyr master tree as of 2026-04 has no PTP support; this AN documents the basic driver only. |
| Summary | A pure Ethernet-driver setup recipe — there is no PTP / time-synchronization content in this document.  Section 1 is a step-by-step Ubuntu host setup for Zephyr 4.1.0+: minimum tool versions (CMake 3.20.5, Python 3.10, devicetree compiler 1.4.6), apt-get dependencies, the python venv + west bootstrap, and Zephyr SDK 0.17.2 installation.  Sections 2 and 3 cover two specific test platforms — the Microchip SAM E54 Xplained Pro and the STM32F413ZH Nucleo-144 — both paired with the MikroE Two-Wire ETH Click board (LAN8651 Rev B1).  Each platform section gives the device-tree overlay snippets, the configuration changes to enable LAN8651 support, and the west build commands.  Section 4 is flashing instructions, section 5 is a Zperf vs iperf 2.0.5 throughput test that demonstrates roughly maximum 10BASE-T1S half-duplex bandwidth between two nodes.  Useful as a reference for what the upstream Zephyr LAN865x driver actually exposes today, and as a starting checklist if a Zephyr port becomes part of the project. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/LAN8651-Zephyr-Driver-Application-Note-00006170.pdf |
| Read first if | you are evaluating the Zephyr porting path for the PTP work |

### LAN865x-Linux-Driver-Install-Application-Note-00005990.pdf

| | |
|---|---|
| Document | AN00005990 — *LAN865x Linux Driver Installation* (revision DS00005990C, 2025) |
| Length | application note |
| Use here | Linux-side driver install instructions.  Background only.  Of particular interest because Microchip's Linux engineer Parthiban Veerasooran added LAN8651 TSU configuration to the upstream Linux driver in August 2025 (see `documentation/ptp/README_cross.md` §9.7) — the Linux driver is the closest equivalent of "what Annex H support could look like in a kernel-quality Open Source codebase". |
| Summary | Step-by-step build and install guide for the upstream Linux LAN865x MAC-PHY driver on a Raspberry Pi 4 Model B running kernels 6.6.51 / 6.12.25, paired with the MikroE Two-Wire ETH Click board (LAN8651 Rev B1).  Lists the minimum supported kernels — 6.12 onwards for B0 silicon, 6.13 onwards for B1 — and describes two integration paths: built-in kernel support and Loadable Kernel Module (LKM).  Most steps are mechanical: apt-get prerequisites, git-clone the Raspberry Pi kernel tree, and overwrite four driver files (drivers/net/ethernet/microchip/lan865x/lan865x.c, drivers/net/phy/microchip_t1s.c, drivers/net/ethernet/oa_tc6.c, include/linux/oa_tc6.h) with the matching files from the rpi-6.13.y branch when running on a 6.12 source tree.  Build (bcm2711_defconfig, KERNEL=kernel8), install modules, edit config.txt overlays, then bring up the interface with ip / iproute2 utilities.  Like the Zephyr AN, this document is purely about getting an Ethernet link running — there is no PTP, TSU, or 1588 configuration discussion. |
| URL | https://ww1.microchip.com/downloads/aemDocuments/documents/NCS/ApplicationNotes/ApplicationNotes/LAN865x-Linux-Driver-Install-Application-Note-00005990.pdf |
| Read first if | you want to compare the Linux driver's PTP integration model with what we are designing for Harmony / Zephyr |

---

## 4. Recommended reading order

For someone starting on the PTP / Annex H roadmap:

1. **DS60001734** (datasheet) — sections covering the TSU and PLCA registers
2. **AN60001847** (time-synch AN) — full read; this is the canonical PTP-on-T1S reference
3. **AN60001760** (configuration AN) — the init flow context
4. **ER80001075** (errata) — for the specific values you'll write to the chip
5. **AN00006067** (topology discovery) — only when you start on multi-node work

Followed by the project-internal docs:

6. `documentation/ptp/plca_ptp_asymmetrie.md` — the architectural picture
7. `documentation/ptp/README_cross.md` §2 — the irreducible driver patches today
8. `PROMPT_annex_h_implementation.md` (when written) — the implementation plan
9. `PROMPT_annex_h_test_rig.md` — the verification harness for autonomous execution

---

## 5. How to find newer revisions

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

**Index created:** 2026-04-27
