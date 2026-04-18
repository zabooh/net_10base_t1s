# Software NTP — Application-Layer Time Sync Reference

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. How it Works](#2-how-it-works)
  - [2.1 The Protocol](#21-the-protocol)
  - [2.2 Packet Format](#22-packet-format)
  - [2.3 Why Four Timestamps](#23-why-four-timestamps)
- [3. Module Structure](#3-module-structure)
- [4. CLI Commands](#4-cli-commands)
  - [4.1 Master-side](#41-master-side)
  - [4.2 Follower-side](#42-follower-side)
  - [4.3 Manual workflow](#43-manual-workflow)
- [5. Firmware Flow](#5-firmware-flow)
  - [5.1 Master service](#51-master-service)
  - [5.2 Follower service](#52-follower-service)
  - [5.3 Ring buffer](#53-ring-buffer)
- [6. Test Script](#6-test-script)
- [7. Measured Results](#7-measured-results)
- [8. Interpretation and Limits](#8-interpretation-and-limits)
- [9. What This Test Proves About PTP Correctness](#9-what-this-test-proves-about-ptp-correctness)
- [10. Using PTP_CLOCK in Your Own Code](#10-using-ptp_clock-in-your-own-code)

---

## 1. Purpose

This module is a **minimal, measurement-only software NTP** (client + server) built on
top of the Microchip Harmony 3 UDP stack. It exists to answer one specific question:

> *How good would a pure-software time-sync protocol be on this platform,
> with no hardware timestamping support?*

The four timestamps (T1..T4) are all taken in the application layer with
`PTP_CLOCK_GetTime_ns()`, **after** the TCP/IP stack has processed the frame. No
hardware timestamp capture, no PHY assist — all SPI, FreeRTOS, and TCP/IP stack
latencies end up inside the measured jitter. That is the point.

**The follower only measures — it never corrects the clock.** This is deliberate: it
lets SW-NTP run alongside HW-PTP and observe the underlying clock sync without
interfering with it. As a direct consequence, two identical captures (one with HW-PTP
off, one with HW-PTP running) can be compared in a single test.

---

## 2. How it Works

### 2.1 The Protocol

The classic NTP four-timestamp exchange:

```
 Follower                                     Master
 --------                                     ------
 T1 = PTP_CLOCK_GetTime_ns()
               ── UDP request (type=1, seq=N) ──▶
                                              T2 = PTP_CLOCK_GetTime_ns()
                                              (process packet)
                                              T3 = PTP_CLOCK_GetTime_ns()
               ◀── UDP reply (type=2, T1,T2,T3) ──
 T4 = PTP_CLOCK_GetTime_ns()

 offset = ((T2 - T1) + (T3 - T4)) / 2
```

UDP port **12345** (avoids collision with real NTP on 123 and iperf on 5001).
Default poll interval **1000 ms**. Response timeout **200 ms** — late replies are
counted as timeouts and recorded as `(0, valid=0)` in the ring buffer.

### 2.2 Packet Format

32 bytes total, packed, little-endian (no host/network conversion — both peers run the
same MCU):

| Offset | Size | Field       | Description                         |
|--------|------|-------------|-------------------------------------|
| 0      | 1    | `type`      | 1 = request, 2 = response           |
| 1      | 1    | `seq`       | Follower sequence number            |
| 2      | 6    | `reserved`  | Alignment / future use              |
| 8      | 8    | `t1_ns`     | Follower send time (int64, ns)      |
| 16     | 8    | `t2_ns`     | Master recv time  (int64, ns)       |
| 24     | 8    | `t3_ns`     | Master send time  (int64, ns)       |

T4 is stamped only on the follower and never transmitted — it stays local for the
offset computation.

### 2.3 Why Four Timestamps

With just a one-way request you cannot separate clock offset from path delay; they
are numerically indistinguishable. The four-timestamp round-trip factors them:

```
T2 − T1 = forward_delay + offset
T4 − T3 = backward_delay − offset

If forward_delay ≈ backward_delay (symmetric path):
  offset    = ((T2 − T1) + (T3 − T4)) / 2
  pathDelay = ((T2 − T1) − (T3 − T4)) / 2
```

Any asymmetry in the forward vs. backward delay leaks directly into the offset
estimate. On 10BASE-T1S with PLCA this can produce a small systematic bias (~100 µs)
that no amount of filtering removes.

---

## 3. Module Structure

```
apps/tcpip_iperf_lan865x/firmware/src/
├── sw_ntp.h                       # Public API
├── sw_ntp.c                       # UDP client+server, T1..T4 stamping, offset calc
├── sw_ntp_offset_trace.h          # Ring-buffer API
└── sw_ntp_offset_trace.c          # 1024-entry int64 ring buffer, batched UART dump
```

Wired into the main loop via:

| Location                   | Call                                         |
|----------------------------|----------------------------------------------|
| `APP_Initialize()`         | `sw_ntp_init()`                              |
| `APP_Tasks()` STATE_IDLE   | `sw_ntp_service()` — called every iteration |

The service is called on **every** main-loop iteration, not once per ms. This keeps
RX-polling latency low: the master can respond to an incoming request within a few
µs of the Harmony stack surfacing it, and the follower can stamp T4 within a few µs
of the response becoming visible.

Build integration:

```cmake
# apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/cmake/.../user.cmake
target_sources(... PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/sw_ntp.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../src/sw_ntp_offset_trace.c"
)
```

---

## 4. CLI Commands

### 4.1 Master-side

```
sw_ntp_mode master          # Open UDP server socket on port 12345
sw_ntp_mode off             # Close socket, stop responding
sw_ntp_status               # Show current mode
sw_ntp_trace on|off         # Verbose per-packet UART output (disturbs timing)
```

### 4.2 Follower-side

```
sw_ntp_mode follower <ip>   # Open UDP client socket to <ip>:12345
sw_ntp_poll <ms>            # Set poll interval (10..10000 ms, default 1000)
sw_ntp_status               # Mode / samples / timeouts / last_offset_ns
sw_ntp_trace on|off         # Verbose trace with T1/T2/T3/T4/offset/RTT per packet
sw_ntp_offset_reset         # Clear the ring buffer
sw_ntp_offset_dump          # Dump all samples in one batch
sw_ntp_mode off             # Close socket, stop polling
```

### 4.3 Manual workflow

```
# On both boards: set up IP
[GM]  setip eth0 192.168.0.30 255.255.255.0
[FOL] setip eth0 192.168.0.20 255.255.255.0

# Seed the software clock on both — otherwise PTP_CLOCK_GetTime_ns()
# returns 0 for every call and all offsets read 0.
[GM]  clk_set 0
[FOL] clk_set 0

# Start master and follower
[GM]  sw_ntp_mode master
[FOL] sw_ntp_mode follower 192.168.0.30

# Wait ~3 s for the first exchanges to settle
[FOL] sw_ntp_status              # expect samples > 0

# Capture
[FOL] sw_ntp_offset_reset
#   ... let SW-NTP run for the desired window ...
[FOL] sw_ntp_offset_dump
```

Dump output format (one line per sample, chronological order):

```
sw_ntp_offset_dump: start count=60 overwrites=0 capacity=1024
-147 1
-132 1
-198 1
0 0          <- timeout (valid=0); offset value is meaningless
-151 1
...
sw_ntp_offset_dump: end
```

Columns: `<offset_ns> <valid>` where `valid=1` means a complete round-trip produced a
real offset, `valid=0` means the response did not arrive within the 200 ms timeout.

---

## 5. Firmware Flow

### 5.1 Master service

```c
while (TCPIP_UDP_GetIsReady(s_sock) >= sizeof(pkt)) {
    int64_t t2 = PTP_CLOCK_GetTime_ns();   /* stamp as close to RX as layer allows */
    TCPIP_UDP_ArrayGet(s_sock, &pkt, sizeof(pkt));
    if (pkt.type != REQ) continue;

    pkt.type  = RESP;
    pkt.t2_ns = t2;
    pkt.t3_ns = PTP_CLOCK_GetTime_ns();    /* stamp as late as possible before TX */
    TCPIP_UDP_ArrayPut(s_sock, &pkt, sizeof(pkt));
    TCPIP_UDP_Flush(s_sock);
}
```

The server socket automatically replies to the last-received remote address; no
explicit `RemoteBind` is needed. T1 is echoed back verbatim so the follower can pair
the response with its original request, making the protocol robust against
reordering.

### 5.2 Follower service

```c
/* Drain responses first so T4 stamping is low-latency */
while (TCPIP_UDP_GetIsReady(s_sock) >= sizeof(pkt)) {
    int64_t t4 = PTP_CLOCK_GetTime_ns();
    TCPIP_UDP_ArrayGet(s_sock, &pkt, sizeof(pkt));
    if (pkt.type != RESP || pkt.seq != pending_seq) continue;
    int64_t offset = ((pkt.t2_ns - pkt.t1_ns) + (pkt.t3_ns - t4)) / 2;
    sw_ntp_offset_trace_record(offset, 1);
    pending = false;
}

/* Timeout */
if (pending && now - pending_deadline >= 0) {
    sw_ntp_offset_trace_record(0, 0);    /* mark timeout */
    pending = false;
}

/* Send next request when interval has elapsed */
if (!pending && now - last_tx >= poll_interval) {
    pkt.type  = REQ;
    pkt.seq   = ++seq;
    pkt.t1_ns = PTP_CLOCK_GetTime_ns();
    TCPIP_UDP_ArrayPut(s_sock, &pkt, sizeof(pkt));
    TCPIP_UDP_Flush(s_sock);
    pending  = true;
}
```

### 5.3 Ring buffer

`sw_ntp_offset_trace` is deliberately shaped like `ptp_offset_trace`:

- **1024 entries × 9 bytes** (8-byte `int64_t offset_ns` + 1-byte `valid` flag) = 9 KB.
- Overflow wraps around; the `overwrites` counter in the dump header flags this.
- Dump rate-limits to **4 lines per 20 ms** to avoid overrunning the UART TX buffer.
  A 60-sample capture dumps in well under one second.
- **No UART traffic during measurement.** Samples accumulate silently in RAM; all
  UART activity happens before (`sw_ntp_offset_reset`) or after (`sw_ntp_offset_dump`).
  This isolates the measured jitter from UART serialization jitter — a key reason
  the measured numbers are trustworthy.

---

## 6. Test Script

`sw_ntp_vs_ptp_test.py` automates the two-phase comparison:

```
python sw_ntp_vs_ptp_test.py --gm-port COM8 --fol-port COM10
```

Script sequence:

1. Reset both boards, set IPs.
2. `clk_set 0` on **both** boards in parallel threads (skew ~50 µs) — makes
   `PTP_CLOCK` valid so timestamps are non-zero.
3. Start SW-NTP master and follower.
4. **Phase A — HW-PTP OFF:** capture 60 s of SW-NTP offsets. The two PTP clocks
   free-run on their own crystals; drift is the dominant effect.
5. Enable HW-PTP master/follower, wait for FINE (typically < 5 s).
6. **Phase B — HW-PTP ON:** capture another 60 s of SW-NTP offsets. HW-PTP holds
   the PTP clocks in sync; only SW-NTP measurement noise remains.
7. Print per-phase statistics (robust + classical) and a side-by-side comparison.

Statistics computed per phase:

- **Robust (primary):** median, MAD (Median Absolute Deviation), robust stdev
  (= 1.4826 × MAD), IQR (p75 − p25), p05..p95.
- **Classical (sensitive to outliers):** mean, stdev, min, max, |offset| mean.
- **Linear regression:** slope (ns/s, interpretable as ppm crystal drift),
  intercept (ns), classical residual stdev, robust residual stdev.

If the classical stdev exceeds 5× the robust stdev, a "heavy-tailed distribution"
warning is printed — trust the robust number in that case.

Options:

```
--capture-s 60      # duration per phase (default 60 s)
--poll-ms 1000      # SW-NTP poll interval (default 1000 ms)
--csv-a out_a.csv   # save Phase A raw samples
--csv-b out_b.csv   # save Phase B raw samples
--skip-phase-a      # run only Phase B
--skip-phase-b      # run only Phase A
--no-reset          # skip board reset + IP config (assume already set)
```

---

## 7. Measured Results

One representative run on two ATSAME54P20A + LAN865x boards, 1 Hz poll, 60 s per phase,
approx. 50–59 valid samples per phase (the remainder were timeouts):

| Metric                        | Phase A (HW-PTP off) | Phase B (HW-PTP on, FINE) | Ratio   |
|-------------------------------|---------------------:|--------------------------:|--------:|
| Valid samples                 | 54                   | 59                        |         |
| Timeouts                      | 7                    | 2                         |         |
| **Slope**                     | +165 142 ns/s        | +90 ns/s                  | 1800×   |
| &nbsp;&nbsp;(= ppm)           | +165.1 ppm           | +0.09 ppm                 |         |
| **Median offset**             | +4 502 220 ns        | −149 512 ns               | 30×     |
| &nbsp;&nbsp;(= µs)            | +4 502 µs            | −150 µs                   |         |
| **Robust stdev (1.4826·MAD)** | 2 643 µs             | **24 µs**                 | 110×    |
| **IQR** (p75 − p25)           | 4 543 µs             | 32 µs                     | 142×    |
| **Residual robust stdev**     | 850 µs               | **23 µs**                 | 37×     |
| Classical mean                | +5 103 µs            | −169 µs                   |         |
| Classical stdev               | 2 678 µs             | 1 106 µs†                 |         |

† Classical stdev in Phase B is inflated by 2–3 outliers (min −6.4 ms, max +5.5 ms
out of 59 samples). The heavy-tail warning triggers correctly
(classical = 45× robust).

Raw captures (`sw_ntp_vs_ptp_test_*.log`) are stored in this directory.

---

## 8. Interpretation and Limits

### What Phase A tells you

With HW-PTP disabled, the two PTP_CLOCKs run purely off their local TC0 crystals.
SW-NTP's slope estimate is a direct measurement of the **relative crystal frequency
offset** between the two boards:

- Observed: **+165 ppm.** That means one crystal is ~165 ppm faster than the other.
- Typical SAME54 crystal spec: ±20–50 ppm each → up to ±100 ppm combined. 165 ppm
  is on the high side but not abnormal for cheap boards.
- Residual robust stdev after removing the linear trend: **~850 µs.** This is the
  SW-NTP jitter floor *on a free-running clock*. It includes crystal short-term
  noise (temperature, supply) plus all the SW stacking jitter.

### What Phase B tells you

With HW-PTP active and FINE:

- Slope collapses from 165 ppm to 0.09 ppm — the HW-PTP PI controller tracks the
  grandmaster's rate to within a fraction of a ppm.
- Median offset sits at a systematic **−150 µs**. This is not clock skew (HW-PTP
  holds the clocks to ns-level); it is **TX/RX stack asymmetry**. The forward path
  and reverse path do not have exactly the same application-layer latency, and the
  NTP formula assumes they do. No amount of filtering removes this bias.
- Robust stdev: **24 µs.** This is the SW-NTP jitter floor *on a regulated clock*.
- Classical stdev is ~1 ms due to occasional 5–10 ms outliers — likely SPI bus
  contention with concurrent HW-PTP Sync frames. Use the robust figure.

### The surprise

A naive prediction would be that HW-PTP only flattens the mean (kills drift),
leaving short-term jitter unchanged — because jitter "should" be dominated by stack
latency, which HW-PTP cannot affect.

That prediction is wrong by a factor of ~37×. The regulated PTP_CLOCK is not just
*more accurate* than the free-running one; it is also **inherently steadier.** The
PI controller applies tens of tiny corrections per second, damping the fine-grained
crystal noise that would otherwise leak into every SW-NTP reading.

Practical consequence: SW-NTP-style protocols on microcontrollers can be
considerably more accurate than expected *if* they run on top of a HW-disciplined
clock, even when the SW protocol itself knows nothing about PTP.

### Limits

- **Bias** (~100–200 µs). Systematic stack/PLCA asymmetry between TX and RX paths.
  Not removable with this design.
- **Heavy tail.** 3–5 % of samples in either phase can be delayed by 5–10 ms
  because of FreeRTOS task preemption or SPI bus contention with the HW-PTP driver.
- **Timeouts** (3–10 % typical). 200 ms is plenty for a healthy round-trip; a
  timeout at this length means a packet was genuinely lost or the stack was blocked
  for that long.
- **No regulation.** This module deliberately does not discipline `PTP_CLOCK`.
  Building an actual SW-NTP-disciplined clock variant would require a PI loop on
  top of `sw_ntp_offset_trace_record()`; the jitter floor numbers above would
  become the input noise to that loop.

---

## 9. What This Test Proves About PTP Correctness

Beyond characterising software-NTP itself, `sw_ntp_vs_ptp_test.py` doubles as an
**independent, black-box, end-to-end validation of the HW-PTP implementation.**
It complements the two other PTP tests; each covers a class of bugs the others
cannot catch.

### 9.1 What this test does prove

Because SW-NTP never touches any PTP internals — it only opens a UDP socket and
reads `PTP_CLOCK_GetTime_ns()` — its view of the clocks is completely independent
of the HW-PTP path. Any disagreement between expectation and measurement would
expose a real problem. The observed behaviour constitutes strong positive
evidence that:

1. **PTP_CLOCK is a correct neutral timebase.** Phase A measures exactly the
   relative crystal offset between the two boards (+165 ppm), linearly growing
   with time. No curvature, no jumps — the clock truthfully tracks TC0 when
   un-disciplined.
2. **The PTP servo is actually running and correcting.** Phase B collapses the
   slope by **1800×** and the mean offset toward 0. Without a working PTP
   implementation, Phase B would look identical to Phase A.
3. **The regulation is tight, not just averaging.** The **37× residual-jitter
   reduction** is the decisive signal: a loose "eventually-consistent" sync
   mechanism (or one that only adjusts anchors sporadically) would leave the
   short-term jitter untouched. Seeing the high-frequency crystal noise damped
   out proves the PI loop is actively pulling the clock at a high enough rate.
4. **The sync signal is reaching the application layer.** HW-PTP could in
   principle look correct internally but fail to make its output visible to
   `PTP_CLOCK_GetTime_ns()` (stale anchor, race condition, wrong unit). SW-NTP
   measures what application code actually sees — and sees it working.

### 9.2 What this test does *not* prove

- **IEEE 1588 protocol compliance.** Two boards could synchronise their clocks
  via a non-standard or proprietary mechanism and still pass this test.
  Message-type encodings, sequence-ID handling, twoStep flags, and the full
  field-by-field layout are outside SW-NTP's line of sight.
- **Absolute hardware-timestamp accuracy.** SW-NTP cannot see the sub-µs
  precision of the PHY-level HW timestamping. A PTP implementation that
  synchronised within 20 µs (instead of 20 ns) at the PHY level would still
  produce passing results here, because the ~25 µs SW-stamping floor hides
  anything tighter than that.
- **Edge cases in the servo.** Convergence from UNINIT, behaviour under packet
  loss, recovery after link bounce, role-swap transitions — none of these are
  probed. The test captures only steady-state FINE-state behaviour.

### 9.3 The three-way test matrix

| Test | Proves | Perspective | Gap it leaves |
|---|---|---|---|
| `ptp_trace_debug_test.py` | IEEE 1588 protocol compliance (message types, sequence IDs, twoStep, HW t3 capture, convergence) | internal (trace logs from GM + FOL) | Protocol is formally correct, but may not actually produce a synchronised clock from outside |
| `ptp_offset_capture.py` | Sub-100-ns HW-timestamp offset at the PHY level during FINE | PTP-internal (ring buffer of computed offsets inside FOL) | Could show tight internal offsets while the user-visible clock is wrong (anchor bug, stale data, unit error) |
| `sw_ntp_vs_ptp_test.py` | User-visible clock alignment via an independent protocol; drift elimination; tight short-term regulation | external, black-box (UDP + `PTP_CLOCK_GetTime_ns()`) | Cannot verify protocol compliance or sub-µs accuracy |

All three together form a fairly complete proof: any failure mode that would
slip past one typically trips another. In particular, the SW-NTP test
closes a gap the first two cannot cover — the scenario *"protocol is compliant
and internal offsets look good, but the application still sees a wrong clock"*
— which is exactly the scenario a production user would notice in the field.

---

## 10. Using PTP_CLOCK in Your Own Code

The SW-NTP results above answer one specific question — *"how accurate is a
software time-sync protocol?"* — but a follow-up question is more practical:
*"if HW-PTP is running, how precisely can I use `PTP_CLOCK_GetTime_ns()` from
my application code?"*

The answer is not a single number. Accuracy depends on which of three layers
you care about.

### 9.1 Layer 1 — The PTP_CLOCK timebase itself

This is the accuracy of the value returned by `PTP_CLOCK_GetTime_ns()` vs. the
Grandmaster's true time, *before* any call-site latency is added.

| Component                                           | Typical error |
|-----------------------------------------------------|--------------:|
| HW-PTP anchor accuracy (SFD-to-SFD at FINE)         | **10–100 ns**   (measured: offsets of −6 ns, −130 ns during FINE) |
| Drift-corrected interpolation between anchors       | 100–500 ns   (IIR residual ~1 ppm over ~125 ms Sync interval) |
| TC0 tick quantisation (60 MHz → 16.67 ns/tick)      | ±8 ns |
| `SYS_TIME_Counter64Get()` read latency              | negligible |

**Total: ~100 ns–1 µs**, depending on how long ago the last Sync anchored the
clock and how well the drift filter has converged.

### 9.2 Layer 2 — Calling it from software context

Between the *event* you want to timestamp and the *moment* your code actually
calls `PTP_CLOCK_GetTime_ns()`, there is unavoidable latency:

| Calling context                                    | Typical jitter |
|----------------------------------------------------|---------------:|
| From an ISR (e.g. EIC EXTINT for an external pin)  | **200 ns – 1 µs**   (ISR dispatch + prolog) |
| From a FreeRTOS task                               | **1 – 10 µs** nominal, up to ms under load |
| From the main loop (`APP_Tasks` IDLE)              | 1 µs – 1 ms depending on loop phase |
| With UART `SYS_CONSOLE_PRINT` running concurrently | up to several ms (blocking) |

### 9.3 Layer 3 — Practical use cases

| What you're doing                                    | Expected accuracy |
|------------------------------------------------------|------------------:|
| **Measuring a local duration** (two reads on same board, e.g. profiling a code section) | **~30–100 ns** — both reads see the same clock; only TC0 quantisation + read jitter contribute. |
| **Timestamping a local event in software** (e.g. GPIO edge handled in ISR) | **~100 ns – 1 µs** — dominated by ISR latency. |
| **Timestamping a cross-board event in software** (both boards stamp the same external trigger in their own app code) | **~25 µs** (robust stdev from §7 of this document) — dominated by SW-stamping asymmetry, NOT by clock accuracy. |
| **Timestamping a cross-board event with HW capture** (LAN865x TTSCA/RTSA on the PHY, or SAME54 TC capture input) | **sub-µs** — same class as PTP itself. |

### 9.4 Rule of thumb

- **Local** (single MCU): the software clock is as good as whatever the compiler
  produces for your code — effectively ns-precise.
- **Cross-board via software**: ~25 µs is the practical floor, limited by SPI +
  stack stamping asymmetry, **not** by PTP.
- **Cross-board sub-µs**: requires a hardware-capture mechanism for the event
  itself. The PTP frames achieve this via LAN865x TTSCA/RTSA; a GPIO event could
  do the same via SAME54 TC/EIC capture.

The `PTP_CLOCK` itself is **not** the bottleneck — anything that needs sub-µs
cross-board resolution needs to capture the event in hardware too.
