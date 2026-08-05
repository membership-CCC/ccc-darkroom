# Changelog

Written mainly to stop the version naming from misleading anyone again. The
zips that circulated before this repo existed do not mean what their filenames
say.

**Tell v2 from v3 in one command:** `ls darkroom_curate.py`. If it is absent,
it is v2, whatever the filename claims. Secondary check: `darkroom.py` is
~20.6 KB in v2 and ~27.2 KB in v3.

---

## Unreleased — 4 August 2026 (j)

### The silent-failure redesign: the pipeline now proves it ran, or says it didn't

The automation died silently three separate times. Root cause of the standing
outage: the launchd plist installed on Ada was a v3-era hand-edit template
pointing at `/Users/johndolsson/`, a home directory that does not exist —
launchd fired every 15 minutes into a void, and could not even log the failure
because its log path was inside the nonexistent home too. Compounding it,
`apply.sh` said "there is nothing to reload," so every update faithfully
repaired the scripts while preserving the broken agent that runs them.

The redesign's governing rule: **every failure mode either self-heals, retries
next cycle, or announces itself somewhere John actually looks.**

- **`_darkroom_status.txt`** at the root of `CCC/Photos` in Drive, rewritten by
  every cycle. Drive syncs it to the phone: a fresh timestamp is a live
  pipeline, a stale one is the alarm, and the file names the command to run.
- **The heartbeat moves at cycle START** (`.state/heartbeat`) — it answers "is
  launchd running me at all", the question none of the three failures could.
- **`install_launchd.sh` verifies END-TO-END**: after loading it waits for the
  heartbeat to move under launchd and HALTs if it doesn't. "launchctl list
  shows it" was true during all three outages; it is no longer accepted as
  proof. `apply.sh` now reinstalls the agents on every run.
- **WatchPaths on `_dump`**: a new roll triggers a cycle in ~2 minutes; the
  15-minute timer remains the completeness guarantee. `__DUMP__` is
  substituted from the account-patched cycle script — one source of truth.
- **Roll readiness gate**: a roll whose contents change across a 3-second
  window (mid-sync) or whose files are cloud-only placeholders (0 allocated
  blocks) is DEFERRED, not half-processed; placeholders get a 1MB read nudge
  to start materializing. >12 deferrals flags the roll as stuck in the status
  file. Matters double for 35mm lab-scan TIFFs, which are large and sync slow.
- **Drive self-heal**: if the configured mount is gone but another
  `GoogleDrive-*` mount has the right shape, the cycle uses it and warns —
  an account rename degrades to a log line instead of an outage.
- **Cross-watchdog**: the Sunday learn job runs via `darkroom_learn.sh`, which
  first checks the main heartbeat and raises the alarm at >2h stale. Two
  independent schedules watching each other; it reports, never repairs.
- **`darkroom_doctor.sh`** (new): the 30-second diagnosis. Every check is a
  scar — wrong-user plists, installed-but-not-loaded agents, TCC-blocked
  CloudStorage reads, EDEADLK listings, placeholder files, stale locks, the
  machine sleeping through its own timer. PASS/WARN/FAIL with the fix command.
- Plist substitution moved from sed to python: the dump path contains an
  email and spaces, and a sed metacharacter in a path corrupts a plist
  silently — the exact class of failure this release exists to kill.

**v5.1, same day.** First deploy of v5 halted at apply.sh's verification
gate: the new cycle script said `GoogleDrive-*` in a comment and a glob, and
set_drive_account.sh's rewrite pattern (`GoogleDrive-[^/"']+` — newlines ARE
in that class) ate seven lines through it, snapping the script. The gate
caught the mangle before anything loaded — the first field save of the v5
design. Fix: the Drive token now appears exactly once per patched file, on
the DUMP= line; the self-heal glob matches any CloudStorage mount with the
right shape, which is also more general. Verified against the patcher's
verbatim regex: post-patch diff touches one line, post-patch script parses,
full simulated cycle processes a roll.

**v5.2, first proven cycle.** The install PASSED its end-to-end proof and
immediately taught three more lessons, live:

- **Reads are ground truth; block counts are folklore.** stat said a roll was
  materialized; PIL's read got EDEADLK. roll_ready now probe-reads the head
  of every file (the probe doubles as the materialization nudge) and defers
  on any failure. Retry-on-EDEADLK added at the read layer of curate AND
  renderer — the renderer previously SKIPPED a frame on first read error,
  which in a merged roll is silent data loss, the same failure shape as the
  fingerprint bug of (i).
- **The renderer scans _dump for itself**, so wrapper-level deferral alone
  could not protect a roll: it skipped curation and the renderer consumed
  the roll unjudged anyway. New mechanism: the wrapper writes
  `.state/skip_rolls.txt` each cycle; darkroom.py honors it. Held rolls wait,
  healthy rolls process normally in the same cycle.
- **The "curation failed -> render on v2 rules" fallback is retired** for the
  first 12 attempts. It converted transient I/O errors into rolls consumed
  without judgment. After 12 consecutive failures it re-arms, loudly, so one
  permanently bad roll cannot wedge the pipeline.
- Doctor false positives fixed: path extraction now stops at spaces (the
  substituted plist COMMENT read as a wrong path), and the launchd-state
  loop no longer loses its counters to a pipe subshell.

Lesson recorded for the next redesign: an unattended system without a
proof-of-life channel is not automated, it is abandoned with extra steps.
Second lesson, an hour later: before feeding new text to an existing
rewriter, read the rewriter. Third, an hour after that: a mock that deletes
whatever the real component would have refused to touch proves nothing —
make the mock honor the contract before trusting the green light.

---

## Unreleased — 2 August 2026 (i)

**Originals are filed by photographer.**

    _originals/2026-08-01_borderlands/
        by_donalrey/                 52 frames
        by_the_catskill_weekender/   14 frames
        _credits.json
        _curation*.json

The flat archive worked but was answering the question badly. "Whose is this?"
needed a tool; now the folder says so. Three things fall out of it:

- **retest_roll.sh becomes symmetric.** Each `by_<handle>/` stages back as
  `<roll>_by_<handle>` — the exact folder shape the frames arrived in — so a
  re-render takes credit from the folder name, the same code path as a fresh
  upload. There is no re-render-specific attribution path left to get wrong,
  which is the whole of yesterday's bug class.
- **Filename collisions between photographers are unreachable.** Two people can
  both hand over `IMG_0001.jpg`; they land in different folders. The `__2`
  suffixing that once doubled the archive has nothing to fire on.
- The credit ledger stays, re-keyed to `by_<handle>/<file>`, as a redundant
  record and as the thing that makes "uncredited" explicit rather than absent.

**A dedup bug found while testing the above.** `fingerprint()` was
`name|size|mtime` with no folder in it. Two photographers' identically-named
frames of equal size, written in the same second, produced the same
fingerprint — and the second was silently skipped as already-processed. The
frame simply never appeared, no warning. Reproduced with two solid test frames;
the fingerprint now includes the containing folder. Idempotence is preserved
because the round-trip restores the same folder name and `cp -p` keeps mtime.

`backfill_credits.py --reorganize` migrates a flat archive in one step, and is
idempotent. `tidy_dump.py` and `analyze_run.py` now walk the subfolders — a
one-level scan would have reported a fully credited roll as empty.

**The credit is settled at 3.2%** Bebas Neue, bottom-left, adaptive colour —
confirmed legible at a glance on a phone, on real exports, by eye.

---

## Unreleased — 2 August 2026 (h)

**The photo credit was not faint. It was not being drawn.**

Three rounds of size and opacity tuning were spent on a mark that a re-render
had stopped rendering entirely. Reported as "still not visible", then as "there
aren't any credits in anything in that folder" — the second report is the one
that identified it.

The credit arrives in the dump folder name, `..._by_donalrey`. That name is
consumed by the merge: two photographers' folders become one roll,
`2026-08-01_borderlands`, and no path anywhere still carries a handle.
`retest_roll.sh` re-renders by copying `_originals/<roll>/` back into
`_dump/<roll>/` — a folder name with no `_by_` in it. The renderer read no
credit, which is a legitimate state, so it said nothing and marked nothing.

Credit is now stored per frame in `_originals/<roll>/_credits.json`, written as
each frame is archived and merged on write so a photographer's folder arriving
days later cannot erase an earlier one's entries. Per frame, the folder name
still wins where present; the ledger is the fallback. Verified end to end
against a simulated two-photographer merge followed by an archive round-trip:
credits survive, and the same round-trip on the old code produces a credit
corner measuring exactly zero.

`backfill_credits.py` recovers the ledger for rolls processed before this,
from their own manifests (including the copies `retest_roll.sh` sets aside as
`<roll>_pre-curator_*`) and from qualified plan filenames, which still contain
the handle. It reports what it could not attribute rather than guessing.

Two lessons: a value carried as metadata on a container does not survive the
container being merged away — write it down beside what it describes. And
before tuning a treatment that looks wrong, confirm it is being applied.

---

## Unreleased — 2 August 2026 (g)

**HOLD's text heuristic matched words, not locations.**

First real ride roll held 7 of 66 frames. Six were the judgment layer working
exactly as intended — the Windsor Festival poster, "Smith's Deli & Mart", the
STATE marquee, "Seasonal Limited Use Highway", "Country Cream". The seventh
was the measurement heuristic firing on a jersey that read
**"ALL ROAD APPAREL"**, because `road` was in a flat list of signage words.

That is the same failure that once held 6 of 7 frames on cap embroidery,
surviving in a narrower form. Locational text is a *construction*, not a
vocabulary: "Platte Clove Rd" is a street sign, "All Road Apparel" is a brand,
and the difference is what surrounds the word. The heuristic now requires

- a name followed by a road type that **ends** the phrase or is followed by a
  direction or number — `PLATTE CLOVE RD`, `Old Mill Rd N`
- a route, exit or mile marker with a number — `Route 28`, `EXIT 19`
- place-boundary phrasing — `Welcome to …`, `Town of …`

19 cases pass, including every real string from this roll.

Deliberately unchanged: **HOLD remains a union.** The fix makes the heuristic
more accurate rather than letting the model overrule it. A crude proxy that
fires on brand text should be sharpened, not silenced.

---

## Unreleased — 2 August 2026 (f)

**Credit sized against the real viewing condition. Third attempt.**

1.45%, then 1.8%, both judged at 1:1 or zoomed in, where they looked fine.
They were not fine. A 1080px feed post renders at roughly 390pt on a phone — a
0.36x downscale — and at that size both were illegible. Now **3.2%**, chosen by
rendering candidates onto real exports and viewing them downscaled to phone
width.

`SIZE_MAX` also raised from 34 to 80. At 34 the clamp silently capped the
ratio: a 3.2% and a 4.0% test rendered identically and I nearly drew a
conclusion from the comparison.

The rule that should have been applied from the start: **evaluate an image
treatment at the size it will be seen, not at the size it is convenient to
inspect.**

---

## Unreleased — 2 August 2026 (e)

**Collision-safety no longer duplicates the archive on every re-test.**

Introduced two commits earlier and caught on the first real use: `retest_roll.sh`
copies originals from `_originals` into `_dump`, and the renderer moves them
back — so the "never overwrite an original" guard suffixed all 66 frames as
`__2` and doubled the archive.

The guard now compares content first (size, then a hash of the head and tail —
enough to distinguish "same photograph coming back round" from "two
photographers used the same filename" without reading hundreds of megabytes).
Identical files replace silently; genuinely different ones still suffix, with a
warning that now says *a different* file is already archived.

Cleaning up the `__2` copies from the 2 Aug re-test is safe — they are
byte-identical duplicates.

---

## Unreleased — 2 August 2026 (d)

**The credit was invisible on real photographs. Retuned against them.**

Everything before this was validated on synthetic gradients, which have almost
no high-frequency detail. A real frame of gravel, foliage and clothing has it
everywhere, and Montserrat Regular at ~20px vanished into it completely. Worse,
the halo added to compensate turned the thin strokes to mush.

Changed after testing four treatments on actual Borderlands exports:

- **Face is now Bebas Neue, not Montserrat.** The original reasoning — Bebas is
  a display face built to shout, so a credit shouldn't use it — was sound in
  the abstract and wrong in practice. Bebas is condensed and heavier, so it
  holds shape at this size against a busy ground. Its lack of lowercase means
  handles set as caps, which is not ideal; an unreadable credit in the right
  case credits nobody.
- **1.45% → 1.8%** of the long edge.
- **Opacity 82/78% → 97/95%.** Against photographic detail, transparency reads
  as smudge rather than restraint. Restraint comes from size and placement.
- **Halo radius tightened** (0.16 → 0.14em) and strengthened to 85%. A wide
  soft halo blurs the very strokes it is meant to separate.
- A **scrim plate** behind the text was tried and rejected: the visible
  rectangle is more intrusive than the halo it replaces.

Measured on five real exports: 3.7–11.1:1 contrast, all clear of 3:1.

**Only one cycle runs at a time now.**

The launchd timer fires every fifteen minutes and does not care that you are
running a cycle by hand. On 2 August the two overlapped: one emptied a dump
folder and `rmdir`'d it, and the other reported that roll as failed mid-run.
The outcome was benign — all 66 frames landed — but both processes derive the
next sequence number from the same state file, so a collision could have
overwritten exports. `darkroom_cycle.sh` now takes an atomic `mkdir` lock,
detects and clears a stale one by PID, and releases on exit or interrupt.
`flock` is not available on macOS.

---

## Unreleased — 2 August 2026 (c)

`analyze_run.py` now loads **every** `_curation*.json` in a roll, not just the
first. A merged roll carries one plan per source folder, so reading only
`_curation.json` reported every frame from the second and third photographers
as uncurated — exactly backwards, and it would have sent someone hunting for a
curation failure that never happened.


**A roll can grow after it has already been rendered.**

Merged rolls made this a normal case rather than an edge case: a second or
third photographer's folder for the same ride can arrive days after the first
was processed. Everything about the render path already handled it — sequence
numbering continues from the shared state, fingerprints keep it idempotent,
credits attach per frame — but three things did not:

- **The contact sheet was never rebuilt.** `darkroom_sheet.py --all` built
  only for rolls *missing* a sheet, so late frames rendered into the roll and
  never appeared on the one artefact the cull is actually done from. It now
  rebuilds when `manifest.csv` is newer than `contact_sheet.png`, and stays
  idempotent when nothing changed.
- **The manifest lost its earlier rows on a column change.** A header mismatch
  renamed the old file aside and started fresh, orphaning the first
  photographers' rows in a sidecar nobody reads. It now migrates: every
  existing row is kept, new columns fill blank, and the previous file is saved
  as `manifest.pre-migration.csv`.
- **`HOLD_REVIEW.txt` stacked duplicates** on repeated `--force` runs. Entries
  already present are skipped.

`darkroom_sheet.py` also skips `xArchive` now, matching `NOT_A_ROLL` in the
renderer and curator — it was previously willing to build a contact sheet for
the bin.

---

## Unreleased — 2 August 2026 (b)

**Photo credit, from the folder name.**

`<date>_<label>_by_<handle>` marks every export from that folder with
`@handle`. Omitting `_by_` is how a roll goes uncredited — per-folder opt-in
with no config file to drift out of sync.

`_by_<handle>` is stripped from the roll label, which **merges** two
photographers' folders of the same ride into one output roll: continuous
sequence numbering, per-frame credit, one contact sheet. Merging exposed three
places where the second folder would have quietly overwritten the first:

- **the curation plan** — `_curation.json` was copied to the output folder
  unconditionally. The second plan now lands as
  `_curation_<source-folder>.json` rather than replacing the first.
- **`HOLD_REVIEW.txt`** — was rewritten per source folder, so the first
  folder's held frames disappeared from the notice. Now appends. A held frame
  missing from the notice is a held frame nobody reviews.
- **`_originals/`** — two folders can contain the same filename. Now suffixed
  `__2` with a warning rather than overwriting an original.

Manifest gains a `credit` column.

The mark: Montserrat Regular, lowercase, bottom-left, 1.45% of the long edge
(clamped 11–30px). Ink or Oat chosen per frame by sampling luminance under the
text — a fixed colour disappears against skies. A blurred halo rather than an
offset drop shadow, which at this size overlaps the letterform and reads
embossed; it strengthens when the ground measures busy (stddev > 42), the
dappled-light case where part of the text lands on highlight and part on
shadow. Measured contrast 5.3:1 on blown highlight, 7.2:1 on deep shadow,
3.9:1 on dappled — all clear of the 3:1 bar for a findable byline. Stories get
a 15.5% bottom inset to clear Instagram's UI. Originals are never marked.

---

## Unreleased — 2 August 2026

**Drive listing failures no longer abort the cycle.**

First real roll (67 frames across two folders, ~250 MB) failed to process.
Google Drive's filesystem refused the directory listing while still
materialising the files:

```
OSError: [Errno 11] Resource deadlock avoided: .../_dump/2026-08-01_Borderlands_Don
```

Drive was reporting nonsense metadata for those folders at the time — link
count 65535, directory size 2 MB — and `find` walked them without complaint
once sync settled. So the condition is transient, but two things made it far
worse than it needed to be:

- **No retry.** A listing that would have succeeded seconds later killed the
  attempt outright. `listdir_retry()` now retries four times with exponential
  backoff (2s, 4s, 8s) in both `darkroom.py` and `darkroom_curate.py`.
- **No per-roll isolation.** `main()` ran `sum(process_roll(d) for d in dirs)`,
  so the first roll's OSError killed the generator and the second roll was
  never attempted — even though it was perfectly readable. Each roll is now
  wrapped individually; a failed one is logged, left in `_dump` for the next
  cycle, and the others still render.

The renderer's non-destructive contract means a deferred roll loses nothing:
sources stay in `_dump` and are retried on the next 15-minute cycle.

---

## Unreleased — 31 July 2026

**`xArchive` is now recognised as a bin, not a roll.**

The convention on Ada is that `xArchive` folders sit inside `Photos/`,
`_originals/` and potentially `_dump/`, holding test material staged for real
deletion later. Nothing in the pipeline knew that, and two things followed:

- `darkroom.py` and `darkroom_curate.py` treat every non-dotted directory in
  `_dump` as a roll. Dropping test images into `_dump/xArchive/` to bin them
  would have had the next cycle process them straight back out — resurrecting
  work that was deliberately thrown away.
- `analyze_run.py` with no argument picks the newest directory under
  `_originals`, which was `xArchive` itself.

Both now skip it, via a shared `NOT_A_ROLL` set compared case-insensitively
(`darkroom.py`, `darkroom_curate.py`, `analyze_run.py`) and a matching
lowercased `case` guard in `darkroom_cycle.sh`.

Deliberately *not* changed: `darkroom_learn.py` still reads `decisions.txt`
only from live rolls under `Photos/`. Since binned rolls are discarded test
material, their decisions must never reach the calibration data.

---

## v4 package — 31 July 2026

The first package that actually deploys the v3 curator. Deployed to Ada and
verified end-to-end on the launchd timer the same day.

**Packaging**

- Merged the v3.8 curator bundle with the still-current v2-era utilities
  (`darkroom_intake.py`, `darkroom_learn.py`, fonts) into one installable
  package. Before this, the curator existed only in a loose `curator_bundle/`
  that no deploy path referenced.
- **launchd now runs `darkroom_cycle.sh`, not `darkroom.py`.** This is the
  most important change in the release. The renderer alone finds no curation
  plan and silently falls back to v2 orientation rules — output that looks
  plausible and is wrong.
- plists ship as templates with a `__HOME__` placeholder, substituted from
  `$HOME` and `plutil`-linted *before* installing. Removes the two classic
  failure modes: a malformed hand-edited plist, and the wrong username. (The old plists hardcoded a username that did not match the
  machine they were installed on.)

**New scripts**

- `install_darkroom.sh` — fresh install; backs up anything it replaces;
  asserts installed scripts are writable.
- `set_drive_account.sh` — rewrites the Drive account across all **nine**
  files that embed it. The previous runbook listed only four.
- `install_launchd.sh` — renders, lints, loads and verifies the agents.

**Fixed**

- `analyze_run.py` rewritten for v3. It previously narrated v2
  orientation-bucket logic — asserting "content is NOT consulted", which is
  false in v3 — and crashed on `_curation.json` because it tried to open the
  plan as an image. It now reads the plan and reconstructs the real chain:
  verdict → subject box → coverage floor → per-format geometry veto → tone →
  HOLD → what was actually written, cross-checked against `manifest.csv`, with
  `!!` on any variant whose render mode differs from the plan. Falls back to
  the v2 narration, clearly labelled, when a roll genuinely has no plan.
- **Perl array interpolation silently corrupted every Drive path.**
  `perl -pi -e` with an account name containing `@` wrote
  `GoogleDrive-newaccount\.com` into all nine files — no error, just a broken
  install. Rewritten in Python with exact string replacement.
- **Scripts installed from a read-only archive landed at mode 555**, so the
  path-rewriter could not open them for writing. Invisible when testing as
  root. Now `chmod 755` explicitly, with a writability assertion.

**Documented**

- **Full Disk Access is required for launchd**, separately from Terminal.
  Add `/bin/bash` and `/usr/bin/python3` in System Settings → Privacy &
  Security. On the first deploy the identical cycle script ran perfectly by
  hand and crashed with `PermissionError` on all 386 launchd attempts. This
  recurs on any rebuild or macOS major upgrade.

---

## v3 curator — 27 July 2026

Added the judgment the v2 pipeline never had. Shipped as
`darkroom_curator_v3.1` … `v3.8.zip`; `v3.8` is the version carried into v4.

| | v2 | v3 |
|---|---|---|
| format choice | orientation bucket — landscape gets 9, portrait 6 | per photograph, from what is actually in it |
| crop | busiest-texture window | centred on the detected subject |
| bars | any crop over 45% loss silently matted | only when a format is genuinely chosen for it |
| colour | duotone always | colour or duotone, decided per frame |
| quality | measured, then ignored | keep / review / reject, acted on |
| route safety | manual, by eye | flagged automatically, exports quarantined |

**Measurement** — macOS Vision, local and free: saliency, person and face
detection, legible text, scene classification, plus exposure/sharpness/
saturation. Always runs; degrades to a Pillow-only fallback.

**Judgment** — an LLM sees the photograph *alongside* those measurements.
Optional, cached, costs tokens.

**The geometry veto** — the model proposes, the geometry code disposes. A
confident but wrong format suggestion cannot produce a butchered crop.

**HOLD as a union** — either layer can raise it; only a human can clear it.

### Bugs found and fixed during the v3 build

- **HOLD fired on any legible text**, so cap embroidery and bike branding held
  6 of 7 frames. A flag that fires on everything is one nobody reads. Narrowed
  to genuinely locational text.
- **Vision reports a person and their face as separate detections**, so one
  rider counted as two — and that wrong number was handed to the model as
  fact. Now takes the larger of the two lists rather than the sum.
- **The contact sheet broke** when frames were held: everything landed in
  `_hold/web/` and the builder only looked in `web/`.
- **The curation plan was deleted** with the emptied dump folder, silently
  breaking the documented "edit it and re-run" override. Now copied to both
  the output folder and `_originals/`.
- **The ingest size floor stranded small images forever.** `MIN_BYTES` was
  200,000, so a legitimate 168 KB image reported "still syncing" on every
  cycle in perpetuity. Now 20,000, and a file that will never qualify says so.
- **The re-test script staged a stale plan** back into the dump, making the
  cycle treat a roll as already curated and skip judging it.

### Established by testing

- **Tone needed the disagreement rule to work at all.** Asking the model to
  choose colour or duotone produced colour on 7/7 frames across two attempts.
  Prose tuning did not fix it. Passing the measurement layer's opinion as
  evidence and rendering **both** when the layers disagree did.
- **The LLM's calls vary between runs on identical input** — the same frame
  came back `hold: false` then `hold: true`. Inherent to the judgment layer;
  the cost of what v2's byte-identical determinism bought. Mitigated by
  caching each roll's judgment.
- **v2's advisory triage was dead code.** The `soft` gate shipped at 0.8 while
  the softest real frame measured 4.42. Every frame read `ok` forever.

---

## v2 — July 2026

Original film-scan pipeline. Fixed Ink/Oat tone LUT, orientation-bucket format
selection, edge-energy crop search, byte-identical every run. Preserved under
`legacy/v2/`.

Pointed at colour phone photographs rather than film scans, which is the root
of several design tensions v3 had to resolve — most obviously the tone
treatment, and the black bars that started the whole investigation (every test
frame was shot 16:9; cropping to Instagram's 4:5 discards 55%, tripping v2's
45% ceiling).

### Misleading artefacts from this era

- **`CCC_Darkroom_v3.zip`** contains **v2 code** under a v3 name.
  `darkroom.py` 20,624 bytes, no `darkroom_curate.py`.
- **`CCC_Darkroom_v4.zip`**, referenced by the original `START_HERE.md`, did
  not exist anywhere until the 31 July package was built.
