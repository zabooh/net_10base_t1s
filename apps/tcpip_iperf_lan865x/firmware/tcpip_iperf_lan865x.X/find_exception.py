#!/usr/bin/env python3
"""find_exception.py — decode a Cortex-M4 crash dump and locate the
                       faulting source line(s)

Four input modes — pick whichever is easiest after a crash:

  python find_exception.py 0x000249d4         # PC-only, fastest
  python find_exception.py --clipboard        # COPY dump in TeraTerm,
                                              #   then run this — no
                                              #   manual paste at all
  python find_exception.py --paste            # interactive multi-line
                                              #   prompt; end with empty
                                              #   line + Ctrl+Z (Win) /
                                              #   Ctrl+D (Linux).
                                              #   Works for clipboard
                                              #   pastes that line-feed
                                              #   per input() call.
  python find_exception.py --file crash.txt   # read from file
  type crash.txt | python find_exception.py --stdin

For each address found in the dump (PC and stacked LR), the script:

  1. Generates / reuses xc32-objdump -d -S of out/.../default.elf
     into out/.../exception_listing.lst (auto-refresh if the ELF is
     newer than the cache; force with --refresh).
  2. Runs xc32-addr2line for the function name + file:line + inlined
     frames at that address.
  3. Greps the disassembly for the exact instruction and prints
     ~30 instructions of context with a >>> marker on the hit, with
     C source intermixed via -S.

When the full dump is parsed (--paste / --stdin), the script also
decodes the Cortex-M4 fault-status and exception-context registers:

    CFSR    → which sub-fault (UFSR.DIVBYZERO, BFSR.PRECISERR + BFAR
              valid, MMFSR.IACCVIOL, ...)
    HFSR    → FORCED (escalated) / VECTTBL / DEBUGEVT
    EXC_RETURN → MSP vs PSP, Thread vs Handler mode, basic vs extended
                 (FP) stack frame
    ICSR.VECTACTIVE → which vector was running at the time of fault
                      (Thread / system handler / external IRQ N)
    BFAR / MMFAR     → reported as "valid" only if the corresponding
                       BFARVALID / MMARVALID bit is set
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ELF      = "out/tcpip_iperf_lan865x/default.elf"
DEFAULT_LISTING  = "out/tcpip_iperf_lan865x/exception_listing.lst"
DEFAULT_XC32_BIN = r"C:\Program Files\Microchip\xc32\v5.10\bin"


# ---------------------------------------------------------------------------
# Cortex-M4 fault status decoders
# ---------------------------------------------------------------------------

UFSR_BITS = [    # CFSR bits 16-31
    (16, "UNDEFINSTR (undefined instruction)"),
    (17, "INVSTATE (invalid EPSR.T or invalid IT)"),
    (18, "INVPC (invalid PC load by EXC_RETURN)"),
    (19, "NOCP (coprocessor disabled / not present)"),
    (24, "UNALIGNED (unaligned access)"),
    (25, "DIVBYZERO (divide by zero)"),
]
BFSR_BITS = [    # CFSR bits 8-15
    ( 8, "IBUSERR (instruction bus error)"),
    ( 9, "PRECISERR (precise data bus error)"),
    (10, "IMPRECISERR (imprecise data bus error)"),
    (11, "UNSTKERR (stack unwind on exit)"),
    (12, "STKERR (stack frame push on entry)"),
    (13, "LSPERR (FP lazy state preservation)"),
    (15, "BFARVALID (BFAR holds the faulting address)"),
]
MMFSR_BITS = [   # CFSR bits 0-7
    ( 0, "IACCVIOL (instruction-access violation)"),
    ( 1, "DACCVIOL (data-access violation)"),
    ( 3, "MUNSTKERR (stack unwind on exit)"),
    ( 4, "MSTKERR (stack frame push on entry)"),
    ( 5, "MLSPERR (FP lazy state preservation)"),
    ( 7, "MMARVALID (MMFAR holds the faulting address)"),
]
HFSR_BITS = [
    ( 1, "VECTTBL (read of vector table failed)"),
    (30, "FORCED (escalated from configurable fault)"),
    (31, "DEBUGEVT (debug event in handler mode)"),
]

# ARMv7-M system-exception numbers (1..15).  Anything ≥16 is an
# external IRQ and we report just the NVIC index — the user can grep
# same54p20a.h for the symbolic name.
SYSTEM_VECTOR_NAMES = {
    1: "Reset",
    2: "NMI",
    3: "HardFault",
    4: "MemManage",
    5: "BusFault",
    6: "UsageFault",
    11: "SVCall",
    12: "DebugMonitor",
    14: "PendSV",
    15: "SysTick",
}


def decode_bits(value, table):
    """Return list of human-readable bit-name strings for the bits set
    in `value` matching `table` entries (bit, label)."""
    return [label for bit, label in table if (value >> bit) & 1]


def decode_cfsr(cfsr):
    out = []
    mmfsr = cfsr & 0xFF
    bfsr  = (cfsr >> 8) & 0xFF
    ufsr  = (cfsr >> 16) & 0xFFFF
    if mmfsr: out += [f"MMFSR.{x}" for x in decode_bits(cfsr, MMFSR_BITS)]
    if bfsr:  out += [f"BFSR.{x}"  for x in decode_bits(cfsr, BFSR_BITS)]
    if ufsr:  out += [f"UFSR.{x}"  for x in decode_bits(cfsr, UFSR_BITS)]
    return out


def decode_hfsr(hfsr):
    return [f"HFSR.{x}" for x in decode_bits(hfsr, HFSR_BITS)]


def decode_exc_return(lr):
    """Cortex-M4 EXC_RETURN bits (the value loaded into LR on
    exception entry).  Bits 31:28 are always 1111 (0xF...).  We care
    about bits 4 (FP frame), 3 (Thread/Handler), 2 (PSP/MSP)."""
    out = []
    out.append("PSP" if (lr & 0x4) else "MSP")
    out.append("Thread mode" if (lr & 0x8) else "Handler mode (nested)")
    out.append("basic 8-word frame" if (lr & 0x10) else "extended 26-word frame (FP)")
    return out


def decode_icsr(icsr):
    """ICSR.VECTACTIVE = bits 8:0.  0 = Thread mode."""
    vec = icsr & 0x1FF
    if vec == 0:
        return f"VECTACTIVE=0 (Thread mode — no exception was running)"
    if vec in SYSTEM_VECTOR_NAMES:
        return f"VECTACTIVE={vec} ({SYSTEM_VECTOR_NAMES[vec]})"
    return (f"VECTACTIVE={vec} (External IRQ {vec - 16}; "
            f"see same54p20a.h IRQn enum)")


# ---------------------------------------------------------------------------
# Crash-dump parser — turns the raw exception_handler.c output text
# into a dict of {name: int}.  Tolerant of small format variations.
# ---------------------------------------------------------------------------

DUMP_KEY_PAT = re.compile(
    r"\b(CFSR|HFSR|DFSR|MMFAR|BFAR|AFSR|ICSR|SHCSR|EXC_RETURN|"
    r"SP|R0|R1|R2|R3|R12|LR|PC|xPSR)\b"
    r"\s*\(?[^=]*\)?\s*=\s*(0x[0-9a-fA-F]+)"
)
FAULT_NAME_PAT = re.compile(r"^Fault\s*:\s*(\S+)", re.MULTILINE)


def parse_dump(text):
    """Pull register-name → value pairs out of the raw dump text.
    Normalises CRLF to LF and strips zero-width / non-breaking
    characters that some terminals inject around copy operations."""
    if not text:
        return {}, None
    # Normalise line endings + strip BOM / zero-width clipboard noise.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "").replace("​", "")

    found = {}
    for m in DUMP_KEY_PAT.finditer(text):
        name, val = m.group(1), m.group(2)
        try:
            found[name] = int(val, 16)
        except ValueError:
            pass
    fault = None
    fm = FAULT_NAME_PAT.search(text)
    if fm:
        fault = fm.group(1)
    return found, fault, text


def parse_addr(s):
    s = s.strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s, 16)


def thumb_clean(addr):
    return addr & ~1


# ---------------------------------------------------------------------------
# XC32 toolchain wrappers
# ---------------------------------------------------------------------------

def find_xc32_tool(xc32_bin, name):
    for p in (os.path.join(xc32_bin, name), os.path.join(xc32_bin, name + ".exe")):
        if os.path.isfile(p):
            return p
    sys.exit(f"[ERROR] Can't find {name} in {xc32_bin}")


def regenerate_listing(elf, listing, objdump):
    listing.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Generating listing -> {listing}")
    with open(listing, "w", encoding="utf-8", errors="replace") as fout:
        cp = subprocess.run(
            [objdump, "-d", "-S", "--demangle", str(elf)],
            stdout=fout, stderr=subprocess.PIPE, text=True,
        )
    if cp.returncode != 0:
        sys.exit(f"[ERROR] objdump failed: {cp.stderr}")
    print(f"[INFO] Listing written ({listing.stat().st_size // 1024} KiB)")


def addr2line(elf, addr, addr2line_tool):
    cp = subprocess.run(
        [addr2line_tool, "-f", "-i", "-C", "-e", str(elf), f"0x{addr:x}"],
        capture_output=True, text=True,
    )
    if cp.returncode != 0:
        return f"(addr2line failed: {cp.stderr.strip()})"
    return cp.stdout.strip() or "(no debug info for this address)"


def grep_listing(listing, addr, context):
    target_hex = f"{addr:x}"
    target_pat = re.compile(rf"(?<![0-9a-fA-F]){re.escape(target_hex)}:")
    lines = listing.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for i, ln in enumerate(lines):
        if target_pat.match(ln.lstrip()):
            hits.append(i)
    out = []
    for h in hits:
        lo = max(0, h - context)
        hi = min(len(lines), h + context // 2 + 1)
        out.append((h, lines[lo:hi], lo))
    return out


# ---------------------------------------------------------------------------
# Address-resolution helper used for both PC and stacked LR
# ---------------------------------------------------------------------------

def resolve_address(label, raw_addr, elf, listing, addr2line_tool, context):
    addr = thumb_clean(raw_addr)
    print()
    print("=" * 72)
    print(f"  {label} : 0x{raw_addr:08x}  (Thumb-cleaned 0x{addr:08x})")
    print("=" * 72)

    print()
    print(f"--- addr2line for {label} ---")
    print(addr2line(elf, addr, addr2line_tool))

    hits = grep_listing(listing, addr, context)
    print()
    if not hits:
        print(f"[WARN] No exact match for 0x{addr:x} in the disassembly.")
        print("       Try --refresh in case the listing is stale, or check")
        print("       that the firmware on the board matches the local ELF.")
        return False

    for hit_line, slab, base in hits:
        rel = hit_line - base
        print(f"--- Disassembly context for {label} "
              f"(line {hit_line+1} of {listing.name}) ---")
        for i, ln in enumerate(slab):
            marker = "  >>> " if i == rel else "      "
            print(f"{marker}{ln}")
        print()
    return True


# ---------------------------------------------------------------------------
# Dump-decoding output
# ---------------------------------------------------------------------------

def print_decoded_dump(regs, fault_name):
    print()
    print("=" * 72)
    print("  Decoded crash dump")
    print("=" * 72)
    if fault_name:
        print(f"  Fault entry      : {fault_name}_Handler")
    if "CFSR" in regs:
        cfsr  = regs["CFSR"]
        flags = decode_cfsr(cfsr)
        print(f"  CFSR  = 0x{cfsr:08x}  -> "
              + (", ".join(flags) if flags else "no flags set"))
    if "HFSR" in regs:
        hfsr  = regs["HFSR"]
        flags = decode_hfsr(hfsr)
        print(f"  HFSR  = 0x{hfsr:08x}  -> "
              + (", ".join(flags) if flags else "no flags set"))
    if "BFAR" in regs and "CFSR" in regs:
        valid = (regs["CFSR"] >> 15) & 1
        addr  = regs["BFAR"]
        print(f"  BFAR  = 0x{addr:08x}  ({'VALID — faulting address' if valid else 'invalid (BFARVALID=0)'})")
    if "MMFAR" in regs and "CFSR" in regs:
        valid = (regs["CFSR"] >> 7) & 1
        addr  = regs["MMFAR"]
        print(f"  MMFAR = 0x{addr:08x}  ({'VALID — faulting address' if valid else 'invalid (MMARVALID=0)'})")
    if "EXC_RETURN" in regs:
        lr = regs["EXC_RETURN"]
        decode = decode_exc_return(lr)
        print(f"  EXC_RETURN = 0x{lr:08x}  -> " + ", ".join(decode))
    if "ICSR" in regs:
        print(f"  ICSR  = 0x{regs['ICSR']:08x}  -> " + decode_icsr(regs["ICSR"]))


# ---------------------------------------------------------------------------
# Input acquisition
# ---------------------------------------------------------------------------

def read_paste_or_stdin(use_stdin):
    if use_stdin:
        return sys.stdin.read()
    print("Paste the full crash dump (end with an empty line and Ctrl+Z<Enter>"
          " on Windows, Ctrl+D on Linux):", flush=True)
    lines = []
    try:
        while True:
            ln = input()
            lines.append(ln)
    except (EOFError, KeyboardInterrupt):
        pass
    return "\n".join(lines)


def read_clipboard():
    """Read the system clipboard via tkinter (Python stdlib).  No pip
    install needed.  Avoids all the line-buffering quirks of pasting
    multi-line text into a Windows / Linux console input()."""
    try:
        import tkinter
    except ImportError:
        sys.exit("[ERROR] tkinter not available in this Python install — "
                 "use --paste, --stdin or --file instead.")
    try:
        root = tkinter.Tk()
        root.withdraw()                  # hide the otherwise-empty window
        root.update()                    # required on some platforms
        text = root.clipboard_get()
        root.destroy()
    except Exception as exc:
        sys.exit(f"[ERROR] Could not read clipboard: {exc}")
    if not text.strip():
        sys.exit("[ERROR] Clipboard is empty.")
    return text


def read_file(path):
    p = Path(path)
    if not p.is_file():
        sys.exit(f"[ERROR] File not found: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("address", nargs="?",
                   help="exception PC, e.g. 0x000249d4 (interactive if omitted)")
    p.add_argument("--paste", action="store_true",
                   help="prompt for a full multi-line crash dump")
    p.add_argument("--clipboard", "-c", action="store_true",
                   help="read crash dump from system clipboard (tkinter)")
    p.add_argument("--file", "-f",
                   help="read crash dump from a text file")
    p.add_argument("--stdin", action="store_true",
                   help="read full crash dump from stdin (e.g. type x.txt | ...)")
    p.add_argument("--elf",      default=DEFAULT_ELF)
    p.add_argument("--listing",  default=DEFAULT_LISTING)
    p.add_argument("--xc32-bin", default=DEFAULT_XC32_BIN)
    p.add_argument("--context",  type=int, default=30)
    p.add_argument("--refresh",  action="store_true")
    args = p.parse_args()

    here    = Path(__file__).resolve().parent
    elf     = (here / args.elf).resolve()
    listing = (here / args.listing).resolve()
    if not elf.is_file():
        sys.exit(f"[ERROR] ELF not found: {elf}\n"
                 f"        Build first ('build.bat') or pass --elf.")

    objdump        = find_xc32_tool(args.xc32_bin, "xc32-objdump")
    addr2line_tool = find_xc32_tool(args.xc32_bin, "xc32-addr2line")

    if args.refresh or not listing.is_file():
        regenerate_listing(elf, listing, objdump)
    elif elf.stat().st_mtime > listing.stat().st_mtime:
        print("[INFO] ELF newer than listing — regenerating")
        regenerate_listing(elf, listing, objdump)
    else:
        print(f"[INFO] Reusing existing listing: {listing}")

    # ------------------------------------------------------------------
    # Mode A — full crash dump from clipboard / file / stdin / paste
    # ------------------------------------------------------------------
    if args.clipboard or args.file or args.paste or args.stdin:
        if args.clipboard:
            raw = read_clipboard()
        elif args.file:
            raw = read_file(args.file)
        else:
            raw = read_paste_or_stdin(args.stdin)
        regs, fault_name, normalised = parse_dump(raw)
        if not regs:
            print("[ERROR] No recognisable register lines in input.", file=sys.stderr)
            print(f"[DEBUG] Raw length: {len(raw)} chars  "
                  f"({sum(1 for c in raw if c == chr(10))} LF, "
                  f"{sum(1 for c in raw if c == chr(13))} CR)",
                  file=sys.stderr)
            preview = normalised[:400].replace("\t", "·").replace("\n", "\\n\n")
            print("[DEBUG] First 400 chars after normalisation:",
                  file=sys.stderr)
            print("--------------------------------------------",
                  file=sys.stderr)
            print(preview, file=sys.stderr)
            print("--------------------------------------------",
                  file=sys.stderr)
            print("[HINT] Expected lines like 'PC (EXCEPTION ADDRESS) = 0x000249d4'.",
                  file=sys.stderr)
            print("       If the clipboard came from a non-text format (e.g. RTF",
                  file=sys.stderr)
            print("       from PuTTY's Edit menu), try copying as plain text or",
                  file=sys.stderr)
            print("       use --file <path>.", file=sys.stderr)
            return 3
        print_decoded_dump(regs, fault_name)
        # Resolve PC + stacked LR (LR is "return address" of the
        # function in which the fault happened — the caller).
        if "PC" in regs:
            resolve_address("PC (faulting instruction)",
                            regs["PC"], elf, listing,
                            addr2line_tool, args.context)
        if "LR" in regs and regs["LR"] != regs.get("PC"):
            resolve_address("LR (caller — return address)",
                            regs["LR"], elf, listing,
                            addr2line_tool, args.context)
        return 0

    # ------------------------------------------------------------------
    # Mode B — single PC address
    # ------------------------------------------------------------------
    if args.address is None:
        try:
            args.address = input("Exception address (hex, e.g. 0x000249d4): ")
        except (EOFError, KeyboardInterrupt):
            return 1
    try:
        raw_addr = parse_addr(args.address)
    except ValueError:
        sys.exit(f"[ERROR] cannot parse address {args.address!r} as hex")

    ok = resolve_address("Exception address",
                         raw_addr, elf, listing, addr2line_tool, args.context)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
