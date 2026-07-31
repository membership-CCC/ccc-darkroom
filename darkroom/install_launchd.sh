#!/bin/bash
# CCC Darkroom v4 — install and load the two launchd agents.
#
# Substitutes the real home directory into both plist templates, validates
# them with plutil BEFORE installing, then loads and verifies. Hand-editing
# plists is the usual cause of a failed load, so this does it mechanically.
#
#   ./install_launchd.sh            install and load
#   ./install_launchd.sh --unload   stop the automation
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/CCC/Darkroom/bin"
AGENTS="$HOME/Library/LaunchAgents"
PLISTS=(com.ccc.darkroom.plist com.ccc.darkroom.learn.plist)

if [ "${1:-}" = "--unload" ]; then
  for p in "${PLISTS[@]}"; do
    launchctl unload "$AGENTS/$p" 2>/dev/null && echo "unloaded $p" || echo "$p was not loaded"
  done
  echo ""
  launchctl list | grep -i darkroom || echo "no darkroom jobs loaded — automation is stopped"
  exit 0
fi

echo "== 0. Preconditions =="
[ -d "$BIN" ] || { echo "ERROR: $BIN not found — run ./install_darkroom.sh first."; exit 1; }
[ -f "$BIN/darkroom_cycle.sh" ] || { echo "ERROR: darkroom_cycle.sh not installed."; exit 1; }
[ -f "$BIN/darkroom_learn.py" ] || { echo "ERROR: darkroom_learn.py not installed."; exit 1; }
if grep -q "GoogleDrive-account@example.com" "$BIN/darkroom_cycle.sh" 2>/dev/null; then
  echo "ERROR: darkroom_cycle.sh still carries the placeholder account name."
  echo "       Nothing was scheduled. Loading the job now would give you a"
  echo "       timer that fires every 15 minutes and finds no Drive folder."
  echo ""
  echo "       Run this first:  ./set_drive_account.sh <your-account-name>"
  echo "       (ls ~/Library/CloudStorage/ tells you the name)"
  exit 2
fi
echo "home: $HOME    user: $(whoami)"
mkdir -p "$AGENTS" "$HOME/CCC/Darkroom/logs"

echo ""
echo "== 1. Rendering templates =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for p in "${PLISTS[@]}"; do
  [ -f "$SRC/$p" ] || { echo "ERROR: $p missing from $SRC"; exit 1; }
  sed "s|__HOME__|$HOME|g" "$SRC/$p" > "$TMP/$p"
  if grep -q "__HOME__" "$TMP/$p"; then
    echo "ERROR: substitution failed in $p"; exit 1
  fi
  echo "  $p"
  grep -A1 "ProgramArguments" -m1 "$TMP/$p" >/dev/null
  grep "<string>$HOME" "$TMP/$p" | sed 's/^ */    /'
done

echo ""
echo "== 2. Validating before installing =="
for p in "${PLISTS[@]}"; do
  if plutil -lint "$TMP/$p"; then :; else
    echo "!! $p is malformed. Nothing installed. This is HALT-06."
    exit 6
  fi
done

echo ""
echo "== 3. Installing and loading =="
for p in "${PLISTS[@]}"; do
  launchctl unload "$AGENTS/$p" 2>/dev/null || true
  cp "$TMP/$p" "$AGENTS/$p"
  if launchctl load "$AGENTS/$p"; then
    echo "  loaded $p"
  else
    echo "!! launchctl load failed for $p — this is HALT-06."
    plutil -lint "$AGENTS/$p" || true
    exit 6
  fi
done

echo ""
echo "== 4. Verifying =="
launchctl list | grep -i darkroom || { echo "!! no darkroom jobs listed — HALT-06"; exit 6; }

echo ""
echo "com.ccc.darkroom has RunAtLoad, so a cycle just started. Waiting 45s..."
sleep 45
echo ""
echo "== 5. Log tail =="
tail -40 "$HOME/CCC/Darkroom/logs/darkroom.log" 2>&1 || echo "(no log yet)"

cat <<EOF

Loaded. The cycle now runs every 15 minutes.

  watch:  tail -f $HOME/CCC/Darkroom/logs/darkroom.log
  stop:   ./install_launchd.sh --unload
EOF
