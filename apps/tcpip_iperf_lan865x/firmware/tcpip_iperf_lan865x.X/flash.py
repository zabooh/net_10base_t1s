#!/usr/bin/env python3
"""
flash.py
--------
Programmiert das iperf-Firmware-Image auf zwei Boards via MPLAB MDB.

Voraussetzung: build.bat bzw. ninja muss vorher ausgeführt worden sein.

Aufruf:
  python flash.py
  python flash.py --hex <pfad/zur/firmware.hex>
  python flash.py --board1-only
  python flash.py --board2-only
"""

import sys
import os
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))

HEX_DEFAULT = os.path.join(_HERE, r"out\tcpip_iperf_lan865x\default.hex")

BOARD1_SERIAL = "ATML3264031800001049"
BOARD2_SERIAL = "ATML3264031800001290"

sys.path.insert(0, _HERE)
from mdb_flash import flash


def main():
    ap = argparse.ArgumentParser(
        description="Flash iperf firmware auf beide Boards via MDB"
    )
    ap.add_argument(
        "--hex", default=HEX_DEFAULT,
        help=f"Pfad zur HEX-Datei (default: {HEX_DEFAULT})"
    )
    ap.add_argument(
        "--swd-khz", type=int, default=2000,
        help="SWD-Takt in kHz (default: 2000)"
    )
    ap.add_argument(
        "--board1-only", action="store_true",
        help="Nur Board 1 programmieren (SN: ATML3264031800001049)"
    )
    ap.add_argument(
        "--board2-only", action="store_true",
        help="Nur Board 2 programmieren (SN: ATML3264031800001290)"
    )
    args = ap.parse_args()

    hex_path = os.path.abspath(args.hex)

    if not os.path.isfile(hex_path):
        print(f"[ERROR] HEX nicht gefunden: {hex_path}")
        print("        Bitte zuerst build.bat ausführen.")
        return 1

    print(f"\n=== Flash tcpip_iperf_lan865x ===")
    print(f"    HEX: {hex_path}")
    print()

    errors = 0

    if not args.board2_only:
        print("### Flash BOARD 1 ###")
        rc = flash(hex_path, BOARD1_SERIAL, label="BOARD1", swd_khz=args.swd_khz)
        if rc != 0:
            print("[BOARD1] FEHLER beim Programmieren!")
            errors += 1
        else:
            print("[BOARD1] OK")
        print()

    if not args.board1_only:
        print("### Flash BOARD 2 ###")
        rc = flash(hex_path, BOARD2_SERIAL, label="BOARD2", swd_khz=args.swd_khz)
        if rc != 0:
            print("[BOARD2] FEHLER beim Programmieren!")
            errors += 1
        else:
            print("[BOARD2] OK")
        print()

    if errors == 0:
        print("=== Beide Boards erfolgreich programmiert. ===")
    else:
        print(f"=== {errors} Board(s) konnten nicht programmiert werden. ===")

    return errors


if __name__ == "__main__":
    sys.exit(main())
