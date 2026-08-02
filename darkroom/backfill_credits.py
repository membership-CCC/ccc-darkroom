#!/usr/bin/env python3
"""
CCC Darkroom — rebuild a roll's _credits.json from what the roll already knows.

Why this exists
---------------
Photo credit arrives in the dump folder name (`..._by_donalrey`) and that name
is consumed by the merge: two photographers' folders become one roll, and no
path anywhere still carries a handle. Anything that re-renders from the
_originals archive — retest_roll.sh does exactly this — therefore produced
uncredited exports, silently. darkroom.py now writes `_originals/<roll>/
_credits.json` as it archives, so new work is safe. This backfills rolls
processed before that existed.

It never guesses. Every credit written here is one this roll recorded at the
time, recovered from one of three places, most trustworthy first:

  1. manifest.csv `credit` column, in the live output folder and in any
     `<roll>_pre-curator_*` folder set aside by retest_roll.sh
  2. `manifest.pre-migration.csv`, saved when the manifest gained new columns
  3. qualified plan files — `_curation_<dump folder name>.json` — where the
     dump folder name still contains `_by_<handle>` and the plan's keys are
     that photographer's filenames

Frames it cannot attribute are listed, not filled in.

    python3 backfill_credits.py 2026-08-01_borderlands
    python3 backfill_credits.py 2026-08-01_borderlands --write
    python3 backfill_credits.py --all
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

HOME = Path.home()
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
ORIGINALS = DRIVE / "_originals"
CREDITS_NAME = "_credits.json"
SOURCE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}

BY_RE = re.compile(r"^(.*)_by_(.+)$", re.IGNORECASE)


def handle_from_folder(name: str) -> str | None:
    m = BY_RE.match(name.strip())
    if not m:
        return None
    h = m.group(2).strip()
    return "@" + h.lstrip("@").lower() if h else None


def from_manifests(roll: str) -> tuple[dict, list[str]]:
    """Every source->credit pair any manifest for this roll still remembers."""
    found: dict[str, str] = {}
    read: list[str] = []
    candidates: list[Path] = []
    live = DRIVE / roll
    for d in sorted(DRIVE.glob(f"{roll}_pre-curator_*")) + [live]:
        candidates.append(d / "manifest.csv")
        candidates.append(d / "manifest.pre-migration.csv")
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            print(f"   !! could not read {path.name} in {path.parent.name}: {exc}")
            continue
        n = 0
        for row in rows:
            src = (row.get("source") or row.get("original") or "").strip()
            cred = (row.get("credit") or "").strip()
            if src and cred:
                found.setdefault(src, cred)
                n += 1
        read.append(f"{path.parent.name}/{path.name}: {n} credited row(s)")
    return found, read


def from_plans(roll: str) -> tuple[dict, list[str]]:
    """Qualified plan filenames still carry the dump folder, hence the handle.

    `_curation_2026-08-01_borderlands_by_donalrey.json` names its photographer;
    its keys are that photographer's frames. The unqualified `_curation.json`
    does not name anyone and is deliberately ignored.
    """
    found: dict[str, str] = {}
    read: list[str] = []
    orig = ORIGINALS / roll
    if not orig.is_dir():
        return found, read
    for path in sorted(orig.glob("_curation_*.json")):
        handle = handle_from_folder(path.stem[len("_curation_"):])
        if not handle:
            continue
        try:
            plan = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            print(f"   !! could not read {path.name}: {exc}")
            continue
        frames = plan.get("frames") if isinstance(plan, dict) else None
        keys = frames if isinstance(frames, dict) else (plan if isinstance(plan, dict) else {})
        n = 0
        for k in keys:
            if Path(str(k)).suffix.lower() in SOURCE_EXTS:
                found.setdefault(str(k), handle)
                n += 1
        read.append(f"{path.name}: {n} frame(s) -> {handle}")
    return found, read


def backfill(roll: str, write: bool) -> int:
    orig = ORIGINALS / roll
    if not orig.is_dir():
        print(f"!! no originals at {orig}")
        return 1

    archived = sorted(p.name for p in orig.iterdir()
                      if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                      and not p.name.startswith("."))
    print(f"\n== {roll} ==")
    print(f"   {len(archived)} archived frame(s)")

    credits, notes = from_manifests(roll)
    plan_credits, plan_notes = from_plans(roll)
    for k, v in plan_credits.items():
        credits.setdefault(k, v)
    for line in notes + plan_notes:
        print(f"   {line}")

    # Only claim frames that are actually in the archive.
    mapped = {name: credits[name] for name in archived if name in credits}
    missing = [n for n in archived if n not in mapped]

    existing = {}
    path = orig / CREDITS_NAME
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError):
            existing = {}
        print(f"   existing {CREDITS_NAME}: {len(existing)} entry(ies) — kept, not overwritten")

    merged = dict(mapped)
    merged.update(existing)          # anything already recorded wins

    by_handle: dict[str, int] = {}
    for h in merged.values():
        by_handle[h] = by_handle.get(h, 0) + 1
    print("   attributed:")
    for h, n in sorted(by_handle.items(), key=lambda kv: -kv[1]):
        print(f"      {h:<28} {n} frame(s)")
    if missing:
        print(f"   !! {len(missing)} frame(s) could not be attributed:")
        for n in missing[:12]:
            print(f"      {n}")
        if len(missing) > 12:
            print(f"      ... and {len(missing) - 12} more")
        print("      Add them by hand to _credits.json, or leave them uncredited.")

    if not write:
        print(f"\n   dry run — nothing written. Re-run with --write to save {CREDITS_NAME}.")
        return 0
    if not merged:
        print("\n   nothing to write.")
        return 1
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    print(f"\n   wrote {path}")
    print("   Re-render with:  retest_roll.sh " + roll)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roll", nargs="?", help="roll folder name under _originals")
    ap.add_argument("--all", action="store_true", help="every roll in _originals")
    ap.add_argument("--write", action="store_true", help="actually save the file")
    args = ap.parse_args()

    if not ORIGINALS.is_dir():
        print(f"!! no _originals at {ORIGINALS} — is Drive for Desktop running?")
        return 1

    if args.all:
        rolls = sorted(p.name for p in ORIGINALS.iterdir()
                       if p.is_dir() and not p.name.startswith("."))
    elif args.roll:
        rolls = [args.roll]
    else:
        print("usage: backfill_credits.py <roll> [--write]   |   --all [--write]")
        print("\nrolls under _originals:")
        for p in sorted(ORIGINALS.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                print("   " + p.name)
        return 1

    rc = 0
    for roll in rolls:
        rc |= backfill(roll, args.write)
    return rc


if __name__ == "__main__":
    sys.exit(main())
