#!/bin/bash
# CCC Darkroom v4 — fresh install on Ada.
#
# Installs the full v3 pipeline: curator (judgment) + renderer (pixels) +
# contact sheet + cycle script + utilities. Safe to re-run: it backs up any
# existing scripts before overwriting them and never touches Drive.
#
#   ./install_darkroom.sh
#
# Does NOT set the Drive account path and does NOT load launchd.
# Those are separate, deliberate steps:
#   ./set_drive_account.sh <account-folder-name>
#   ./install_launchd.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HOME/CCC/Darkroom"
BIN="$ROOT/bin"
STAMP="$(date +%Y%m%d-%H%M%S)"

SCRIPTS=(
  darkroom.py
  darkroom_curate.py
  darkroom_credit.py
  darkroom_sheet.py
  darkroom_learn.py
  darkroom_intake.py
  tidy_dump.py
  analyze_run.py
  backfill_credits.py
  darkroom_cycle.sh
  darkroom_learn.sh
  darkroom_doctor.sh
  retest_roll.sh
)

echo "== 0. Sanity =="
if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: this installs a macOS pipeline (launchd, Vision). Not Darwin here."
  exit 1
fi
for f in "${SCRIPTS[@]}"; do
  if [ ! -f "$SRC/$f" ]; then
    echo "ERROR: $f missing from $SRC — the package is incomplete."
    exit 1
  fi
done
if [ ! -d "$SRC/fonts" ]; then
  echo "ERROR: fonts/ missing from $SRC — the contact sheet needs it."
  exit 1
fi
echo "package complete: ${#SCRIPTS[@]} scripts + fonts"
echo "user: $(whoami)   python3: $(command -v python3)   $(python3 --version 2>&1)"

echo ""
echo "== 1. Folders =="
mkdir -p "$BIN" "$ROOT/logs" "$ROOT/.state" "$ROOT/reports"
echo "$ROOT/{bin,logs,.state,reports}"

echo ""
echo "== 2. Backing up anything already installed =="
BACKED=0
for f in "${SCRIPTS[@]}"; do
  if [ -f "$BIN/$f" ] && ! cmp -s "$BIN/$f" "$SRC/$f"; then
    cp -p "$BIN/$f" "$BIN/$f.bak_$STAMP"
    echo "  saved $BIN/$f.bak_$STAMP"
    BACKED=$((BACKED + 1))
  fi
done
[ "$BACKED" -eq 0 ] && echo "  nothing to back up (fresh install or identical files)"

echo ""
echo "== 3. Installing scripts + fonts =="
for f in "${SCRIPTS[@]}"; do
  cp "$SRC/$f" "$BIN/"
done
rm -rf "$BIN/fonts"
cp -R "$SRC/fonts" "$BIN/"
# Explicit modes, not chmod +x. Files unzipped from a read-only archive can
# land 555, and set_drive_account.sh has to be able to WRITE these.
chmod 755 "$BIN"/*.py "$BIN"/*.sh
chmod -R u+rwX "$BIN/fonts"
ls -la "$BIN"

echo ""
echo "-- writability check (set_drive_account.sh needs it) --"
NOTW=0
for f in "${SCRIPTS[@]}"; do
  [ -w "$BIN/$f" ] || { echo "  !! NOT WRITABLE: $BIN/$f"; NOTW=1; }
done
[ "$NOTW" -eq 0 ] && echo "  all installed scripts are writable" || {
  echo "  !! Fix with: chmod u+w $BIN/*.py $BIN/*.sh"; exit 1; }

echo ""
echo "== 4. Dependencies =="
echo "-- Pillow (required — the renderer cannot run without it)"
python3 -m pip install --user --quiet Pillow || {
  echo "!! Pillow install FAILED."
  echo "!! If the error mentions xcrun / gcc / developer directory, run:"
  echo "!!   xcode-select --install"
  echo "!! approve the GUI prompt, then re-run this script."
  exit 4
}
python3 -c "import PIL; print('   Pillow OK', PIL.__version__)"

echo "-- pyobjc / Vision (optional — enables saliency, people, text, scene)"
if python3 -m pip install --user --quiet pyobjc-framework-Vision pyobjc-framework-Quartz; then
  python3 - <<'PY'
try:
    import Vision  # noqa: F401
    print("   Vision OK — full measurement layer available")
except Exception as exc:
    print(f"   Vision import failed ({exc}) — curator will use basic fallback")
PY
else
  echo "   pyobjc install failed — curator falls back to Pillow-only measurement."
  echo "   Not fatal. The pipeline still runs, with less to judge from."
fi

echo ""
echo "== 5. API key for the judgment layer =="
KEYFILE="$ROOT/.anthropic_key"
if [ -f "$KEYFILE" ]; then
  chmod 600 "$KEYFILE"
  echo "key file present: $KEYFILE (chmod 600)"
else
  cat <<EOF
No key file yet. The curator runs without one (measurement layer only), but
the editorial judgment — worth publishing, which channels, colour or duotone,
route-safety hold — needs an Anthropic API key.

  printf '%s' 'sk-ant-...' > $KEYFILE
  chmod 600 $KEYFILE

console.anthropic.com. Billed per use, separate from a Claude subscription.
EOF
fi

cat <<EOF

Installed. Next, in order:

  1. ./set_drive_account.sh <account-folder-name>
     e.g. ./set_drive_account.sh you@gmail.com
     (ls ~/Library/CloudStorage/ tells you the name)

  2. python3 $BIN/darkroom_curate.py --selftest

  3. bash $BIN/darkroom_cycle.sh        # one full cycle by hand

  4. ./install_launchd.sh               # only after step 3 works
EOF
