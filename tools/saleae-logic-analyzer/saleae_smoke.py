#!/usr/bin/env python3
"""saleae_smoke.py — verify Saleae Logic 2 connectivity
========================================================

Minimal sanity check before running cyclic_fire_hw_test.py.  Does four
things in order, reports PASS/FAIL for each, and exits non-zero if any
step fails:

  1. Import `logic2-automation` (verifies the package is installed).
  2. Connect to Logic 2 on localhost:10430.
  3. List connected devices (confirms your Logic 8 is visible).
  4. Run a 200 ms dummy capture on channels 0+1 at 10 MS/s to verify
     that the full start → wait → close path works.

Usage:
    python saleae_smoke.py
    python saleae_smoke.py --duration 1.0 --sample-rate 25000000
    python saleae_smoke.py --port 10430            # non-default gRPC port

If step 2 fails with "Connection refused" / "DNS resolution failed",
enable the scripting socket server in Logic 2:
    Options → Preferences → Developer → "Enable scripting socket server"
and make sure the Logic 2 desktop app is running.
"""

import argparse
import sys
import time
import traceback


def step(label: str, fn):
    print(f"[ .. ] {label} ...", end="", flush=True)
    try:
        result = fn()
    except Exception as exc:
        print(f"\r[FAIL] {label}")
        print(f"       {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False, None
    print(f"\r[PASS] {label}")
    return True, result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port",        default=10430, type=int,
                   help="Logic 2 gRPC port (default 10430)")
    p.add_argument("--duration",    default=0.2,   type=float,
                   help="dummy-capture duration in seconds")
    p.add_argument("--sample-rate", default=10_000_000, type=int,
                   help="dummy-capture digital sample rate in Hz")
    args = p.parse_args()

    print("=" * 62)
    print(" Saleae Logic 2 connectivity smoke test")
    print("=" * 62)

    # 1. Import
    ok, _ = step("import logic2-automation",
                 lambda: __import__("saleae.automation", fromlist=["automation"]))
    if not ok:
        print("\nInstall with:  pip install logic2-automation")
        return 1

    from saleae import automation

    # 2. Connect
    ok, mgr = step(
        f"connect to Logic 2 on 127.0.0.1:{args.port}",
        lambda: automation.Manager.connect(address="127.0.0.1", port=args.port))
    if not ok:
        print("\nHints:")
        print("  - Is the Logic 2 desktop app running?")
        print("  - Options → Preferences → Developer → "
              "Enable scripting socket server")
        return 1

    try:
        # 3. List devices
        def _devices():
            devs = mgr.get_devices()
            if not devs:
                raise RuntimeError("Logic 2 reports zero connected devices")
            return devs

        ok, devs = step("list connected devices", _devices)
        if not ok:
            return 1
        print()
        for d in devs:
            # Different lib versions expose slightly different attributes — fall
            # back through common ones.
            attrs = []
            for a in ("device_id", "device_type", "device_serial_number",
                      "serial_number", "name", "type"):
                v = getattr(d, a, None)
                if v is not None:
                    attrs.append(f"{a}={v}")
            print(f"       {' '.join(attrs) or repr(d)}")
        print()

        # 4. Dummy capture
        def _capture():
            dev_cfg = automation.LogicDeviceConfiguration(
                enabled_digital_channels=[0, 1],
                digital_sample_rate=args.sample_rate,
                # Logic 8 (non-Pro) has a fixed 1.65 V threshold —
                # digital_threshold_volts is not accepted on this device.
            )
            cap_cfg = automation.CaptureConfiguration(
                capture_mode=automation.TimedCaptureMode(
                    duration_seconds=args.duration)
            )
            cap = mgr.start_capture(device_configuration=dev_cfg,
                                    capture_configuration=cap_cfg)
            cap.wait()
            cap.close()

        ok, _ = step(
            f"start/wait/close a {args.duration:.2f} s dummy capture "
            f"(Ch0+Ch1, {args.sample_rate/1_000_000:.0f} MS/s)",
            _capture)
        if not ok:
            return 1
    finally:
        try:
            mgr.close()
        except Exception:
            pass

    print()
    print("All checks green — Logic 2 is ready for cyclic_fire_hw_test.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
