# CCC Darkroom — domain context

Durable facts about the Catskills Cycling Club photo pipeline. Written so a
future session can pick this up without rediscovering any of it.

**Status: deployed and running unattended on Ada since 31 July 2026.**
Build session 27 July; deploy session 31 July; archived to GitHub 31 July.

---

## Start here if you are a new session

**Canonical source: https://github.com/membership-CCC/ccc-darkroom** — public,
so it can be read with no auth:

```
https://raw.githubusercontent.com/membership-CCC/ccc-darkroom/main/docs/CCC_DARKROOM_context.md
```

Read this file first, then `README.md` at the repo root. The runbook
(`darkroom/DARKROOM_DEPLOY_RUNBOOK.md`) is for deploying to a machine, not for
understanding the system.

**The repo is scrubbed.** Every script carries a placeholder Drive account,
`GoogleDrive-account@example.com`, because the repo is public. The real
account is written in at install time by `set_drive_account.sh`. Two
consequences that have already caused one confusing failure:

- A clone deployed without running `set_drive_account.sh` finds no Drive
  folder. `install_launchd.sh` refuses to schedule in that state rather than
  loading a timer that fires every 15 minutes into a path that does not exist.
- **Copying a single file out of a clone onto a working install re-introduces
  the placeholder into that file.** Re-run `set_drive_account.sh` afterwards —
  it is idempotent and reports which files it actually had to change.

---

## Working on this from a clone

The repo is the source of truth; `~/CCC/Darkroom/bin` on Ada is a deployed
copy. Edit in the clone, then reinstall — do not edit `bin/` directly, or the
next deploy silently reverts your change.

```bash
git clone https://github.com/membership-CCC/ccc-darkroom
cd ccc-darkroom/darkroom

# ... edit ...

./install_darkroom.sh                       # backs up whatever it replaces
./set_drive_account.sh <your-account-name>  # ALWAYS after installing
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh   # prove it by hand
```

Only touch `install_launchd.sh` if the schedule itself changed; the installed
scripts are what the existing timer already runs.

To try a change against real material without risking a roll, use
`retest_roll.sh <roll-name>` — it re-curates and re-renders from the untouched
originals and moves the previous outputs aside rather than deleting them.

Anything learned that a future session would otherwise rediscover belongs in
**this file**, not in a commit message. That is the whole reason it exists.

---

## What the system is

A local, unattended pipeline that turns phone uploads into publish-ready
images in every format the club uses. Photographs are uploaded from a phone
into a Google Drive folder; a Mac mini named **Ada** processes them on a
fifteen-minute timer; finished files sync back to Drive sorted by roll.

Nothing publishes. Every manifest row is written `status: draft`. Posting,
sending and scheduling remain manual, and that is deliberate.

The original v2 was written for **film scans**. It was pointed at colour
phone photographs instead, which is the root of several design tensions
below — most obviously the brand tone treatment.

---

## Architecture: three stages

The important structural fact is that **judgment and rendering are separate
programs**, and the renderer never makes an editorial decision.

**1 · Curator — `darkroom_curate.py`** decides what should happen. Two layers
inside it:

*Measurement* runs locally and free via macOS Vision (pyobjc): attention
saliency, person and face detection, legible text recognition, scene
classification, plus exposure/sharpness/saturation statistics. This layer
always runs and degrades to a Pillow-only fallback if Vision is unavailable.

*Judgment* sends the photograph plus those measurements to an LLM, which
returns: a one-line description, a technical verdict, which formats suit this
particular picture, colour or duotone, a route-safety call, and a caption
hint. Optional — with no API key the curator still produces a plan from the
measurement layer alone.

Output is `_curation.json`, written beside the frames. It is plain JSON and
meant to be edited; a human override beats both layers.

**2 · Renderer — `darkroom.py` (v3)** reads that plan and owns every pixel it
writes. Idempotent by fingerprint, never deletes anything, and with no plan
present it behaves exactly as v2 did (orientation-bucket rules). That
fallback is the safety net if curation ever fails.

**3 · Contact sheet — `darkroom_sheet.py`** builds a branded review sheet with
the CCC selection rubric, reading from `web/` and `_hold/web/`.

`darkroom_cycle.sh` chains all three and is **what launchd must run**. Running
`darkroom.py` alone silently reverts to shape-only rules, because no plan
would ever exist.

---

## Two rules that matter more than they look

**The geometry veto.** The model *proposes* formats; the curator then computes
whether each proposed ratio can actually frame the detected subject, and
rejects any that would cut more than a threshold off it. A confident but wrong
suggestion therefore cannot produce a butchered crop. The floor eases as the
subject grows — a subject filling the frame cannot be preserved by any crop,
so demanding 92% coverage there would veto everything; it slides from 92% for
a tight subject to 55% for a sprawling one, with a 15-point near-miss rescue
so a keepable frame is never left with only uncropped formats.

**HOLD is a union, never an override.** If either layer raises a route-safety
concern it stands. The model can add a hold; it can never clear one. Held
frames render into `_hold/` rather than the publish folders, with
`HOLD_REVIEW.txt` explaining what triggered each. Releasing one is a
deliberate two-step human action: set `"hold": false` in the plan, re-run with
`--force`.

The reasoning behind HOLD: the club publishes photographs of its own members
riding regular routes on a predictable schedule. Enough frames showing
readable road signs, junction names or trailhead markers effectively publishes
where members will be and when. It is a duty-of-care question, and the
original README was explicit that it must not be automated away.

---

## Facts established by testing

**Every test frame was shot 16:9, not the sensor's native 4:3.** This is the
single most consequential fact about the source material. Cropping 16:9 to
Instagram's 4:5 portrait discards 55% of the frame, which trips v2's 45%
ceiling and mats the picture on a near-black field — the black bars that
started this whole investigation. At 4:3 the same crop discards 40%, clears
the ceiling, and crops cleanly. **Shooting 4:3 remains worth doing**; the
curator works around the problem but is working around one that need not
exist.

**v2's advisory triage was dead code.** Its thresholds could never fire
against real photographs — the `soft` gate ships at 0.8 while the softest real
frame measured 4.42, roughly five times above it. Every frame read `ok`
forever. The curator's gates are separate and calibrated to actual measured
values.

**The LLM's calls vary between runs on identical input.** Observed directly:
the same frame came back `hold: false` on one run and `hold: true` on the
next; format choices shifted too. This is inherent to the judgment layer and
is the cost of what was traded away — v2's proudest property was being
byte-identical every run. In practice each roll is judged once, cached, and
reused, so a given roll is stable after its first pass; but two similar rolls
may be treated differently.

**Tone needed the disagreement rule to work at all.** Asking the model to
choose colour or duotone produced colour on 7/7 frames across two attempts,
even for a hazy ridge that the prompt explicitly named as a duotone candidate.
Prose tuning did not fix it. What fixed it: passing the measurement layer's
tone opinion to the model as evidence, and rendering **both** treatments when
the two layers disagree. Result on the same roll: 2 colour, 1 duotone, 4 both.
`both` writes each chosen format twice, the second with a `_mono` suffix.

The deploy roll reproduced this distribution on four fresh frames: **1 colour,
1 duotone, 2 both.** The rule holds on material it had never seen.

---

## Deployment — how it actually went (31 July 2026)

Deployed by Claude Code on Ada from `~/Downloads/darkroom`, working
`DARKROOM_DEPLOY_RUNBOOK.md` v2.0. Outcome: **COMPLETE**, timer-driven run
verified.

### The trap that cost the most time: Full Disk Access

**launchd-spawned processes do not inherit Terminal's file-access grants.**
The identical `darkroom_cycle.sh` that ran perfectly by hand crashed with
`PermissionError` on all 386 launchd attempts, because the job could not read
the Google Drive folder.

Fix: System Settings → Privacy & Security → **Full Disk Access**, add and
enable **`/bin/bash`** and **`/usr/bin/python3`** (Cmd+Shift+G to type the
path). Terminal and Claude having access is not enough — the binaries launchd
actually executes need their own grant.

This will recur on any rebuild, new machine, or macOS major upgrade. It is the
first thing to check if the pipeline silently stops producing output while
manual runs still work.

### Proof the automation works

Timer-fired, no manual invocation:

```
=== darkroom cycle 2026-07-31 11:22:47 ===
-- curating 2026-07-31_launchd-verify
-- rendering
INFO  curation plan loaded — 1 frame(s), decided by llm
INFO  verify_001.jpg -> CCC_2026-07-31_launchd-verify_001  [portrait, both, 10 formats]
INFO  done — 1 frame(s)
-- contact sheets
INFO  2026-07-31_launchd-verify -> contact_sheet.png (1 frames)
=== cycle done ===
```

`_dump/` self-cleaned; output landed in the publish folders and
`_originals/2026-07-31_launchd-verify/`.

### Root cause of the original symptom

Three faults stacked, which is why it looked inexplicable:

1. Darkroom was not fully installed on Ada (6 of 9 scripts present).
2. The first four files were loose in the `_dump/` root — ignored by design,
   since a file with no roll folder has no label and no date.
3. The roll folder created from the Drive app was literally named
   `_dump/2026-07-31_test`, with a forward slash *inside the name*. Drive
   permits `/`; macOS filesystems do not. Renamed to `2026-07-31_test`.

And then, once all three were fixed, the Full Disk Access issue above kept the
timer from working even though everything ran by hand.

### Verified state on Ada

| | |
|---|---|
| Measurement backend | `vision` — full detection available |
| Judgment layer | **ACTIVE**, model `claude-sonnet-5` |
| Scripts | 9 of 9, Drive path correct in all 9 |
| v2 backups | kept in place as `darkroom.v2.py`, `darkroom_sheet.v2.py` |
| `com.ccc.darkroom` | LOADED, runs `/bin/bash .../darkroom_cycle.sh` |
| `com.ccc.darkroom.learn` | LOADED, `python3 darkroom_learn.py --write` |
| Ada sleep | `sleep 0` — never sleeps, unchanged |

---

## Package and version lineage — read before trusting any zip

The naming is genuinely misleading and cost a near-miss deploy of the wrong
pipeline:

- **`CCC_Darkroom_v3.zip`** in the project folder is **v2 code** under a v3
  name. `darkroom.py` 20,624 bytes, no `darkroom_curate.py`.
- **`darkroom_curator_v3.1` … `v3.8.zip`** are the real v3 curator layer.
  `v3.8` is current and matches `curator_bundle/`.
- **`CCC_Darkroom_v4.zip`** (31 July) is the deployable package: v3.8 curator
  merged with the still-current v2-era utilities, corrected plists, and three
  install helpers. **This is the one to use.**

**Tell v2 from v3 in one command:** `ls darkroom_curate.py`. If it is absent,
it is v2, whatever the filename claims. Secondary check: `darkroom.py` is
~20.6 KB in v2 and ~27.2 KB in v3.

### What v4 added over the raw curator bundle

- `install_darkroom.sh` — fresh install; backs up anything it replaces;
  asserts the installed scripts are **writable** (files unzipped from a
  read-only archive land at mode 555, which silently breaks the path-rewriter).
- `set_drive_account.sh` — rewrites the Drive account across **all nine**
  files that embed it. The old runbook listed only four. Substitution is done
  in Python with exact string replacement: **Perl interpolates `@example` in
  an email address as an empty array and corrupts every path without
  erroring.**
- `install_launchd.sh` — plists ship as templates with a `__HOME__`
  placeholder, substituted from `$HOME` and `plutil`-linted *before*
  installing. This removes the two classic failure modes: a malformed
  hand-edited plist, and the wrong username.
- The old plists hardcoded `/Users/<you>/...`. **Ada's short name is
  `john`.** Any surviving v2 plist points at a path that does not exist.

---

## Where things live

| | |
|---|---|
| Scripts | `~/CCC/Darkroom/bin/` |
| Logs | `~/CCC/Darkroom/logs/darkroom.log`, `learn.log` |
| Fingerprint ledger | `~/CCC/Darkroom/.state/` — outside Drive by design, so a sync fault cannot cause reprocessing |
| LLM response cache | `~/CCC/Darkroom/.state/llm_cache/` |
| API key | `~/CCC/Darkroom/.anthropic_key`, chmod 600 |
| Drive root | `~/Library/CloudStorage/GoogleDrive-<account>/My Drive/CCC/Photos` — placeholder in the repo, set by `set_drive_account.sh` |
| Canonical source | https://github.com/membership-CCC/ccc-darkroom (public) |
| Working clone on Ada | `~/Documents/ccc-darkroom/` |
| Uploads land in | `CCC/Photos/_dump/<roll-name>/` |
| Sources archived to | `CCC/Photos/_originals/<roll>/` |
| Deploy package on Ada | `~/Downloads/darkroom/` |
| launchd jobs | `~/Library/LaunchAgents/com.ccc.darkroom.plist` (15 min), `.learn.plist` (Sun 20:00) |

Model in use: **claude-sonnet-5**, set as `MODEL` near the top of
`darkroom_curate.py`. `--selftest` lists what the account actually has;
the API console has its own credit balance, separate from a Claude
subscription.

The Drive folder **must be set Available Offline in Finder**. Streamed
placeholder files have no bytes on disk and cannot be read.

Roll folder names: letters, numbers, hyphens, underscores. **No slashes, no
spaces.** Format `YYYY-MM-DD_label`. Do not prefix with `_dump/` — it is
already inside `_dump`.

---

## Operating it

```bash
# health check — both layers, API, model validity, Drive path
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest

# full cycle by hand (what launchd runs every 15 min)
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh

# sort loose files in the dump root into dated rolls (dry run first)
python3 ~/CCC/Darkroom/bin/tidy_dump.py
python3 ~/CCC/Darkroom/bin/tidy_dump.py --apply

# re-run a finished roll from its untouched originals, non-destructively
~/CCC/Darkroom/bin/retest_roll.sh <roll-name>

# after editing _curation.json by hand
python3 ~/CCC/Darkroom/bin/darkroom.py --force

# stage lab scans from an Exposure Therapy portal zip
python3 ~/CCC/Darkroom/bin/darkroom_intake.py --latest --roll 2026-08-01_borderlands

# watch the log
tail -f ~/CCC/Darkroom/logs/darkroom.log

# stop the automation
cd ~/Downloads/darkroom && ./install_launchd.sh --unload
```

---

## Bugs found and fixed

### During the v3 build

The **HOLD flag fired on any legible text**, so cap embroidery and bike
branding held 6 of 7 frames. A flag that fires on everything is one nobody
reads. Now only genuinely locational text qualifies.

**Vision reports a person and their face as separate detections**, so one
rider counted as two — and that wrong number was handed to the model as fact.
Now takes the larger of the two lists rather than the sum.

**The contact sheet broke** when frames were held, because everything landed
in `_hold/web/` and the builder only looked in `web/`.

**The curation plan was deleted** along with the emptied dump folder, which
silently broke the documented "edit it and re-run" override — there was
nothing left to edit. Now copied to both the output folder and `_originals/`.

**The ingest size floor stranded small images forever.** `MIN_BYTES` was
200,000, so a legitimate 168 KB image reported "still syncing" on every cycle
in perpetuity. The settle time already proves a write has finished; the floor
is now 20,000 and exists only to skip junk, and a file that will never qualify
says so explicitly instead of pretending to wait.

**The re-test script staged a stale plan** back into the dump alongside the
originals, which would make the cycle treat a roll as already curated and skip
judging it.

### During the v4 packaging

**Perl array interpolation silently corrupted every Drive path.** `perl -pi -e`
with an account name containing `@` wrote `GoogleDrive-newaccount\.com` into
all nine files — no error, just a broken install. Caught in a sandbox test
before shipping. Rewritten in Python.

**Scripts installed from a read-only archive landed at mode 555**, so the
path-rewriter could not open them for writing. Invisible when testing as root.
Now `chmod 755` explicitly, with a writability assertion.

---

## Local conventions the tools must respect

**`xArchive` is a bin, not an archive.** Folders named `xArchive` exist inside
`Photos/` and `_originals/` (and may appear in `_dump/`) as a staging area for
test material intended for real deletion later. It is a hand-rolled trash can,
not a store of finished work.

The pipeline now skips it everywhere a roll is enumerated — `NOT_A_ROLL` in
`darkroom.py`, `darkroom_curate.py` and `analyze_run.py`, and a lowercased
`case` guard in `darkroom_cycle.sh`, all case-insensitive. Before that:

- test images binned into `_dump/xArchive/` would have been processed straight
  back out on the next cycle;
- `analyze_run.py` with no argument picked `xArchive` as the newest roll.

**`darkroom_learn.py` deliberately does not skip it**, because it never looks
in the bin in the first place — it reads `decisions.txt` from live rolls under
`Photos/` only. That is correct and must stay that way: decisions recorded
against discarded test material would poison the calibration data, which is
the one thing in this system built from real human judgment.

If another bin name is ever adopted, it has to be added to `NOT_A_ROLL` **and**
to the `case` guard in `darkroom_cycle.sh` — two places, in two languages.

---

## Google Drive's filesystem is not a reliable POSIX filesystem

Treat every directory listing under `CloudStorage/` as something that can fail
transiently. Observed on the first real roll (67 frames, ~250 MB, 2 Aug 2026):

```
OSError: [Errno 11] Resource deadlock avoided: .../_dump/2026-08-01_Borderlands_Don
```

At the time Drive reported link count **65535** and a directory size of **2 MB**
for those folders — both meaningless. Minutes later `find` traversed them
without complaint. DriveFS refuses `readdir` while it is still materialising a
folder's contents, and recovers on its own.

Consequences now handled, and which any new code touching Drive must respect:

- **Retry, don't crash.** `listdir_retry()` in `darkroom.py` and
  `darkroom_curate.py` retries four times with exponential backoff.
- **Isolate per roll.** One unreadable roll must never abort the cycle. This
  bit once: an OSError on the first roll killed the whole render pass and the
  second, perfectly readable roll was never attempted.
- **Deferral is safe.** Failure is non-destructive by design — sources stay in
  `_dump` and are retried on the next cycle. A deferred roll is a delay, not a
  loss.

Related, same root cause: **a large upload takes materially longer to become
readable than it does to appear.** The folder shows in Finder and in the Drive
app almost immediately; the bytes land much later. `SETTLE_SECONDS` (45) guards
against a partially-written *file*, not against a partially-materialised
*folder*.

---

## Photo credit and roll merging

The dump folder name carries the photographer: `<date>_<label>_by_<handle>`.
No lookup table — the folder name is the whole mechanism, so it cannot drift
out of sync with a config file, and omitting `_by_` is how a roll goes
uncredited.

**`_by_<handle>` is stripped from the roll label, and that is deliberate:** it
merges two photographers' folders of the same ride into one output roll, with
continuous sequence numbering and one contact sheet. The credit is therefore
per *frame*, not per roll.

Merging is the part that carries risk. Three things silently overwrote before
this was handled, and any new code writing into a roll must assume more than
one source folder feeds it:

- the curation plan copied into the output folder
- `HOLD_REVIEW.txt`, rewritten rather than appended
- `_originals/`, where two folders can hold the same filename

Design of the mark, and why: Montserrat Regular because Bebas is the display
face and a credit that shouts is a watermark. Lowercase because Instagram
handles are. Bottom-left because right is where Instagram stacks carousel dots.
Colour chosen per frame from the luminance beneath the text — a fixed colour
disappears against skies, and this is the same measure-then-decide principle as
the curator. A blurred halo rather than an offset shadow, which at ~16px
overlaps the letterform and reads embossed.

**Originals are never marked.** The credit exists only on exports, so the
archive stays clean and a re-render can change the treatment freely.

**The credit was designed twice.** The first version — Montserrat Regular at
1.45%, 80% opacity, soft halo — was validated on synthetic gradients and was
completely invisible on real photographs. Synthetic test grounds have almost no
high-frequency detail; gravel, foliage and clothing have it everywhere, and
thin type at 20px simply disappears. The halo added to compensate made it
worse, blurring the strokes it was meant to separate. It now uses **Bebas
Neue** (condensed, heavier, holds shape small) at 1.8%, near-solid, with a
tight strong halo. Measured 3.7–11.1:1 on real exports.

The lesson generalises: **validate image treatments on real source material, not
on generated test patterns.** A contrast ratio computed against a smooth
gradient says nothing about legibility against a photograph.

**Only one cycle may run at a time.** The launchd timer does not know you are
running one by hand. Two overlapping cycles raced on 2 August — one emptied and
removed a dump folder the other was mid-way through reading. Both derive the
next sequence number from the same state file, so a collision could overwrite
exports. `darkroom_cycle.sh` holds an atomic `mkdir` lock in `.state/`, with
stale-lock detection by PID. `flock` does not exist on macOS.

### A roll can grow after it has been rendered

Because rolls merge, a third photographer's folder arriving days later is
normal, not exceptional. Anything written per-roll must assume it will be
written again, later, by a different source folder. The render path was
already safe — sequence numbering continues from shared state, fingerprints
keep it idempotent — but the artefacts around it were not:

- the **contact sheet** built only when missing, so late frames never appeared
  on the sheet the cull is done from. Now rebuilds when the manifest is newer.
- the **manifest** started a new file on any column change, orphaning earlier
  rows. Now migrates them forward.
- **`HOLD_REVIEW.txt`** stacked duplicate entries on repeated `--force`.

The general rule: **the last write must not assume it is the only write.**

A corollary, learned the hard way: **the same file legitimately comes back
round.** `retest_roll.sh` copies originals out of `_originals` into `_dump` and
the renderer moves them back, so a name clash in the archive is usually the
same photograph returning, not a conflict. A guard that suffixed every clash
doubled the archive on the first real re-test. Compare content before assuming
a collision is real.

---

## Known limits and open questions

**HOLD calibration is still unverified.** Zero frames were held across both
the manual test roll and the launchd-verified roll, so the route-safety logic
has never fired against a real case. Given it previously *over*-fired badly,
the current silence is not reassurance. **The first real ride roll with
readable signage or junction names is the test** — check `_hold/` and
`HOLD_REVIEW.txt` on it deliberately.

**`analyze_run.py` is stale.** Its reconstruction narrates v2
orientation-only logic and errors on `_curation.json`. Output correctness was
verified against `manifest.csv` independently, so this is a reporting bug, not
a pipeline bug — but the tool cannot currently be trusted to explain a v3 run.
Worth fixing separately.

**The curator's quality gates were set against a single seven-frame roll.**
They are honest starting values, not calibrated ones, and deserve revisiting
once several real rolls have been through.

`darkroom_learn.py` still calibrates v2's advisory thresholds, which the
curator largely bypasses — it is closer to vestigial than the README implies.
Filling in `decisions.txt` remains worthwhile because that record of what was
actually published is the only ground truth available if the curator's gates
are later tuned to real judgment rather than assumed values.

**Unexplained `xArchive` directory** (mode 700) sits alongside the Darkroom
folders in both `Photos/` and `_originals/`. Outside the pipeline's scope and
not opened during deploy. Flagged in case it is unexpected.

The pipeline cannot know **provenance**. A downloaded image from another
source will be processed into publish-ready exports exactly like an original
photograph. Rights and consent stay a human responsibility.

Editorial scope is currently **technical only**: subject matter can never
cause a rejection. A café table, a parked bike, an empty landscape and a
road sign are all treated as legitimate club material, and relevance is
decided by a human from the contact sheet. This was an explicit choice after
an earlier configuration rejected a café shot as "no cycling context."

---

## Design principles inherited from v2, still true

All compute is local; the recurring sync costs no tokens by design (the
curator's judgment layer is the one exception, and it is optional). State
lives outside Drive so a sync fault cannot cause reprocessing or duplicate
sequence numbers. Files are keyed on name, size and mtime, so re-running is
safe. Failure is non-destructive: if no variant renders, the source stays put
and is retried, and the original moves only after at least one export
succeeds. Tone, crop and scale only — no content is altered, added or removed,
because these are documentary photographs of real club members and
regenerating pixels would be a credibility problem for a nonprofit publishing
images of its own community.

The system does not choose pictures, does not edit content, and does not
publish.
