#!/usr/bin/env python3
"""
CCC Darkroom — photo credit rendering.

Imported by darkroom.py. The handle comes from the dump folder name via the
`_by_` convention; see parse_roll() there.

Design intent: a byline, not a watermark. It should be findable by someone who
wants to know who shot the frame, and ignorable by everyone else.

Decisions and why:

  face      Montserrat Regular. Bebas Neue is the display face — condensed and
            all-caps, built to shout. A credit that shouts is a watermark.
  case      lowercase, as typed. Instagram handles are lowercase; setting
            @DONALREY in caps breaks the handle's own identity.
  position  bottom-left. Right is where Instagram stacks carousel dots and the
            overflow menu, and left-aligned reads as a print byline.
  size      3.2% of the long edge — ~43px on a 1080 feed post. Clamped 14–80px;
            the ceiling is deliberately far above what any preset needs, because
            an earlier 34px clamp silently capped the ratio and made two test
            sizes render identically.

            Sized against the real viewing condition, which took three attempts
            to get right. 1.45% and then 1.8% were both judged at 1:1 or zoomed
            in, where they looked fine. A 1080px feed post renders at roughly
            390pt on a phone — a 0.36x downscale — and at that size both were
            illegible. **Evaluate at phone scale, not at 1:1.**
  colour    Ink #1A1812 / Oat #ECE6D6, chosen per frame by sampling the
            luminance under the text. Fixed-colour credits disappear against
            skies. This is the same principle as the rest of the pipeline:
            measure, then decide.
  opacity   97% on dark ground, 95% on light. Near-solid: against photographic
            detail, transparency reads as "smudge" rather than "restraint".
            Restraint comes from size and placement, not from fading the ink.
  tracking  +0.06em. At this size, slight positive tracking reads as
            deliberate rather than cramped.
  halo      a tight, strong blurred copy of the text in the opposite colour,
            sitting under it — not an offset drop shadow, which at this size
            overlaps the letterform and reads embossed. Radius is deliberately
            small (0.14em): a wide soft halo blurs the strokes it is meant to
            separate. A scrim plate behind the text was tried and rejected —
            the visible rectangle is more intrusive than the halo it replaces.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

INK = (26, 24, 18)
OAT = (236, 230, 214)

SIZE_RATIO = 0.032
SIZE_MIN, SIZE_MAX = 14, 80
FACE = "BebasNeue-Regular.ttf"
INSET_RATIO = 0.032
TRACKING_EM = 0.06

OPACITY_ON_DARK = 0.97
OPACITY_ON_LIGHT = 0.95
HALO_OPACITY = 0.85
# Busy ground needs more separation than flat ground. Measured, not guessed.
HALO_BUSY_BOOST = 0.05
HALO_RADIUS_EM = 0.14
BUSY_STDDEV = 42.0

# Instagram's UI covers roughly the bottom eighth of a story. Anything placed
# in the standard position there is simply not visible.
INSET_OVERRIDE = {
    "ig_story": 0.155,
}


def _font(px: int) -> ImageFont.FreeTypeFont:
    here = Path(__file__).resolve().parent
    for cand in (here / "fonts" / FACE, here / FACE):
        if cand.exists():
            return ImageFont.truetype(str(cand), px)
    return ImageFont.load_default()


def _tracked_width(draw, text: str, font, tracking: float) -> int:
    w = sum(draw.textlength(ch, font=font) for ch in text)
    return int(w + tracking * max(0, len(text) - 1))


def _draw_tracked(draw, xy, text: str, font, fill, tracking: float) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def draw_credit(img: Image.Image, handle: str, preset: str = "") -> Image.Image:
    """Return a copy of img with the credit rendered bottom-left.

    Never mutates the input. Returns the original unchanged if handle is falsy,
    so callers can pass through unconditionally.
    """
    if not handle:
        return img

    out = img.convert("RGB")
    W, H = out.size
    long_edge = max(W, H)

    px = int(round(long_edge * SIZE_RATIO))
    px = max(SIZE_MIN, min(SIZE_MAX, px))
    font = _font(px)
    tracking = px * TRACKING_EM

    inset_ratio = INSET_OVERRIDE.get(preset, INSET_RATIO)
    inset = int(round(long_edge * inset_ratio))
    # A square-ish frame gets a visually larger side inset from a long-edge
    # ratio than a wide one; clamp so the mark never drifts toward centre.
    inset_x = min(inset, int(W * 0.09))
    inset_y = min(inset, int(H * 0.09)) if preset not in INSET_OVERRIDE else inset

    probe = ImageDraw.Draw(out)
    text_w = _tracked_width(probe, handle, font, tracking)
    ascent, descent = font.getmetrics()
    text_h = ascent + descent

    x = inset_x
    y = H - inset_y - text_h

    # Sample what the text will actually sit on, with a little margin, and let
    # the ground decide the mark's colour.
    pad = px // 2
    box = (max(0, x - pad), max(0, y - pad),
           min(W, x + text_w + pad), min(H, y + text_h + pad))
    patch = out.crop(box).convert("L")
    patch = patch.resize((max(1, patch.width // 8), max(1, patch.height // 8)))
    px_vals = list(patch.getdata())
    n = len(px_vals) or 1
    mean_luma = sum(px_vals) / n
    var = sum((v - mean_luma) ** 2 for v in px_vals) / n
    stddev = var ** 0.5

    on_light = mean_luma > 128
    colour = INK if on_light else OAT
    halo = OAT if on_light else INK
    alpha = OPACITY_ON_LIGHT if on_light else OPACITY_ON_DARK
    # Dappled light is the case that defeats a flat credit: parts of the text
    # land on highlight, parts on shadow, and one colour cannot serve both.
    # More shadow is less intrusive than a scrim over the photograph.
    halo_alpha = HALO_OPACITY + (HALO_BUSY_BOOST if stddev > BUSY_STDDEV else 0.0)
    halo_alpha = min(0.9, halo_alpha)

    # Halo: draw the text into a mask, blur it, and lay it down in the
    # opposite colour. Because it never sits *beside* the strokes the way an
    # offset shadow does, the letterforms stay clean at small sizes.
    mask = Image.new("L", out.size, 0)
    _draw_tracked(ImageDraw.Draw(mask), (x, y), handle, font, 255, tracking)
    radius = max(1.0, px * HALO_RADIUS_EM)
    halo_mask = mask.filter(ImageFilter.GaussianBlur(radius))
    halo_mask = halo_mask.point(lambda v: min(255, int(v * 2.2 * halo_alpha)))

    layer = Image.new("RGBA", out.size, halo + (0,))
    layer.putalpha(halo_mask)
    out = Image.alpha_composite(out.convert("RGBA"), layer)

    text_layer = Image.new("RGBA", out.size, colour + (0,))
    text_layer.putalpha(mask.point(lambda v: int(v * alpha)))
    out = Image.alpha_composite(out, text_layer).convert("RGB")
    return out
