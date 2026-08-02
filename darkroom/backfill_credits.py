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

Once a roll has a ledger, --reorganize files its frames into
_originals/<roll>/by_<handle>/ — the layout darkroom.py writes now, and the one
retest_roll.sh stages back from. Existing rolls were archived flat; this is the
one-time migration.

    python3 backfill_credits.py 2026-08-01_borderlands --reorganize
    python3 backfill_credits.py 2026-08-01_borderlands --reorganize --write
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
CREDIT_DIR_PREFIX = "by_"


def credit_subdir(handle: str) -> str:
    return CREDIT_DIR_PREFIX + str(handle).lstrip("@").strip().lower()


def archived_frames(orig: Path) -> list:
    """Every source frame in a roll, roll root and by_<handle>/ alike."""
    out = [p for p in orig.iterdir()
           if p.is_file() and p.suffix.lower() in SOURCE_EXTS
           and not p.name.startswith(".")]
    for d in sorted(orig.iterdir()):
        if d.is_dir() and d.name.lower().startswith(CREDIT_DIR_PREFIX):
            out += [p for p in d.iterdir()
                    if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                    and not p.name.startswith(".")]
    return sorted(out, key=lambda p: p.name)


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

    archived = [p.name for p in archived_frames(orig)]
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


def reorganize(roll: str, write: bool) -> int:
    """Move a flat archive into by_<handle>/ subfolders, per the ledger.

    Moves only frames the ledger names. Anything uncredited stays in the roll
    root, which is where the renderer puts uncredited frames anyway. Refuses to
    overwrite: if the destination already holds a file of that name, the frame
    is left where it is and reported, because two files with one name is
    exactly the ambiguity this layout exists to prevent.
    """
    orig = ORIGINALS / roll
    ledger_path = orig / CREDITS_NAME
    if not orig.is_dir():
        print(f"!! no originals at {orig}")
        return 1
    try:
        ledger = json.loads(ledger_path.read_text())
    except (OSError, ValueError):
        print(f"!! no readable {CREDITS_NAME} in {orig}")
        print("   Run the backfill first, without --reorganize.")
        return 1

    by_base = {str(k).rsplit("/", 1)[-1]: str(v) for k, v in ledger.items() if v}
    loose = [p for p in orig.iterdir()
             if p.is_file() and p.suffix.lower() in SOURCE_EXTS
             and not p.name.startswith(".")]

    moves, blocked, uncredited = [], [], []
    for f in sorted(loose, key=lambda p: p.name):
        handle = by_base.get(f.name)
        if not handle:
            uncredited.append(f.name)
            continue
        dest = orig / credit_subdir(handle) / f.name
        if dest.exists():
            blocked.append(f.name)
            continue
        moves.append((f, dest, handle))

    print(f"\n== {roll} — reorganize ==")
    already = sum(1 for d in orig.iterdir()
                  if d.is_dir() and d.name.lower().startswith(CREDIT_DIR_PREFIX))
    if already:
        print(f"   {already} by_* folder(s) already present")
    counts: dict[str, int] = {}
    for _, _, h in moves:
        counts[h] = counts.get(h, 0) + 1
    if counts:
        print(f"   {len(moves)} frame(s) to move:")
        for h, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"      {credit_subdir(h) + '/':<32} {n}")
    else:
        print("   nothing loose to move")
    if uncredited:
        print(f"   {len(uncredited)} uncredited frame(s) stay in the roll root")
    if blocked:
        print(f"   !! {len(blocked)} blocked — a file of that name is already filed:")
        for n in blocked[:10]:
            print(f"      {n}")

    if not write:
        print("\n   dry run — nothing moved. Re-run with --write.")
        return 0

    done = 0
    for src, dest, _ in moves:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
            done += 1
        except OSError as exc:                         # noqa: BLE001
            print(f"   !! {src.name}: {exc}")

    # Re-key the ledger to match: "by_handle/name" is what darkroom.py writes.
    rekeyed = {}
    for k, v in ledger.items():
        base = str(k).rsplit("/", 1)[-1]
        sub = orig / credit_subdir(v) / base
        rekeyed[f"{credit_subdir(v)}/{base}" if sub.exists() else base] = v
    tmp = ledger_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rekeyed, indent=2, sort_keys=True) + "\n")
    tmp.replace(ledger_path)

    print(f"\n   moved {done} frame(s); {CREDITS_NAME} re-keyed")
    print("   Re-render with:  retest_roll.sh " + roll)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roll", nargs="?", help="roll folder name under _originals")
    ap.add_argument("--all", action="store_true", help="every roll in _originals")
    ap.add_argument("--write", action="store_true", help="actually save the file")
    ap.add_argument("--reorganize", action="store_true",
                    help="file frames into by_<handle>/ subfolders per the ledger")
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
        rc |= (reorganize(roll, args.write) if args.reorganize
               else backfill(roll, args.write))
    return rc


if __name__ == "__main__":
    sys.exit(main())
