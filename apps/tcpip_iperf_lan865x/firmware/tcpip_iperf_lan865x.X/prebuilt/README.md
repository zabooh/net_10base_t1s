# Prebuilt firmware images

Firmware HEX files checked in to git so the repository works out-of-the-box
after clone without requiring the XC32 toolchain or an MPLAB X install.

## Current contents

| File                         | Built from branch         | Purpose                    |
|------------------------------|---------------------------|----------------------------|
| `ptp_standalone_demo.hex`    | `ptp-standlone-demo`      | Two-board SW1/SW2/LED demo |

## How to use

```
python setup_flasher.py   # one-shot: detect + assign both debuggers
python flash.py           # flashes the prebuilt HEX onto both boards
```

`flash.py` automatically prefers `out/tcpip_iperf_lan865x/default.hex` if
it exists (i.e. you have built locally), otherwise it falls back to the
prebuilt image in this directory.  `--hex <path>` overrides the default.

## Updating the prebuilt

After merging a new firmware change that should ship with the repo:

```
./build.bat
cp out/tcpip_iperf_lan865x/default.hex prebuilt/ptp_standalone_demo.hex
git add prebuilt/ptp_standalone_demo.hex
git commit -m "chore(prebuilt): refresh standalone demo HEX"
```

`prebuilt/*.hex` is explicitly whitelisted in the root `.gitignore`
alongside `image/*.hex`, so the check-in succeeds without `--force`.
