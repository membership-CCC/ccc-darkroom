#!/bin/bash
# CCC Darkroom — one full unattended cycle: curate, render, contact sheet.
# This is what launchd should run, NOT darkroom.py on its own — the renderer
# alone would silently fall back to v2 orientation rules with no plan present.
BIN="$HOME/CCC/Darkroom/bin"

# Only one cycle at a time. The launchd timer fires every 15 minutes and does
# not care that you are running one by hand: on 2 Aug two cycles overlapped,
# one emptied a dump folder and rmdir'd it, and the other reported that roll as
# failed mid-run. Benign that time, but both processes derive the next sequence
# number from the same state file, so a collision could overwrite exports.
#
# mkdir is atomic on every filesystem this touches; flock is not on macOS.
LOCK="$HOME/CCC/Darkroom/.state/cycle.lock"
mkdir -p "$HOME/CCC/Darkroom/.state"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
    echo "=== another cycle is already running (pid $(cat "$LOCK/pid")) — skipping ==="
    exit 0
  fi
  echo "clearing a stale lock from a previous run"
  rm -rf "$LOCK"
  mkdir "$LOCK" || { echo "could not take the lock"; exit 1; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM
DUMP="$HOME/Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos/_dump"

echo "=== darkroom cycle $(date '+%Y-%m-%d %H:%M:%S') ==="

if [ ! -d "$DUMP" ]; then
  echo "dump folder unavailable — is Drive for Desktop running?"
  exit 0
fi

# Curate every pending roll, not just the newest: several can queue up between
# runs, and an uncurated roll would render on the old shape-only rules.
shopt -s nullglob
for roll in "$DUMP"/*/; do
  name="$(basename "$roll")"
  # Skip dot-dirs and the xArchive bin (test material staged for real deletion
  # later). Lowercased so the match is case-insensitive, matching NOT_A_ROLL
  # in darkroom.py and darkroom_curate.py.
  lname="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
  case "$lname" in .*|xarchive) continue ;; esac
  if [ -f "$roll/_curation.json" ]; then
    echo "-- $name already curated, skipping judgment"
    continue
  fi
  if ! ls "$roll"*.[jJ][pP][gG] "$roll"*.[jJ][pP][eE][gG] "$roll"*.[pP][nN][gG] \
        "$roll"*.[tT][iI][fF] "$roll"*.[tT][iI][fF][fF] "$roll"*.[wW][eE][bB][pP] \
        >/dev/null 2>&1; then
    continue
  fi
  echo "-- curating $name"
  # A curation failure must not strand the roll: the renderer still handles it
  # on the old rules, which is worse output but not lost work.
  python3 "$BIN/darkroom_curate.py" --dir "$roll" -q \
    || echo "!! curation failed for $name — rendering on v2 rules instead"
done

echo "-- rendering"
python3 "$BIN/darkroom.py"

echo "-- contact sheets"
python3 "$BIN/darkroom_sheet.py" --all

echo "=== cycle done ==="
