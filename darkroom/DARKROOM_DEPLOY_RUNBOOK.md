# CCC Darkroom — Deploy & Diagnose Runbook

**Target machine:** Ada (Mac mini)
**Operator:** club photo lead — typically monitoring from Claude mobile
**Package:** CCC_Darkroom_v4 — v3 curator pipeline
**Runbook version:** 2.0 · 31 July 2026

---

## READ THIS FIRST — agent instructions

You are running on Ada, John's Mac mini. Your job is to get **CCC Darkroom**
installed, running, and verified.

**Symptom that triggered this runbook:** John uploaded four `.jpg` files to the
Google Drive folder `CCC/Photos/_dump/` from his phone, waited more than 15
minutes on wifi, and nothing was processed. No output folders appeared.

**Three known or suspected causes. Check all of them:**

1. **Darkroom was probably never installed on Ada.** The package was delivered
   but there is no evidence the deploy steps were run. Creating folders in
   Drive does not put software on the machine. **Check this first** — if
   `~/CCC/Darkroom/bin` is empty and no launchd job is loaded, this is the
   whole answer and everything else is secondary.
2. **The files are loose in the `_dump/` root**, not inside a named roll
   subfolder. Darkroom ignores loose files on purpose — they have no roll
   label and no date. Confirmed from a screenshot.
3. **A roll folder was created named `_dump/2026-07-31_test`** — with a forward
   slash *in the folder name*. Drive permits `/`; macOS filesystems do not.
   Drive for Desktop either refuses to sync it or substitutes a colon.

Work the phases in order. **Do not skip ahead.** Each phase ends with an
assertion. If an assertion fails, follow the HALT code exactly and stop.

### Operating rules

- **Read-only until Phase 4.** Phases 0–3 gather evidence. Change nothing.
- **Never delete any file** in `_dump/`, `_originals/`, or any roll folder.
  Moving files within `_dump/` is permitted from Phase 6 onward.
- **Never publish, post, send, or schedule anything.** This pipeline produces
  drafts only. No step here touches Instagram, Mailchimp, Strava, Substack,
  Gmail, or any external service. The one network call the pipeline makes is
  to the Anthropic API, for editorial judgment, and it writes nothing anywhere.
- **Print every command you run and its output.** John is reading this from
  his phone and needs evidence, not summaries.
- If a HALT fires, state the HALT code, what you observed, and what John needs
  to decide. Then stop. Do not improvise a workaround.
- **Prefer the supplied scripts over hand-editing.** `install_darkroom.sh`,
  `set_drive_account.sh`, and `install_launchd.sh` exist because those three
  steps are where previous manual deploys went wrong.

---

## Background — what this system is, in the version you are installing

Darkroom turns photographs dropped into a Drive folder into publish-ready
exports in every format the club uses. **Judgment and rendering are separate
programs, and the renderer never makes an editorial decision.**

```
  Phone upload  ──▶  Drive: CCC/Photos/_dump/<roll-name>/
                            │  Drive for Desktop syncs down to Ada
                            ▼
              darkroom_cycle.sh   (launchd, every 15 min)
                            │
        1. darkroom_curate.py ── decides WHAT should happen
             • measurement (local, free): macOS Vision — saliency, people,
               faces, legible text, scene; plus exposure/sharpness/saturation
             • judgment (optional, costs tokens): an LLM returns a
               description, technical verdict, which formats suit this
               picture, colour or duotone, a route-safety call
             • writes _curation.json beside the frames — plain JSON,
               meant to be edited; a human override beats both layers
                            │
        2. darkroom.py ────────── owns every pixel, follows the plan
             • idempotent by fingerprint, never deletes
             • with no plan present, behaves exactly as v2 did
                            │
        3. darkroom_sheet.py ──── branded contact sheet
                            │
                            ├──▶ CCC/Photos/<date>_<roll>/instagram strava email web
                            ├──▶ CCC/Photos/<date>_<roll>/_hold/  (route-safety)
                            └──▶ CCC/Photos/_originals/<date>_<roll>/
```

**Critical design facts you must not violate:**

- **launchd must run `darkroom_cycle.sh`, never `darkroom.py` alone.** The
  renderer on its own finds no curation plan and silently reverts to v2
  orientation-bucket rules. The output looks plausible and is wrong. The
  supplied plist already points at the cycle script — do not "simplify" it.
- All compute is local to Ada. n8n Cloud cannot read the filesystem and has no
  Execute Command node. Do not route any of this through n8n.
- The tone map is a fixed lookup table, deterministic by design. Do not
  substitute a model, a filter, or a "smarter" adaptive version.
- **HOLD is a union, never an override.** If either layer raises a
  route-safety concern it stands. The model can add a hold; it can never
  clear one. Do not add logic that lets it.
- Every manifest row is written `status: draft`. Nothing auto-publishes.
- Frames must land in a **named subfolder** of `_dump/`. Loose files are
  ignored on purpose.

### What changed since the last package John was sent

The previous deploy package contained **v2 code** — `darkroom.py` at 20,624
bytes with no `darkroom_curate.py`. Deploying it would have installed a
duotone-only pipeline with no curator, no colour path, and no HOLD
quarantine. This package carries the v3 curator layer. If you find a v2
install already on Ada, `install_darkroom.sh` backs it up before replacing it.

### What the package must contain

```
darkroom.py               v3 renderer          ~27 KB
darkroom_curate.py        curator              ~37 KB   <- absent in v2 packages
darkroom_sheet.py         contact sheet         ~9 KB
darkroom_cycle.sh         the thing launchd runs
darkroom_learn.py         feedback loop
darkroom_intake.py        stages lab scans from a zip
tidy_dump.py              sorts loose files into dated rolls
retest_roll.sh            re-run a finished roll from originals
analyze_run.py            reconstruct what was decided and why
install_darkroom.sh       fresh install
set_drive_account.sh      point all nine scripts at the real Drive account
install_launchd.sh        render, lint, load the launchd agents
com.ccc.darkroom.plist          launchd template, 15 min
com.ccc.darkroom.learn.plist    launchd template, Sundays 20:00
fonts/                    Bebas Neue + Montserrat, required by the sheet
```

---

## Expected paths

| Thing | Path |
|---|---|
| Drive mount root | `~/Library/CloudStorage/GoogleDrive-<account>/My Drive` |
| Photos root | `<Drive>/CCC/Photos` |
| Dump / landing zone | `<Photos>/_dump` |
| Originals archive | `<Photos>/_originals` |
| Install dir | `~/CCC/Darkroom` |
| Scripts | `~/CCC/Darkroom/bin` |
| Logs | `~/CCC/Darkroom/logs/darkroom.log`, `learn.log` |
| State ledger | `~/CCC/Darkroom/.state` (outside Drive by design) |
| LLM response cache | `~/CCC/Darkroom/.state/llm_cache` |
| API key | `~/CCC/Darkroom/.anthropic_key`, chmod 600 |
| launchd agents | `~/Library/LaunchAgents` |

The Drive account folder name is **not** assumed. Phase 1 discovers it.

---

# PHASE 0 — Establish context

Read-only. Gather facts before touching anything.

```bash
echo "=== whoami / host ==="
whoami
scutil --get LocalHostName 2>/dev/null || hostname
sw_vers

echo
echo "=== python ==="
which python3
python3 --version
python3 -c "import PIL; print('Pillow', PIL.__version__)" 2>&1
python3 -c "import Vision; print('Vision OK')" 2>&1

echo
echo "=== is Darkroom installed? ==="
ls -la ~/CCC/Darkroom 2>&1
ls -la ~/CCC/Darkroom/bin 2>&1

echo
echo "=== if installed, which version? ==="
ls -la ~/CCC/Darkroom/bin/darkroom_curate.py 2>&1
wc -c ~/CCC/Darkroom/bin/darkroom.py 2>&1

echo
echo "=== are the launchd jobs loaded? ==="
launchctl list | grep -i darkroom || echo "NO DARKROOM JOBS LOADED"
ls -la ~/Library/LaunchAgents/ | grep -i darkroom || echo "NO DARKROOM PLISTS PRESENT"

echo
echo "=== if a job is loaded, what does it actually run? ==="
grep -A4 ProgramArguments ~/Library/LaunchAgents/com.ccc.darkroom.plist 2>&1

echo
echo "=== sleep settings ==="
pmset -g | grep -Ei "sleep|hibernate" || true

echo
echo "=== package present in working dir? ==="
ls -la
```

**Record and report:**

- macOS version, `python3` version and path
- Whether Pillow imports; whether Vision imports
- Whether `~/CCC/Darkroom/bin` exists and what is in it
- **If a previous install exists:** does it have `darkroom_curate.py`? If not,
  it is a v2 install and Phase 4 will replace it (with a backup)
- **If a launchd job is loaded:** does it run `darkroom_cycle.sh` or
  `darkroom.py`? If the latter, it is a v2 job and Phase 9 replaces it
- Whether the package files are in the working directory

### HALT-00 — package missing or incomplete

If `darkroom.py`, `darkroom_curate.py`, `darkroom_cycle.sh`, `fonts/`, or the
three `install_*.sh` / `set_*.sh` scripts are not in the working directory:
**HALT-00.**

Report: "Darkroom package not found or incomplete in the working directory.
Present: `<list>`. Missing: `<list>`. John needs to unzip
`CCC_Darkroom_v4.zip` here before this runbook can proceed."

**Specifically: if `darkroom_curate.py` is missing but the other files are
present, this is a v2 package, not v4.** Say so explicitly — deploying it
would install the wrong pipeline. That is HALT-00 too.

### Assertion 0

You can state, as facts: whether Darkroom is installed, whether it is v2 or
v3, whether its launchd jobs are loaded, and what they run. Say all four
explicitly before continuing.

---

# PHASE 1 — Find the Drive path

The single most common failure. The account folder name varies.

```bash
echo "=== CloudStorage mounts ==="
ls -la ~/Library/CloudStorage/ 2>&1

echo
echo "=== is Drive for Desktop running? ==="
pgrep -lf "Google Drive" || echo "GOOGLE DRIVE NOT RUNNING"
```

Then locate the Photos folder. Try each mount found above:

```bash
for d in ~/Library/CloudStorage/GoogleDrive-*; do
  echo "--- $d ---"
  ls -la "$d/My Drive/CCC/Photos" 2>&1 | head -20
done
```

**Record:** the exact, full path to `CCC/Photos`, including the account folder
name. You need it in Phase 4.

### HALT-01 — Drive not running

If `pgrep` finds no Google Drive process: **HALT-01.**
Report: "Google Drive for Desktop is not running on Ada. It must be running
and signed in for Darkroom to see any files. John needs to launch it and
confirm it is signed in to the account holding CCC/Photos."

### HALT-02 — Photos folder not found

If no `CCC/Photos` exists under any mount: **HALT-02.**
Report which mounts exist and what is inside each `My Drive`. Ask John which
Google account holds the CCC folder. Note: his Claude connectors use
`<work-account>` (Rootd business), but CCC material is expected under
his personal account. The mount name tells you which is signed in.

### Assertion 1

You have one exact path of the form:

```
/Users/<user>/Library/CloudStorage/GoogleDrive-<account>/My Drive/CCC/Photos
```

Print it. Everything after this uses it.

---

# PHASE 2 — Verify the files are really on disk

Second most common failure. Google Drive can *stream* files — showing them in
Finder as placeholders with no bytes on disk. Darkroom cannot read a
placeholder.

```bash
PHOTOS="<path from Phase 1>"

echo "=== _dump contents ==="
ls -la "$PHOTOS/_dump/" 2>&1

echo
echo "=== recursive listing with real sizes ==="
find "$PHOTOS/_dump" -type f -exec ls -la {} \; 2>&1

echo
echo "=== can we actually READ the bytes? ==="
find "$PHOTOS/_dump" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.png" -o -iname "*.webp" \) | while read -r f; do
  size=$(stat -f%z "$f" 2>/dev/null || echo 0)
  head -c 4 "$f" > /dev/null 2>&1 && readable="YES" || readable="NO"
  echo "$size bytes  readable=$readable  $f"
done
```

**Interpreting this:**

- **Size 0, or far smaller than expected** (a phone JPG is 1–5 MB; a flat TIFF
  15–18 MB) → streamed placeholder.
- **`readable=NO`** → placeholder or a permissions problem.
- **Full size and readable** → sync is fine, the problem is elsewhere.

Note: `MIN_BYTES` is 20,000. A legitimate small image will process; anything
under that is treated as junk and says so explicitly rather than reporting
"still syncing" forever.

### HALT-03 — files are placeholders

If any file reports size 0 or `readable=NO`: **HALT-03.**

Report: "Files in `_dump` are streamed placeholders, not local copies.
Darkroom cannot read them. John needs to open Finder, right-click
`My Drive/CCC/Photos`, choose **Offline access → Available offline**, and wait
for sync to complete. This is a Finder action; it cannot be done reliably from
the command line."

Do not force this with `touch`, `cat`, or by opening files to trigger a
download. That can partially materialise files and produce half-written reads.

### Assertion 2

Every image file in `_dump` reports a plausible size and `readable=YES`.

---

# PHASE 3 — Diagnose the loose-files problem

```bash
PHOTOS="<path from Phase 1>"

echo "=== files loose in _dump root (IGNORED by design) ==="
find "$PHOTOS/_dump" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.png" -o -iname "*.webp" \) -print

echo
echo "=== roll subfolders (these ARE processed) ==="
find "$PHOTOS/_dump" -mindepth 1 -maxdepth 1 -type d -print

echo
echo "=== roll folder names, verbatim (watch for : or /) ==="
find "$PHOTOS/_dump" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | while IFS= read -r -d "" d; do
  printf "  [%s]\n" "$(basename "$d")"
done

echo
echo "=== existing processed output, if any ==="
ls -la "$PHOTOS/" 2>&1
```

### Illegal characters in roll folder names

A folder created in the Drive web or mobile app can contain `/`, which no
macOS filesystem allows. Drive for Desktop substitutes a colon, so a folder
named `_dump/2026-07-31_test` in the cloud appears on Ada as
`_dump:2026-07-31_test` — or fails to sync at all.

The verbatim listing shows this. Look for `:` or a leading `_dump` inside a
name.

**If found**, rename it. The folder is already inside `_dump`, so the prefix
is redundant:

```bash
PHOTOS="<path from Phase 1>"
# adjust the source name to exactly what the listing showed
mv "$PHOTOS/_dump/_dump:2026-07-31_test" "$PHOTOS/_dump/2026-07-31_test"
ls -la "$PHOTOS/_dump/"
```

If the folder does not appear on Ada at all but is visible in the Drive mobile
app, it never synced. Report **HALT-07**: "A roll folder exists in Drive but
not on Ada, most likely because its name contains a character macOS cannot
use. John needs to rename it in the Drive app to a plain label such as
`2026-07-31_test` — letters, numbers, hyphens and underscores only, no
slashes."

**Expected finding**, based on the screenshot John supplied: four `PXL_*.jpg`
files sitting directly in `_dump/`, and no valid subfolders.

**Explain this in your report.** Darkroom requires a named subfolder because
the folder name becomes the roll label and supplies the date. A file in the
root has neither, so it is skipped with a warning rather than given an
arbitrary name.

### Note on these particular files

They are `PXL_*.jpg` — Google Pixel phone photos, not film scans. That is fine
for a pipeline test. **Correction to earlier documentation:** v3 *does* have a
colour path. The curator decides colour or brand duotone per frame, and
renders **both** when its two layers disagree (the second gets a `_mono`
suffix). If John was told these would all come back black and white, that was
true of v2 and is no longer true.

Also worth flagging to John: every frame in the original test roll was shot
**16:9, not the sensor's native 4:3**. Cropping 16:9 to Instagram's 4:5
discards 55% of the frame. At 4:3 the same crop discards 40%. The curator
works around this, but shooting 4:3 gives it materially more room.

### Assertion 3

You can state exactly how many files are loose in the root and how many valid
roll subfolders exist.

---

# PHASE 4 — Install

Run this even if Phase 0 found a v2 install — the installer backs up anything
it replaces. Skip only if Phase 0 found a complete v3 install *and* Phase 5's
selftest passes.

```bash
./install_darkroom.sh
```

It creates the folders, backs up any existing scripts, installs all nine
scripts plus `fonts/`, installs Pillow (required) and pyobjc/Vision
(optional), and reports on the API key.

### HALT-04 — Pillow will not install

The installer exits 4 and prints the error. Common cause: Command Line Tools
missing. If the error mentions `xcrun`, `gcc`, or a missing developer
directory, report **HALT-04**: "Command Line Tools are required. John needs to
run `xcode-select --install` in Terminal and approve the GUI prompt, then this
runbook resumes from Phase 4."

Do not install via Homebrew, pyenv, or a virtualenv without asking. Changing
the Python that launchd uses is a bigger change than it looks — the learn
plist hardcodes `/usr/bin/python3`.

**pyobjc failing is not a HALT.** The curator degrades to a Pillow-only
measurement fallback. Note it in the report and continue.

### 4b — Point the scripts at the real Drive account

Nine files embed the Drive path. Do not edit them by hand.

```bash
./set_drive_account.sh --check
./set_drive_account.sh <account-name-from-phase-1>
```

The script verifies the target path exists *before* changing anything, backs
up each file, shows before/after for every edit, and fails loudly if any
reference is left pointing elsewhere. Show John its full output.

### Assertion 4

All nine scripts point at the real Drive path from Phase 1, Pillow imports,
and you have stated whether Vision is available.

---

# PHASE 5 — Curator selftest

New in v3. This is the cheapest possible check that the judgment layer is
wired up correctly, and it makes no API call that costs anything meaningful.

```bash
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest
```

It reports four things:

1. **measurement backend** — `vision` or `basic`. `basic` means pyobjc is
   missing; the pipeline still works with less to judge from.
2. **judgment layer** — key found, or heuristic only.
3. **model list** — if a key is present, the models the account actually has,
   with an arrow at the configured one.
4. **drive path exists** — must be `True`.

### HALT-08 — model not available, or API unreachable

If the selftest prints `MODEL is set to '<x>', which is NOT in that list`, or
an API error: **HALT-08.**

Report the full selftest output, the model currently set, and the models
listed. John decides one of:

- **Change the model** — edit `MODEL` near the top of
  `~/CCC/Darkroom/bin/darkroom_curate.py` to one from the list. Ask which.
- **Fix billing** — the API console has its own credit balance, separate from
  a Claude subscription. A 401/403 usually means key or credit, not code.
- **Proceed without judgment** — the curator runs on the measurement layer
  alone. Output is worse but the pipeline works. This is a legitimate choice.

Do not pick a model yourself. Do not proceed past this without an answer.

**No key at all is not a HALT** — it is a documented supported mode. Report it
clearly and continue.

### Assertion 5

You can state the measurement backend, whether judgment is active, the model
in use if so, and that the Drive path resolves.

---

# PHASE 6 — Stage the loose files into a roll folder

Now fix the reported symptom.

```bash
PHOTOS="<path from Phase 1>"
ROLL="2026-07-31_pipeline-test"

mkdir -p "$PHOTOS/_dump/$ROLL"

# move, don't copy — duplicates in the root would keep warning
find "$PHOTOS/_dump" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.png" -o -iname "*.webp" \) -exec mv {} "$PHOTOS/_dump/$ROLL/" \;

echo "=== staged ==="
ls -la "$PHOTOS/_dump/$ROLL/"
```

There is also `tidy_dump.py`, which sorts loose files into dated rolls by
mtime. For four files from one shoot the explicit `mkdir` above is clearer.
For a messier dump:

```bash
python3 ~/CCC/Darkroom/bin/tidy_dump.py           # dry run
python3 ~/CCC/Darkroom/bin/tidy_dump.py --apply
```

Use the roll name `2026-07-31_pipeline-test`. It carries today's date and
makes clear in the output that this was a test, not real film.

**Do not delete anything.** `mv` within `_dump` only.

### Assertion 6

`_dump/2026-07-31_pipeline-test/` contains the four files and the `_dump` root
contains no loose image files.

---

# PHASE 7 — First manual cycle

Do not wait for the timer. Run the cycle directly so you can see each stage.

`SETTLE_SECONDS` is 45 — a file must be untouched for 45 seconds before the
renderer will process it. You just moved these files, so wait first.

```bash
sleep 60

echo "=== 1. CURATE ==="
PHOTOS="<path from Phase 1>"
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --dir "$PHOTOS/_dump/2026-07-31_pipeline-test"

echo
echo "=== the plan it wrote ==="
cat "$PHOTOS/_dump/2026-07-31_pipeline-test/_curation.json"
```

Read the plan out loud in your report — per frame: verdict, description,
tone, formats chosen, and `hold`. This is the part John cannot see from his
phone and most wants to know about.

```bash
echo "=== 2. DRY RUN the renderer ==="
python3 ~/CCC/Darkroom/bin/darkroom.py --dry-run -v
```

If it says `nothing to do`, something in Phases 1–6 is still wrong. Go back
rather than forcing it.

```bash
echo "=== 3. RENDER ==="
python3 ~/CCC/Darkroom/bin/darkroom.py -v

echo "=== 4. CONTACT SHEET ==="
python3 ~/CCC/Darkroom/bin/darkroom_sheet.py --all
```

Then inspect:

```bash
PHOTOS="<path from Phase 1>"
echo "=== output tree ==="
find "$PHOTOS/2026-07-31_pipeline-test" -type f | sort

echo
echo "=== manifest ==="
cat "$PHOTOS/2026-07-31_pipeline-test/manifest.csv"

echo
echo "=== anything held? ==="
cat "$PHOTOS/2026-07-31_pipeline-test/_hold/HOLD_REVIEW.txt" 2>&1

echo
echo "=== originals archived? ==="
ls -la "$PHOTOS/_originals/2026-07-31_pipeline-test/" 2>&1

echo
echo "=== dump folder cleaned up? ==="
ls -la "$PHOTOS/_dump/" 2>&1

echo
echo "=== full reconstruction of what was decided and why ==="
python3 ~/CCC/Darkroom/bin/analyze_run.py 2026-07-31_pipeline-test
```

**Expected result:**

- Four frames curated and rendered
- Variants in `instagram/ strava/ email/ web/` — the *count varies per frame*,
  because the curator chooses formats per picture rather than by orientation
  bucket. Do not treat a frame with fewer variants as a failure.
- Some frames may be `colour`, some `duotone`, some `both` (the `both` ones
  write each format twice, the second with `_mono`)
- `manifest.csv` with every row `status: draft`
- `decisions.txt` stub created
- `_curation.json` copied to the output folder and `_originals/`
- Originals moved to `_originals/2026-07-31_pipeline-test/`
- `_dump/2026-07-31_pipeline-test/` gone (it self-deletes when empty)
- Possibly a `_hold/` folder — see Phase 8

### HALT-05 — cycle produces nothing

If the renderer reports `done — 0 frame(s)` or errors on every file, capture
the full verbose output and **HALT-05**. Include exact error text. Do not
retry more than twice.

### Assertion 7

At least one frame curated and rendered, and its variants exist on disk.
Report actual counts, and the tone and format decision for each frame.

---

# PHASE 8 — Review what was held

New in v3, and the phase most worth doing carefully. Whether HOLD is
calibrated correctly has **never been verified against real frames** — the
held frames from the original test roll were never visually reviewed.

```bash
PHOTOS="<path from Phase 1>"
echo "=== held frames ==="
ls -la "$PHOTOS/2026-07-31_pipeline-test/_hold/" 2>&1
find "$PHOTOS/2026-07-31_pipeline-test/_hold" -type f | sort
echo
cat "$PHOTOS/2026-07-31_pipeline-test/_hold/HOLD_REVIEW.txt" 2>&1
```

Held frames render into `_hold/` rather than the publish folders. The reason
matters: the club publishes photographs of its own members riding regular
routes on a predictable schedule, so enough frames showing readable road
signs, junction names or trailhead markers effectively publishes where members
will be and when. It is a duty-of-care question and must not be automated
away.

**Report to John, per held frame:** the filename, and the exact reason string
from `HOLD_REVIEW.txt`. He reviews the pictures on his phone from the contact
sheet and decides.

**Do not release a hold yourself.** Releasing is a deliberate two-step human
action: set `"hold": false` in `_curation.json`, then re-run
`python3 ~/CCC/Darkroom/bin/darkroom.py --force`. Only do this if John says
so explicitly, naming the frame.

### HALT-09 — everything held, or nothing published

If **every** frame is held, or `instagram/ strava/ email/ web/` are all empty
while `_hold/` has content: **HALT-09.**

Report the hold reasons verbatim. A flag that fires on everything is one
nobody reads — this exact failure already happened once, when the HOLD test
matched any legible text and cap embroidery held 6 of 7 frames. It was
narrowed to genuinely locational text. If it is over-firing again, John needs
to see the reasons before anything is tuned. Do not adjust the thresholds
yourself.

### Assertion 8

You can state how many frames were held, why each was held, and that at least
one frame reached the publish folders — or you have raised HALT-09.

---

# PHASE 9 — Schedule the launchd jobs

Only after Phases 7 and 8 succeed manually.

```bash
./install_launchd.sh
```

It substitutes the real `$HOME` into both plist templates, validates them with
`plutil` **before** installing, unloads any previous version, loads both,
verifies with `launchctl list`, waits 45 s for the `RunAtLoad` cycle, and
tails the log.

Confirm from its output that the processing job runs
**`/bin/bash .../darkroom_cycle.sh`** and not `darkroom.py`. If a v2 job was
previously loaded, this replaces it.

### HALT-06 — job will not load

The script exits 6 and prints the `plutil -lint` output. Report **HALT-06**
with that output. Do not hand-edit the installed plist as a workaround — fix
the template in the package and re-run.

### Assertion 9

`launchctl list` shows both jobs, the processing job runs `darkroom_cycle.sh`,
and `darkroom.log` contains a timestamped entry from the load-time run.

---

# PHASE 10 — End-to-end verification

Prove the real path works, not just the manual one. **This is the actual thing
being verified** — everything before it only proves the scripts run.

```bash
PHOTOS="<path from Phase 1>"
ROLL="2026-07-31_launchd-verify"

mkdir -p "$PHOTOS/_dump/$ROLL"

# reuse one already-processed frame as a fresh source
SRC=$(find "$PHOTOS/_originals/2026-07-31_pipeline-test" -type f ! -name "_curation.json" ! -name ".*" | head -1)
cp "$SRC" "$PHOTOS/_dump/$ROLL/verify_001.${SRC##*.}"

echo "staged:"
ls -la "$PHOTOS/_dump/$ROLL/"
echo
echo "Now waiting for the 15-minute timer. Do NOT run anything manually."
```

Wait for the timer — up to 15 minutes plus the 45-second settle. Then:

```bash
PHOTOS="<path from Phase 1>"
tail -40 ~/CCC/Darkroom/logs/darkroom.log
find "$PHOTOS/2026-07-31_launchd-verify" -type f 2>&1 | sort
```

The log should show `=== darkroom cycle <timestamp> ===`, then
`-- curating 2026-07-31_launchd-verify`, then `-- rendering`, then
`-- contact sheets`.

If output appears without you having run anything manually, the automation
works.

### Assertion 10

A frame was curated, rendered, and sheeted by the scheduled job with no manual
invocation. Quote the log lines that prove it.

---

# PHASE 11 — Sleep check

launchd does not fire while the machine is asleep. It catches up on wake, so
sleep delays rather than loses work — but for unattended running Ada should
stay awake.

```bash
pmset -g | grep -Ei "sleep|standby"
```

If `sleep` is not `0`, report it with the current value. **Do not change power
settings without asking** — Ada's sleep configuration is shared with the
Cowork setup and changing it has effects beyond this pipeline.

---

# DEPLOY REPORT

Produce this at the end, whether you finished or halted.

```
CCC DARKROOM — DEPLOY REPORT
============================
Date:            <date/time>
Package:         CCC_Darkroom_v4 (v3 curator pipeline)
Runbook:         Deploy & Diagnose v2.0
Outcome:         COMPLETE / HALTED AT <code>

ROOT CAUSE OF ORIGINAL SYMPTOM
  <state plainly why the four files were not processed>

ENVIRONMENT
  macOS:              <version>
  python3:            <version and path>
  Pillow:             <version>
  Vision (pyobjc):    AVAILABLE / FALLBACK TO BASIC
  Drive account:      <folder name found in Phase 1>
  Photos path:        <full path>
  Offline sync:       CONFIRMED / PLACEHOLDER FILES FOUND

INSTALL
  Previously installed:            NO / YES — v2 / YES — v3
  Backed up to:                    <paths, or n/a>
  Scripts installed:               <count>
  Drive path rewritten in:         <n> files

CURATOR
  Measurement backend:             vision / basic
  Judgment layer:                  ACTIVE / NO KEY / DISABLED BY JOHN
  Model:                           <id, or n/a>

PROCESSING
  Files staged:        <n> into <roll name>
  Frames curated:      <n>
  Frames rendered:     <n>
  Tone decisions:      <n> colour, <n> duotone, <n> both
  Variants written:    <n>
  Frames held:         <n>  — reasons: <verbatim>
  Manifest rows:       <n>, all status=draft: YES / NO
  Contact sheet:       BUILT / FAILED

SCHEDULING
  com.ccc.darkroom:        LOADED, runs darkroom_cycle.sh / NOT LOADED
  com.ccc.darkroom.learn:  LOADED / NOT LOADED
  Timer-driven run verified: YES / NO
  Ada sleep setting:         <value>

OPEN ITEMS FOR JOHN
  <anything requiring a human decision — held frames especially>

NOTHING WAS PUBLISHED, SENT, OR SCHEDULED EXTERNALLY.
```

---

# Quick reference — for John, after this runbook

**Normal use, phone:**
Drive app → `CCC/Photos/_dump/` → create a folder named `YYYY-MM-DD_label` →
upload into **that folder**, never into `_dump` directly.

**Folder naming — both rules matter:**

- Letters, numbers, hyphens, underscores only. **No slashes, no spaces.**
  Drive allows them; macOS does not, and the folder will not sync.
- Do not prefix with `_dump/`. It is already inside `_dump`.

Good: `2026-08-01_borderlands` · `2026-07-31_test`
Bad: `_dump/2026-07-31_test` · `Borderlands 8/1`

**Everyday commands on Ada:**

```bash
# health check — both layers, API, model validity, Drive path
python3 ~/CCC/Darkroom/bin/darkroom_curate.py --selftest

# one full cycle by hand (what launchd runs every 15 min)
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh

# stage lab scans from the Exposure Therapy portal zip
python3 ~/CCC/Darkroom/bin/darkroom_intake.py --latest --roll 2026-08-01_borderlands

# sort loose files in the dump root into dated rolls
python3 ~/CCC/Darkroom/bin/tidy_dump.py           # dry run
python3 ~/CCC/Darkroom/bin/tidy_dump.py --apply

# re-run a finished roll from its untouched originals, non-destructively
~/CCC/Darkroom/bin/retest_roll.sh <roll-name>

# after editing _curation.json by hand
python3 ~/CCC/Darkroom/bin/darkroom.py --force

# reconstruct what was decided for every frame and why
python3 ~/CCC/Darkroom/bin/analyze_run.py [roll-name]

# watch the log
tail -f ~/CCC/Darkroom/logs/darkroom.log

# stop the automation
./install_launchd.sh --unload
```

---

## HALT code index

| Code | Meaning | Single prescribed action |
|---|---|---|
| HALT-00 | Package missing, incomplete, or v2 not v4 | John unzips `CCC_Darkroom_v4.zip` in the working directory |
| HALT-01 | Google Drive for Desktop not running | John launches it and confirms sign-in |
| HALT-02 | `CCC/Photos` not found under any mount | John confirms which Google account holds it |
| HALT-03 | Files are streamed placeholders | John sets `CCC/Photos` to Available Offline in Finder |
| HALT-04 | Pillow will not install | John runs `xcode-select --install` |
| HALT-05 | Cycle produces nothing | Report full verbose output; await instruction |
| HALT-06 | launchd job will not load | Report `plutil -lint` output |
| HALT-07 | Roll folder in Drive but not on Ada | John renames it in the Drive app — no slashes |
| HALT-08 | Model unavailable / API unreachable | John picks a model, fixes billing, or accepts measurement-only |
| HALT-09 | Every frame held, nothing published | John reviews the hold reasons before anything is tuned |

---

Catskills Cycling Club, Inc. · internal technical documentation
