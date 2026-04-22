#!/usr/bin/env python3
"""find_exception.py — locate the source line for an exception PC

Pasted next to the standard XC32 build artefacts.  Given an exception
address from the crash-dump produced by exception_handler.c (the line
that says "PC (EXCEPTION ADDRESS) = 0x000xxxxx"), this script:

  1. Generates a full disassembly listing of out/.../default.elf
     (xc32-objdump -d -S) into exception_listing.lst — kept in
     the build directory for inspection.
  2. Looks up the address with xc32-addr2line and prints the
     resolved file:line + function name.
  3. Greps the disassembly for the address (with the Thumb bit
     masked off) and prints ~20 instructions of context plus the
     surrounding C source intermixed (-S option).

Usage:
    python find_exception.py 0x000249d0
    python find_exception.py            # interactive prompt

Optional:
    --elf <path>     # default: out/tcpip_iperf_lan865x/default.elf
    --xc32-bin <dir> # default: C:\\Program Files\\Microchip\\xc32\\v5.10\\bin
    --context N      # lines of context around the hit (default 30)
    --refresh        # always regenerate the listing even if already there
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


def parse_addr(s: str) -> int:
    s = s.strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    return int(s, 16)   # hex by default — addresses come from a hex dump


def thumb_clean(addr: int) -> int:
    """ARM Cortex-M Thumb-mode addresses have bit 0 set as the mode bit;
    the actual instruction is at addr & ~1."""
    return addr & ~1


def find_xc32_tool(xc32_bin: str, name: str) -> str:
    candidates = [
        os.path.join(xc32_bin, name),
        os.path.join(xc32_bin, name + ".exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    sys.exit(f"[ERROR] Can't find {name} in {xc32_bin}")


def regenerate_listing(elf: Path, listing: Path, objdump: str) -> None:
    listing.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Generating listing -> {listing}")
    with open(listing, "w", encoding="utf-8", errors="replace") as fout:
        cp = subprocess.run(
            [objdump, "-d", "-S", "--demangle", str(elf)],
            stdout=fout, stderr=subprocess.PIPE, text=True,
        )
    if cp.returncode != 0:
        sys.exit(f"[ERROR] objdump failed: {cp.stderr}")
    sz_kb = listing.stat().st_size // 1024
    print(f"[INFO] Listing written ({sz_kb} KiB)")


def addr2line(elf: Path, addr: int, addr2line_tool: str) -> str:
    cp = subprocess.run(
        [addr2line_tool, "-f", "-i", "-C", "-e", str(elf), f"0x{addr:x}"],
        capture_output=True, text=True,
    )
    if cp.returncode != 0:
        return f"(addr2line failed: {cp.stderr.strip()})"
    return cp.stdout.strip() or "(no debug info for this address)"


def grep_listing(listing: Path, addr: int, context: int) -> list:
    """Find the line containing the disassembled instruction at addr.
    objdump emits the address with leading whitespace + variable width
    (no fixed zero-pad on the leading nibble), e.g.:
       '   249d4:\\tfb93 f3f2 \\tsdiv\\tr3, r3, r2'
    so we accept the bare hex tag preceded by any whitespace and not
    preceded by another hex digit (avoids matching '1249d4:')."""
    target_hex = f"{addr:x}"
    target_pat = re.compile(rf"(?<![0-9a-fA-F]){re.escape(target_hex)}:")
    lines = listing.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for i, ln in enumerate(lines):
        # Address tag is at the very start of the line (after optional WS)
        s = ln.lstrip()
        if not s:
            continue
        if target_pat.match(s):
            hits.append(i)
    if not hits:
        return []
    out = []
    for h in hits:
        lo = max(0, h - context)
        hi = min(len(lines), h + context // 2 + 1)
        out.append((h, lines[lo:hi], lo))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("address", nargs="?",
                   help="exception PC, e.g. 0x000249d0 (interactive if omitted)")
    p.add_argument("--elf",      default=DEFAULT_ELF)
    p.add_argument("--listing",  default=DEFAULT_LISTING)
    p.add_argument("--xc32-bin", default=DEFAULT_XC32_BIN)
    p.add_argument("--context",  type=int, default=30,
                   help="instructions of context before the hit (default 30)")
    p.add_argument("--refresh",  action="store_true",
                   help="regenerate the listing even if it already exists")
    args = p.parse_args()

    if args.address is None:
        try:
            args.address = input("Exception address (hex, e.g. 0x000249d0): ")
        except (EOFError, KeyboardInterrupt):
            return 1

    try:
        raw_addr = parse_addr(args.address)
    except ValueError:
        sys.exit(f"[ERROR] cannot parse address {args.address!r} as hex")
    addr = thumb_clean(raw_addr)

    here = Path(__file__).resolve().parent
    elf     = (here / args.elf).resolve()
    listing = (here / args.listing).resolve()
    if not elf.is_file():
        sys.exit(f"[ERROR] ELF not found: {elf}\n"
                 f"        Build first ('build.bat') or pass --elf.")

    objdump   = find_xc32_tool(args.xc32_bin, "xc32-objdump")
    addr2line_tool = find_xc32_tool(args.xc32_bin, "xc32-addr2line")

    if args.refresh or not listing.is_file():
        regenerate_listing(elf, listing, objdump)
    else:
        # Auto-refresh if ELF is newer than the listing.
        if elf.stat().st_mtime > listing.stat().st_mtime:
            print("[INFO] ELF newer than listing — regenerating")
            regenerate_listing(elf, listing, objdump)
        else:
            print(f"[INFO] Reusing existing listing: {listing}")

    print()
    print("=" * 72)
    print(f"  Exception address : 0x{raw_addr:08x}  (Thumb-cleaned 0x{addr:08x})")
    print("=" * 72)

    print()
    print("--- addr2line (function / file:line, with inlined frames) ---")
    print(addr2line(elf, addr, addr2line_tool))

    hits = grep_listing(listing, addr, args.context)
    print()
    if not hits:
        print(f"[WARN] No exact match for 0x{addr:x} in the disassembly.")
        print("       Try --refresh in case the listing is stale, or check")
        print("       that the firmware on the board matches the local ELF.")
        return 2

    for hit_line, slab, base in hits:
        rel = hit_line - base
        print(f"--- Disassembly listing (hit at line {hit_line+1} of {listing.name}) ---")
        for i, ln in enumerate(slab):
            marker = "  >>> " if i == rel else "      "
            print(f"{marker}{ln}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
