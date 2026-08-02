#!/usr/bin/env python3
"""
CCC DARKROOM — processor (v3, curation-aware)
=============================================
v3 changes one thing: if a `_curation.json` sits beside the frames, this
renders what the curator decided instead of guessing from the file's shape.

    with a plan          without a plan (unchanged v2 behaviour)
    -------------------  -----------------------------------------
    formats chosen per   formats chosen by orientation bucket
      photograph
    crop centred on the  crop centred on the busiest texture
      detected subject
    colour or duotone    duotone, always
      per photograph
    HOLD frames quar-    no route-safety concept
      antined to _hold/
    rejects recorded,    everything rendered regardless
      not rendered

The plan is advisory input, not control: this script still owns every pixel
it writes, is still idempotent by fingerprint, and still never deletes
anything. Delete `_curation.json` and it behaves exactly like v2.

    python3 darkroom.py
    python3 darkroom.py --dry-run
    python3 darkroom.py --force
    python3 darkroom.py --only ig_portrait,web
    python3 darkroom.py --ignore-curation
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
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"

INBOX = DRIVE / "_dump"
OUTPUT = DRIVE
ORIGINALS = DRIVE / "_originals"

STATE_DIR = HOME / "CCC" / "Darkroom" / ".state"

try:
    from darkroom_credit import draw_credit
except ImportError:                                    # pragma: no cover
    def draw_credit(img, handle, preset=""):           # noqa: D103
        return img

PLAN_NAME = "_curation.json"
# Who shot which frame, kept beside the frames themselves in _originals/<roll>/.
# The folder name carries the credit on the way IN, but that name is consumed by
# the merge: two "_by_" folders become one roll and the handles are gone from
# every path. Anything that re-renders from the archive — retest_roll.sh does
# exactly this — then produced uncredited exports. The ledger is the durable
# record. Plain JSON, keyed by original filename, safe to hand-edit.
CREDITS_NAME = "_credits.json"
# Each photographer's frames are archived in their own subfolder,
# _originals/<roll>/by_<handle>/. Three reasons, in order of how much they
# cost when absent:
#   1. retest_roll.sh can stage them back as <roll>_by_<handle> — the exact
#      shape they arrived in — so a re-render reads credit from the folder
#      name, the same path as a fresh ingest. No special case to forget.
#   2. two photographers can no longer collide on a filename. The __2
#      suffixing that once doubled the archive is unreachable.
#   3. it answers "whose is this?" in the Finder, without a tool.
# Frames with no credit stay in the roll root.
CREDIT_DIR_PREFIX = "by_"


def credit_subdir(handle: str) -> str:
    """"@donalrey" -> "by_donalrey". Mirrors the _by_ dump-folder convention."""
    return CREDIT_DIR_PREFIX + str(handle).lstrip("@").strip().lower()


def is_credit_dir(p) -> bool:
    return p.is_dir() and p.name.lower().startswith(CREDIT_DIR_PREFIX)
HOLD_DIRNAME = "_hold"

# Directories that are never rolls. `xArchive` is the convention for a bin of
# test material staged for real deletion later — processing it would resurrect
# work that was deliberately thrown away. Compared case-insensitively.
NOT_A_ROLL = {"xarchive"}


def is_roll_dir(p) -> bool:
    return p.is_dir() and not p.name.startswith(".") and p.name.lower() not in NOT_A_ROLL


# --- brand tone -----------------------------------------------------------
INK = (26, 24, 18)        # #1A1812
OAT = (236, 230, 214)     # #ECE6D6

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

BY_ORIENTATION = {
    "landscape": ["ig_landscape", "ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "email_header", "web", "web_hero"],
    "portrait":  ["ig_portrait", "ig_story", "ig_square",
                  "strava", "email_body", "web"],
    "square":    ["ig_square", "ig_portrait", "ig_story",
                  "strava", "email_body", "web"],
}

MAT_IF_CROP_EXCEEDS = 0.45

CALIBRATION_FILE = HOME / "CCC" / "Darkroom" / "calibration.json"

THRESH = {
    "blank_variance": 40.0,
    "shadow_clip": 0.92,
    "highlight_clip": 0.92,
    "soft_sharpness": 0.8,
}
PRESETS_DISABLED: set[str] = set()

# A file untouched for SETTLE_SECONDS has finished syncing, whatever its size —
# that is what actually proves the write is done. MIN_BYTES is only a junk
# filter (thumbnails, icons, stray artefacts), so it must sit well below any
# real photograph. At 200_000 it silently rejected legitimate small images
# forever, reporting them as "still syncing" on every cycle.
MIN_BYTES = 20_000
SETTLE_SECONDS = 45
SOURCE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}

LOG = logging.getLogger("ccc-film")


def load_calibration() -> dict:
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
# geometry
# ===========================================================================

GRID = 160


def _edge_energy(img: Image.Image) -> list[int]:
    small = ImageOps.grayscale(img).resize((GRID, GRID), Image.BILINEAR)
    px = list(small.tobytes())
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
    if ratio > target_ratio:
        new_w = round(h * target_ratio)
        frac = _best_offset(energy, new_w / w, horizontal=True)
        left = round((w - new_w) * frac)
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / target_ratio)
    frac = _best_offset(energy, new_h / h, horizontal=False)
    top = round((h - new_h) * frac)
    return img.crop((0, top, w, top + new_h))


def crop_to_box(img: Image.Image, box) -> Image.Image:
    """Crop to a normalised [x0, y0, x1, y1] window from the curator."""
    w, h = img.size
    x0 = max(0, min(w - 1, round(box[0] * w)))
    y0 = max(0, min(h - 1, round(box[1] * h)))
    x1 = max(x0 + 1, min(w, round(box[2] * w)))
    y1 = max(y0 + 1, min(h, round(box[3] * h)))
    return img.crop((x0, y0, x1, y1))


def mat_on_ink(img: Image.Image, tw: int, th: int) -> Image.Image:
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
    w, h = img.size
    tr, r = tw / th, w / h
    if abs(r - tr) < 1e-6:
        return 0.0
    if r > tr:
        return 1.0 - (round(h * tr) / w)
    return 1.0 - (round(w / tr) / h)


def render_preset(img: Image.Image, spec: dict) -> tuple[Image.Image, str]:
    """Uncurated path — v2 behaviour, unchanged."""
    mode, tw, th = spec["mode"], spec["w"], spec["h"]
    if mode == "fit" or th is None:
        return fit_long_edge(img, tw), "fit"
    if mode == "mat":
        return mat_on_ink(img, tw, th), "mat"
    if crop_loss(img, tw, th) > MAT_IF_CROP_EXCEEDS:
        return mat_on_ink(img, tw, th), "mat"
    return smart_crop(img, tw, th).resize((tw, th), Image.LANCZOS), "crop"


def render_curated(img: Image.Image, spec: dict, crop) -> tuple[Image.Image, str]:
    """
    Curated path. `crop` is one of:
        None          -> fit by long edge (no ratio constraint)
        "mat"         -> whole frame on an Ink field
        [x0,y0,x1,y1] -> normalised window centred on the detected subject
    """
    tw, th = spec["w"], spec["h"]
    if crop is None or th is None:
        return fit_long_edge(img, tw), "fit"
    if crop == "mat":
        return mat_on_ink(img, tw, th), "mat"
    return crop_to_box(img, crop).resize((tw, th), Image.LANCZOS), "crop"


# ===========================================================================
# triage / naming / state
# ===========================================================================

def assess_frame(img: Image.Image) -> tuple[str, str, dict]:
    small = ImageOps.grayscale(img).resize((256, 256), Image.BILINEAR)
    px = list(small.tobytes())
    n = len(px)
    mean = sum(px) / n
    var = sum((p - mean) ** 2 for p in px) / n
    shadow = sum(1 for p in px if p < 24) / n
    highlight = sum(1 for p in px if p > 240) / n
    edges = sum(abs(px[i + 1] - px[i]) for i in range(n - 1) if (i + 1) % 256)
    sharp = edges / n
    metrics = {
        "mean_luma": round(mean, 1), "variance": round(var, 1),
        "shadow_frac": round(shadow, 4), "highlight_frac": round(highlight, 4),
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


SLUG_RE = re.compile(r"[^a-z0-9]+")
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-](.+)$")


def slugify(t: str) -> str:
    return SLUG_RE.sub("-", t.strip().lower()).strip("-") or "roll"


# Photo credit lives in the folder name: <date>_<label>_by_<handle>.
# Greedy on the left so it splits at the LAST "_by_" — a label like
# "ride_by_the_lake_by_donalrey" must credit donalrey, not "the_lake_by_donalrey".
BY_RE = re.compile(r"^(.*)_by_(.+)$", re.IGNORECASE)


def parse_roll(name: str) -> tuple[str, str, str | None]:
    """(date, slug, credit-handle-or-None) from a dump folder name.

        2026-08-01_borderlands_by_donalrey  -> 2026-08-01, borderlands, @donalrey
        2026-08-01_borderlands              -> 2026-08-01, borderlands, None

    The credit is taken before slugify so underscores inside a handle survive:
    "the_catskill_weekender" must not become "the-catskill-weekender".

    Stripping "_by_<handle>" from the label is what merges two photographers'
    folders of the same ride into one output roll.
    """
    m = DATED_RE.match(name.strip())
    if m:
        date_str, label = m.group(1), m.group(2)
    else:
        date_str, label = dt.date.today().isoformat(), name.strip()

    credit = None
    b = BY_RE.match(label)
    if b:
        label, handle = b.group(1), b.group(2).strip()
        if handle:
            credit = "@" + handle.lstrip("@").lower()
        if not label.strip():
            LOG.warning("roll %r has a credit but no label — using 'roll'", name)
    return date_str, slugify(label), credit


def load_credit_ledger(key: str) -> dict:
    """{original filename: "@handle"} for a roll, or {} if there is none.

    Never raises: a corrupt ledger costs the credits on a re-render, which is
    the status quo it was written to fix, and must not strand the roll.
    """
    path = ORIGINALS / key / CREDITS_NAME
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def ledger_by_basename(ledger: dict) -> dict:
    """Basename -> handle, for looking up a frame staged out of a subfolder.

    Keys written since the archive gained per-photographer subfolders are
    relative paths ("by_donalrey/x.jpg"); older ones are bare filenames. A
    basename that is ambiguous across two photographers is dropped rather than
    guessed at — a mis-attributed credit is worse than none.
    """
    seen, out = {}, {}
    for k, v in ledger.items():
        base = k.rsplit("/", 1)[-1]
        if base in seen and seen[base] != v:
            out.pop(base, None)
            continue
        seen[base] = v
        out[base] = v
    return out


def save_credit_ledger(key: str, additions: dict) -> None:
    """Merge new frame->handle pairs into the roll's ledger.

    Merge rather than replace: a roll grows. The second photographer's folder
    arrives days after the first has been archived, and rewriting the file with
    only the current batch would erase the first photographer's credits.
    An existing entry wins — the archive is the record of what was actually
    rendered, and a later pass should not silently reattribute a frame.
    """
    if not additions:
        return
    path = ORIGINALS / key / CREDITS_NAME
    merged = load_credit_ledger(key)
    changed = False
    for name, handle in additions.items():
        if handle and name not in merged:
            merged[name] = handle
            changed = True
    if not changed:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
    except OSError as exc:                             # noqa: BLE001
        LOG.warning("[%s] could not write %s: %s", key, CREDITS_NAME, exc)


def same_file(a: Path, b: Path) -> bool:
    """Cheap content equality: size, then a hash of the head and tail.

    Enough to tell "this is the same photograph coming back through the dump"
    from "two photographers used the same filename", without reading hundreds
    of megabytes on every cycle.
    """
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    try:
        h = []
        for p in (a, b):
            with p.open("rb") as fh:
                head = fh.read(262144)
                fh.seek(max(0, p.stat().st_size - 262144))
                tail = fh.read(262144)
            h.append(hashlib.sha1(head + tail).hexdigest())
        return h[0] == h[1]
    except OSError:
        return False


def fingerprint(p: Path) -> str:
    """Identity of a source frame, for "have I already processed this?".

    The containing folder is part of it. Two photographers on the same ride can
    both hand over an IMG_0001.jpg; without the folder, whichever was processed
    second matched the first's fingerprint and was silently skipped — the frame
    simply never appeared, with no warning. Observed in test with two solid
    frames of equal size written in the same second.

    Including the folder is safe for idempotence because the round-trip is
    symmetric: retest_roll.sh stages by_<handle>/ back into
    <roll>_by_<handle>/, the same folder name the frame arrived in, and cp -p
    preserves mtime. The same file coming back round still matches itself.
    """
    st = p.stat()
    raw = f"{p.parent.name}/{p.name}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


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


def settled(p: Path) -> tuple[bool, str]:
    """(ready, reason). Reason distinguishes 'wait' from 'never' — a file that
    will never be accepted must say so instead of looping as 'still syncing'."""
    try:
        st = p.stat()
    except (FileNotFoundError, OSError):
        return False, "unreadable"
    if (time.time() - st.st_mtime) < SETTLE_SECONDS:
        return False, "still syncing"
    if st.st_size < MIN_BYTES:
        return False, (f"only {st.st_size:,} bytes — under the {MIN_BYTES:,} junk "
                       f"floor, so it will never be processed. Move it out of "
                       f"_dump, or lower MIN_BYTES in darkroom.py if it is real")
    return True, ""


def load_plan(roll_dir: Path, ignore: bool) -> dict:
    if ignore:
        return {}
    f = roll_dir / PLAN_NAME
    if not f.exists():
        return {}
    try:
        plan = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warning("curation plan unreadable (%s) — falling back to v2 rules", exc)
        return {}
    frames = plan.get("frames") or {}
    LOG.info("curation plan loaded — %d frame(s), decided by %s",
             len(frames), plan.get("judgment", "?"))
    return frames


# ===========================================================================
# processing
# ===========================================================================


# Google Drive's filesystem intermittently refuses a directory listing while it
# is still materialising files, raising OSError EDEADLK ("Resource deadlock
# avoided") — observed on a 67-frame roll mid-sync. It clears on its own, so a
# short retry is the honest response; a crash is not.
def listdir_retry(d: Path, attempts: int = 4, pause: float = 2.0) -> list:
    for i in range(attempts):
        try:
            return list(d.iterdir())
        except OSError as exc:
            if i == attempts - 1:
                raise
            LOG.warning("listing %s failed (%s) — retry %d/%d in %.0fs",
                        d.name, exc.strerror or exc, i + 1, attempts - 1, pause)
            time.sleep(pause)
            pause *= 2
    return []


def process_roll(roll_dir: Path, dry: bool, force: bool, only: set[str] | None,
                 ignore_curation: bool) -> int:
    date_str, slug, credit = parse_roll(roll_dir.name)
    key = f"{date_str}_{slug}"

    sources = sorted(p for p in listdir_retry(roll_dir)
                     if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                     and not p.name.startswith("."))
    if not sources:
        return 0

    plan = load_plan(roll_dir, ignore_curation)
    state = load_state(key)
    out_root = OUTPUT / key
    orig_dir = ORIGINALS / key
    # The folder name is authoritative when it carries "_by_". When it does not
    # — a re-render staged out of the archive, where the merge already ate the
    # handles — fall back to what this roll recorded the first time round.
    ledger = ledger_by_basename(load_credit_ledger(key))
    new_credits: dict[str, str] = {}
    rows: list[dict] = []
    seq = max((int(v.get("seq", 0)) for v in state.values()), default=0)
    done = 0

    for src in sources:
        ready, why = settled(src)
        if not ready:
            level = LOG.warning if "never" in why else LOG.info
            level("[%s] %s %s", key, src.name, why)
            continue
        fp = fingerprint(src)
        if fp in state and not force:
            continue

        entry = plan.get(src.name) or {}
        frame_credit = credit or ledger.get(src.name)
        seq += 1
        stem = f"CCC_{date_str}_{slug}_{seq:03d}"

        if dry:
            if entry:
                LOG.info("[dry-run] %s -> %s  [%s, %s, %s%s]", src.name, stem,
                         entry.get("verdict", "?"), entry.get("tone", "?"),
                         ",".join(entry.get("formats", {})) or "no formats",
                         "  HOLD" if entry.get("hold") else "")
            else:
                LOG.info("[dry-run] %s -> %s", src.name, stem)
            done += 1
            continue

        try:
            with Image.open(src) as im:
                im.load()
                im = ImageOps.exif_transpose(im)
                flag, note, metrics = assess_frame(im)
                orient = orientation_of(im)
                base = im.convert("RGB")
        except Exception as exc:                       # noqa: BLE001
            LOG.error("[%s] FAILED to read %s: %s", key, src.name, exc)
            continue

        # ---- curated rejection: record it, do not render, do not re-try ---
        if entry.get("verdict") == "reject":
            bin_dir = orig_dir / credit_subdir(frame_credit) if frame_credit else orig_dir
            bin_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(bin_dir / src.name))
            if frame_credit:
                new_credits[f"{bin_dir.name}/{src.name}"] = frame_credit
            stamp = dt.datetime.now().isoformat(timespec="seconds")
            state[fp] = {"seq": seq, "source": src.name, "processed_at": stamp,
                         "rejected": True}
            rows.append(_row(seq, key, date_str, src, stem, orient, [], flag, note,
                             metrics, stamp, entry, status="rejected",
                             credit=frame_credit))
            done += 1
            LOG.info("[%s] %s -> REJECTED (%s) — archived, nothing rendered",
                     key, src.name, entry.get("verdict_note", "no reason given"))
            continue

        # ---- tone: per-photograph when curated, duotone otherwise ---------
        # "both" renders each chosen format twice so a borderline frame can be
        # compared rather than decided blind.
        tone = entry.get("tone", "duotone") if entry else "duotone"
        if tone == "colour":
            treatments = [("", base)]
        elif tone == "both":
            treatments = [("", base), ("_mono", apply_tone(base))]
        else:
            treatments = [("", apply_tone(base))]

        # ---- format list --------------------------------------------------
        if entry.get("formats"):
            wanted = {n: v.get("crop") for n, v in entry["formats"].items()
                      if n in PRESETS}
        else:
            wanted = {n: "auto" for n in BY_ORIENTATION.get(
                orient, BY_ORIENTATION["landscape"])}
        wanted = {n: c for n, c in wanted.items() if n not in PRESETS_DISABLED}
        if only:
            wanted = {n: c for n, c in wanted.items() if n in only}

        # ---- HOLD quarantine ----------------------------------------------
        held = bool(entry.get("hold"))
        dest_root = (out_root / HOLD_DIRNAME) if held else out_root

        made: list[str] = []
        for pname, crop in wanted.items():
            spec = PRESETS[pname]
            for suffix, img in treatments:
                try:
                    if crop == "auto":
                        out_img, used = render_preset(img, spec)
                    else:
                        out_img, used = render_curated(img, spec, crop)
                    # After resize, so the mark is sized against the exported
                    # dimensions rather than the source. Originals stay clean.
                    if frame_credit:
                        out_img = draw_credit(out_img, frame_credit, pname)
                    d = dest_root / spec["dir"]
                    d.mkdir(parents=True, exist_ok=True)
                    out_img.save(d / f"{stem}_{pname}{suffix}.jpg", "JPEG",
                                 quality=spec["q"], optimize=True, progressive=True,
                                 subsampling=0 if spec["q"] >= 90 else 2)
                    made.append(f"{pname}{suffix}:{used}")
                except Exception as exc:               # noqa: BLE001
                    LOG.error("[%s] %s preset %s%s failed: %s", key, stem, pname,
                              suffix, exc)

        if not made:
            LOG.error("[%s] %s produced nothing — source left in place", key, src.name)
            continue

        # Credited frames go to _originals/<roll>/by_<handle>/, which is what
        # makes a name clash between two photographers impossible. Uncredited
        # frames stay in the roll root.
        bin_dir = orig_dir / credit_subdir(frame_credit) if frame_credit else orig_dir
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Never overwrite a *different* original — but the common case is the
        # same file coming back round: retest_roll.sh stages from _originals
        # into _dump and the renderer moves it back. Suffixing that would
        # double the archive on every re-test, which it did once.
        dest = bin_dir / src.name
        if dest.exists():
            if same_file(src, dest):
                shutil.move(str(src), str(dest))          # identical, replace
            else:
                i = 2
                while dest.exists():
                    dest = bin_dir / f"{src.stem}__{i}{src.suffix}"
                    i += 1
                LOG.warning("[%s] a different %s is already archived — saved as %s",
                            key, src.name, dest.name)
                shutil.move(str(src), str(dest))
        else:
            shutil.move(str(src), str(dest))

        if frame_credit:
            new_credits[f"{bin_dir.name}/{dest.name}"] = frame_credit
        stamp = dt.datetime.now().isoformat(timespec="seconds")
        state[fp] = {"seq": seq, "source": src.name, "processed_at": stamp}
        rows.append(_row(seq, key, date_str, src, stem, orient, made, flag, note,
                         metrics, stamp, entry,
                         status="hold" if held else "draft", credit=frame_credit))
        done += 1
        LOG.info("[%s] %s -> %s  [%s, %s, %d formats]%s%s", key, src.name, stem,
                 orient, tone, len(made),
                 "  HOLD" if held else "",
                 "" if flag == "ok" else f"  ({flag})")

    if rows and not dry:
        save_credit_ledger(key, new_credits)
        write_manifest(out_root / "manifest.csv", rows)
        write_decisions_stub(out_root / "decisions.txt", key)
        held_rows = [r for r in rows if r["status"] == "hold"]
        if held_rows:
            write_hold_notice(out_root / "HOLD_REVIEW.txt", key, held_rows)
        save_state(key, state)
        try:
            # Keep the plan with the results. It was being deleted along with
            # the emptied dump folder, which broke the documented "edit it and
            # re-run with --force" override — there was nothing left to edit.
            src_plan = roll_dir / PLAN_NAME
            if src_plan.exists():
                # A merged roll is fed by more than one dump folder, each with
                # its own plan. The first keeps the documented name; the rest
                # are qualified by source so neither is silently overwritten.
                dest_plan = out_root / PLAN_NAME
                if dest_plan.exists():
                    dest_plan = out_root / f"_curation_{roll_dir.name}.json"
                    LOG.info("[%s] second plan for this roll -> %s",
                             key, dest_plan.name)
                shutil.copy2(str(src_plan), str(dest_plan))
                orig_dir.mkdir(parents=True, exist_ok=True)
                oplan = orig_dir / dest_plan.name
                shutil.copy2(str(src_plan), str(oplan))
            if not any(p for p in roll_dir.iterdir() if p.name != PLAN_NAME):
                src_plan.unlink(missing_ok=True)
                roll_dir.rmdir()
        except OSError:
            pass
    return done


def _row(seq, key, date_str, src, stem, orient, made, flag, note, metrics,
         stamp, entry, status, credit=None) -> dict:
    return {
        "sequence": seq, "roll": key, "date": date_str,
        "source_file": src.name, "stem": stem,
        "orientation": orient, "variants": " ".join(made),
        "variant_count": len(made),
        "tech_flag": flag, "tech_note": note,
        "mean_luma": metrics["mean_luma"], "variance": metrics["variance"],
        "shadow_frac": metrics["shadow_frac"],
        "highlight_frac": metrics["highlight_frac"],
        "sharpness": metrics["sharpness"],
        "curated": "yes" if entry else "no",
        "decided_by": entry.get("decided_by", ""),
        "verdict": entry.get("verdict", ""),
        "tone": entry.get("tone", "duotone" if not entry else ""),
        "hold": "yes" if entry.get("hold") else "",
        "hold_reason": entry.get("hold_reason", ""),
        "description": entry.get("description", ""),
        "caption_hint": entry.get("caption_hint", ""),
        "credit": credit or "",
        "processed_at": stamp, "status": status,
    }


HOLD_TEMPLATE = """CCC DARKROOM — ROUTE-SAFETY HOLD
{roll}

The frames below contain something that could locate a club route — signage,
a junction, a landmark, a business frontage. Their exports are in {holddir}/
and are NOT in the normal publish folders.

This is a human decision. Nothing here is blocked permanently.

{items}

To release a frame:
  1. open _curation.json in the roll's source folder (or _originals)
  2. set  "hold": false  on that frame
  3. re-run:  python3 ~/CCC/Darkroom/bin/darkroom.py --force

To keep the hold, do nothing — the files stay in {holddir}/ and out of the way.
"""


def write_hold_notice(path: Path, roll_key: str, rows: list[dict]) -> None:
    items = "\n".join(
        f"  {r['stem']}  ({r['source_file']})"
        + (f"  [{r['credit']}]" if r.get("credit") else "") + "\n"
        f"      reason: {r['hold_reason'] or 'unspecified'}\n"
        f"      subject: {r['description'] or '—'}"
        for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A merged roll is written once per source folder. Appending keeps the
    # first folder's holds instead of silently replacing them with the
    # second's — a held frame that vanishes from the notice is a held frame
    # nobody reviews.
    if path.exists():
        # --force re-renders frames already in the ledger, so appending blindly
        # would stack the same holds on every run.
        existing = path.read_text(encoding="utf-8")
        fresh = [blk for blk in items.split("\n\n")
                 if blk.strip() and blk.split("\n")[0].strip() not in existing]
        if fresh:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + "\n\n".join(fresh) + "\n")
        return
    path.write_text(HOLD_TEMPLATE.format(roll=roll_key, holddir=HOLD_DIRNAME,
                                         items=items))


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
    if path.exists():
        return
    path.write_text(DECISIONS_TEMPLATE.format(roll=roll_key))


MANIFEST_FIELDS = ["sequence", "roll", "date", "source_file", "stem", "orientation",
                   "variants", "variant_count", "tech_flag", "tech_note",
                   "mean_luma", "variance", "shadow_frac", "highlight_frac",
                   "sharpness", "curated", "decided_by", "verdict", "tone",
                   "hold", "hold_reason", "description", "caption_hint",
                   "credit", "processed_at", "status"]


def write_manifest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    if exists:
        with path.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        if header and header != MANIFEST_FIELDS:
            # An older manifest has fewer columns. Rolls are merged and can
            # grow days later, so simply starting a new file would leave the
            # earlier photographers' rows orphaned in a sidecar nobody reads.
            # Migrate instead: keep every existing row, fill the new columns
            # blank, and rewrite under the current header.
            try:
                with path.open(newline="", encoding="utf-8") as fh:
                    old_rows = list(csv.DictReader(fh))
            except OSError:
                old_rows = []
            path.replace(path.with_suffix(".pre-migration.csv"))
            with path.open("w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
                wr.writeheader()
                for r in old_rows:
                    wr.writerow({k: r.get(k, "") for k in MANIFEST_FIELDS})
            LOG.info("manifest migrated to the current columns — %d existing "
                     "row(s) kept, previous file saved as %s",
                     len(old_rows), path.with_suffix(".pre-migration.csv").name)
            exists = True
    with path.open("a", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        if not exists:
            wr.writeheader()
        wr.writerows(rows)


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="CCC film processor v3")
    ap.add_argument("--inbox", type=Path, default=INBOX)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", type=str, default="", help="comma-separated preset names")
    ap.add_argument("--ignore-curation", action="store_true",
                    help="ignore any _curation.json and use v2 orientation rules")
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

    dirs = [d for d in sorted(inbox.iterdir()) if is_roll_dir(d)]
    if not dirs:
        LOG.info("nothing to do")
        return 0

    # One bad roll must not abort the cycle. Before this, an OSError on the
    # first roll's listing killed the whole run and the second roll was never
    # attempted, even though it was perfectly readable.
    total, failed = 0, []
    for d in dirs:
        try:
            total += process_roll(d, a.dry_run, a.force, only, a.ignore_curation)
        except Exception as exc:                        # noqa: BLE001
            LOG.error("[%s] roll failed, left in place for the next cycle: %s",
                      d.name, exc)
            failed.append(d.name)
    LOG.info("done — %d frame(s)%s", total,
             f", {len(failed)} roll(s) deferred: {', '.join(failed)}" if failed else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
