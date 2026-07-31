#!/usr/bin/env python3
"""
CCC DARKROOM — dump tidier
==========================
Files dropped loose in `_dump/` are never processed: the folder name is what
supplies the roll label and date for every output filename, so a loose file
has no identity to inherit. This sorts them into dated roll folders.

Roll date is taken from, in order of preference:
    1. a date in the filename   (PXL_20260606_..., IMG_20171027_...)
    2. EXIF DateTimeOriginal
    3. the file's modification time

Files whose name already appears under `_originals/` are left alone and
reported — they have been through the pipeline once already, and moving them
back in would process them a second time under a new sequence number.

Dry run by default. Nothing moves until you pass --apply.

    python3 tidy_dump.py                    # show the plan
    python3 tidy_dump.py --apply            # do it
    python3 tidy_dump.py --label shakedown  # name the rolls
    python3 tidy_dump.py --apply --include-duplicates
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

HOME = Path.home()
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
INBOX = DRIVE / "_dump"
ORIGINALS = DRIVE / "_originals"
SOURCE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}

DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")


def date_from_name(name: str) -> str | None:
    m = DATE_RE.search(name)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def date_from_exif(p: Path) -> str | None:
    if Image is None:
        return None
    try:
        with Image.open(p) as im:
            exif = im.getexif()
            for tag in (36867, 306):          # DateTimeOriginal, DateTime
                v = exif.get(tag)
                if v:
                    return dt.datetime.strptime(
                        str(v)[:19], "%Y:%m:%d %H:%M:%S").date().isoformat()
    except Exception:
        pass
    return None


def roll_date(p: Path) -> tuple[str, str]:
    d = date_from_name(p.name)
    if d:
        return d, "filename"
    d = date_from_exif(p)
    if d:
        return d, "EXIF"
    return dt.date.fromtimestamp(p.stat().st_mtime).isoformat(), "file date"


def already_processed() -> set[str]:
    if not ORIGINALS.is_dir():
        return set()
    return {p.name for d in ORIGINALS.iterdir() if d.is_dir()
            for p in d.iterdir() if p.is_file()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Sort loose files in _dump into rolls")
    ap.add_argument("--apply", action="store_true", help="actually move files")
    ap.add_argument("--label", default="loose",
                    help="suffix for the roll folders (default: loose)")
    ap.add_argument("--include-duplicates", action="store_true",
                    help="also move files already present under _originals")
    a = ap.parse_args()

    if not INBOX.is_dir():
        sys.exit(f"no dump folder at {INBOX}")

    loose = sorted(p for p in INBOX.iterdir()
                   if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                   and not p.name.startswith("."))
    if not loose:
        print("Nothing loose in the dump root — already tidy.")
        return 0

    seen = already_processed()
    groups: dict[str, list[tuple[Path, str]]] = {}
    dupes: list[Path] = []

    for p in loose:
        if p.name in seen and not a.include_duplicates:
            dupes.append(p)
            continue
        d, how = roll_date(p)
        groups.setdefault(d, []).append((p, how))

    print(f"{len(loose)} loose file(s) in {INBOX}\n")
    for d in sorted(groups):
        folder = f"{d}_{a.label}"
        print(f"  {folder}/   ({len(groups[d])} frame(s))")
        for p, how in groups[d]:
            print(f"      {p.name}    [date from {how}]")
    if dupes:
        print(f"\n  SKIPPED — already processed once, under _originals/:")
        for p in dupes:
            print(f"      {p.name}")
        print("  Moving these back in would re-process them under a new sequence")
        print("  number. Pass --include-duplicates if that is what you want.")

    if not a.apply:
        print("\nDry run. Nothing moved. Re-run with --apply to do it.")
        return 0

    moved = 0
    for d, items in groups.items():
        dest = INBOX / f"{d}_{a.label}"
        dest.mkdir(parents=True, exist_ok=True)
        for p, _ in items:
            target = dest / p.name
            if target.exists():
                print(f"  !! {target.name} already in {dest.name} — left in place")
                continue
            shutil.move(str(p), str(target))
            moved += 1
    print(f"\nMoved {moved} file(s) into {len(groups)} roll folder(s).")
    print("They will be picked up on the next cycle (or run darkroom_cycle.sh now).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
