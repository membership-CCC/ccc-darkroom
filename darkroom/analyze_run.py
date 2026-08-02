#!/usr/bin/env python3
"""
CCC Darkroom — run analyzer (v3).

Reconstructs, per source frame, exactly what the pipeline decided and why.

For a CURATED roll (the normal v3 case) it reads `_curation.json` and narrates
the real decision chain:

    measurement + judgment -> verdict -> subject box -> coverage floor
      -> per-format geometry veto -> tone -> HOLD -> what was actually written

For an UNCURATED roll (no plan present — the v2 fallback the renderer still
uses) it narrates the old orientation-bucket path instead, and says so.

Either way it cross-checks the reconstruction against `manifest.csv` and flags
any format whose actual render mode differs from what the plan called for.

Read-only. Touches nothing.

    python3 analyze_run.py                        # newest roll
    python3 analyze_run.py 2026-07-31_test
    python3 analyze_run.py 2026-07-31_test --plan # dump the raw plan entry too
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow required: python3 -m pip install --user Pillow")

HOME = Path.home()
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
ORIGINALS = DRIVE / "_originals"

PLAN_NAME = "_curation.json"
HOLD_DIRNAME = "_hold"
SOURCE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# Directories that are never rolls. `xArchive` is the convention for a bin of
# test material staged for real deletion later — processing it would resurrect
# work that was deliberately thrown away. Compared case-insensitively.
NOT_A_ROLL = {"xarchive"}


def is_roll_dir(p) -> bool:
    return p.is_dir() and not p.name.startswith(".") and p.name.lower() not in NOT_A_ROLL


# Mirrored from darkroom.py so the numbers are the real ones.
PRESETS = {
    "ig_portrait":  dict(w=1080, h=1350, mode="crop", dir="instagram"),
    "ig_square":    dict(w=1080, h=1080, mode="crop", dir="instagram"),
    "ig_landscape": dict(w=1080, h=566,  mode="crop", dir="instagram"),
    "ig_story":     dict(w=1080, h=1920, mode="mat",  dir="instagram"),
    "strava":       dict(w=1200, h=None, mode="fit",  dir="strava"),
    "email_body":   dict(w=1200, h=None, mode="fit",  dir="email"),
    "email_header": dict(w=1200, h=600,  mode="crop", dir="email"),
    "web":          dict(w=1600, h=None, mode="fit",  dir="web"),
    "web_hero":     dict(w=2400, h=1000, mode="crop", dir="web"),
}
BY_ORIENTATION = {
    "landscape": ["ig_landscape", "ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "email_header", "web", "web_hero"],
    "portrait":  ["ig_portrait", "ig_story", "ig_square",
                  "strava", "email_body", "web"],
    "square":    ["ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "web"],
}
MAT_IF_CROP_EXCEEDS = 0.45

# Mirrored from darkroom_curate.py — the geometry veto's sliding floor.
MIN_SUBJECT_COVERAGE = 0.92
MIN_SUBJECT_COVERAGE_WIDE = 0.55
SUBJECT_AREA_TIGHT = 0.40
SUBJECT_AREA_SPRAWL = 0.85
NEAR_MISS_SLACK = 0.15


def coverage_floor(area_frac: float) -> float:
    """Required subject coverage, eased by how much of the frame it occupies."""
    if area_frac <= SUBJECT_AREA_TIGHT:
        return MIN_SUBJECT_COVERAGE
    if area_frac >= SUBJECT_AREA_SPRAWL:
        return MIN_SUBJECT_COVERAGE_WIDE
    t = (area_frac - SUBJECT_AREA_TIGHT) / (SUBJECT_AREA_SPRAWL - SUBJECT_AREA_TIGHT)
    return MIN_SUBJECT_COVERAGE + t * (MIN_SUBJECT_COVERAGE_WIDE - MIN_SUBJECT_COVERAGE)


def crop_loss(w: int, h: int, tw: int, th: int) -> float:
    tr, r = tw / th, w / h
    if abs(r - tr) < 1e-6:
        return 0.0
    if r > tr:
        return 1.0 - (round(h * tr) / w)
    return 1.0 - (round(w / tr) / h)


def orientation_of(w: int, h: int) -> str:
    r = w / h
    if r > 1.08:
        return "landscape"
    if r < 0.93:
        return "portrait"
    return "square"


def pick_roll(arg: str | None) -> Path:
    if arg:
        p = ORIGINALS / arg
        if not p.is_dir():
            avail = sorted(d.name for d in ORIGINALS.iterdir()
                           if is_roll_dir(d)) if ORIGINALS.is_dir() else []
            msg = f"no such roll under _originals: {arg}"
            if avail:
                msg += "\navailable: " + ", ".join(avail)
            sys.exit(msg)
        return p
    rolls = [d for d in ORIGINALS.iterdir() if is_roll_dir(d)] if ORIGINALS.is_dir() else []
    if not rolls:
        sys.exit(f"no rolls found under {ORIGINALS}")
    return max(rolls, key=lambda d: d.stat().st_mtime)


def load_plan(roll: Path, out_root: Path) -> tuple[dict, dict, list[Path]]:
    """Returns (meta, frames, paths).

    A merged roll is fed by more than one dump folder, so it carries more than
    one plan: `_curation.json` plus `_curation_<source-folder>.json` for each
    additional photographer. Loading only the first would report every frame
    from the second and third folders as uncurated, which is exactly backwards.

    darkroom.py copies plans to both the originals archive and the output
    folder; prefer whichever directory actually has them.
    """
    for d in (roll, out_root):
        found = sorted(d.glob("_curation*.json")) if d.is_dir() else []
        if not found:
            continue
        meta, frames, used = {}, {}, []
        for candidate in found:
            try:
                plan = json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                print(f"!! plan at {candidate.name} unreadable ({exc})")
                continue
            if not meta:
                meta = {k: v for k, v in plan.items() if k != "frames"}
            frames.update(plan.get("frames") or {})
            used.append(candidate)
        if frames:
            return meta, frames, used
    return {}, {}, []


def crop_form(crop) -> tuple[str, str]:
    """Map a plan crop value to (predicted render mode, human description)."""
    if crop is None:
        return "fit", "no ratio constraint — fit by long edge, nothing discarded"
    if crop == "mat":
        return "mat", "whole frame on an Ink field, deliberately (bars)"
    if isinstance(crop, (list, tuple)) and len(crop) == 4:
        x0, y0, x1, y1 = crop
        return "crop", (f"window [{x0:.3f} {y0:.3f} {x1:.3f} {y1:.3f}] "
                        f"= {(x1 - x0):.0%} x {(y1 - y0):.0%} of frame, "
                        f"centred on the subject")
    return "?", f"unrecognised crop value {crop!r}"


def parse_variants(row: dict) -> dict[str, str]:
    """manifest 'variants' is space-separated 'name:mode' tokens."""
    actual = {}
    for tok in (row.get("variants") or "").split():
        if ":" in tok:
            n, m = tok.split(":", 1)
            actual[n] = m
    return actual


def report_curated(entry: dict, actual: dict) -> None:
    print(f"        curated by: {entry.get('decided_by', '?')}")

    verdict = entry.get("verdict", "?")
    vnote = entry.get("verdict_note", "")
    print(f"        verdict:    {verdict}" + (f" — {vnote}" if vnote else ""))

    if entry.get("description"):
        print(f"        sees:       {entry['description']}")
    if entry.get("caption_hint"):
        print(f"        caption:    {entry['caption_hint']}")

    subj = entry.get("subject_box")
    if subj and len(subj) == 4:
        area = (subj[2] - subj[0]) * (subj[3] - subj[1])
        floor = coverage_floor(area)
        print(f"        subject:    box [{subj[0]:.3f} {subj[1]:.3f} "
              f"{subj[2]:.3f} {subj[3]:.3f}]  = {area:.0%} of frame "
              f"({entry.get('subject_shape', '?')}, "
              f"{entry.get('people_detected', '?')} person/people)")
        print(f"        floor:      {floor:.0%} of the subject must survive a crop "
              f"(slides 92%→55% as the subject grows)")

    tone = entry.get("tone", "?")
    extra = "  — each format written twice, second with _mono" if tone == "both" else ""
    print(f"        tone:       {tone}{extra}")
    if entry.get("tone_reason"):
        print(f"                    {entry['tone_reason']}")

    if entry.get("hold"):
        print(f"        HOLD:       YES — {entry.get('hold_reason') or 'no reason given'}")
        print(f"                    exports quarantined to {HOLD_DIRNAME}/, "
              f"not the publish folders")
    else:
        print("        HOLD:       no")

    formats = entry.get("formats") or {}
    rejected = entry.get("formats_rejected") or {}

    print(f"        {len(formats)} format(s) chosen for THIS photograph "
          f"(content decides, not orientation):")
    for pname in sorted(formats, key=lambda n: (n not in PRESETS, n)):
        spec = PRESETS.get(pname)
        if spec is None:
            print(f"          {pname:<17} {'?':>9}  — not a known preset")
            continue
        info = formats[pname] or {}
        predicted, why = crop_form(info.get("crop"))
        cov = info.get("subject_coverage")
        if cov is not None:
            why += f"; keeps {cov:.0%} of the subject"
        got = actual.get(pname, "—")
        mark = " " if got in (predicted, "—") else " !!"
        dims = f"{spec['w']}x{spec['h']}" if spec["h"] else f"{spec['w']}w"
        print(f"          {pname:<17} {dims:>9}  {got:<5}{mark}  {why}")
        if tone == "both":
            gm = actual.get(f"{pname}_mono", "—")
            mm = " " if gm in (predicted, "—") else " !!"
            mono_name = pname + "_mono"
            print(f"          {mono_name:<17} {dims:>9}  {gm:<5}{mm}  "
                  f"duotone twin (tone=both)")

    real_rejects = {k: v for k, v in rejected.items() if k != "_note"}
    if real_rejects:
        print(f"        {len(real_rejects)} format(s) vetoed:")
        for pname, why in sorted(real_rejects.items()):
            print(f"          {pname:<17} {why}")
    if rejected.get("_note"):
        print(f"        note:       {rejected['_note']}")

    plain = {k for k in actual if not k.endswith("_mono")}
    unexpected = plain - set(formats)
    if unexpected:
        print(f"        !! variants present but not in the plan: {sorted(unexpected)}")
    missing = set(formats) - plain
    if missing and actual:
        print(f"        !! planned but not written (render failure?): {sorted(missing)}")


def report_uncurated(actual: dict, w: int, h: int, orient: str) -> None:
    print("        NO CURATION PLAN — renderer fell back to v2 orientation rules.")
    print("        (This is the documented safety net, not necessarily a fault:")
    print("         it happens when curation failed or --ignore-curation was used.)")
    presets = BY_ORIENTATION[orient]
    print(f"        {len(presets)} presets selected by orientation bucket alone "
          f"(content is NOT consulted):")
    for pname in presets:
        spec = PRESETS[pname]
        tw, th = spec["w"], spec["h"]
        if spec["mode"] == "fit" or th is None:
            why, predicted = f"fit to {tw}px long edge, nothing discarded", "fit"
        elif spec["mode"] == "mat":
            why, predicted = "preset is ALWAYS matted, regardless of source", "mat"
        else:
            loss = crop_loss(w, h, tw, th)
            if loss > MAT_IF_CROP_EXCEEDS:
                predicted = "mat"
                why = (f"crop would discard {loss:.0%} > {MAT_IF_CROP_EXCEEDS:.0%} "
                       f"threshold -> MATTED (bars added)")
            else:
                predicted = "crop"
                why = f"crop discards {loss:.0%} (under {MAT_IF_CROP_EXCEEDS:.0%})"
        got = actual.get(pname, "—")
        mark = " " if got in (predicted, "—") else " !!"
        dims = f"{tw}x{th}" if th else f"{tw}w"
        print(f"          {pname:<17} {dims:>9}  {got:<5}{mark}  {why}")

    extra = set(actual) - set(presets)
    if extra:
        print(f"        unexpected extra variants: {sorted(extra)}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_plan = "--plan" in sys.argv[1:]

    roll = pick_roll(args[0] if args else None)
    key = roll.name
    out_root = DRIVE / key

    meta, frames, plan_paths = load_plan(roll, out_root)

    print("=" * 78)
    print(f"ROLL: {key}")
    print(f"originals: {roll}")
    print(f"outputs:   {out_root}")
    if plan_paths:
        print(f"plan:      {plan_paths[0]}")
        for extra in plan_paths[1:]:
            print(f"           + {extra.name}   (merged roll)")
        print(f"           backend={meta.get('backend', '?')}  "
              f"judgment={meta.get('judgment', '?')}  "
              f"model={meta.get('model') or 'n/a'}")
    else:
        print("plan:      NONE — this roll was rendered on the v2 fallback rules")
    print("=" * 78)

    # manifest, keyed by source filename
    man: dict[str, dict] = {}
    mpath = out_root / "manifest.csv"
    if mpath.exists():
        with mpath.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                man[row["source_file"]] = row
    else:
        print(f"!! no manifest at {mpath} — cross-check unavailable")

    # Only real image files. The plan itself lives here too and is not a frame.
    entries = [p for p in roll.iterdir()
               if p.is_file() and not p.name.startswith(".")
               and not p.name.startswith("_curation")]
    sources = sorted(p for p in entries if p.suffix.lower() in SOURCE_EXTS)
    skipped = sorted(p.name for p in entries
                     if p.suffix.lower() not in SOURCE_EXTS
                     and not p.name.startswith("_curation"))
    if skipped:
        print(f"(ignoring non-image files: {', '.join(skipped)})")

    if not sources:
        print("no source frames found in this roll")
        return 1

    held = 0
    for src in sources:
        row = man.get(src.name, {})
        entry = frames.get(src.name)

        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                w, h = im.size
                mode_in = im.mode
        except Exception as exc:                       # noqa: BLE001
            print(f"\n{src.name}: UNREADABLE ({exc})")
            continue

        orient = orientation_of(w, h)
        print()
        print("-" * 78)
        print(f"SOURCE  {src.name}")
        print(f"        {w} x {h} px   ratio {w / h:.3f}   colour mode {mode_in}"
              f"   -> orientation bucket: {orient}")
        if row:
            print(f"        -> {row.get('stem', '?')}   "
                  f"tech_flag={row.get('tech_flag', '?')} {row.get('tech_note', '')}"
                  f"   status={row.get('status', '?')}")
            print(f"        metrics: luma {row.get('mean_luma')}  var {row.get('variance')}"
                  f"  shadow {row.get('shadow_frac')}  highlight {row.get('highlight_frac')}"
                  f"  sharpness {row.get('sharpness')}")

        actual = parse_variants(row)

        if entry:
            report_curated(entry, actual)
            if entry.get("hold"):
                held += 1
            if show_plan:
                print("        --- raw plan entry ---")
                for line in json.dumps(entry, indent=2).splitlines():
                    print(f"        {line}")
        else:
            if frames:
                print("        !! this frame has no entry in the curation plan")
            report_uncurated(actual, w, h, orient)

    print()
    print("=" * 78)
    if frames:
        print(f"{len(frames)} frame(s) in plan · {held} held for route safety")
        print("Legend: 'crop' = window centred on the detected subject, admitted")
        print("only if enough of that subject survives (the geometry veto).")
        print("'fit'  = long-edge resize, nothing discarded.")
        print("'mat'  = whole frame on an Ink (#1A1812) field, i.e. bars —")
        print("         in v3 only when a format is genuinely chosen for it.")
        print("Formats are chosen per photograph. Tone is decided per frame;")
        print("'both' means the layers disagreed and each format was written")
        print("twice, the duotone copy suffixed _mono.")
        print("A '!!' marks a variant whose actual mode differs from the plan.")
    else:
        print("Legend: 'mat' = whole frame shrunk onto an Ink (#1A1812) field,")
        print("i.e. bars. 'crop' = exact ratio, window chosen by edge-energy only.")
        print("On this fallback path no preset choice considers subject or content.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
