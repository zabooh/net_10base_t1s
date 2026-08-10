# Salvaged work from sibling clones

`c:\work\ptp\` held 13 clones of this repository. On 2026-08-10 they were
surveyed before being cleaned up. No clone had unpushed commits, but **two of
them carried uncommitted source-level changes that exist nowhere else** — not on
`master`, `mult-sync`, `cross`, `cross-minimize` or `cross-driverless`, and not
on any remote. They are preserved here so the working directories can be
deleted.

Each item is stored twice, deliberately:

- `*.patch` — `git diff` against the clone's HEAD, keeps the intent visible
- `files/*` — the complete resulting file, so nothing depends on the patch
  applying cleanly

Both base commits (`babadd2`, `c319f43`) and both pre-image blobs are present in
this repository, so either form can be recovered.

---

## 1. `ptp_c__PTP_FOL_task.patch` — follower delay-request correctness fix

| | |
|---|---|
| Origin | `AN1847/ptp_c/net_10base_t1s` |
| Branch / base | `copilot/ptp-code` @ `babadd2` ("ptp complete works, but has to be verified") |
| File | `apps/tcpip_iperf_lan865x/firmware/src/PTP_FOL_task.c` |
| Size | +38 / -8 |
| Full copy | `files/PTP_FOL_task.c.from-ptp_c` |

Introduces `fol_delay_t1_snapshot` / `fol_delay_t2_snapshot` and
`fol_delay_req_timeout_ms`. The rationale, quoted from the change itself:

> `processDelayResp()` must use THESE values, not the live `TS_SYNC`, because
> `TS_SYNC` is updated on every FollowUp and may be several cycles ahead by the
> time the `Delay_Resp` arrives. Using stale `TS_SYNC` introduces an error of
> Δoffset/2 into `fol_mean_path_delay`.

Neither symbol occurs anywhere in the `mult-sync` tree, so the fix was never
carried over. Note that `mult-sync` refactored the PTP implementation to
AN1847 style, so the fix cannot be applied verbatim — but **the underlying
error class (stale `TS_SYNC` at `Delay_Resp` time) must be re-checked against
the current code.** That is the reason this patch was kept.

## 2. `check3__drv_lan865x_api.patch` — minimized LAN865x driver variant

| | |
|---|---|
| Origin | `check3/net_10base_t1s` |
| Branch / base | `master` @ `c319f43` ("docs(pptx): add Live-CLI-Test slide") |
| File | `apps/tcpip_iperf_lan865x/firmware/src/config/default/driver/lan865x/src/dynamic/drv_lan865x_api.c` |
| Size | +39 / -215 |
| Full copy | `files/drv_lan865x_api.c.from-check3` |

An intermediate step of the driver-minimization work described in
`documentation/ptp/README_cross.md`. Its MD5 (`43a477a7…`) matches no branch in
this repository:

| Branch | MD5 of that file |
|---|---|
| `master`, `cross` | `249ce36a…` |
| `cross-minimize` | `b5e62839…` |
| `cross-driverless`, `mult-sync` | `296afe9d…` |
| **this salvage** | **`43a477a7…`** |

So it is neither the starting point nor the end state of `cross-minimize` — it
is a distinct attempt. Keep it until the minimization question is settled.

---

## Applying one of these

```sh
git checkout -b salvage/ptp-fol-snapshot babadd2
git apply _salvage/ptp_c__PTP_FOL_task.patch
```

or simply copy the corresponding file out of `files/` and diff it by hand
against whatever the current tree contains.
