# CCC Darkroom

Photograph ingestion, editorial curation, brand tone mapping, and multi-format
publishing for Catskills Cycling Club.

Upload frames from your phone into a Google Drive folder. Ada does everything
else, unattended, on a fifteen-minute timer. You come back to publish-ready
files in every format you use, sorted by roll, plus a contact sheet to cull
from and a plain-JSON record of every decision the system made.

**Version 3 · package v4 · July 2026**

---

## Contents

| File | What it is |
|---|---|
| `darkroom_curate.py` | **the curator** — decides what should happen |
| `darkroom.py` | the renderer — owns every pixel, follows the plan |
| `darkroom_sheet.py` | contact sheet builder |
| `darkroom_cycle.sh` | **what launchd runs** — curate → render → sheet |
| `darkroom_learn.py` | feedback loop / calibration |
| `darkroom_intake.py` | stages lab scans from a portal zip into `_dump` |
| `tidy_dump.py` | sorts loose files in the dump root into dated rolls |
| `retest_roll.sh` | re-run a finished roll from its untouched originals |
| `analyze_run.py` | reconstruct what was decided for every frame, and why |
| `install_darkroom.sh` | fresh install on Ada |
| `set_drive_account.sh` | point all nine scripts at the real Drive account |
| `install_launchd.sh` | render, lint, and load the launchd agents |
| `com.ccc.darkroom.plist` | launchd template — processing, every 15 min |
| `com.ccc.darkroom.learn.plist` | launchd template — learning, Sundays 20:00 |
| `fonts/` | Bebas Neue and Montserrat, required by the sheet builder |

---

## The one structural fact

**Judgment and rendering are separate programs, and the renderer never makes
an editorial decision.**

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

## What it does

| Stage | Behaviour |
|---|---|
| Ingest | Watches `CCC/Photos/_dump/<roll-name>/`. Waits 45 s after last write before touching a file. |
| Measure | macOS Vision, locally and free: attention saliency, person and face detection, legible text recognition, scene classification. Plus exposure, sharpness, saturation. Always runs; degrades to a Pillow-only fallback. |
| Judge | An LLM sees the photograph *and* those measurements, and returns a description, a technical verdict, which formats suit this picture, colour or duotone, a route-safety call, and a caption hint. Optional — costs tokens. |
| Plan | Writes `_curation.json` beside the frames. Editable. Copied to the output folder and `_originals/` so it survives the dump folder being cleaned up. |
| Tone | Colour, or the brand duotone: luminance mapped onto black point `#1A1812` and white point `#ECE6D6`. Fixed LUT, identical every run. |
| Export | Per-photograph format selection, crop centred on the detected subject. |
| Quarantine | Route-safety holds render into `_hold/` with a `HOLD_REVIEW.txt`, not into the publish folders. |
| Organise | Sorted into `instagram/ strava/ email/ web/`, sequentially named, manifest written. |
| Archive | Source moved to `_originals/`. Dump folder deletes itself when empty. |
| Review | `darkroom_sheet.py` builds a branded contact sheet with the CCC rubric. |
| Learn | `darkroom_learn.py` joins your recorded decisions to the measurements. |

Every manifest row is written `status: draft`. **Nothing publishes.**

---

## Two rules that matter more than they look

### The geometry veto

The model *proposes* formats. The curator then computes whether each proposed
ratio can actually frame the detected subject, and rejects any that would cut
more than a threshold off it. A confident but wrong suggestion therefore
cannot produce a butchered crop.

The floor eases as the subject grows — a subject filling the frame cannot be
preserved by any crop, so demanding 92% coverage there would veto everything.
It slides from 92% for a tight subject to 55% for a sprawling one, with a
15-point near-miss rescue so a keepable frame is never left with only
uncropped formats.

### HOLD is a union, never an override

If either layer raises a route-safety concern, it stands. The model can add a
hold; **it can never clear one.**

The reasoning: the club publishes photographs of its own members riding
regular routes on a predictable schedule. Enough frames showing readable road
signs, junction names or trailhead markers effectively publishes where members
will be and when. It is a duty-of-care question, and it must not be automated
away.

Held frames render into `_hold/` with `HOLD_REVIEW.txt` explaining what
triggered each. Releasing one is a deliberate two-step human action:

```bash
# 1. edit _curation.json, set "hold": false for that frame
# 2. re-render
python3 ~/CCC/Darkroom/bin/darkroom.py --force
```

---

## Deployment on Ada

Full step-by-step with diagnostics and HALT codes:
**`DARKROOM_DEPLOY_RUNBOOK.md`**. Short version:

```bash
cd ~/Downloads/darkroom

./install_darkroom.sh                        # folders, scripts, fonts, deps
ls ~/Library/CloudStorage/                   # find the account folder name
./set_drive_account.sh you@gmail.com # rewrite the path in 9 files

python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest   # verify both layers
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh                  # one cycle by hand

./install_launchd.sh                         # only after the manual cycle works
```

Two things the scripts cannot do for you:

**Set `CCC/Photos` to Available Offline.** Finder → right-click → Offline
access → Available offline. Streamed files are placeholders with no bytes on
disk. Not optional.

**Add an API key**, if you want the judgment layer:

```bash
printf '%s' 'sk-ant-...' > ~/CCC/Darkroom/.anthropic_key
chmod 600 ~/CCC/Darkroom/.anthropic_key
```

Billed per use at console.anthropic.com, **separate from a Claude
subscription**. `--selftest` lists the models the account actually has; set
`MODEL` near the top of `darkroom_curate.py` to one of them. Currently
`claude-sonnet-5`.

**Prevent sleep.** launchd will not fire while asleep. It catches up on wake,
so sleep delays rather than loses work — but for unattended running you want
Ada awake. `pmset -g | grep sleep`.

To stop the automation: `./install_launchd.sh --unload`.

---

## Daily use

**Phone path**, no Ada access needed: open the Drive app, create a folder
under `CCC/Photos/_dump/` named for the roll, upload into it.

Name the folder `YYYY-MM-DD_label` and that date carries into every filename:

```
_dump/2026-08-01_borderlands/     →  CCC_2026-08-01_borderlands_001_ig_portrait.jpg
_dump/shakedown-ride/             →  date defaults to today
```

**Letters, numbers, hyphens, underscores only.** No slashes — Drive allows
them, macOS does not, and the folder will not sync. Don't prefix with
`_dump/`; it's already in `_dump`.

Files dropped loose in `_dump/` are ignored — they'd have no roll label.
`tidy_dump.py` sorts them out:

```bash
python3 ~/CCC/Darkroom/bin/tidy_dump.py           # dry run
python3 ~/CCC/Darkroom/bin/tidy_dump.py --apply
```

**Within about fifteen minutes** (plus Drive sync time):

```
CCC/Photos/2026-08-01_borderlands/
    instagram/    ig_portrait  ig_square  ig_landscape  ig_story
    strava/       strava
    email/        email_body  email_header
    web/          web  web_hero
    _hold/        anything flagged for route safety
                  HOLD_REVIEW.txt
    _curation.json             ← every decision, and why
    manifest.csv
    decisions.txt              ← for you to fill in
    contact_sheet.png
CCC/Photos/_originals/2026-08-01_borderlands/
```

**Then:** open the contact sheet on your phone, check `_hold/` if it exists,
note the frame numbers you want, publish those manually.

---

## The plan file

`_curation.json` is plain JSON and meant to be edited. **Your override beats
both layers.**

```json
{
  "verdict": "keep",
  "description": "two riders cresting a gravel climb, low sun behind",
  "subject_box": [0.31, 0.22, 0.68, 0.81],
  "formats": {
    "ig_portrait": { "crop": [0.28, 0.10, 0.71, 0.98] },
    "web":         { "crop": null }
  },
  "tone": "colour",
  "hold": false,
  "caption_hint": "..."
}
```

`crop` takes three forms: `null` fits by long edge without cropping, a
four-number box crops to that window, and the string `"mat"` gives the old
whole-frame-on-Ink look deliberately.

Edit anything, then re-render:

```bash
python3 ~/CCC/Darkroom/bin/darkroom.py --force
```

To see the whole picture — measurements, model response, geometry veto
outcomes, final decision — for a finished roll:

```bash
python3 ~/CCC/Darkroom/bin/analyze_run.py 2026-08-01_borderlands
```

---

## Formats

| Preset | Size | Mode | Use |
|---|---|---|---|
| `ig_portrait` | 1080×1350 | crop | Instagram feed 4:5 |
| `ig_square` | 1080×1080 | crop | Instagram feed 1:1 |
| `ig_landscape` | 1080×566 | crop | Instagram feed 1.91:1 |
| `ig_story` | 1080×1920 | mat | Story / Reels 9:16 |
| `strava` | 1200 long edge | fit | Strava post |
| `email_body` | 1200 wide | fit | Mailchimp body (600 pt @2×) |
| `email_header` | 1200×600 | crop | Mailchimp header |
| `web` | 1600 long edge | fit | Website standard |
| `web_hero` | 2400×1000 | crop | Website hero band |

**In v3, which formats get built is decided per photograph**, not by
orientation bucket. A frame with fewer variants than its neighbour is not a
failure — it is the curator declining to make pictures that wouldn't work.
With no plan present the old `BY_ORIENTATION` rules still apply as the
fallback.

**crop** — exact ratio, centred on the detected subject, subject to the
geometry veto above.
**mat** — whole frame centred on an Ink field.
**fit** — resize by long edge, nothing cropped.

The manifest records which mode each variant actually used.

---

## Tone: colour or duotone

v2 was duotone always. v3 decides per frame.

Getting this to work took more than asking. Asking the model to choose colour
or duotone produced colour on 7/7 frames across two attempts, even for a hazy
ridge the prompt explicitly named as a duotone candidate. Prose tuning did not
fix it.

What fixed it: passing the **measurement layer's** tone opinion to the model
as evidence, and rendering **both** treatments when the two layers disagree.
On the same roll that gave 2 colour, 1 duotone, 4 both. A `both` frame writes
each chosen format twice, the second with a `_mono` suffix.

The tone map itself is unchanged and still a fixed LUT: `INK` and `OAT` at the
top of `darkroom.py`.

---

## Contact sheets

```bash
python3 ~/CCC/Darkroom/bin/darkroom_sheet.py --all
python3 ~/CCC/Darkroom/bin/darkroom_sheet.py "<roll folder>"
```

Drops `contact_sheet.png` beside the images so it syncs to Drive with them.
Numbered thumbnails, technical flags, three criteria columns. Reads from both
`web/` and `_hold/web/`, so a roll where everything was held still gets a
sheet.

**The criteria live in `darkroom_sheet.py`** as `KEEP_CRITERIA`,
`CUT_CRITERIA`, `HOLD_CRITERIA`. They start as a reflection of what this
camera can and can't physically do. Revise them after the first couple of
rolls — that constant becomes the actual CCC photo standard, and it's more
useful as something you wrote than something inferred.

---

## Lab workflow

The lab is Exposure Therapy, dropped at the Camp Kingston box (36 St. James
St, Kingston). Scans come back through their online portal — free unlimited
cloud storage, and the emailed link does not expire.

### Filling out the order form

One roll of Tri-X, standard:

| Field | Mark |
|---|---|
| Rolls | Black & White · 35mm · 1 |
| Scan tier | **Premium $15.99** |
| Advanced Scan Options | **Flat Scans (+$2.00)** — nothing else |
| Prints | No Prints |
| Turnaround | Standard (about 3 days) |
| How are you submitting | Camp Kingston dropbox (Hudson Valley) |

**$17.99 per roll.** Premium is the right tier: the form recommends it for
disposable cameras and over/underexposed film, which is what a fixed-aperture
EC35 produces. It is also the cheapest tier that allows Flat Scans, and flat
is what this pipeline needs — a non-flat scan arrives with the lab's curve
already baked in, and Darkroom's LUT would then map already-clipped values.

**Do not order:** 16-bit TIFF (+$3) — Darkroom applies an 8-bit LUT and
discards the depth. Pro tier — 4000×6000 exceeds every output size here.
Metadata Entry (+$2) — only worth it when dropping several rolls at once.

**The paper form has no push/pull checkbox.** For a pushed roll, write it in
*Notes or Special Instructions*: `Push 2 stops.` ($5/roll, any amount.)

Also worth writing in Notes:
`Warm-neutral B&W scan, hold shadow detail, no cool shift.`

**Negatives.** Pickup is Brooklyn, not Kingston, so the realistic options are
Return Shipping (+$7.50 per order) or Discard. Negatives are the actual master
and discarding is irreversible — pay the $7.50, and batch multiple rolls per
order to amortise it. Leave "Do Not Cut" unchecked; cut strips sleeve and
store far more easily.

**Timing.** Turnaround is about three days *after the invoice is paid*, not
after drop-off. They email an invoice on receipt. Watch for it and pay
immediately or the clock never starts.

Keep the receipt stub — that number is your twin check.

### Bringing scans in

Download the portal zip on Ada, then:

```bash
python3 ~/CCC/Darkroom/bin/darkroom_intake.py --latest --roll 2026-08-01_borderlands
```

`--latest` grabs the newest `.zip` from `~/Downloads`. Or point at a path:

```bash
python3 ~/CCC/Darkroom/bin/darkroom_intake.py ~/Downloads/scans.zip --roll 2026-08-01_borderlands
python3 ~/CCC/Darkroom/bin/darkroom_intake.py ~/Downloads/some_folder --roll shakedown
```

Intake unzips, flattens whatever nesting the portal used, ignores `__MACOSX`
and `.DS_Store`, and copies the images into `_dump/<roll>/` with mtimes
preserved so the settle check behaves. It reports frame count and total size —
check that against your order before walking away.

It refuses to write into a roll folder that already has files unless you pass
`--overwrite`, and it warns if the files are JPEGs, since Flat Scans should
arrive as TIFF.

**It does not download from the portal.** That stays a click. The portal
layout isn't something to guess at, and a scraper that silently fetches
nothing is worse than a manual step.

Flags: `--dry-run`, `--overwrite`, `--move` (delete the zip after staging).

---

## The feedback loop

Every roll gets a `decisions.txt` stub. Fill it in after culling.

```
keep:      3 7 12 19 24
published: 7 19 24
hold:      11
formats:   7=ig_portrait|web 19=ig_story 24=ig_portrait
notes:     heavy dust 14-18, lab issue
```

Twenty seconds of typing. `formats:` is optional but unlocks preset pruning;
use `|` when one frame went to several places.

`darkroom_learn.py` runs Sundays at 20:00, or on demand:

```bash
python3 ~/CCC/Darkroom/bin/darkroom_learn.py            # report only
python3 ~/CCC/Darkroom/bin/darkroom_learn.py --write    # apply calibration
```

**Be clear about what this is now.** The learner still calibrates v2's
*advisory* thresholds inside `darkroom.py` — which the v3 curator largely
bypasses. It is closer to vestigial than it looks.

Filling in `decisions.txt` is still worth doing, for a different reason: that
record of what you actually published is the only ground truth available if
the curator's quality gates are ever tuned to your real judgment rather than
assumed values. The data is the point, not the current calibration.

Nothing changes until there are at least 60 decided frames across 2+ rolls.
Below that the report says so and defaults stay in force. That is correct
behaviour, not a fault.

The most valuable thing this loop produces is feedback about **shooting**, not
processing — keep rate by orientation, and the ratio of matted to cropped
exports. If most exports are matted, your source frames are the wrong shape
for your output formats. That's a camera problem, and acting on it will
improve the photographs far more than any threshold ever will.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Drive path unavailable` | Drive for Desktop not running, or wrong account | `./set_drive_account.sh --check` |
| Files sit in `_dump` forever | Folder is streamed, not offline | Finder → Available offline |
| Loose files never processed | No roll subfolder — by design | `tidy_dump.py --apply` |
| Roll folder invisible on Ada | `/` in the folder name | Rename it in the Drive app |
| `still syncing` repeatedly | Drive hasn't finished writing | Normal — wait a cycle |
| Output looks like v2 (9 formats, always mono) | launchd is running `darkroom.py`, not `darkroom_cycle.sh` | Re-run `./install_launchd.sh` |
| Everything landed in `_hold/` | HOLD over-firing | Read `HOLD_REVIEW.txt` before tuning anything |
| Curator says `basic` backend | pyobjc not installed | `pip install --user pyobjc-framework-Vision pyobjc-framework-Quartz` |
| `MODEL ... NOT in that list` | Model unavailable on the API account | Edit `MODEL` in `darkroom_curate.py` |
| Judgment layer silently absent | No key, or API credit exhausted | `--selftest` says which |
| Same roll judged differently twice | Inherent to the judgment layer | Each roll is judged once and cached; see below |
| Sheet looks plain / wrong font | Fonts missing | `fonts/` must sit beside the script |
| Frames reprocessed | State dir wiped | `.state` must persist; outside Drive by design |
| Wrong tone | LUT edited | `INK` and `OAT` at the top of `darkroom.py` |

Logs: `~/CCC/Darkroom/logs/darkroom.log`, `learn.log`

Useful flags:

```bash
# curator
--dir <path>               curate a specific roll
--backend vision|basic     force a measurement backend
--llm on|off               force the judgment layer on or off
--no-cache                 ignore the cached model response
--selftest                 health check
--json                     machine-readable output

# renderer
--dry-run                  show what would happen, touch nothing
--force                    reprocess frames already in the ledger
--only ig_portrait,web     build a subset of presets
--ignore-curation          render as if no plan existed
-v                         verbose
```

---

## Known limits and open questions

**The curator's quality gates were set against a single seven-frame roll.**
`Q_SOFT_SHARP` and friends near the top of `darkroom_curate.py` are honest
starting values, not calibrated ones. Revisit them once several real rolls
have been through.

**The LLM's calls vary between runs on identical input.** Observed directly:
the same frame came back `hold: false` on one run and `hold: true` on the
next; format choices shifted too. This is the cost of what was traded away —
v2's proudest property was being byte-identical every run. In practice each
roll is judged once, cached in `.state/llm_cache/`, and reused, so a given
roll is stable after its first pass. But two similar rolls may be treated
differently.

**Whether HOLD is calibrated correctly is still unverified.** The held frames
from the original test roll were never visually reviewed. This is the first
thing to check after a real roll.

**The pipeline cannot know provenance.** A downloaded image from another
source will be processed into publish-ready exports exactly like an original
photograph. Rights and consent stay a human responsibility.

**Editorial scope is technical only.** Subject matter can never cause a
rejection. A café table, a parked bike, an empty landscape and a road sign are
all legitimate club material, and relevance is decided by a human from the
contact sheet. This was an explicit choice after an earlier configuration
rejected a café shot as "no cycling context."

**v2's advisory triage was dead code.** Its thresholds could never fire
against real photographs — the `soft` gate ships at 0.8 while the softest real
frame measured 4.42, roughly five times above it. Every frame read `ok`
forever. The curator's gates are separate and calibrated to actual measured
values.

---

## Design decisions worth knowing

**All compute is local.** n8n Cloud cannot read Ada's filesystem and has no
Execute Command node, so it cannot participate in the image path. If you wire
n8n in later, it should trigger on `manifest.csv` appearing in Drive and
handle downstream orchestration only.

**The recurring sync costs no tokens by design.** The curator's judgment layer
is the one exception, and it is optional and cached.

**State is local, not in Drive.** The fingerprint ledger lives in
`~/CCC/Darkroom/.state`. Keeping it out of Drive means a sync fault can't
cause reprocessing or duplicate sequence numbers.

**Idempotent by fingerprint.** Files are keyed on name, size, and mtime.
Rerunning is safe. A partially-copied file is skipped and picked up next
cycle.

**Failure is non-destructive.** If no variant renders, the source stays in
`_dump` and is retried. The original moves only after at least one export
succeeds. A curation failure does not strand a roll — the renderer still
handles it on the old rules, which is worse output but not lost work.

**Deterministic where it counts.** Tone, crop, and scale only. No content is
altered, added, or removed. These are documentary photographs of real club
members — regenerating pixels is a credibility problem for a nonprofit
publishing images of its own community.

---

## What this system does not do

- **It does not choose pictures.** The curator's verdict is advisory and its
  scope is technical. Which frames are worth publishing is a human judgement
  and stays one.
- **It does not edit content.** No retouching, no object removal, no
  generative fill.
- **It does not publish.** Every manifest row is `draft`. Posting, sending,
  and scheduling remain manual and explicitly approved.

---

## Bugs found and fixed during the v3 build

Recorded because each represents a trap that could recur.

**The HOLD flag fired on any legible text**, so cap embroidery and bike
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
in perpetuity. The floor is now 20,000 and exists only to skip junk, and a
file that will never qualify says so explicitly instead of pretending to wait.

**The re-test script staged a stale plan** back into the dump alongside the
originals, which made the cycle treat a roll as already curated and skip
judging it.

---

Catskills Cycling Club, Inc. · internal technical documentation
