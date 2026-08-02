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
# Copy, not move: the originals archive stays intact as the safety net.
#
# The archive keeps each photographer's frames in by_<handle>/, so each one is
# staged back as <roll>_by_<handle> — the exact folder shape it arrived in.
# The credit is then read from the folder name on the way back in, the same
# code path as a fresh upload. That symmetry is the point: there is no
# re-render-specific attribution path left to get wrong.
#
# _curation.json is excluded deliberately — staging a stale plan back would
# make darkroom_cycle.sh treat the roll as already curated and skip judging it.
# _credits.json stays in the archive too; the renderer reads it there by roll
# name, and a stray file in the dump stops the emptied folder being removed.
STAGED=0
DUMPS=""

stage_dir() {          # $1 = source dir, $2 = destination dump folder name
  local src="$1" name="$2" n=0 f
  [ -d "$src" ] || return 0
  for f in "$src"/*; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in _curation*.json|_credits.json|.*) continue ;; esac
    mkdir -p "$PHOTOS/_dump/$name"
    cp -p "$f" "$PHOTOS/_dump/$name"/
    n=$((n + 1))
  done
  [ "$n" -gt 0 ] || return 0
  STAGED=$((STAGED + n))
  DUMPS="$DUMPS $name"
  echo "  $n frame(s) -> _dump/$name"
}

# Uncredited frames live in the roll root and go back under the plain name.
stage_dir "$ORIG" "$ROLL"
# One dump folder per photographer.
for d in "$ORIG"/by_*/; do
  [ -d "$d" ] || continue
  handle="$(basename "$d")"; handle="${handle#by_}"
  stage_dir "$d" "${ROLL}_by_${handle}"
done

if [ "$STAGED" -eq 0 ]; then
  echo "ERROR: no frames found under $ORIG"
  exit 1
fi
echo "$STAGED frame(s) staged"

echo ""
echo "== 4. Curating (this is the new part) =="
for name in $DUMPS; do
  rm -f "$PHOTOS/_dump/$name/_curation.json"
  python3 "$BIN/darkroom_curate.py" --dir "$PHOTOS/_dump/$name"
done

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
