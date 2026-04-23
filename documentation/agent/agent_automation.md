# Agent-Driven Automation in VS Code + Claude Code

A practical guide for setting up a VS Code workspace so that Claude Code (or any coding agent with the same permission model) can iterate on a goal **fully autonomously** — editing code, building, flashing, testing, committing and pushing — without stopping to ask the developer for confirmation on every step.

Target audience: developer who wants to type _"refactor `app.c` into 6 modules, one commit per module, smoke test must stay green"_ and walk away.

## Table of Contents

- [1. What "fully autonomous" means](#1-what-fully-autonomous-means)
- [2. Prerequisites](#2-prerequisites)
- [3. The three friction sources](#3-the-three-friction-sources)
- [4. Permission modes (Shift+Tab)](#4-permission-modes-shifttab)
- [5. The allowlist file](#5-the-allowlist-file)
- [6. Writing good allowlist patterns](#6-writing-good-allowlist-patterns)
- [7. What to never blindly allow](#7-what-to-never-blindly-allow)
- [8. Recommended workflow](#8-recommended-workflow)
- [9. Troubleshooting](#9-troubleshooting)
- [10. Example: this project's `settings.local.json`](#10-example-this-projects-settingslocaljson)
- [11. Keep the allowlist tidy](#11-keep-the-allowlist-tidy)

---

## 1. What "fully autonomous" means

You state a goal once, and the agent runs the **write → build → test → commit → push** loop per iteration until the goal is reached, without a single "Do you want to proceed?" prompt in between.

Example goals that map to this pattern:

- _"Refactor `app.c` into six themed modules; run smoke_test.py after each module; if green, commit and push."_
- _"Add a feature X, iterate until all tests pass, then open a PR."_
- _"Investigate bias Y: run this diagnostic script, analyse results, adjust code, rerun."_

If the loop takes 20 minutes and produces 8 commits, you want to see 8 green log lines and 0 prompts during those 20 minutes.

## 2. Prerequisites

- **VS Code** with the **Claude Code extension** installed and connected to an Anthropic account.
- **A real test harness** the agent can call from the command line. If your verification requires clicking around a UI, autonomy is not available — invest in a script first.
- **Physical hardware present**, if the project builds firmware. The agent can run `build.bat` / `flash.py` / `smoke_test.py`, but the boards need to be powered and connected.
- **Clean `.gitignore`** so build artefacts, logs, IDE settings, and vendor PDFs are out of the way. Fine-grained `git add <specific path>` commands are safer than `git add .`.

## 3. The three friction sources

Every autonomous run fights three kinds of interruption:

| Source                          | Example trigger                                      | Fix                                       |
| ------------------------------- | ---------------------------------------------------- | ----------------------------------------- |
| **Bash-command prompts**        | `git commit`, `./build.bat`, `python flash.py`       | Allowlist in `.claude/settings.local.json`|
| **Edit/Write tool prompts**     | Every `Edit` or `Write` on a source file             | Switch to auto-accept-edits mode (Shift+Tab) |
| **Destructive-action warnings** | `rm -rf`, `git push --force`, uploads to third-party | Keep these **explicit** — don't blanket-allow |

The first two are what stop you mid-flow. The third is a guardrail you actually want — resist the urge to silence it.

## 4. Permission modes (Shift+Tab)

Claude Code cycles through permission modes when you press **Shift+Tab**:

1. **Default** — asks for every file edit and non-allowlisted Bash command. Safest, most interrupts.
2. **Auto-accept edits** — Edit/Write tools run without prompting; Bash still asks for non-allowlisted commands. **This is the sweet spot for autonomous work.**
3. **Plan mode** — agent can't touch anything, only proposes a plan. Use to preview before starting a long run.
4. **Bypass permissions** — nothing asks. Highest risk, use only for scratch/throwaway work.

For the workflow this README is about: **Auto-accept edits + well-stocked Bash allowlist**. That gives autonomy for the common operations while keeping you in the loop for anything unusual the agent tries to run.

## 5. The allowlist file

Claude Code looks for two files in the workspace, in order:

1. `.claude/settings.json` — shared, commit-into-git settings.
2. `.claude/settings.local.json` — your personal, machine-specific settings. **Gitignored by default.**

Put allowlist entries in `.claude/settings.local.json` unless every member of the team should share the same automation setup.

Minimal example:

```json
{
  "permissions": {
    "allow": [
      "Bash(git -C /path/to/repo *)",
      "Bash(./build.bat)",
      "Bash(python /absolute/path/to/flash.py)",
      "Bash(python /absolute/path/to/smoke_test.py)",
      "Bash(python /absolute/path/to/smoke_test.py --no-reset)"
    ]
  }
}
```

Each entry is a tool invocation **pattern**, not a literal string. `*` is a wildcard covering any characters.

## 6. Writing good allowlist patterns

Three rules learned the hard way:

### Rule 1 — Use absolute paths

The agent's current working directory changes between Bash calls. A pattern like `Bash(./build.bat)` matches only when cwd is the build dir — brittle.

**Prefer:**

```
"Bash(python /c/work/project/tools/smoke_test.py)"
"Bash(python /c/work/project/tools/smoke_test.py --no-reset)"
```

### Rule 2 — `cd X && ...` patterns don't match a bare `git *`

A compound command like

```bash
cd /c/work/project && git add foo && git commit -m "..."
```

starts with `cd`, not `git`, so it doesn't match `Bash(git *)`. Two clean alternatives:

```
"Bash(git -C /c/work/project *)"      # use git's own -C flag
"Bash(cd /c/work/project && git *)"   # pattern for the compound
```

The first is cleaner — no `cd` needed at all. Teach the agent to always use `git -C <repo>`.

### Rule 3 — Prefer broad patterns over many narrow ones

Instead of separately allowing `git add *`, `git commit *`, `git push *`, `git log *`, ... just allow:

```
"Bash(git -C /c/work/project *)"
```

That covers any `git` subcommand in that repo. If you're worried about _one_ specific destructive git command, keep the broad pattern and add an explicit _deny_ for the dangerous one (see section 7).

## 7. What to never blindly allow

Autonomy is cheap; unintended destruction is expensive. Keep **explicit confirmation** for:

- `rm -rf`, any recursive delete
- `git push --force`, `--force-with-lease`, force-push to main/master
- `git reset --hard`, `git clean -f`
- Package manager installs on the system level (`apt`, `brew`, `pip install --user` outside a venv)
- Uploads to external services (gists, pastebins, diagram renderers, cloud buckets)
- Anything that modifies CI/CD pipelines, shared infrastructure, or runs in production

Setting these in the `deny` block of `settings.local.json` makes the rule explicit. A real-world deny block as used in this project:

```json
{
  "permissions": {
    "allow": [ "..." ],
    "deny": [
      "Bash(git push --force *)",
      "Bash(git push -f *)",
      "Bash(git push --force-with-lease *)",
      "Bash(git reset --hard *)",
      "Bash(git clean -f*)",
      "Bash(rm -rf *)",
      "Bash(rm -r *)"
    ]
  }
}
```

`deny` wins over `allow` when both match — so a broad `Bash(git *)` allow combined with the `deny` entries above still blocks the dangerous variants. That is exactly the combination you want: maximum autonomy on safe commands, zero autonomy on destructive ones.

A well-written agent won't run these routinely, but the deny list is a hard backstop.

## 8. Recommended workflow

### Before starting an autonomous run

1. Switch to **auto-accept edits** mode (Shift+Tab until the mode banner shows it).
2. Make sure your working tree is clean (`git status`) or on a dedicated branch.
3. Have the test harness that verifies "done" ready and fast. For long tasks, 3-minute tests are better than 30-minute tests.
4. Write a **short, concrete goal** — one paragraph. Include the stopping condition: "when smoke_test.py returns 35/35 PASS for 5 modules in a row".

### During the run

- The agent narrates major steps (build succeeded, test passed, committed X).
- Watch for `[FAIL]` lines in test output — if the agent misreads a failure as success, stop it.
- If the agent asks for confirmation, that's useful signal: something _isn't_ in your allowlist for a reason. Answer, then decide whether to add the pattern.

### After the run

- Review the commits the agent produced. `git log --oneline` and a short `git diff` per commit is enough for most cases.
- Run the test harness one more time yourself, just to be sure nothing was cached.
- If the run produced new command patterns you'd want to automate next time, add them to `settings.local.json` now while you remember.

## 9. Troubleshooting

### "The agent still asks for every Bash command"

Most likely: the agent is using a shape that doesn't match your pattern. Look at the exact command string in the prompt, and compare to your allowlist. Common mismatches:

- Pattern has literal quotes that the actual command doesn't use.
- Pattern expects `/c/...` (MSYS) but the command uses `C:/...` (Windows).
- Pattern starts with `python` but the agent uses `python.exe` or `py`.

Fix by copying the exact command into the allowlist with `*` wildcards where arguments differ.

### "The agent still asks for every file edit"

You're still in **default** permission mode. Shift+Tab once to reach **auto-accept edits**.

### "`cd X && ...` commands always prompt"

Edit your allowlist to use `git -C <path>` patterns and drop the `cd`. Teach the agent via the prompt: _"use `git -C /c/work/project` so you don't need cd."_

### "The agent's commit messages are ugly / too short"

That's orthogonal to permissions — write the commit-message expectation into your goal prompt ("use Conventional Commits; body explains the why"). The agent will comply and subsequent commits will match.

### "The run stopped halfway and I can't tell where"

Open the log file the test harness wrote; most harnesses have a `--log-file` option. Check `git log --oneline` to see what commits the agent did manage to create. Resume from there.

## 10. Example: this project's `settings.local.json`

The actual, in-use allowlist for this 10BASE-T1S firmware project (after consolidation — see section 11):

```json
{
  "permissions": {
    "allow": [
      "Bash(git *)",
      "Bash(git -C /c/work/ptp/check/net_10base_t1s *)",
      "Bash(cd /c/work/ptp/check/net_10base_t1s && git *)",

      "Bash(cmd.exe //c \"build.bat *\")",
      "Bash(cmd.exe //c \"build.bat\")",
      "Bash(./build.bat *)",
      "Bash(./build.bat)",
      "Bash(/c/work/ptp/check/net_10base_t1s/apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/build.bat *)",
      "Bash(cd /c/work/ptp/check/net_10base_t1s/apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X && ./build.bat *)",

      "Bash(python /c/work/ptp/check/net_10base_t1s/tools/flash/flash.py)",
      "Bash(python /c/work/ptp/check/net_10base_t1s/tools/test-harness/smoke_test.py)",
      "Bash(python /c/work/ptp/check/net_10base_t1s/tools/test-harness/smoke_test.py --no-reset)",

      "Bash(python ptp_drift_compensate_test.py *)",
      "Bash(python -u ptp_offset_capture.py *)",
      "Bash(python saleae_high_phase.py *)",
      "Bash(python -c \"import ast; ast.parse*\")",

      "Bash(ls *)",
      "Bash(stat *)",
      "Bash(touch *)",
      "Bash(find /c/work/ptp/check/net_10base_t1s *)",
      "Bash(xargs grep *)",
      "Bash(rm /c/work/ptp/check/net_10base_t1s/apps/tcpip_iperf_lan865x/firmware/tcpip_iperf_lan865x.X/out/*)",
      "WebFetch(domain:onlinedocs.microchip.com)",

      "Skill(update-config)",
      "Skill(update-config:*)"
    ],
    "deny": [
      "Bash(git push --force *)",
      "Bash(git push -f *)",
      "Bash(git push --force-with-lease *)",
      "Bash(git reset --hard *)",
      "Bash(git clean -f*)",
      "Bash(rm -rf *)",
      "Bash(rm -r *)"
    ]
  }
}
```

Notes on the shape:

- **Path duplication by design.** `Bash(./build.bat *)`, the absolute `/c/work/…/build.bat *` form, and the `cd … && ./build.bat *` compound are all listed because the agent's current working directory varies between calls. Covering all three shapes is cheaper than one prompt interrupt.
- **Test scripts use wildcard args** (`ptp_drift_compensate_test.py *`, `saleae_high_phase.py *`) so the allowlist doesn't need an update every time `--duration` or `--gm-port` changes.
- **`Skill(update-config)`** is allowlisted so the agent can edit `settings.local.json` itself when asked — keeps the configuration loop autonomous too.

With this allowlist + auto-accept-edits mode, the agent can run the full refactor loop:

```
For each module in [lan_regs, ptp, sw_ntp, tfuture, loop_stats, ptp_rx]:
    1. Create <module>.c / <module>.h     (Edit/Write, auto-accepted)
    2. Edit app.c to remove extracted code (Edit, auto-accepted)
    3. Edit user.cmake to add new source   (Edit, auto-accepted)
    4. ./build.bat                         (allowlisted)
    5. python .../flash.py                 (allowlisted)
    6. python .../smoke_test.py --no-reset (allowlisted)
    7. If 38/38 PASS:
         git -C /c/work/... add <files>    (allowlisted)
         git -C /c/work/... commit -m ...  (allowlisted)
         git -C /c/work/... push           (allowlisted)
       else:
         stop and ask the developer
```

Zero confirmation prompts during the 20-30 minutes this loop takes. That's the target.

## 11. Keep the allowlist tidy

Allowlists grow fast. Every time the agent runs a slightly-different command shape — a new flag, a different COM-port, a one-off `python -c "..."` — you either approve it once (gone after the session) or add it to `settings.local.json` (permanent). The second path is the one that makes the next run smoother, but it's also the one that produces a 60-line file of near-duplicates within a few weeks.

Symptoms that it's time to consolidate:

- Multiple entries differ only in a literal flag value (`--duration 3` vs `--duration 5`, `--gm-port COM8` vs `--gm-port COM9`).
- Many entries are just specific `git` subcommands already covered by a broader `Bash(git *)`.
- Several `python -c "import ast; ast.parse(open('foo.py').read())"` entries, one per test file.
- Several variants of `build.bat` that only differ in quoting, path shape, or the presence of `cd`.

Pattern for cleanup:

1. **Read the current file.** Note which entries are covered by a broader existing pattern.
2. **Collapse flag-literal entries** to a trailing wildcard: `Bash(python foo.py --port COM8)` + `--port COM9` + `--port COM10` → `Bash(python foo.py *)`.
3. **Drop any entry already covered by a broader one.** `Bash(git add *)`, `Bash(git commit *)`, `Bash(git push *)` are all redundant when `Bash(git *)` is present.
4. **Add a `deny` block** (see section 7) so the broad `Bash(git *)` doesn't accidentally cover force-push / hard-reset.
5. **Validate the JSON** (`python -c "import json; json.load(open('.claude/settings.local.json'))"`). A broken settings file silently disables ALL rules from that file.
6. **Commit the cleanup** — it's personal, so it goes to `settings.local.json` (gitignored by default); if you want the team to share it, move the non-personal parts to `settings.json`.

A good heuristic: if the allowlist is longer than ~35 lines for a single project, you can probably halve it without losing any autonomy. The one we ship here (section 10) went from 54 entries to 33 in a single consolidation pass, and the autonomous loop hasn't prompted once since.

---

**Bottom line:** Autonomy is not about giving up control — it's about **telling the system in advance which operations are known-safe for this project**, so the agent doesn't have to ask about them a thousand times. A 30-40 line `settings.local.json` with a proper `deny` block is the difference between watching the agent work and babysitting it.
