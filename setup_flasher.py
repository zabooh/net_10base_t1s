#!/usr/bin/env python3
"""
setup_flasher.py
----------------
Detects connected Microchip/Atmel EDBG debuggers (e.g. Curiosity Nano),
lets the user assign Board 1 and Board 2, and saves the result to
setup_flasher.config (JSON).

Usage:
  python setup_flasher.py
"""

import sys
import json
import os
import serial.tools.list_ports

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_HERE, "setup_flasher.config")


def _is_microchip_debugger(port_info):
    """Heuristic: detect EDBG / PKOB4 / Curiosity Nano debuggers."""
    # Atmel/Microchip USB VID 0x03EB
    if port_info.vid == 0x03EB:
        return True
    # Serial number starts with ATML (Atmel debugger format)
    if port_info.serial_number and port_info.serial_number.startswith("ATML"):
        return True
    # Manufacturer contains Microchip or Atmel
    if port_info.manufacturer:
        mfr = port_info.manufacturer.lower()
        if "microchip" in mfr or "atmel" in mfr:
            return True
    return False


def _com_port_number(port_info):
    """Sort key: numeric part of the COM port name."""
    for part in port_info.device.split("COM"):
        if part.isdigit():
            return int(part)
    return 9999


def _find_debuggers():
    """Return list of all detected debugger ports, sorted by COM number."""
    all_ports = serial.tools.list_ports.comports()
    debuggers = [p for p in all_ports if _is_microchip_debugger(p)]
    return sorted(debuggers, key=_com_port_number)


def _print_port(label, port_info):
    vid_str = hex(port_info.vid) if port_info.vid is not None else "N/A"
    pid_str = hex(port_info.pid) if port_info.pid is not None else "N/A"
    print(f"  {label}:")
    print(f"    Device      : {port_info.device}")
    print(f"    Description : {port_info.description}")
    print(f"    Serial Nr.  : {port_info.serial_number or 'N/A'}")
    print(f"    Manufacturer: {port_info.manufacturer or 'N/A'}")
    print(f"    VID:PID     : {vid_str}:{pid_str}")


def _select_from_list(debuggers, role):
    """Interactively select a port from the list."""
    print(f"\nWhich port is {role}?")
    for i, p in enumerate(debuggers):
        print(f"  [{i + 1}] {p.device}  SN={p.serial_number or 'N/A'}  {p.description}")
    while True:
        raw = input(f"  Selection (1-{len(debuggers)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(debuggers):
            return debuggers[int(raw) - 1]
        print("  Invalid input, please try again.")


def main():
    print("=" * 60)
    print("  setup_flasher.py — Board Configuration")
    print("=" * 60)
    print()

    debuggers = _find_debuggers()

    if not debuggers:
        print("[ERROR] No Microchip/Atmel debuggers found.")
        print("        Please connect both boards via USB and run again.")
        return 1

    print(f"Debuggers found: {len(debuggers)}")
    for i, p in enumerate(debuggers):
        _print_port(f"#{i + 1}", p)
        print()

    # --- Assign Board 1 / Board 2 ---
    if len(debuggers) == 2:
        print("Exactly 2 debuggers found.")
        print(f"  Board 1 -> {debuggers[0].device}  (SN: {debuggers[0].serial_number})")
        print(f"  Board 2 -> {debuggers[1].device}  (SN: {debuggers[1].serial_number})")
        ans = input("\nAccept this assignment? [Y/n]: ").strip().lower()
        if ans in ("n", "no"):
            board1 = _select_from_list(debuggers, "Board 1 (Grandmaster)")
            board2 = _select_from_list(debuggers, "Board 2 (Follower)")
        else:
            board1, board2 = debuggers[0], debuggers[1]
    else:
        print(f"{len(debuggers)} debugger(s) found — please assign manually.")
        board1 = _select_from_list(debuggers, "Board 1 (Grandmaster)")
        board2 = _select_from_list(debuggers, "Board 2 (Follower)")

    # --- Validate ---
    if board1.device == board2.device:
        print("[ERROR] Board 1 and Board 2 are the same device!")
        return 1

    # --- Write config ---
    config = {
        "board1": {
            "serial":   board1.serial_number or "",
            "com_port": board1.device,
            "description": board1.description or ""
        },
        "board2": {
            "serial":   board2.serial_number or "",
            "com_port": board2.device,
            "description": board2.description or ""
        }
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

    print()
    print(f"[OK] Configuration saved: {CONFIG_FILE}")
    print()
    print("  Board 1:")
    print(f"    COM Port  : {board1.device}")
    print(f"    Serial Nr.: {board1.serial_number}")
    print()
    print("  Board 2:")
    print(f"    COM Port  : {board2.device}")
    print(f"    Serial Nr.: {board2.serial_number}")
    print()
    print("Done. flash.py can now be used without any modifications.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
