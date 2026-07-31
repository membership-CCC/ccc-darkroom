#!/usr/bin/env python3
"""
CCC DARKROOM — intake
=====================
Stages lab scans into the Darkroom dump folder, correctly named, so the
processor picks them up on its next cycle.

Exposure Therapy delivers scans via an online portal. Download the zip (or
the loose files) on Ada, then point this at it:

    python3 darkroom_intake.py ~/Downloads/scans.zip --roll 2026-08-01_borderlands
    python3 darkroom_intake.py --latest --roll 2026-08-01_borderlands
    python3 darkroom_intake.py ~/Downloads/some_folder --roll shakedown

What it handles
---------------
  * unzips, or copies a folder
  * flattens nested directory structures (portals nest unpredictably)
  * ignores __MACOSX, .DS_Store, thumbnails, and non-image files
  * refuses to clobber an existing roll unless you say so
  * reports frame count and size so you can check it against the order

It does not download from the portal. That stays a click — the portal
layout is not something to guess at, and a broken scraper that silently
fetches nothing is worse than a manual step.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

LOG = logging.getLogger("darkroom-intake")

HOME = Path.home()

# Must match DRIVE in darkroom.py
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
INBOX = DRIVE / "_dump"

DOWNLOADS = HOME / "Downloads"

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}
JUNK_DIRS = {"__MACOSX", ".Trashes", ".Spotlight-V100"}
JUNK_PREFIXES = (".", "._")

DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[_-].+$")
SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


# ===========================================================================

def is_image(p: Path) -> bool:
    if p.name.startswith(JUNK_PREFIXES):
        return False
    if any(part in JUNK_DIRS for part in p.parts):
        return False
    return p.suffix.lower() in IMAGE_EXTS


def collect_images(root: Path) -> list[Path]:
    """Every image anywhere under root, regardless of nesting."""
    return sorted(p for p in root.rglob("*") if p.is_file() and is_image(p))


def latest_download() -> Path | None:
    """Most recently modified zip in ~/Downloads."""
    if not DOWNLOADS.exists():
        return None
    zips = [p for p in DOWNLOADS.glob("*.zip") if p.is_file()]
    if not zips:
        return None
    return max(zips, key=lambda p: p.stat().st_mtime)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def safe_roll_name(name: str) -> str:
    cleaned = SLUG_RE.sub("-", name.strip()).strip("-_")
    return cleaned or "roll"


# ===========================================================================

def stage(source: Path, roll: str, inbox: Path, overwrite: bool,
          move: bool, dry: bool) -> int:
    if not source.exists():
        LOG.error("source not found: %s", source)
        return 2

    dest = inbox / roll
    if dest.exists() and any(dest.iterdir()) and not overwrite:
        LOG.error("%s already exists and is not empty", dest)
        LOG.error("use --overwrite to add to it, or pick a different --roll")
        return 2

    tmpdir: tempfile.TemporaryDirectory | None = None
    try:
        # ---- resolve the source into a directory of files
        if source.is_file() and source.suffix.lower() == ".zip":
            tmpdir = tempfile.TemporaryDirectory()
            work = Path(tmpdir.name)
            LOG.info("unzipping %s", source.name)
            try:
                with zipfile.ZipFile(source) as zf:
                    zf.extractall(work)
            except zipfile.BadZipFile:
                LOG.error("%s is not a readable zip", source.name)
                return 2
        elif source.is_dir():
            work = source
        elif source.is_file() and is_image(source):
            work = source.parent
        else:
            LOG.error("source must be a .zip, a folder, or an image file")
            return 2

        images = collect_images(work)
        if not images:
            LOG.error("no image files found in %s", source.name)
            LOG.error("expected one of: %s", ", ".join(sorted(IMAGE_EXTS)))
            return 2

        total = sum(p.stat().st_size for p in images)
        exts = sorted({p.suffix.lower() for p in images})
        LOG.info("found %d image(s), %s, types: %s",
                 len(images), human(total), " ".join(exts))

        if ".jpg" in exts or ".jpeg" in exts:
            LOG.warning("JPEGs present — if you ordered Flat Scans these should")
            LOG.warning("be TIFFs. Check you downloaded the right portal folder.")

        if dry:
            LOG.info("[dry-run] would stage into %s", dest)
            for p in images[:5]:
                LOG.info("[dry-run]   %s", p.name)
            if len(images) > 5:
                LOG.info("[dry-run]   ... and %d more", len(images) - 5)
            return 0

        dest.mkdir(parents=True, exist_ok=True)

        staged = 0
        for src in images:
            target = dest / src.name
            n = 1
            while target.exists():
                target = dest / f"{src.stem}_{n}{src.suffix}"
                n += 1
            # copy2 preserves mtime, which the processor's settle check reads
            shutil.copy2(src, target)
            staged += 1

        LOG.info("staged %d file(s) into %s", staged, dest)

        if move and source.is_file():
            source.unlink()
            LOG.info("removed %s", source.name)

    finally:
        if tmpdir is not None:
            tmpdir.cleanup()

    LOG.info("")
    LOG.info("Darkroom will pick these up within 15 minutes.")
    LOG.info("To run it now:  python3 ~/CCC/Darkroom/bin/darkroom.py")
    return 0


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage lab scans into the Darkroom dump folder")
    ap.add_argument("source", nargs="?", type=Path,
                    help="zip file or folder of scans")
    ap.add_argument("--latest", action="store_true",
                    help="use the newest .zip in ~/Downloads")
    ap.add_argument("--roll", type=str, required=True,
                    help="roll name, ideally YYYY-MM-DD_label")
    ap.add_argument("--inbox", type=Path, default=INBOX)
    ap.add_argument("--overwrite", action="store_true",
                    help="add to an existing roll folder")
    ap.add_argument("--move", action="store_true",
                    help="delete the source zip after staging")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if a.latest:
        found = latest_download()
        if found is None:
            LOG.error("no .zip found in %s", DOWNLOADS)
            return 2
        source = found
        LOG.info("using newest download: %s", source.name)
    elif a.source:
        source = a.source
    else:
        ap.error("give a source path or use --latest")
        return 2

    roll = safe_roll_name(a.roll)
    if roll != a.roll:
        LOG.info("roll name normalised to %s", roll)
    if not DATED_RE.match(roll):
        LOG.warning("roll name has no YYYY-MM-DD prefix — the processor will")
        LOG.warning("date these frames today rather than the day you shot them")

    if a.inbox == INBOX and not DRIVE.exists():
        LOG.error("Drive path unavailable: %s", DRIVE)
        LOG.error("Is Google Drive for Desktop running?")
        return 2

    return stage(source, roll, a.inbox, a.overwrite, a.move, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
