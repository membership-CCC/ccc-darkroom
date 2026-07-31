#!/bin/bash
# CCC Darkroom — one full unattended cycle: curate, render, contact sheet.
# This is what launchd should run, NOT darkroom.py on its own — the renderer
# alone would silently fall back to v2 orientation rules with no plan present.
BIN="$HOME/CCC/Darkroom/bin"
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
