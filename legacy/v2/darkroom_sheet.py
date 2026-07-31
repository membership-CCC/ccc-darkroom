#!/usr/bin/env python3
"""
CCC DARKROOM — contact sheet
=================
Builds a branded review sheet for a processed roll: numbered thumbnails,
technical flags, and the CCC selection criteria printed alongside — so the
cull happens fast, against explicit standards, with you making the calls.

Run after darkroom.py:

    python3 darkroom_sheet.py /path/to/Photos/2026-08-01_borderlands
    python3 darkroom_sheet.py --all        # every roll missing a sheet

Output lands beside the images as  contact_sheet.png  and syncs with Drive.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required:  python3 -m pip install --user Pillow")

LOG = logging.getLogger("ccc-sheet")

# ===========================================================================
# CONFIG
# ===========================================================================

PHOTOS_ROOT = Path.home() / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"

# Brand fonts. Put the TTFs beside this script, or point at wherever they live.
FONT_DIR = Path(__file__).resolve().parent / "fonts"
BEBAS = FONT_DIR / "BebasNeue-Regular.ttf"
MONTSERRAT = FONT_DIR / "Montserrat-Regular.ttf"

INK = (26, 24, 18)
OAT = (236, 230, 214)
SKY = (141, 200, 232)
MUTED = (150, 146, 134)
RULE = (70, 66, 58)

COLS = 6
THUMB_W = 300
MARGIN = 60
GUTTER = 18

# Criteria printed on every sheet. Edit freely — this is the rubric, and it
# should reflect what you actually decide you want over time.
KEEP_CRITERIA = [
    "Effort visible — after the climb, not mid-pose",
    "People legible at 2–3m, full body or waist-up",
    "Riders sharp, near verge streaking, ridge clean",
    "Hands, bar tape, grease, texture",
    "Weather doing something — fog, dust, wet road",
    "Reads as from the ride, not about it",
]

CUT_CRITERIA = [
    "Anyone crossing frame under ~20m (smeared)",
    "Subject closer than 1m (soft, fixed focus)",
    "Riders as specks — nothing anchoring the frame",
    "Empty ridge with no foreground",
    "Vibration blur across the whole frame",
]

HOLD_CRITERIA = [
    "Identifiable junction, signage, or landmark",
    "Anything that locates the route",
    "Faces of anyone who hasn't agreed to be posted",
]


# ===========================================================================
# helpers
# ===========================================================================

def font(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        LOG.warning("font %s not found, falling back to default", path.name)
        return ImageFont.load_default()


def read_manifest(roll_dir: Path) -> list[dict]:
    mf = roll_dir / "manifest.csv"
    if not mf.exists():
        return []
    with mf.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def flag_colour(flag: str) -> tuple[int, int, int]:
    if flag in ("blank", "underexposed", "overexposed"):
        return (201, 106, 43)      # Ember — only ever appears as a warning dot
    if flag == "soft":
        return MUTED
    return SKY


# ===========================================================================
# sheet
# ===========================================================================

def build_sheet(roll_dir: Path, out_path: Path | None = None) -> Path | None:
    rows = read_manifest(roll_dir)
    web_dir = roll_dir / "web"
    if not rows or not web_dir.exists():
        LOG.warning("%s: no manifest or web folder", roll_dir.name)
        return None

    f_title = font(BEBAS, 58)
    f_head = font(BEBAS, 30)
    f_body = font(MONTSERRAT, 19)
    f_small = font(MONTSERRAT, 16)
    f_num = font(BEBAS, 26)

    # --- lay out thumbnails
    thumbs = []
    for row in rows:
        p = web_dir / f"{row['stem']}_web.jpg"
        if not p.exists():
            continue
        with Image.open(p) as im:
            im = im.convert("RGB")
            ratio = im.height / im.width
            th = im.resize((THUMB_W, max(1, round(THUMB_W * ratio))), Image.LANCZOS)
        thumbs.append((row, th))

    if not thumbs:
        LOG.warning("%s: no images found", roll_dir.name)
        return None

    rows_n = (len(thumbs) + COLS - 1) // COLS
    cell_h = max(t.height for _, t in thumbs) + 46

    header_h = 170
    grid_h = rows_n * (cell_h + GUTTER)
    criteria_h = 430
    W = MARGIN * 2 + COLS * THUMB_W + (COLS - 1) * GUTTER
    H = header_h + grid_h + criteria_h + MARGIN

    im = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(im)

    # --- header
    d.text((MARGIN, 46), roll_dir.name.upper().replace("_", "  ·  "),
           font=f_title, fill=OAT)
    counts: dict[str, int] = {}
    for r, _ in thumbs:
        counts[r.get("tech_flag", "ok")] = counts.get(r.get("tech_flag", "ok"), 0) + 1
    summary = f"{len(thumbs)} frames   ·   " + "   ".join(
        f"{k} {v}" for k, v in sorted(counts.items()))
    d.text((MARGIN + 2, 116), summary, font=f_small, fill=MUTED)
    d.line([MARGIN, header_h - 18, W - MARGIN, header_h - 18], fill=SKY, width=3)

    # --- grid
    for i, (row, th) in enumerate(thumbs):
        c, r = i % COLS, i // COLS
        x = MARGIN + c * (THUMB_W + GUTTER)
        y = header_h + r * (cell_h + GUTTER)
        im.paste(th, (x, y))
        d.rectangle([x, y, x + THUMB_W - 1, y + th.height - 1], outline=RULE)

        ly = y + th.height + 8
        d.text((x, ly), str(int(row["sequence"])).zfill(3), font=f_num, fill=OAT)
        flag = row.get("tech_flag", "ok")
        if flag and flag != "ok":
            d.ellipse([x + 52, ly + 9, x + 64, ly + 21], fill=flag_colour(flag))
            d.text((x + 72, ly + 5), flag, font=f_small, fill=MUTED)

    # --- criteria block
    cy = header_h + grid_h + 26
    d.line([MARGIN, cy, W - MARGIN, cy], fill=RULE, width=2)
    cy += 30

    colw = (W - MARGIN * 2 - 60) // 3
    blocks = [("KEEP", KEEP_CRITERIA, SKY),
              ("CUT", CUT_CRITERIA, MUTED),
              ("HOLD — REVIEW BEFORE ANY PUBLIC USE", HOLD_CRITERIA, (201, 106, 43))]

    for j, (title, items, colour) in enumerate(blocks):
        x = MARGIN + j * (colw + 30)
        d.text((x, cy), title, font=f_head, fill=colour)
        yy = cy + 44
        for it in items:
            d.text((x, yy), "—", font=f_body, fill=colour)
            # wrap by hand at column width
            words, line = it.split(), ""
            lines: list[str] = []
            for wd in words:
                trial = f"{line} {wd}".strip()
                if d.textlength(trial, font=f_body) > colw - 34:
                    lines.append(line)
                    line = wd
                else:
                    line = trial
            lines.append(line)
            for k, ln in enumerate(lines):
                d.text((x + 26, yy + k * 25), ln, font=f_body, fill=OAT)
            yy += 25 * len(lines) + 12

    d.text((MARGIN, H - 46),
           "Draft review sheet — nothing here is approved for publication.",
           font=f_small, fill=MUTED)

    out = out_path or (roll_dir / "contact_sheet.png")
    im.save(out)
    LOG.info("%s -> %s (%d frames)", roll_dir.name, out.name, len(thumbs))
    return out


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="CCC contact sheet builder")
    ap.add_argument("roll", nargs="?", type=Path, help="path to a processed roll folder")
    ap.add_argument("--all", action="store_true", help="every roll missing a sheet")
    ap.add_argument("--force", action="store_true", help="rebuild existing sheets")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.all:
        if not PHOTOS_ROOT.exists():
            LOG.error("photos root not found: %s", PHOTOS_ROOT)
            return 2
        targets = [d for d in sorted(PHOTOS_ROOT.iterdir())
                   if d.is_dir() and not d.name.startswith("_")]
        if not args.force:
            targets = [d for d in targets if not (d / "contact_sheet.png").exists()]
    elif args.roll:
        targets = [args.roll]
    else:
        ap.error("give a roll folder or --all")
        return 2

    if not targets:
        LOG.info("nothing to build")
        return 0

    for t in targets:
        build_sheet(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
