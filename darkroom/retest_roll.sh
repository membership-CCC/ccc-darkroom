#!/bin/bash
# CCC Darkroom — re-run an already-processed roll through the curator.
# Non-destructive: previous outputs and state are moved aside, never deleted.
#
#   ./retest_roll.sh 2026-07-27_test-roll
set -euo pipefail

ROLL="${1:-}"
if [ -z "$ROLL" ]; then
  echo "usage: ./retest_roll.sh <roll-folder-name>"
  echo "e.g.   ./retest_roll.sh 2026-07-27_test-roll"
  exit 1
fi

PHOTOS="$HOME/Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos"
BIN="$HOME/CCC/Darkroom/bin"
STATE="$HOME/CCC/Darkroom/.state"
ORIG="$PHOTOS/_originals/$ROLL"
OUT="$PHOTOS/$ROLL"
DUMP="$PHOTOS/_dump/$ROLL"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -d "$ORIG" ]; then
  echo "ERROR: no originals at $ORIG"
  echo "Available:"
  ls "$PHOTOS/_originals/" 2>/dev/null || echo "  (none)"
  exit 1
fi

echo "== 1. Moving previous outputs aside =="
if [ -d "$OUT" ]; then
  mv "$OUT" "${OUT}_pre-curator_$STAMP"
  echo "Old outputs -> ${OUT}_pre-curator_$STAMP"
else
  echo "No previous output folder."
fi

echo ""
echo "== 2. Clearing the processed-frame ledger for this roll =="
if [ -f "$STATE/$ROLL.json" ]; then
  mv "$STATE/$ROLL.json" "$STATE/$ROLL.json.pre-curator_$STAMP"
  echo "Ledger set aside — frames will renumber from 001."
fi

echo ""
echo "== 3. Returning originals to the dump for reprocessing =="
mkdir -p "$DUMP"
# Copy, not move: the originals archive stays intact as the safety net.
# Exclude _curation.json — the archive keeps a copy for reference, but staging
# a stale plan back into the dump means darkroom_cycle.sh would see a roll as
# "already curated" and skip judging it entirely.
for f in "$ORIG"/*; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in _curation.json|.*) continue ;; esac
  cp -p "$f" "$DUMP"/
done
rm -f "$DUMP/_curation.json"
echo "$(find "$DUMP" -maxdepth 1 -type f ! -name '_curation.json' ! -name '.*' | wc -l | tr -d ' ') frame(s) staged in _dump/$ROLL"

echo ""
echo "== 4. Curating (this is the new part) =="
python3 "$BIN/darkroom_curate.py" --dir "$DUMP"

echo ""
echo "== 5. Rendering to the curator's plan =="
python3 "$BIN/darkroom.py" --force

echo ""
echo "== 6. Contact sheet =="
python3 "$BIN/darkroom_sheet.py" --all || true

cat <<EOF

Done. Compare:
  new:  $OUT
  old:  ${OUT}_pre-curator_$STAMP

The plan the renderer followed is in the roll's _curation.json (moved with
the originals). Edit it and re-run step 5 with --force to override anything.
EOF
