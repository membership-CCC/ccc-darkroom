# CCC Darkroom

Photograph ingestion, editorial curation, brand tone mapping, and multi-format
publishing for **Catskills Cycling Club**.

Upload frames from a phone into a Google Drive folder. A Mac mini named **Ada**
does everything else, unattended, on a fifteen-minute timer. You come back to
publish-ready files in every format the club uses, sorted by roll, plus a
contact sheet to cull from and a plain-JSON record of every decision made.

**Nothing publishes.** Every manifest row is written `status: draft`. Posting,
sending and scheduling remain manual, and that is deliberate.

> **Current: v3 pipeline, packaged as v4.** Deployed and running on Ada since
> 31 July 2026, verified end-to-end on the launchd timer.

---

## Repository layout

```
darkroom/                    the deployable package — this is what ships to Ada
  darkroom_curate.py           curator: decides WHAT should happen
  darkroom.py                  renderer: owns every pixel, follows the plan
  darkroom_sheet.py            contact sheet builder
  darkroom_cycle.sh            what launchd runs: curate -> render -> sheet
  darkroom_learn.py            feedback loop / calibration
  darkroom_intake.py           stages lab scans from a portal zip
  tidy_dump.py                 sorts loose files into dated rolls
  retest_roll.sh               re-run a finished roll from untouched originals
  analyze_run.py               reconstruct what was decided, and why
  install_darkroom.sh          fresh install on Ada
  set_drive_account.sh         point all 9 scripts at the real Drive account
  install_launchd.sh           render, lint and load the launchd agents
  com.ccc.darkroom*.plist      launchd templates (__HOME__ substituted at install)
  fonts/                       Bebas Neue + Montserrat, required by the sheet
  START_HERE.md                read this first when deploying
  DARKROOM_DEPLOY_RUNBOOK.md   11 phases, HALT codes, deploy report
  README.md                    full operating manual

docs/
  CCC_DARKROOM_context.md      domain context — the single most useful file here

legacy/v2/                   superseded v2 renderer + diagrams, kept for reference
```

**If you read one file, read `docs/CCC_DARKROOM_context.md`.** It carries the
design reasoning, the traps that have already bitten once, and the open
questions — everything a future session would otherwise have to rediscover.

---

## The one structural fact

**Judgment and rendering are separate programs, and the renderer never makes an
editorial decision.**

```
darkroom_curate.py  ──▶  _curation.json  ──▶  darkroom.py  ──▶  exports
   what should                 the plan            the pixels
    happen                (plain JSON, editable —
                          your override beats
                          both layers)
```

`darkroom_cycle.sh` chains curate → render → sheet, and **that is what launchd
must run.** Running `darkroom.py` on its own silently reverts to v2's
orientation-bucket rules, because no plan would ever exist. The output looks
plausible and is wrong.

---

## Deploy to Ada

Full procedure with diagnostics and HALT codes: **`darkroom/DARKROOM_DEPLOY_RUNBOOK.md`**.

```bash
git clone <this-repo> ccc-darkroom
cd ccc-darkroom/darkroom

./install_darkroom.sh                        # folders, scripts, fonts, deps
ls ~/Library/CloudStorage/                   # find the account folder name
./set_drive_account.sh you@gmail.com       # rewrite the path in 9 files

python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest   # verify both layers
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh                  # one cycle by hand

./install_launchd.sh                         # only after the manual cycle works
```

Or hand the runbook to Claude Code running on Ada:

> Read DARKROOM_DEPLOY_RUNBOOK.md and execute it from Phase 0. Follow the
> operating rules exactly. Work the phases in order, do not skip ahead, and
> stop at any HALT code rather than improvising. Print every command and its
> output. Produce the deploy report at the end.

### Three things no script can do for you

| | |
|---|---|
| **Available Offline** | Finder → right-click `My Drive/CCC/Photos` → Offline access → Available offline. Streamed files are placeholders with no bytes on disk. |
| **Full Disk Access** | System Settings → Privacy & Security → Full Disk Access → add **`/bin/bash`** and **`/usr/bin/python3`**. launchd-spawned processes do not inherit Terminal's grants. |
| **API key** | `printf '%s' 'sk-ant-...' > ~/CCC/Darkroom/.anthropic_key && chmod 600` — optional, enables the judgment layer. |

The Full Disk Access one is the trap that cost the most time on the first
deploy: the identical script ran perfectly by hand and crashed on all 386
launchd attempts. **If output silently stops while manual runs still work,
check this first.**

---

## Everyday commands

```bash
# health check — both layers, API, model validity, Drive path
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest

# one full cycle by hand (what launchd runs every 15 min)
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh

# reconstruct what was decided for every frame, and why
python3 ~/CCC/Darkroom/bin/analyze_run.py [roll-name] [--plan]

# after editing _curation.json by hand
python3 ~/CCC/Darkroom/bin/darkroom.py --force

# stage lab scans from an Exposure Therapy portal zip
python3 ~/CCC/Darkroom/bin/darkroom_intake.py --latest --roll 2026-08-01_borderlands

# sort loose files in the dump root into dated rolls
python3 ~/CCC/Darkroom/bin/tidy_dump.py            # dry run
python3 ~/CCC/Darkroom/bin/tidy_dump.py --apply

# re-run a finished roll, non-destructively
~/CCC/Darkroom/bin/retest_roll.sh <roll-name>

# watch the log / stop the automation
tail -f ~/CCC/Darkroom/logs/darkroom.log
./install_launchd.sh --unload
```

---

## Uploading a roll

From the phone: Drive app → `CCC/Photos/_dump/` → create a folder named
`YYYY-MM-DD_label` → upload **into that folder**, never into `_dump` directly.

**Naming rules, both of which have already caused a failure:**

- Letters, numbers, hyphens, underscores only. **No slashes, no spaces.**
  Drive allows them; macOS filesystems do not, and the folder will not sync.
- Do not prefix with `_dump/`. It is already inside `_dump`.

Good: `2026-08-01_borderlands` · `2026-07-31_test`
Bad: `_dump/2026-07-31_test` · `Borderlands 8/1`

Loose files in the `_dump` root are ignored on purpose — they have no roll
label and no date. `tidy_dump.py` sorts them out.

---

## Two rules that matter more than they look

**The geometry veto.** The model *proposes* formats; the curator then computes
whether each proposed ratio can actually frame the detected subject, and
rejects any that would cut more than a threshold off it. A confident but wrong
suggestion cannot produce a butchered crop. The floor slides from 92% for a
tight subject to 55% for a sprawling one, with a 15-point near-miss rescue.

**HOLD is a union, never an override.** If either layer raises a route-safety
concern it stands. The model can add a hold; it can never clear one. The club
publishes photographs of its own members riding regular routes on a predictable
schedule — enough frames showing readable signage effectively publishes where
members will be and when. It is a duty-of-care question and must not be
automated away.

Releasing a hold is deliberately two steps: set `"hold": false` in
`_curation.json`, then re-render with `--force`.

---

## Version lineage — read before trusting any zip

The historical naming is misleading and nearly caused the wrong pipeline to
ship. See `CHANGELOG.md` for the full story. The short version:

**Tell v2 from v3 in one command:** `ls darkroom_curate.py`. If it is absent,
it is v2, whatever the filename claims.

---

## What this system does not do

- **It does not choose pictures.** The curator's verdict is advisory and its
  scope is technical. Which frames are worth publishing is a human judgement.
- **It does not edit content.** No retouching, no object removal, no
  generative fill. These are documentary photographs of real club members.
- **It does not publish.** Every manifest row is `draft`.

---

Catskills Cycling Club, Inc. · internal technical documentation
