#!/usr/bin/env python3
"""
CCC DARKROOM — processor (v2)
=====================
Watches a Google Drive folder (synced to Ada by Drive for Desktop), applies the
CCC tone mapping, and exports every format you publish to — sized and cropped
according to the source orientation.

Upload from the phone into the Drive dump folder. Everything after is automatic.

    Drive:  CCC/Photos/_dump/<roll-name>/*.tif
                |   Drive for Desktop syncs down to Ada
                v
            darkroom.py        (launchd, every 15 min)
                |
                v
    Drive:  CCC/Photos/<date>_<roll>/instagram/ email/ web/ strava/
    Drive:  CCC/Photos/_originals/<date>_<roll>/*.tif

Usage
-----
    python3 darkroom.py
    python3 darkroom.py --dry-run
    python3 darkroom.py --force
    python3 darkroom.py --only ig_portrait,web
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is required:  python3 -m pip install --user Pillow")


# ===========================================================================
# CONFIG
# ===========================================================================

HOME = Path.home()

# Drive for Desktop mount. Verify with:  ls ~/Library/CloudStorage/
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"

INBOX = DRIVE / "_dump"           # phone uploads land here
OUTPUT = DRIVE                    # processed rolls
ORIGINALS = DRIVE / "_originals"  # sources, after processing

# State stays local: small, and must survive Drive hiccups.
STATE_DIR = HOME / "CCC" / "Darkroom" / ".state"

# --- brand tone -----------------------------------------------------------
INK = (26, 24, 18)        # #1A1812
OAT = (236, 230, 214)     # #ECE6D6

# --- output presets -------------------------------------------------------
# mode:
#   crop  exact ratio; a smart window picks the most detailed region
#   mat   whole frame preserved, centred on an Ink field
#   fit   resize by long edge, no crop
PRESETS: dict[str, dict] = {
    "ig_portrait":  dict(w=1080, h=1350, mode="crop", dir="instagram", q=92,
                         label="Instagram feed portrait 4:5"),
    "ig_square":    dict(w=1080, h=1080, mode="crop", dir="instagram", q=92,
                         label="Instagram feed square 1:1"),
    "ig_landscape": dict(w=1080, h=566,  mode="crop", dir="instagram", q=92,
                         label="Instagram feed landscape 1.91:1"),
    "ig_story":     dict(w=1080, h=1920, mode="mat",  dir="instagram", q=92,
                         label="Instagram story / Reels 9:16"),
    "strava":       dict(w=1200, h=None, mode="fit",  dir="strava",    q=86,
                         label="Strava post"),
    "email_body":   dict(w=1200, h=None, mode="fit",  dir="email",     q=82,
                         label="Mailchimp body image (600pt @2x)"),
    "email_header": dict(w=1200, h=600,  mode="crop", dir="email",     q=82,
                         label="Mailchimp header"),
    "web":          dict(w=1600, h=None, mode="fit",  dir="web",       q=82,
                         label="Website standard"),
    "web_hero":     dict(w=2400, h=1000, mode="crop", dir="web",       q=84,
                         label="Website hero band"),
}

# Which presets to build, by source orientation.
BY_ORIENTATION = {
    "landscape": ["ig_landscape", "ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "email_header", "web", "web_hero"],
    "portrait":  ["ig_portrait", "ig_story", "ig_square",
                  "strava", "email_body", "web"],
    "square":    ["ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "web"],
}

# A 3:2 landscape cropped to 4:5 discards ~45% of the picture. Past this
# threshold, mat the frame on Ink instead of butchering it.
MAT_IF_CROP_EXCEEDS = 0.45

# --- calibration ----------------------------------------------------------
# Written by darkroom_learn.py from your own recorded decisions. If absent,
# the defaults below apply. Thresholds only ever affect ADVISORY flags and
# which presets get built — never whether a frame is processed or kept.
CALIBRATION_FILE = HOME / "CCC" / "Darkroom" / "calibration.json"

THRESH = {
    "blank_variance": 40.0,
    "shadow_clip": 0.92,
    "highlight_clip": 0.92,
    "soft_sharpness": 0.8,
}
PRESETS_DISABLED: set[str] = set()


def load_calibration() -> dict:
    """Apply learned thresholds and preset pruning, if calibration exists."""
    if not CALIBRATION_FILE.exists():
        return {}
    try:
        cal = json.loads(CALIBRATION_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("calibration unreadable (%s) — using defaults", exc)
        return {}
    for k, v in (cal.get("thresholds") or {}).items():
        if k in THRESH and isinstance(v, (int, float)):
            THRESH[k] = float(v)
    for name in (cal.get("presets_disabled") or []):
        if name in PRESETS:
            PRESETS_DISABLED.add(name)
    return cal


# --- ingest safety --------------------------------------------------------
MIN_BYTES = 200_000
SETTLE_SECONDS = 45          # Drive syncs progressively — be patient
SOURCE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}

LOG = logging.getLogger("ccc-film")


# ===========================================================================
# tone
# ===========================================================================

def build_lut(black, white) -> list[int]:
    lut: list[int] = []
    for ch in range(3):
        b, w = black[ch], white[ch]
        lut.extend(round(b + (w - b) * (i / 255.0)) for i in range(256))
    return lut


TONE_LUT = build_lut(INK, OAT)


def apply_tone(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    return Image.merge("RGB", (gray, gray, gray)).point(TONE_LUT)


# ===========================================================================
# smart crop — edge-energy window search, no model involved
# ===========================================================================

GRID = 160


def _edge_energy(img: Image.Image) -> list[int]:
    """Coarse gradient magnitude on a downscaled grayscale copy."""
    small = ImageOps.grayscale(img).resize((GRID, GRID), Image.BILINEAR)
    px = list(small.getdata())
    out = [0] * (GRID * GRID)
    for y in range(GRID):
        row = y * GRID
        for x in range(GRID):
            i = row + x
            v = px[i]
            dx = abs(px[i + 1] - v) if x + 1 < GRID else 0
            dy = abs(px[i + GRID] - v) if y + 1 < GRID else 0
            out[i] = dx + dy
    return out


def _best_offset(energy: list[int], span_frac: float, horizontal: bool) -> float:
    """Normalised offset (0..1) of the highest-energy window position."""
    win = max(1, round(GRID * span_frac))
    if win >= GRID:
        return 0.5

    sums = [0] * GRID
    for y in range(GRID):
        for x in range(GRID):
            sums[x if horizontal else y] += energy[y * GRID + x]

    prefix = [0] * (GRID + 1)
    for i, v in enumerate(sums):
        prefix[i + 1] = prefix[i] + v

    best_i, best_v = 0, -1
    for i in range(GRID - win + 1):
        v = prefix[i + win] - prefix[i]
        if v > best_v:
            best_v, best_i = v, i

    span = GRID - win
    return 0.5 if span == 0 else best_i / span


def smart_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    w, h = img.size
    target_ratio, ratio = tw / th, w / h
    if abs(ratio - target_ratio) < 1e-6:
        return img.copy()

    energy = _edge_energy(img)

    if ratio > target_ratio:                    # too wide -> trim sides
        new_w = round(h * target_ratio)
        frac = _best_offset(energy, new_w / w, horizontal=True)
        left = round((w - new_w) * frac)
        return img.crop((left, 0, left + new_w, h))

    new_h = round(w / target_ratio)             # too tall -> trim top/bottom
    frac = _best_offset(energy, new_h / h, horizontal=False)
    top = round((h - new_h) * frac)
    return img.crop((0, top, w, top + new_h))


def mat_on_ink(img: Image.Image, tw: int, th: int) -> Image.Image:
    """Preserve the whole frame, centred on an Ink field."""
    canvas = Image.new("RGB", (tw, th), INK)
    scale = min(tw / img.width, th / img.height) * 0.92
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    canvas.paste(img.resize((nw, nh), Image.LANCZOS),
                 ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def fit_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    scale = long_edge / max(img.size)
    if scale >= 1.0:
        return img.copy()
    return img.resize((max(1, round(img.width * scale)),
                       max(1, round(img.height * scale))), Image.LANCZOS)


def crop_loss(img: Image.Image, tw: int, th: int) -> float:
    """Fraction of source area an exact-ratio crop would discard."""
    w, h = img.size
    tr, r = tw / th, w / h
    if abs(r - tr) < 1e-6:
        return 0.0
    if r > tr:
        return 1.0 - (round(h * tr) / w)
    return 1.0 - (round(w / tr) / h)


def render_preset(img: Image.Image, spec: dict) -> tuple[Image.Image, str]:
    mode, tw, th = spec["mode"], spec["w"], spec["h"]

    if mode == "fit" or th is None:
        return fit_long_edge(img, tw), "fit"
    if mode == "mat":
        return mat_on_ink(img, tw, th), "mat"
    if crop_loss(img, tw, th) > MAT_IF_CROP_EXCEEDS:
        return mat_on_ink(img, tw, th), "mat"
    return smart_crop(img, tw, th).resize((tw, th), Image.LANCZOS), "crop"


# ===========================================================================
# triage — technical only, never editorial
# ===========================================================================

def assess_frame(img: Image.Image) -> tuple[str, str, dict]:
    """
    Technical triage plus the raw measurements behind it.

    The measurements are the point: they are the feature set
    darkroom_learn.py uses to calibrate thresholds against your actual
    keep/cut decisions. The flag is advisory and never gates processing.
    """
    small = ImageOps.grayscale(img).resize((256, 256), Image.BILINEAR)
    px = list(small.getdata())
    n = len(px)
    mean = sum(px) / n
    var = sum((p - mean) ** 2 for p in px) / n
    shadow = sum(1 for p in px if p < 24) / n
    highlight = sum(1 for p in px if p > 240) / n
    edges = sum(abs(px[i + 1] - px[i]) for i in range(n - 1) if (i + 1) % 256)
    sharp = edges / n

    metrics = {
        "mean_luma": round(mean, 1),
        "variance": round(var, 1),
        "shadow_frac": round(shadow, 4),
        "highlight_frac": round(highlight, 4),
        "sharpness": round(sharp, 3),
    }

    if var < THRESH["blank_variance"]:
        return ("blank", f"near-uniform frame (var {var:.0f})", metrics)
    if shadow > THRESH["shadow_clip"]:
        return ("underexposed", f"{shadow:.0%} near black", metrics)
    if highlight > THRESH["highlight_clip"]:
        return ("overexposed", f"{highlight:.0%} near white", metrics)
    if sharp < THRESH["soft_sharpness"]:
        return ("soft", f"low edge energy ({sharp:.2f})", metrics)
    return ("ok", "", metrics)


def orientation_of(img: Image.Image) -> str:
    r = img.width / img.height
    if r > 1.08:
        return "landscape"
    if r < 0.93:
        return "portrait"
    return "square"


# ===========================================================================
# naming / state
# ===========================================================================

SLUG_RE = re.compile(r"[^a-z0-9]+")
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-](.+)$")


def slugify(t: str) -> str:
    return SLUG_RE.sub("-", t.strip().lower()).strip("-") or "roll"


def parse_roll(name: str) -> tuple[str, str]:
    m = DATED_RE.match(name.strip())
    if m:
        return m.group(1), slugify(m.group(2))
    return dt.date.today().isoformat(), slugify(name)


def fingerprint(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def load_state(key: str) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = STATE_DIR / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except json.JSONDecodeError:
            LOG.warning("state %s unreadable, starting fresh", f.name)
    return {}


def save_state(key: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{key}.json").write_text(json.dumps(state, indent=2))


def settled(p: Path) -> bool:
    try:
        st = p.stat()
    except (FileNotFoundError, OSError):
        return False
    if st.st_size < MIN_BYTES:
        return False
    return (time.time() - st.st_mtime) >= SETTLE_SECONDS


# ===========================================================================
# processing
# ===========================================================================

def process_roll(roll_dir: Path, dry: bool, force: bool, only: set[str] | None) -> int:
    date_str, slug = parse_roll(roll_dir.name)
    key = f"{date_str}_{slug}"

    sources = sorted(p for p in roll_dir.iterdir()
                     if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                     and not p.name.startswith("."))
    if not sources:
        return 0

    state = load_state(key)
    out_root = OUTPUT / key
    orig_dir = ORIGINALS / key
    rows: list[dict] = []
    seq = max((int(v.get("seq", 0)) for v in state.values()), default=0)
    done = 0

    for src in sources:
        if not settled(src):
            LOG.info("[%s] %s still syncing", key, src.name)
            continue
        fp = fingerprint(src)
        if fp in state and not force:
            continue

        seq += 1
        stem = f"CCC_{date_str}_{slug}_{seq:03d}"

        if dry:
            LOG.info("[dry-run] %s -> %s", src.name, stem)
            done += 1
            continue

        try:
            with Image.open(src) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                flag, note, metrics = assess_frame(im)
                orient = orientation_of(im)
                toned = apply_tone(im)
        except Exception as exc:                       # noqa: BLE001
            LOG.error("[%s] FAILED to read %s: %s", key, src.name, exc)
            continue

        names = [n for n in BY_ORIENTATION.get(orient, BY_ORIENTATION["landscape"])
                 if n not in PRESETS_DISABLED]
        if only:
            names = [n for n in names if n in only]

        made: list[str] = []
        for pname in names:
            spec = PRESETS[pname]
            try:
                out_img, used = render_preset(toned, spec)
                d = out_root / spec["dir"]
                d.mkdir(parents=True, exist_ok=True)
                out_img.save(d / f"{stem}_{pname}.jpg", "JPEG",
                             quality=spec["q"], optimize=True, progressive=True,
                             subsampling=0 if spec["q"] >= 90 else 2)
                made.append(f"{pname}:{used}")
            except Exception as exc:                   # noqa: BLE001
                LOG.error("[%s] %s preset %s failed: %s", key, stem, pname, exc)

        if not made:
            LOG.error("[%s] %s produced nothing — source left in place", key, src.name)
            continue

        orig_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(orig_dir / src.name))

        stamp = dt.datetime.now().isoformat(timespec="seconds")
        state[fp] = {"seq": seq, "source": src.name, "processed_at": stamp}
        rows.append({
            "sequence": seq, "roll": key, "date": date_str,
            "source_file": src.name, "stem": stem,
            "orientation": orient, "variants": " ".join(made),
            "variant_count": len(made),
            "tech_flag": flag, "tech_note": note,
            "mean_luma": metrics["mean_luma"],
            "variance": metrics["variance"],
            "shadow_frac": metrics["shadow_frac"],
            "highlight_frac": metrics["highlight_frac"],
            "sharpness": metrics["sharpness"],
            "processed_at": stamp, "status": "draft",
        })
        done += 1
        LOG.info("[%s] %s -> %s  [%s, %d formats]%s", key, src.name, stem,
                 orient, len(made), "" if flag == "ok" else f"  ({flag})")

    if rows and not dry:
        write_manifest(out_root / "manifest.csv", rows)
        write_decisions_stub(out_root / "decisions.txt", key)
        save_state(key, state)
        try:
            if not any(roll_dir.iterdir()):
                roll_dir.rmdir()
        except OSError:
            pass
    return done


DECISIONS_TEMPLATE = """# CCC Darkroom — decisions for {roll}
#
# Frame numbers come from contact_sheet.png. Space or comma separated.
# Uncomment a line by deleting the leading #, then fill it in.
# darkroom_learn.py reads this to calibrate itself against your judgement.
#
# keep:      frames worth having at all
# published: frames that actually went out somewhere
# hold:      frames held for route-safety or consent review
# formats:   optional — exports you actually used: 7=ig_portrait|web
# notes:     free text, ignored by the learner

# keep:
# published:
# hold:
# formats:
# notes:
"""


def write_decisions_stub(path: Path, roll_key: str) -> None:
    """Create the decisions file once; never overwrite a filled-in one."""
    if path.exists():
        return
    path.write_text(DECISIONS_TEMPLATE.format(roll=roll_key))


def write_manifest(path: Path, rows: list[dict]) -> None:
    fields = ["sequence", "roll", "date", "source_file", "stem", "orientation",
              "variants", "variant_count", "tech_flag", "tech_note",
              "mean_luma", "variance", "shadow_frac", "highlight_frac",
              "sharpness", "processed_at", "status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            wr.writeheader()
        wr.writerows(rows)


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="CCC film processor v2")
    ap.add_argument("--inbox", type=Path, default=INBOX)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", type=str, default="", help="comma-separated preset names")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    if a.inbox == INBOX and not DRIVE.exists():
        LOG.info("Drive path unavailable (%s) — is Drive for Desktop running?", DRIVE)
        return 0

    cal = load_calibration()
    if cal:
        LOG.info("calibration loaded — %s frames, %s rolls; thresholds %s",
                 cal.get("frames_analysed", "?"), cal.get("rolls_analysed", "?"),
                 {k: round(v, 2) for k, v in THRESH.items()})
        if PRESETS_DISABLED:
            LOG.info("presets disabled by calibration: %s",
                     ", ".join(sorted(PRESETS_DISABLED)))

    only = {s.strip() for s in a.only.split(",") if s.strip()} or None
    if only:
        bad = only - set(PRESETS)
        if bad:
            LOG.error("unknown preset(s): %s", ", ".join(sorted(bad)))
            return 2

    inbox = a.inbox
    inbox.mkdir(parents=True, exist_ok=True)

    loose = [p for p in inbox.iterdir()
             if p.is_file() and p.suffix.lower() in SOURCE_EXTS]
    if loose:
        LOG.warning("%d file(s) loose in the dump root — put them in a named "
                    "subfolder so they get a roll label", len(loose))

    dirs = [d for d in sorted(inbox.iterdir())
            if d.is_dir() and not d.name.startswith(".")]
    if not dirs:
        LOG.info("nothing to do")
        return 0

    total = sum(process_roll(d, a.dry_run, a.force, only) for d in dirs)
    LOG.info("done — %d frame(s)", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
