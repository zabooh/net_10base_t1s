#!/usr/bin/env python3
"""
flash.py
--------
Programs the iperf firmware image onto two boards via MPLAB MDB.

Out-of-the-box after a fresh clone:
  1. python setup_flasher.py      # detect + assign the two debugger COM ports
  2. python flash.py              # flashes prebuilt/ptp_standalone_demo.hex

After a local build (build.bat), flash.py automatically prefers the
freshly-built out/tcpip_iperf_lan865x/default.hex instead of the
checked-in prebuilt image.  Override either default with --hex <path>.

Usage:
  python flash.py
  python flash.py --hex <path/to/firmware.hex>
  python flash.py --board1-only
  python flash.py --board2-only
"""

import sys
import os
import argparse
import json

_HERE = os.path.dirname(os.path.abspath(__file__))

# Default HEX resolution:
#   1. Fresh build output  (out/tcpip_iperf_lan865x/default.hex) — preferred
#      when the user has just run build.bat.
#   2. Prebuilt demo HEX   (prebuilt/ptp_standalone_demo.hex) — checked in
#      for out-of-the-box experience: after clone + setup_flasher.py,
#      "python flash.py" immediately programs both boards without needing
#      an XC32 toolchain + full build.
# --hex <path> overrides everything.
HEX_BUILD_OUTPUT = os.path.join(_HERE, r"out\tcpip_iperf_lan865x\default.hex")
HEX_PREBUILT     = os.path.join(_HERE, r"prebuilt\ptp_standalone_demo.hex")
HEX_DEFAULT      = HEX_BUILD_OUTPUT if os.path.isfile(HEX_BUILD_OUTPUT) else HEX_PREBUILT

CONFIG_FILE = os.path.join(_HERE, "setup_flasher.config")


def _load_config():
    """Load setup_flasher.config. Returns (board1, board2) as dicts."""
    if not os.path.isfile(CONFIG_FILE):
        print(f"[ERROR] Configuration file not found: {CONFIG_FILE}")
        print(f"        Please run 'python setup_flasher.py' first to detect and assign your boards.")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    print(f"[INFO] Configuration loaded: {CONFIG_FILE}")
    return cfg["board1"], cfg["board2"]

sys.path.insert(0, _HERE)
from mdb_flash import flash


def main():
    board1_cfg, board2_cfg = _load_config()

    ap = argparse.ArgumentParser(
        description="Flash iperf firmware onto both boards via MDB"
    )
    ap.add_argument(
        "--hex", default=HEX_DEFAULT,
        help=f"Path to HEX file (default: {HEX_DEFAULT})"
    )
    ap.add_argument(
        "--swd-khz", type=int, default=2000,
        help="SWD clock in kHz (default: 2000)"
    )
    ap.add_argument(
        "--board1-only", action="store_true",
        help=f"Flash Board 1 only (SN: {board1_cfg['serial']})"
    )
    ap.add_argument(
        "--board2-only", action="store_true",
        help=f"Flash Board 2 only (SN: {board2_cfg['serial']})"
    )
    args = ap.parse_args()

    hex_path = os.path.abspath(args.hex)

    if not os.path.isfile(hex_path):
        print(f"[ERROR] HEX file not found: {hex_path}")
        print("        Please run build.bat first.")
        return 1

    print(f"\n=== Flash tcpip_iperf_lan865x ===")
    print(f"    HEX: {hex_path}")
    print()

    errors = 0

    if not args.board2_only:
        print(f"### Flash BOARD 1 ({board1_cfg['com_port']}  SN: {board1_cfg['serial']}) ###")
        rc = flash(hex_path, board1_cfg["serial"], label="BOARD1", swd_khz=args.swd_khz)
        if rc != 0:
            print("[BOARD1] ERROR: Programming failed!")
            errors += 1
        else:
            print("[BOARD1] OK")
        print()

    if not args.board1_only:
        print(f"### Flash BOARD 2 ({board2_cfg['com_port']}  SN: {board2_cfg['serial']}) ###")
        rc = flash(hex_path, board2_cfg["serial"], label="BOARD2", swd_khz=args.swd_khz)
        if rc != 0:
            print("[BOARD2] ERROR: Programming failed!")
            errors += 1
        else:
            print("[BOARD2] OK")
        print()

    if errors == 0:
        print("=== Both boards programmed successfully. ===")
    else:
        print(f"=== {errors} board(s) could not be programmed. ===")

    return errors


if __name__ == "__main__":
    sys.exit(main())
