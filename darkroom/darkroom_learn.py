#!/usr/bin/env python3
"""
CCC DARKROOM — learner
======================
Joins your recorded keep/cut decisions to the measurements Darkroom took at
processing time, then:

  1. calibrates the advisory thresholds so they predict your actual judgement
  2. flags export presets you never publish, so they stop being generated
  3. reports patterns worth changing at the camera, not in the pipeline

Writes ~/CCC/Darkroom/calibration.json, which darkroom.py reads on its next
run. Also writes a plain-text report per run.

    python3 darkroom_learn.py                 # analyse and report
    python3 darkroom_learn.py --write         # also update calibration.json
    python3 darkroom_learn.py --min-frames 40 # lower the confidence bar

What this is
------------
A measurement loop. It calibrates parameters against ground truth you supply
and surfaces correlations. It converges on your standards over a handful of
rolls and then holds steady — the gains are front-loaded, not compounding.

What this is not
----------------
It does not learn taste, rank pictures, or decide what to publish. With ~36
frames per roll there is nowhere near enough data for that, and it would be
the wrong thing to automate regardless.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

LOG = logging.getLogger("darkroom-learn")

HOME = Path.home()
PHOTOS = HOME / "Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
CALIBRATION = HOME / "CCC" / "Darkroom" / "calibration.json"
REPORT_DIR = HOME / "CCC" / "Darkroom" / "reports"

# Don't recommend anything until there's enough evidence.
MIN_FRAMES_DEFAULT = 60
MIN_ROLLS = 2
# A preset must be this unused, over this many opportunities, before suggesting
# it be switched off.
PRESET_UNUSED_AFTER = 40

DEFAULT_THRESH = {
    "blank_variance": 40.0,
    "shadow_clip": 0.92,
    "highlight_clip": 0.92,
    "soft_sharpness": 0.8,
}

NUM_RE = re.compile(r"\d+")


# ===========================================================================
# reading
# ===========================================================================

def parse_decisions(path: Path) -> dict:
    """Read a decisions.txt into sets of frame numbers."""
    out: dict = {"keep": set(), "published": set(), "hold": set(),
                 "formats": {}, "notes": ""}
    if not path.exists():
        return out

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if not val:
            continue

        if key in ("keep", "published", "hold"):
            out[key] = {int(n) for n in NUM_RE.findall(val)}
        elif key == "formats":
            # 7=ig_portrait            single
            # 7=ig_portrait|web        several for one frame
            for tok in val.replace(",", " ").split():
                if "=" not in tok:
                    continue
                a, _, b = tok.partition("=")
                if a.strip().isdigit():
                    out["formats"][int(a)] = [x.strip() for x in b.split("|") if x.strip()]
        elif key == "notes":
            out["notes"] = val
    return out


def load_rolls(root: Path) -> list[dict]:
    """One record per frame, with decisions joined on."""
    frames: list[dict] = []
    if not root.exists():
        LOG.error("photos root not found: %s", root)
        return frames

    for roll_dir in sorted(root.iterdir()):
        if not roll_dir.is_dir() or roll_dir.name.startswith("_"):
            continue
        manifest = roll_dir / "manifest.csv"
        if not manifest.exists():
            continue

        dec = parse_decisions(roll_dir / "decisions.txt")
        decided = bool(dec["keep"] or dec["published"] or dec["hold"])

        with manifest.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    seq = int(row["sequence"])
                except (KeyError, ValueError):
                    continue
                frames.append({
                    "roll": roll_dir.name,
                    "seq": seq,
                    "orientation": row.get("orientation", ""),
                    "tech_flag": row.get("tech_flag", ""),
                    "variants": (row.get("variants") or "").split(),
                    "sharpness": _f(row.get("sharpness")),
                    "variance": _f(row.get("variance")),
                    "mean_luma": _f(row.get("mean_luma")),
                    "shadow_frac": _f(row.get("shadow_frac")),
                    "highlight_frac": _f(row.get("highlight_frac")),
                    "decided": decided,
                    "kept": seq in dec["keep"] or seq in dec["published"],
                    "published": seq in dec["published"],
                    "held": seq in dec["hold"],
                    "formats_used": dec["formats"].get(seq, []),
                })
    return frames


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# analysis
# ===========================================================================

def rate(part: int, whole: int) -> str:
    return f"{part}/{whole}  ({part / whole:.0%})" if whole else "—"


def calibrate_soft(decided: list[dict]) -> tuple[float | None, str]:
    """
    Find the sharpness threshold that best separates cut frames from kept ones.

    Chooses the candidate that maximises correctly-identified cuts while
    keeping false positives (kept frames flagged soft) under 5%.
    """
    pts = [(f["sharpness"], f["kept"]) for f in decided if f["sharpness"] is not None]
    if len(pts) < 25:
        return None, (f"only {len(pts)} decided frames carry a sharpness value; "
                      "need 25 before moving this threshold")

    kept = [s for s, k in pts if k]
    cut = [s for s, k in pts if not k]
    if len(kept) < 10 or len(cut) < 10:
        return None, "need at least 10 kept and 10 cut frames"

    best_t, best_score = None, -1.0
    lo, hi = min(s for s, _ in pts), max(s for s, _ in pts)
    steps = 60
    for i in range(steps + 1):
        t = lo + (hi - lo) * i / steps
        true_pos = sum(1 for s in cut if s < t)
        false_pos = sum(1 for s in kept if s < t)
        if false_pos / len(kept) > 0.05:
            continue
        score = true_pos / len(cut)
        if score > best_score:
            best_score, best_t = score, t

    if best_t is None or best_score <= 0:
        return None, "no threshold separates your cuts from your keeps"

    return round(best_t, 3), (
        f"catches {best_score:.0%} of your cuts with under 5% false positives "
        f"(kept median {statistics.median(kept):.2f}, cut median {statistics.median(cut):.2f})"
    )


def preset_usage(frames: list[dict]) -> tuple[Counter, Counter]:
    built, used = Counter(), Counter()
    for f in frames:
        for v in f["variants"]:
            built[v.split(":")[0]] += 1
        for name in f["formats_used"]:
            used[name] += 1
    return built, used


def analyse(frames: list[dict], min_frames: int) -> dict:
    decided = [f for f in frames if f["decided"]]
    rolls = {f["roll"] for f in frames}
    decided_rolls = {f["roll"] for f in decided}

    report: list[str] = []
    add = report.append

    add("CCC DARKROOM — LEARNING REPORT")
    add(dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    add("")
    add(f"Rolls processed        {len(rolls)}")
    add(f"Rolls with decisions   {len(decided_rolls)}")
    add(f"Frames processed       {len(frames)}")
    add(f"Frames decided         {len(decided)}")
    add("")

    result: dict = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "rolls_analysed": len(rolls),
        "frames_analysed": len(frames),
        "decisions_recorded": len(decided),
        "thresholds": dict(DEFAULT_THRESH),
        "presets_disabled": [],
        "notes": [],
    }

    if len(decided) < min_frames or len(decided_rolls) < MIN_ROLLS:
        add("NOT ENOUGH EVIDENCE YET")
        add("-" * 60)
        add(f"Need at least {min_frames} decided frames across {MIN_ROLLS} rolls")
        add("before changing anything. Keep filling in decisions.txt after each")
        add("roll; nothing is adjusted until the sample supports it.")
        add("")
        add("Defaults remain in force. This is the correct behaviour, not a fault.")
        result["notes"].append("insufficient evidence — defaults retained")
        return {"result": result, "report": "\n".join(report), "ready": False}

    # ---- keep rate by orientation
    add("KEEP RATE BY ORIENTATION")
    add("-" * 60)
    by_o: dict[str, list[dict]] = defaultdict(list)
    for f in decided:
        by_o[f["orientation"] or "unknown"].append(f)
    for o, fs in sorted(by_o.items()):
        k = sum(1 for f in fs if f["kept"])
        add(f"  {o:<12} {rate(k, len(fs))}")
    add("")

    # ---- keep rate by technical flag
    add("KEEP RATE BY TECHNICAL FLAG")
    add("-" * 60)
    by_f: dict[str, list[dict]] = defaultdict(list)
    for f in decided:
        by_f[f["tech_flag"] or "unknown"].append(f)
    for fl, fs in sorted(by_f.items()):
        k = sum(1 for f in fs if f["kept"])
        add(f"  {fl:<14} {rate(k, len(fs))}")
    add("")

    # ---- threshold calibration
    add("THRESHOLD CALIBRATION")
    add("-" * 60)
    new_soft, why = calibrate_soft(decided)
    if new_soft is None:
        add(f"  soft_sharpness   unchanged at {DEFAULT_THRESH['soft_sharpness']}")
        add(f"                   {why}")
    else:
        old = DEFAULT_THRESH["soft_sharpness"]
        result["thresholds"]["soft_sharpness"] = new_soft
        direction = "raised" if new_soft > old else "lowered"
        add(f"  soft_sharpness   {old}  ->  {new_soft}   ({direction})")
        add(f"                   {why}")
        result["notes"].append(f"soft_sharpness {old} -> {new_soft}")
    add("")
    add("  blank / clipping thresholds are physical limits, not preferences —")
    add("  they are not calibrated against taste.")
    add("")

    # ---- preset usage
    built, used = preset_usage(frames)
    published = [f for f in decided if f["published"]]
    with_formats = [f for f in published if f["formats_used"]]
    coverage = len(with_formats) / len(published) if published else 0.0

    add("EXPORT PRESET USAGE")
    add("-" * 60)
    if not used:
        add("  No 'formats:' lines recorded yet. Add them to decisions.txt")
        add("  (e.g. formats: 7=ig_portrait|web) to enable this analysis.")
    else:
        add(f"  Format coverage: {len(with_formats)}/{len(published)} published "
            f"frames have a formats: entry ({coverage:.0%}).")
        add("")
        candidates: list[str] = []
        for name, n_built in built.most_common():
            n_used = used.get(name, 0)
            mark = ""
            if n_built >= PRESET_UNUSED_AFTER and n_used == 0:
                mark = "   <- candidate for disabling"
                candidates.append(name)
            add(f"  {name:<16} built {n_built:>4}   used {n_used:>3}{mark}")

        add("")
        if candidates and coverage >= 0.8:
            add("  RECOMMENDED — to stop generating these, add them to")
            add("  \"presets_disabled\" in calibration.json by hand:")
            add("      " + ", ".join(candidates))
            add("")
            add("  Not applied automatically. An unrecorded use is indistinguishable")
            add("  from no use, and silently stopping a format you need is worse")
            add("  than generating a few files you do not.")
            result["notes"].append("preset candidates: " + ", ".join(candidates))
        elif candidates:
            add(f"  Candidates exist but format coverage is only {coverage:.0%}.")
            add("  Record formats: on more published frames before pruning.")
    add("")

    # ---- shooting feedback
    add("SHOOTING FEEDBACK")
    add("-" * 60)
    land = by_o.get("landscape", [])
    port = by_o.get("portrait", [])
    if land and port:
        lr = sum(1 for f in land if f["kept"]) / len(land)
        pr = sum(1 for f in port if f["kept"]) / len(port)
        if pr > lr * 1.4:
            add(f"  Portrait frames keep at {pr:.0%} vs {lr:.0%} for landscape.")
            add("  Shoot more verticals — they also crop to 4:5 without matting.")
        elif lr > pr * 1.4:
            add(f"  Landscape frames keep at {lr:.0%} vs {pr:.0%} for portrait.")
            add("  Your eye is horizontal. Consider dropping the portrait presets.")
        else:
            add(f"  Landscape {lr:.0%} vs portrait {pr:.0%} — no meaningful difference.")

    mats = sum(1 for f in frames for v in f["variants"] if v.endswith(":mat"))
    crops = sum(1 for f in frames for v in f["variants"] if v.endswith(":crop"))
    if mats + crops:
        add(f"  {mats} variants matted vs {crops} cropped "
            f"({mats / (mats + crops):.0%} matted).")
        if mats > crops:
            add("  Most exports are being matted rather than cropped, which means")
            add("  source frames are the wrong shape for your output formats.")
    held = sum(1 for f in decided if f["held"])
    if held:
        add(f"  {held} frame(s) held for route-safety or consent review.")
    add("")

    add("SCOPE")
    add("-" * 60)
    add("  Calibrated: advisory flag thresholds, preset pruning.")
    add("  Reported:   correlations worth acting on at the camera.")
    add("  Not learnt: which photographs are worth publishing. That stays yours.")

    return {"result": result, "report": "\n".join(report), "ready": True}


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="CCC Darkroom learner")
    ap.add_argument("--photos", type=Path, default=PHOTOS)
    ap.add_argument("--write", action="store_true",
                    help="update calibration.json (otherwise report only)")
    ap.add_argument("--min-frames", type=int, default=MIN_FRAMES_DEFAULT)
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    frames = load_rolls(a.photos)
    if not frames:
        LOG.error("no manifests found under %s", a.photos)
        return 2

    out = analyse(frames, a.min_frames)
    print()
    print(out["report"])
    print()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    (REPORT_DIR / f"learning_{stamp}.txt").write_text(out["report"])

    if a.write:
        CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION.write_text(json.dumps(out["result"], indent=2))
        LOG.info("calibration written to %s", CALIBRATION)
        if not out["ready"]:
            LOG.info("(defaults retained — evidence threshold not met)")
    else:
        LOG.info("report only. Re-run with --write to apply calibration.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
