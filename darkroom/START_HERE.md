# START HERE — CCC Darkroom deployment on Ada

Everything you need is in this folder. Read this page, then hand the runbook
to Claude Code on Ada.

**Package: CCC_Darkroom_v4 · the v3 curator pipeline · 31 July 2026**

---

## Read this first — what changed since the last package

The package you were sent before this one contained **v2 code**. `darkroom.py`
was 20,624 bytes and there was no `darkroom_curate.py` in it at all. The zip
labelled `CCC_Darkroom_v3.zip` in the project folder is byte-identical v2 code
under a v3 name, and the `CCC_Darkroom_v4.zip` the old START_HERE told you to
unzip did not exist anywhere.

Deploying that would have installed a duotone-only pipeline with no curator,
no colour path, and no route-safety quarantine — and it would have looked like
it worked.

**This package is the real thing.** The v3 curator layer that was sitting in
`curator_bundle/` has been folded in, the launchd job now runs the full cycle
instead of the renderer alone, and the runbook covers the curator.

Two corrections to what the old START_HERE told you:

- **There is a colour path.** The curator decides colour or brand duotone per
  frame, and renders *both* when its two layers disagree. If your phone photos
  come back in colour, that is correct v3 behaviour. "Everything comes back
  black and white" was true of v2 only.
- **`darkroom.py` alone is not enough.** launchd must run
  `darkroom_cycle.sh` — curate, then render, then contact sheet. The renderer
  on its own finds no plan and silently falls back to v2's orientation rules.
  The output looks plausible and is wrong. The supplied plist already does the
  right thing.

---

## Which tool, which model

**Use Claude Code.** Not Cowork.

This is almost entirely shell work — checking mount paths, installing Python
dependencies, rewriting config constants, loading launchd agents, reading
logs. Claude Code is built for that. A Cowork session in the cloud cannot
reach Ada's filesystem, `launchctl`, or `~/CCC` at all.

Run it on **Ada**, not the work laptop. The whole point is to configure the
machine that does the processing.

**Model: Claude Sonnet 5, effort High.**

The runbook is fully specified — every command written out, every decision
point carrying a HALT code with a single prescribed action. That is mechanical
execution, and Sonnet handles it well and faster.

**Escalate to Opus 5 if** the run halts twice on the same code, hits something
the runbook doesn't cover, or lands on **HALT-08** or **HALT-09** — those two
are judgment calls about the curator rather than plumbing.

---

## Setup, about two minutes

1. Unzip `CCC_Darkroom_v4.zip` somewhere convenient on Ada — `~/Downloads` is
   fine. You get a `darkroom/` folder with the runbook already inside it.

2. Open Terminal on Ada and `cd` into it:

   ```bash
   cd ~/Downloads/darkroom
   ls
   ```

   You should see nine `.py`/`.sh` pipeline scripts, three `install_*`/`set_*`
   helper scripts, two `.plist` files, a `fonts/` folder, the runbook, and
   this page.

3. Start Claude Code in that directory.

4. Give it this prompt:

   > Read DARKROOM_DEPLOY_RUNBOOK.md and execute it from Phase 0. Follow the
   > operating rules exactly. Work the phases in order, do not skip ahead, and
   > stop at any HALT code rather than improvising. Print every command and
   > its output. Produce the deploy report at the end.

It will work through eleven phases and either finish or halt with a specific
question for you.

---

## What it's fixing

You uploaded files to `CCC/Photos/_dump/` and nothing processed. Three likely
causes, and the runbook checks all of them:

1. **Darkroom was probably never installed on Ada.** The package has been
   sitting as files; creating folders in Drive doesn't put software on the
   machine. If nothing is in `~/CCC/Darkroom/bin` and no launchd job is
   loaded, this is the whole answer.

2. **The first four files were loose in `_dump/` root.** Darkroom ignores
   those on purpose — a file with no roll folder has no label and no date.

3. **The folder you then created is named `_dump/2026-07-31_test`** — with a
   forward slash *in the name*. Drive permits that; macOS filesystems do not.
   It will either fail to sync to Ada or arrive renamed with a colon.

The runbook also checks the other usual suspects: wrong Drive account folder
name, files streamed rather than downloaded, launchd not loaded, Ada asleep.

---

## Folder naming rules — worth memorising

Roll folders go **inside** `_dump`, and the name becomes the roll label and
supplies the date.

- Letters, numbers, hyphens, underscores. **No slashes, no spaces.**
- Don't prefix with `_dump/`. It's already in `_dump`.
- Format `YYYY-MM-DD_label` so the shoot date carries into every filename.

| | |
|---|---|
| Good | `2026-08-01_borderlands` |
| Good | `2026-07-31_test` |
| Bad | `_dump/2026-07-31_test` — slash, won't sync |
| Bad | `Borderlands 8/1` — slash and spaces |

---

## Three things you may need to do by hand

The agent can't do these. If it halts asking for one, this is what it means.

**HALT-03 — files are streamed placeholders.**
Finder → right-click `My Drive/CCC/Photos` → Offline access → **Available
offline**. Wait for sync. Darkroom needs real bytes on disk; a streamed file
looks present in Finder but has nothing in it.

**HALT-04 — Pillow won't install.**
Terminal: `xcode-select --install`, approve the prompt, let it finish. Then
tell Claude Code to resume from Phase 4.

**HALT-08 — the API key or model needs a decision.**
The curator's judgment layer needs an Anthropic API key at
`~/CCC/Darkroom/.anthropic_key`, and `MODEL` in `darkroom_curate.py` must be
one the account actually has. That console balance is **separate from your
Claude subscription**. Three valid answers: change the model, top up the API
account, or say "run measurement-only" — the pipeline works without judgment,
just with less of it.

```bash
printf '%s' 'sk-ant-...' > ~/CCC/Darkroom/.anthropic_key
chmod 600 ~/CCC/Darkroom/.anthropic_key
```

---

## What "done" looks like

- `~/CCC/Darkroom/bin/` contains nine scripts and a `fonts/` folder
- `darkroom_curate.py --selftest` reports the Vision backend, a reachable API,
  and a valid model
- `launchctl list | grep darkroom` shows two jobs, and the processing one runs
  **`darkroom_cycle.sh`**, not `darkroom.py`
- A test roll processed into `instagram/ strava/ email/ web/` in Drive, with a
  `_curation.json` beside it explaining every decision
- A `contact_sheet.png` you can open on your phone
- Phase 10 confirms a frame processed **on the timer**, with no manual run

That last one is the real test. Everything before it proves the scripts work;
Phase 10 proves the automation does.

---

## The one thing to actually look at afterwards

**Phase 8 — the held frames.** Anything the curator flags for route safety
lands in `_hold/` instead of the publish folders, with a `HOLD_REVIEW.txt`
saying why.

Whether that flag is calibrated correctly has never been checked against real
pictures. It has already over-fired once — an early version matched any
legible text, so cap embroidery and bike branding held 6 of 7 frames. Open the
held frames on your phone and see whether you agree with them. That is the
one judgment the deploy can't make for you.

Releasing a hold is deliberately two steps: set `"hold": false` in
`_curation.json`, then `python3 ~/CCC/Darkroom/bin/darkroom.py --force`.

---

## After it's running

**From your phone:** Drive app → `_dump/` → new folder `YYYY-MM-DD_label` →
upload into it. Processed within about 15 minutes plus sync time.

**On Ada, when film scans come back from Exposure Therapy's portal:**

```bash
python3 ~/CCC/Darkroom/bin/darkroom_intake.py --latest --roll 2026-08-01_borderlands
```

**Force a cycle instead of waiting:**

```bash
bash ~/CCC/Darkroom/bin/darkroom_cycle.sh
```

**Watch the log:**

```bash
tail -f ~/CCC/Darkroom/logs/darkroom.log
```

Full detail is in `README.md` in this folder.

---

## Still worth doing: shoot 4:3

Every frame in the original test roll was shot **16:9**, not the sensor's
native 4:3. Cropping 16:9 to Instagram's 4:5 portrait discards 55% of the
frame; at 4:3 the same crop discards 40% and crops cleanly. The curator works
around this now, but it is working around a problem that need not exist.

---

Catskills Cycling Club, Inc. · internal technical documentation
