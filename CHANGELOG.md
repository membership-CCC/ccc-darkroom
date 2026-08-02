# Changelog

Written mainly to stop the version naming from misleading anyone again. The
zips that circulated before this repo existed do not mean what their filenames
say.

**Tell v2 from v3 in one command:** `ls darkroom_curate.py`. If it is absent,
it is v2, whatever the filename claims. Secondary check: `darkroom.py` is
~20.6 KB in v2 and ~27.2 KB in v3.

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
