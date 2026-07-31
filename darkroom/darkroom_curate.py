#!/usr/bin/env python3
"""
CCC DARKROOM — curator
======================
The judgment pass darkroom.py deliberately does not have.

Two layers, in order:

  MEASUREMENT (local, free, always runs)
      macOS Vision: attention saliency, people, faces, legible text, scene
      classification. Plus exposure/sharpness/saturation statistics.

  JUDGMENT (optional, costs tokens, degrades cleanly)
      An LLM sees the photograph itself alongside those measurements and
      makes the editorial calls: is this worth publishing, which platforms
      suit THIS picture, colour or brand duotone, does it need a
      route-safety hold.

The split matters. The model proposes formats; the geometry code then
verifies each one can actually frame the subject before it is accepted, so a
confident-but-wrong suggestion still cannot produce a butchered crop. And
route-safety HOLD is a union, never an override — if either layer raises it,
it stands.

Writes `_curation.json` next to the frames. darkroom.py (v3) reads it. The
file is plain JSON and meant to be edited; your override beats both layers.

    python3 darkroom_curate.py --selftest
    python3 darkroom_curate.py                     # newest roll in _dump
    python3 darkroom_curate.py --dir <folder>
    python3 darkroom_curate.py --llm off           # measurement only
    python3 darkroom_curate.py --no-cache          # re-judge everything

SETUP
-----
  Measurement:  python3 -m pip install --user \
                    pyobjc-framework-Vision pyobjc-framework-Quartz
  Judgment:     put an Anthropic API key in  ~/CCC/Darkroom/.anthropic_key
                (or export ANTHROPIC_API_KEY). Then set MODEL below to one
                of the IDs that `--selftest` prints for your account.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageOps, ImageStat
except ImportError:
    sys.exit("Pillow is required:  python3 -m pip install --user Pillow")

HOME = Path.home()
DRIVE = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
INBOX = DRIVE / "_dump"
DARKROOM = HOME / "CCC" / "Darkroom"
KEY_FILE = DARKROOM / ".anthropic_key"
CACHE_DIR = DARKROOM / ".state" / "llm_cache"

SOURCE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp"}
PLAN_NAME = "_curation.json"

# Directories that are never rolls. `xArchive` is the convention for a bin of
# test material staged for real deletion later — processing it would resurrect
# work that was deliberately thrown away. Compared case-insensitively.
NOT_A_ROLL = {"xarchive"}


def is_roll_dir(p) -> bool:
    return p.is_dir() and not p.name.startswith(".") and p.name.lower() not in NOT_A_ROLL


# Model used for the judgment layer. Run --selftest to list what your account
# actually has, then set this to one of those IDs.
MODEL = "claude-sonnet-5"
LLM_MAX_EDGE = 1024          # downscale before upload; keeps cost low
LLM_TIMEOUT = 90
# Bump whenever PROMPT or the judgment tool schema changes, so cached
# verdicts made under the old criteria are not silently reused.
PROMPT_VERSION = 5

# Target ratios, mirrored from darkroom.py. None = fit, no ratio constraint.
FORMAT_RATIO = {
    "ig_portrait": 1080 / 1350,
    "ig_square": 1.0,
    "ig_landscape": 1080 / 566,
    "ig_story": 1080 / 1920,
    "strava": None,
    "email_body": None,
    "email_header": 2.0,
    "web": None,
    "web_hero": 2.4,
}
CROP_FORMATS = [k for k, v in FORMAT_RATIO.items() if v is not None]
FIT_FORMATS = ["web", "strava", "email_body"]

FORMAT_PURPOSE = {
    "ig_portrait": "Instagram feed 4:5 — the highest-impact feed slot, best for a single rider or tight group",
    "ig_square": "Instagram feed 1:1 — safe general-purpose feed post",
    "ig_landscape": "Instagram feed 1.91:1 — wide scenery, peloton strung out, start lines",
    "ig_story": "Story/Reels 9:16 — ephemeral, whole frame preserved on brand field",
    "strava": "Strava post — ride context, route and terrain",
    "email_body": "Mailchimp newsletter body image",
    "email_header": "Mailchimp header banner 2:1 — needs a wide, uncluttered subject",
    "web": "Website standard image",
    "web_hero": "Website hero band 2.4:1 — very wide, subject must survive extreme letterboxing",
}

# How much of the subject must survive a crop for that format to be offered.
# This only makes sense for a *localised* subject: if the subject box sprawls
# across most of the frame then "the subject" is the whole scene, no crop can
# preserve it, and demanding 92% would veto every ratio. So the requirement
# relaxes as the subject grows.
MIN_SUBJECT_COVERAGE = 0.92      # tight subject — protect it hard
MIN_SUBJECT_COVERAGE_WIDE = 0.55  # subject fills the frame — cropping is fine
SUBJECT_AREA_TIGHT = 0.40        # at or below this, use the strict figure
SUBJECT_AREA_SPRAWL = 0.85       # at or above this, use the relaxed figure
SUBJECT_PAD = 0.12
# How far under the floor still counts as a near miss worth rescuing.
NEAR_MISS_SLACK = 0.15


def coverage_floor(area_frac: float) -> float:
    """Required subject coverage, eased by how much of the frame it occupies."""
    if area_frac <= SUBJECT_AREA_TIGHT:
        return MIN_SUBJECT_COVERAGE
    if area_frac >= SUBJECT_AREA_SPRAWL:
        return MIN_SUBJECT_COVERAGE_WIDE
    t = (area_frac - SUBJECT_AREA_TIGHT) / (SUBJECT_AREA_SPRAWL - SUBJECT_AREA_TIGHT)
    return MIN_SUBJECT_COVERAGE + t * (MIN_SUBJECT_COVERAGE_WIDE - MIN_SUBJECT_COVERAGE)

# Quality gates calibrated to actual photographs, unlike the shipped
# darkroom.py values which can never fire.
Q_BLANK_VAR = 300.0
Q_SOFT_SHARP = 3.0
Q_DARK_SHADOW = 0.35
Q_BLOWN_HIGHLIGHT = 0.18
SAT_DUOTONE_MAX = 28.0


# ===========================================================================
# backend: macOS Vision
# ===========================================================================

class VisionBackend:
    name = "vision"

    def __init__(self):
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401
        self.Vision = Vision
        self.NSURL = NSURL

    def _run(self, url, request):
        handler = self.Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Vision request failed: {err}")
        return request.results() or []

    @staticmethod
    def _flip(rect):
        """VNRectangle -> (x0, y0, x1, y1) normalised, origin TOP-left."""
        o, s = rect.origin, rect.size
        return (o.x, 1.0 - (o.y + s.height), o.x + s.width, 1.0 - o.y)

    def analyse(self, path: Path) -> dict:
        V = self.Vision
        url = self.NSURL.fileURLWithPath_(str(path))
        out: dict = {}

        try:
            req = V.VNGenerateAttentionBasedSaliencyImageRequest.alloc().init()
            res = self._run(url, req)
            boxes = []
            if res:
                for so in (res[0].salientObjects() or []):
                    boxes.append(self._flip(so.boundingBox()))
            out["saliency_boxes"] = boxes
        except Exception as exc:
            out["saliency_boxes"] = []
            out.setdefault("warnings", []).append(f"saliency: {exc}")

        for key, cls in (("humans", "VNDetectHumanRectanglesRequest"),
                         ("faces", "VNDetectFaceRectanglesRequest")):
            try:
                req = getattr(V, cls).alloc().init()
                out[key] = [self._flip(o.boundingBox()) for o in self._run(url, req)]
            except Exception as exc:
                out[key] = []
                out.setdefault("warnings", []).append(f"{key}: {exc}")

        try:
            req = V.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(0)
            req.setUsesLanguageCorrection_(True)
            found = []
            for o in self._run(url, req):
                cands = o.topCandidates_(1)
                if cands and len(cands):
                    c = cands[0]
                    if c.confidence() >= 0.4 and len(c.string().strip()) >= 3:
                        found.append({"text": c.string().strip(),
                                      "confidence": round(float(c.confidence()), 3)})
            out["text"] = found
        except Exception as exc:
            out["text"] = []
            out.setdefault("warnings", []).append(f"text: {exc}")

        try:
            req = V.VNClassifyImageRequest.alloc().init()
            tags = [{"tag": str(o.identifier()), "confidence": round(float(o.confidence()), 3)}
                    for o in self._run(url, req) if float(o.confidence()) >= 0.15]
            out["scene"] = sorted(tags, key=lambda t: -t["confidence"])[:8]
        except Exception as exc:
            out["scene"] = []
            out.setdefault("warnings", []).append(f"scene: {exc}")

        return out


class BasicBackend:
    name = "basic"
    GRID = 96

    def analyse(self, path: Path) -> dict:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            g = ImageOps.grayscale(im).resize((self.GRID, self.GRID), Image.BILINEAR)
        px = list(g.tobytes())
        N = self.GRID
        energy = [0.0] * (N * N)
        for y in range(N):
            for x in range(N):
                i = y * N + x
                v = px[i]
                dx = abs(px[i + 1] - v) if x + 1 < N else 0
                dy = abs(px[i + N] - v) if y + 1 < N else 0
                cx, cy = (x - N / 2) / (N / 2), (y - N / 2) / (N / 2)
                energy[i] = (dx + dy) * (1.0 - 0.45 * (cx * cx + cy * cy) ** 0.5)
        thresh = sorted(energy)[int(len(energy) * 0.75)]
        xs = [x for y in range(N) for x in range(N) if energy[y * N + x] >= thresh]
        ys = [y for y in range(N) for x in range(N) if energy[y * N + x] >= thresh]
        box = ((min(xs) / N, min(ys) / N, (max(xs) + 1) / N, (max(ys) + 1) / N)
               if xs else (0.2, 0.2, 0.8, 0.8))
        return {"saliency_boxes": [box], "humans": [], "faces": [], "text": [],
                "scene": [], "warnings": ["basic backend: no people/text/scene detection"]}


def make_backend(prefer: str):
    if prefer in ("auto", "vision"):
        try:
            return VisionBackend()
        except Exception as exc:
            if prefer == "vision":
                sys.exit(f"Vision backend unavailable: {exc}\nInstall:  python3 -m pip "
                         "install --user pyobjc-framework-Vision pyobjc-framework-Quartz")
            print(f"note: Vision unavailable ({type(exc).__name__}) — using basic",
                  file=sys.stderr)
    return BasicBackend()


# ===========================================================================
# measurement
# ===========================================================================

def measure(im: Image.Image) -> dict:
    small = ImageOps.grayscale(im).resize((256, 256), Image.BILINEAR)
    px = list(small.tobytes())
    n = len(px)
    mean = sum(px) / n
    var = sum((p - mean) ** 2 for p in px) / n
    shadow = sum(1 for p in px if p < 24) / n
    highlight = sum(1 for p in px if p > 240) / n
    edges = sum(abs(px[i + 1] - px[i]) for i in range(n - 1) if (i + 1) % 256)
    rgb = im.convert("RGB").resize((128, 128), Image.BILINEAR)
    return {
        "mean_luma": round(mean, 1),
        "variance": round(var, 1),
        "shadow_frac": round(shadow, 4),
        "highlight_frac": round(highlight, 4),
        "sharpness": round(edges / n, 3),
        "saturation": round(ImageStat.Stat(rgb.convert("HSV")).mean[1], 1),
    }


def union(boxes: list):
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def pad_box(b: tuple, pad: float) -> tuple:
    w, h = b[2] - b[0], b[3] - b[1]
    return (max(0.0, b[0] - w * pad), max(0.0, b[1] - h * pad),
            min(1.0, b[2] + w * pad), min(1.0, b[3] + h * pad))


def window_for(iw: int, ih: int, ratio: float, box: tuple):
    """Smallest window of `ratio` centred on the subject, clamped to the image."""
    sx0, sy0, sx1, sy1 = box[0] * iw, box[1] * ih, box[2] * iw, box[3] * ih
    sw, sh = max(1.0, sx1 - sx0), max(1.0, sy1 - sy0)
    if sw / sh < ratio:
        nw, nh = sh * ratio, sh
    else:
        nw, nh = sw, sw / ratio
    if nw > iw:
        nw, nh = float(iw), iw / ratio
    if nh > ih:
        nh, nw = float(ih), ih * ratio
    cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    x0 = min(max(cx - nw / 2, 0.0), iw - nw)
    y0 = min(max(cy - nh / 2, 0.0), ih - nh)
    x1, y1 = x0 + nw, y0 + nh
    inter = (max(0.0, min(x1, sx1) - max(x0, sx0)) *
             max(0.0, min(y1, sy1) - max(y0, sy0)))
    return ((x0 / iw, y0 / ih, x1 / iw, y1 / ih), inter / (sw * sh))


# ===========================================================================
# layer 1 — heuristic judgment (always runs; the floor if the LLM is off)
# ===========================================================================

SIGNAGE_HINTS = ("st", "rd", "ave", "route", "rt", "trail", "road", "lane",
                 "km", "mi", "exit", "welcome", "town", "village", "county")


def heuristic_judge(m: dict, a: dict, iw: int, ih: int) -> dict:
    humans = a.get("humans") or []
    faces = a.get("faces") or []
    people = humans + faces
    # Vision reports a person and their face as separate detections, so a
    # single rider reads as two. Count the larger of the two lists rather
    # than the sum — the union of boxes is still correct for framing.
    people_count = max(len(humans), len(faces))
    subj = union(people) or union(a.get("saliency_boxes") or []) or (0.15, 0.15, 0.85, 0.85)
    subj = pad_box(subj, SUBJECT_PAD)
    reasons = []

    verdict, why = "keep", []
    if m["variance"] < Q_BLANK_VAR:
        verdict, why = "reject", ["near-uniform frame"]
    elif m["sharpness"] < Q_SOFT_SHARP:
        verdict, why = "review", [f"soft (sharpness {m['sharpness']})"]
    elif m["shadow_frac"] > Q_DARK_SHADOW:
        verdict, why = "review", [f"{m['shadow_frac']:.0%} crushed shadow"]
    elif m["highlight_frac"] > Q_BLOWN_HIGHLIGHT:
        verdict, why = "review", [f"{m['highlight_frac']:.0%} blown highlight"]

    sw = (subj[2] - subj[0]) * iw
    sh = (subj[3] - subj[1]) * ih
    s_ratio = sw / max(1.0, sh)
    reasons.append(f"{people_count} person/people detected" if people
                   else "no people — saliency subject")

    if s_ratio < 0.85:
        shape, wanted = "upright", ["ig_portrait", "ig_story", "ig_square"]
    elif s_ratio > 1.6:
        shape, wanted = "wide", ["ig_landscape", "web_hero", "email_header", "ig_square"]
    else:
        shape, wanted = "square-ish", ["ig_square", "ig_portrait", "ig_landscape"]
    reasons.append(f"subject is {shape} ({s_ratio:.2f}:1)")

    # Only *locational* text raises a hold. Holding on any legible text at all
    # fires on kit logos, bottle labels, distant shopfronts and cap embroidery
    # — which meant nearly every frame got held, and a flag that fires on
    # everything is a flag nobody reads. Route-safety has to stay meaningful.
    texts = [t["text"] for t in (a.get("text") or [])]
    hits = [t for t in texts if any(re.search(rf"\b{h}\b", t.lower())
                                    for h in SIGNAGE_HINTS)]
    hold = bool(hits)
    hold_why = f"locational text detected: {hits[:3]}" if hits else ""

    if m["saturation"] <= SAT_DUOTONE_MAX:
        tone, tone_why = "duotone", f"already near-monochrome (sat {m['saturation']})"
    elif m["variance"] > 3500 and m["saturation"] < 90:
        tone, tone_why = "duotone", "strong tonal structure — carries the brand axis"
    elif m["variance"] > 2200 and m["saturation"] < 110:
        tone, tone_why = "both", "structure and colour both plausible — render each"
    else:
        tone, tone_why = "colour", f"colour is doing the work (sat {m['saturation']})"

    return {"verdict": verdict, "verdict_note": "; ".join(why), "subject_box": list(subj),
            "subject_shape": shape, "wanted": wanted, "people": people_count,
            "tone": tone, "tone_reason": tone_why, "hold": hold, "hold_reason": hold_why,
            "reasoning": reasons}


# ===========================================================================
# layer 2 — LLM judgment
# ===========================================================================

JUDGMENT_TOOL = {
    "name": "record_judgment",
    "description": "Record the editorial judgment for this photograph.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject_description": {
                "type": "string",
                "description": "One sentence: what is actually happening in this photograph."},
            "publish_verdict": {
                "type": "string", "enum": ["keep", "review", "reject"],
                "description": "A TECHNICAL judgment only. keep = photographically "
                               "sound. review = a real technical flaw worth a human "
                               "look (soft focus, blown highlights, heavy motion "
                               "blur, badly cut-off framing). reject = technically "
                               "unusable and unrecoverable. SUBJECT MATTER IS NEVER "
                               "GROUNDS for review or reject — what the photograph "
                               "is of has no bearing on this field."},
            "verdict_reason": {"type": "string"},
            "best_formats": {
                "type": "array", "items": {"type": "string", "enum": list(FORMAT_RATIO)},
                "description": "Only formats that genuinely suit THIS photograph. "
                               "Two to four is normal. Do not list everything."},
            "format_reason": {"type": "string"},
            "tone": {
                "type": "string", "enum": ["colour", "duotone", "both"],
                "description": "duotone = the CCC brand film treatment (ink-to-oat "
                               "monochrome), applied to a colour original. colour = "
                               "keep the original colour. both = a genuinely "
                               "borderline frame; render each format twice so the "
                               "owner can compare and pick."},
            "tone_reason": {"type": "string"},
            "route_safety_hold": {
                "type": "boolean",
                "description": "True if anything identifies a specific location: road "
                               "signs, junction names, trailheads, distinctive landmarks, "
                               "house numbers, business frontage."},
            "hold_reason": {"type": "string"},
            "caption_hint": {"type": "string",
                             "description": "A short factual caption the club could use."},
        },
        "required": ["subject_description", "publish_verdict", "verdict_reason",
                     "best_formats", "format_reason", "tone", "tone_reason",
                     "route_safety_hold", "hold_reason", "caption_hint"],
    },
}

PROMPT = """You are curating photographs for the Catskills Cycling Club, a \
small nonprofit. These are documentary photographs of real club members on \
real rides — not stock imagery, not advertising. The club publishes them to \
its own channels.

Judge THIS photograph on its own merits. Your job is the call the processing \
pipeline cannot make: whether it is technically sound, which channels its \
composition genuinely suits, and whether it carries the club's monochrome film \
treatment or needs its colour.

You are NOT the picture editor. Whether a given subject belongs in the club's \
feed is the owner's decision, made later from the contact sheet — never yours.

Available formats and what each is for:
{formats}

Guidance:
- Be selective with best_formats. A photograph that works everywhere usually \
works nowhere in particular. Two to four is normal.
- Only choose a wide format (ig_landscape, web_hero, email_header) when the \
picture genuinely reads wide — scenery, a strung-out group, a start line.
- Only choose ig_portrait when there is an upright subject worth filling the \
frame with.
- The duotone is the club's house treatment and it should be a live option on \
every roll, not a rarity. Do not default to colour simply because the original \
is in colour — every source here is. Ask instead: what is this photograph \
actually made of? Reach for duotone when the picture is carried by light, \
form, texture or gesture — directional or raking light, weather, mist, wet \
road, strong shadow, silhouette, grain and surface, a rider's posture or \
effort, an empty road as shape. Those get stronger without colour, because \
stripping it removes the distraction and leaves the structure.
- Haze, mist, flat overcast light, rain, and distant atmospheric layering are \
strong duotone candidates specifically: the colour in such frames is usually a \
weak blue-grey cast that adds nothing, while the tonal separation between \
layers IS the photograph. Do not read "it is a landscape" as "it must stay \
colour".
- Choose colour when colour is doing real work that monochrome would destroy: \
club kit as identifiable club identity, autumn foliage, low sun warmth, a \
striking sky, food and drink, anything where the hue IS the subject.
- Use "both" when you genuinely cannot call it — a frame with strong structure \
AND meaningful colour. Do not use "both" to avoid deciding; reserve it for \
real ties. Roughly speaking, a mixed roll of ordinary ride photographs should \
not come back entirely one way.
- route_safety_hold is a safety call, not an aesthetic one. Set it true only \
when something in the frame would let a stranger find a specific place: a \
readable road sign or junction name, a trailhead marker, a street address, a \
named business frontage, or an unmistakable named landmark. Generic scenery — \
a river, woodland, an anonymous stretch of road, a distant ridge — does not \
locate anything and is not a hold. Club kit, cap and bottle branding are not \
locational either. Holding everything is the same as holding nothing, so \
reserve it for frames a stranger could actually navigate to.
- publish_verdict judges craft, not content. Focus, exposure, motion blur, \
horizon, obstructions, badly clipped framing. Be honest about those — a soft \
frame is a review, and saying so is more useful than being generous.
- Never let subject matter influence publish_verdict. A cafe table, a parked \
bike, an empty road, a sign, a dog, an empty landscape with no rider in it: \
all are legitimate club material. If it is well made, it is a keep, whatever \
it happens to show. Put what it depicts in subject_description instead, so \
the owner can judge relevance for themselves.

Local image analysis already performed (measurements, not opinions):
{facts}

Look at the photograph and record your judgment."""


def api_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k.strip()
    if KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
        if k:
            return k
    return None


def api_post(path: str, key: str, payload: dict | None, method="POST") -> dict:
    req = urllib.request.Request(
        f"https://api.anthropic.com{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        return json.loads(r.read())


def encode_image(im: Image.Image) -> str:
    im = im.convert("RGB")
    scale = LLM_MAX_EDGE / max(im.size)
    if scale < 1.0:
        im = im.resize((max(1, round(im.width * scale)),
                        max(1, round(im.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def llm_judge(im: Image.Image, facts: dict, key: str, use_cache: bool) -> dict | None:
    b64 = encode_image(im)
    digest = hashlib.sha1(
        (b64[:4096] + json.dumps(facts, sort_keys=True) + MODEL
         + str(PROMPT_VERSION)).encode()).hexdigest()[:20]
    cache = CACHE_DIR / f"{digest}.json"
    if use_cache and cache.exists():
        try:
            out = json.loads(cache.read_text())
            out["_cached"] = True
            return out
        except json.JSONDecodeError:
            pass

    prompt = PROMPT.format(
        formats="\n".join(f"  {k}: {v}" for k, v in FORMAT_PURPOSE.items()),
        facts=json.dumps(facts, indent=2))
    payload = {
        "model": MODEL, "max_tokens": 1024,
        "tools": [JUDGMENT_TOOL],
        "tool_choice": {"type": "tool", "name": "record_judgment"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": prompt}]}],
    }
    resp = api_post("/v1/messages", key, payload)
    for block in resp.get("content", []):
        if block.get("type") == "tool_use":
            out = dict(block["input"])
            out["_cached"] = False
            usage = resp.get("usage", {})
            out["_tokens"] = [usage.get("input_tokens"), usage.get("output_tokens")]
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out))
            return out
    return None


# ===========================================================================
# merge — geometry has the final say on viability; HOLD is a union
# ===========================================================================

def merge(h: dict, l: dict | None, iw: int, ih: int) -> dict:
    subj = tuple(h["subject_box"])
    wanted = list(h["wanted"])
    verdict, vnote = h["verdict"], h["verdict_note"]
    tone, tone_why = h["tone"], h["tone_reason"]
    hold, hold_why = h["hold"], h["hold_reason"]
    layer = "heuristic"
    desc = caption = ""

    if l:
        layer = "llm+heuristic"
        desc = l.get("subject_description", "")
        caption = l.get("caption_hint", "")
        picked = [f for f in l.get("best_formats", []) if f in FORMAT_RATIO]
        if picked:
            wanted = [f for f in picked if f in CROP_FORMATS]
        # A reject from either layer stands; the model may downgrade but the
        # measured floor is never overridden upward.
        order = {"reject": 0, "review": 1, "keep": 2}
        lv = l.get("publish_verdict", "keep")
        if order.get(lv, 2) < order.get(verdict, 2):
            verdict = lv
            vnote = l.get("verdict_reason", "")
        elif verdict == "keep":
            vnote = l.get("verdict_reason", vnote)
        ltone = l.get("tone")
        if ltone in ("colour", "duotone", "both"):
            if "both" in (ltone, tone):
                tone = "both"
                tone_why = l.get("tone_reason", tone_why)
            elif ltone != tone:
                # Measurement and judgment disagree. Neither is authoritative
                # on taste, so render both and let the owner's eye settle it
                # — the same union principle that governs HOLD.
                tone_why = (f"layers disagreed (measured {tone}, judged {ltone}: "
                            f"{l.get('tone_reason', '')}) — rendering both")
                tone = "both"
            else:
                tone_why = l.get("tone_reason", tone_why)
        # Safety: union only. The model can raise a hold, never clear one.
        if l.get("route_safety_hold"):
            hold = True
            hold_why = l.get("hold_reason", "") or hold_why

    formats, rejected = {}, {}
    if verdict == "reject":
        rejected["*"] = "frame rejected on quality/composition"
    else:
        area = (subj[2] - subj[0]) * (subj[3] - subj[1])
        floor = coverage_floor(area)
        scored = []
        for name in wanted:
            ratio = FORMAT_RATIO.get(name)
            if ratio is None:
                continue
            win, cov = window_for(iw, ih, ratio, subj)
            scored.append((name, win, cov))
            if cov >= floor:
                formats[name] = {"crop": [round(v, 5) for v in win],
                                 "subject_coverage": round(cov, 3)}
            else:
                rejected[name] = (f"proposed, but would cut {1 - cov:.0%} off the "
                                  f"subject (floor {floor:.0%}) — geometry veto")
        # Never leave a keepable frame with nothing but uncropped formats:
        # admit the least-damaging proposal rather than silently dropping all.
        if not formats and scored:
            name, win, cov = max(scored, key=lambda s: s[2])
            # Only rescue a near miss. If even the best proposal is well under
            # the floor, no ratio suits this photograph and forcing the
            # "least bad" crop just ships the damage we set out to prevent —
            # the uncropped fit formats are the honest answer instead.
            if cov >= floor - NEAR_MISS_SLACK:
                formats[name] = {"crop": [round(v, 5) for v in win],
                                 "subject_coverage": round(cov, 3)}
                rejected.pop(name, None)
                rejected["_note"] = (f"all proposals under the {floor:.0%} floor; "
                                     f"kept {name} as a near miss at {cov:.0%}")
            else:
                rejected["_note"] = (f"no cropped format suits this frame "
                                     f"(best {name} at {cov:.0%}, floor {floor:.0%}) "
                                     f"— uncropped formats only")
        for name in FIT_FORMATS:
            formats.setdefault(name, {"crop": None, "subject_coverage": 1.0})

    return {
        "verdict": verdict, "verdict_note": vnote,
        "description": desc, "caption_hint": caption,
        "subject_box": [round(v, 5) for v in subj],
        "subject_shape": h["subject_shape"], "people_detected": h["people"],
        "formats": formats, "formats_rejected": rejected,
        "tone": tone, "tone_reason": tone_why,
        "hold": hold, "hold_reason": hold_why,
        "decided_by": layer, "reasoning": h["reasoning"],
    }


# ===========================================================================

def curate_dir(folder: Path, backend, key: str | None, use_cache: bool,
               verbose: bool) -> dict:
    srcs = sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SOURCE_EXTS
                  and not p.name.startswith("."))
    if not srcs:
        sys.exit(f"no images in {folder}")

    plan = {"roll": folder.name, "backend": backend.name,
            "judgment": "llm" if key else "heuristic-only",
            "model": MODEL if key else None, "curator_version": 2, "frames": {}}
    spent = [0, 0]

    for src in srcs:
        with Image.open(src) as raw:
            im = ImageOps.exif_transpose(raw)
            im.load()
            im = im.convert("RGB")
        iw, ih = im.size
        m = measure(im)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            tmp = Path(tf.name)
        im.save(tmp, "JPEG", quality=88)
        try:
            a = backend.analyse(tmp)
        finally:
            tmp.unlink(missing_ok=True)

        h = heuristic_judge(m, a, iw, ih)

        l = None
        if key:
            facts = {"pixels": [iw, ih], "metrics": m,
                     "people_detected": h["people"],
                     "measured_tone_suggestion": h["tone"],
                     "measured_tone_reason": h["tone_reason"],
                     "legible_text": [t["text"] for t in (a.get("text") or [])][:12],
                     "scene_tags": [t["tag"] for t in (a.get("scene") or [])[:6]],
                     "subject_box_normalised": [round(v, 3) for v in h["subject_box"]]}
            try:
                l = llm_judge(im, facts, key, use_cache)
                if l and l.get("_tokens") and not l.get("_cached"):
                    spent[0] += l["_tokens"][0] or 0
                    spent[1] += l["_tokens"][1] or 0
            except urllib.error.HTTPError as exc:
                body = exc.read().decode()[:300]
                print(f"  !! LLM failed on {src.name}: HTTP {exc.code} {body}",
                      file=sys.stderr)
            except Exception as exc:
                print(f"  !! LLM failed on {src.name}: {exc}", file=sys.stderr)

        entry = merge(h, l, iw, ih)
        entry["source"] = src.name
        entry["pixels"] = [iw, ih]
        entry["metrics"] = m
        entry["scene_tags"] = [t["tag"] for t in (a.get("scene") or [])[:4]]
        if a.get("warnings"):
            entry["warnings"] = a["warnings"]
        plan["frames"][src.name] = entry

        if verbose:
            tag = "cached" if (l or {}).get("_cached") else entry["decided_by"]
            print(f"  {src.name}  [{tag}]")
            if entry["description"]:
                print(f"     {entry['description']}")
            print(f"     verdict {entry['verdict']}  tone {entry['tone']}"
                  f"  hold {entry['hold']}  people {entry['people_detected']}")
            print(f"     formats: {', '.join(entry['formats']) or 'none'}")
            for k, v in entry["formats_rejected"].items():
                print(f"       skipped {k}: {v}")

    plan["tokens"] = {"input": spent[0], "output": spent[1]}
    return plan


def selftest(prefer: str) -> int:
    b = make_backend(prefer)
    print(f"measurement backend : {b.name}")
    if b.name == "basic":
        print("   Vision NOT available. For people/text/scene detection:")
        print("   python3 -m pip install --user pyobjc-framework-Vision "
              "pyobjc-framework-Quartz")
    else:
        print("   saliency, people, faces, text, scene — all available")

    k = api_key()
    print(f"judgment layer      : {'key found' if k else 'NO KEY — heuristic only'}")
    if not k:
        print(f"   put a key in {KEY_FILE}  (chmod 600), or export ANTHROPIC_API_KEY")
    else:
        try:
            data = api_post("/v1/models?limit=100", k, None, method="GET")
            ids = [m["id"] for m in data.get("data", [])]
            print(f"   API reachable. {len(ids)} model(s) available:")
            for i in ids[:15]:
                print(f"     {'->' if i == MODEL else '  '} {i}")
            if MODEL not in ids:
                print(f"   !! MODEL is set to '{MODEL}', which is NOT in that list.")
                print(f"   !! Edit MODEL near the top of this file to one of the above.")
        except urllib.error.HTTPError as exc:
            print(f"   !! API error {exc.code}: {exc.read().decode()[:200]}")
        except Exception as exc:
            print(f"   !! API unreachable: {exc}")

    print(f"drive path exists   : {DRIVE.exists()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CCC Darkroom curator")
    ap.add_argument("--dir", type=Path, default=None)
    ap.add_argument("--backend", choices=["auto", "vision", "basic"], default="auto")
    ap.add_argument("--llm", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest(a.backend)

    folder = a.dir
    if folder is None:
        if not INBOX.is_dir():
            sys.exit(f"no inbox at {INBOX}")
        rolls = [d for d in INBOX.iterdir() if is_roll_dir(d)]
        if not rolls:
            sys.exit(f"no rolls in {INBOX}")
        folder = max(rolls, key=lambda d: d.stat().st_mtime)
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")

    key = None if a.llm == "off" else api_key()
    if a.llm == "on" and not key:
        sys.exit(f"--llm on but no API key found ({KEY_FILE} or ANTHROPIC_API_KEY)")

    backend = make_backend(a.backend)
    if not a.quiet:
        print(f"curating {folder.name}  [measure: {backend.name}  "
              f"judge: {MODEL if key else 'heuristic only'}]")

    plan = curate_dir(folder, backend, key, not a.no_cache, verbose=not a.quiet)
    out = folder / PLAN_NAME
    out.write_text(json.dumps(plan, indent=2))

    if not a.quiet:
        fr = plan["frames"].values()
        print(f"\nwrote {out}")
        print(f"{len(plan['frames'])} frames — "
              f"{sum(1 for f in fr if f['verdict'] == 'keep')} keep, "
              f"{sum(1 for f in fr if f['verdict'] == 'review')} review, "
              f"{sum(1 for f in fr if f['verdict'] == 'reject')} reject, "
              f"{sum(1 for f in fr if f['hold'])} HOLD")
        t = plan.get("tokens", {})
        if t.get("input"):
            print(f"tokens this run: {t['input']} in / {t['output']} out "
                  f"(cached frames cost nothing)")
        print("Edit that file to override anything; the renderer obeys it.")
    if a.json:
        print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
