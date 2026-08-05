#!/bin/bash
# CCC Darkroom v5 — install and load the two launchd agents, then PROVE IT.
#
#   ./install_launchd.sh            install, load, verify end-to-end
#   ./install_launchd.sh --unload   stop the automation
#
# v5 changes, each one a response to a real outage:
#
#   - Substitutes __DUMP__ as well as __HOME__: the main plist now carries
#     WatchPaths on the dump folder, taken from the account-patched cycle
#     script so it can never point at the placeholder account.
#   - Uses bootout/bootstrap (with load/unload fallback for older macOS), and
#     kickstarts the job rather than trusting RunAtLoad alone.
#   - VERIFIES END-TO-END. "launchctl list shows it" was true during all
#     three silent failures. The only proof that matters is the v5 cycle's
#     own heartbeat moving after load — so this waits for it, and HALTs if
#     it does not move. An install that cannot demonstrate one real cycle
#     did not happen.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/CCC/Darkroom/bin"
STATE="$HOME/CCC/Darkroom/.state"
AGENTS="$HOME/Library/LaunchAgents"
PLISTS=(com.ccc.darkroom.plist com.ccc.darkroom.learn.plist)
UID_N="$(id -u)"

unload_one() {  # label, plist-path
  launchctl bootout "gui/$UID_N/$1" 2>/dev/null \
    || launchctl unload "$2" 2>/dev/null || true
}

if [ "${1:-}" = "--unload" ]; then
  for p in "${PLISTS[@]}"; do
    unload_one "${p%.plist}" "$AGENTS/$p" && echo "unloaded ${p%.plist}"
  done
  echo ""
  launchctl list | grep -i ccc.darkroom || echo "no darkroom jobs loaded — automation is stopped"
  exit 0
fi

echo "== 0. Preconditions =="
[ -d "$BIN" ] || { echo "ERROR: $BIN not found — run ./install_darkroom.sh first."; exit 1; }
for f in darkroom_cycle.sh darkroom_learn.sh darkroom_learn.py darkroom_doctor.sh; do
  [ -f "$BIN/$f" ] || { echo "ERROR: $f not installed in $BIN — re-run ./install_darkroom.sh."; exit 1; }
done
if grep -q "GoogleDrive-account@example.com" "$BIN/darkroom_cycle.sh"; then
  echo "ERROR: darkroom_cycle.sh still carries the placeholder account name."
  echo "       Loading now would give you a timer that finds no Drive folder."
  echo "       Run this first:  ./set_drive_account.sh <your-account-name>"
  echo "       (ls ~/Library/CloudStorage/ tells you the name)"
  exit 2
fi

# The WatchPaths target comes from the one place the account is already
# correct: the installed cycle script. One source of truth, zero hand edits.
DUMP_LINE="$(grep '^DUMP=' "$BIN/darkroom_cycle.sh" | head -1)"
eval "$DUMP_LINE"
echo "home: $HOME    user: $(whoami)"
echo "dump: $DUMP"
[ -d "$DUMP" ] || echo "NOTE: dump folder not reachable right now — installing anyway; the cycle self-heals and reports."
mkdir -p "$AGENTS" "$HOME/CCC/Darkroom/logs" "$STATE"

echo ""
echo "== 1. Rendering templates =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for p in "${PLISTS[@]}"; do
  [ -f "$SRC/$p" ] || { echo "ERROR: $p missing from $SRC"; exit 1; }
  # python3, not sed: the dump path contains an email address and spaces, and
  # sed replacement metacharacters (&) in a path would corrupt the plist
  # silently — the exact class of quiet failure v5 exists to kill.
  /usr/bin/python3 - "$SRC/$p" "$TMP/$p" "$HOME" "$DUMP" <<'PY'
import sys
src, dst, home, dump = sys.argv[1:5]
text = open(src).read().replace("__HOME__", home).replace("__DUMP__", dump)
open(dst, "w").write(text)
PY
  if grep -q "__HOME__\|__DUMP__" "$TMP/$p"; then
    echo "ERROR: substitution failed in $p"; exit 1
  fi
  echo "  $p"
  grep "<string>$HOME\|<string>$DUMP" "$TMP/$p" | sed 's/^ */    /' || true
done

echo ""
echo "== 2. Validating before installing =="
for p in "${PLISTS[@]}"; do
  plutil -lint "$TMP/$p" || { echo "!! $p is malformed. Nothing installed. HALT-06."; exit 6; }
done

echo ""
echo "== 3. Installing and loading =="
# Mark the moment: the end-to-end check is "did the heartbeat move AFTER this".
T0="$(date +%s)"
for p in "${PLISTS[@]}"; do
  LABEL="${p%.plist}"
  unload_one "$LABEL" "$AGENTS/$p"
  cp "$TMP/$p" "$AGENTS/$p"
  if launchctl bootstrap "gui/$UID_N" "$AGENTS/$p" 2>/dev/null; then
    echo "  bootstrapped $LABEL"
  elif launchctl load "$AGENTS/$p"; then
    echo "  loaded $LABEL (legacy load)"
  else
    echo "!! could not load $LABEL — HALT-06."
    plutil -lint "$AGENTS/$p" || true
    exit 6
  fi
done
launchctl kickstart "gui/$UID_N/com.ccc.darkroom" 2>/dev/null || true

echo ""
echo "== 4. Verifying: launchd knows them =="
launchctl list | grep -i ccc.darkroom || { echo "!! no darkroom jobs listed — HALT-06"; exit 6; }

echo ""
echo "== 5. Verifying END-TO-END: waiting for the cycle's own heartbeat =="
echo "   (RunAtLoad + kickstart fired a cycle; it may be waiting on Drive"
echo "    or downloading placeholders, so allow up to 3 minutes)"
OK=0
for i in $(seq 1 36); do
  if [ -f "$STATE/heartbeat" ] && [ "$(cat "$STATE/heartbeat" 2>/dev/null || echo 0)" -ge "$T0" ]; then
    OK=1; break
  fi
  sleep 5
done
if [ "$OK" -eq 1 ]; then
  echo "   heartbeat moved — a real cycle is running under launchd. PROOF."
else
  echo "!! the heartbeat did not move within 3 minutes — HALT-07."
  echo "   The agent is loaded but the cycle is not starting. Diagnose:"
  echo "     tail -40 ~/CCC/Darkroom/logs/darkroom.log"
  echo "     $BIN/darkroom_doctor.sh"
  exit 7
fi

echo ""
echo "== 6. Doctor's opinion =="
"$BIN/darkroom_doctor.sh" || true

cat <<EOF

════════════════════════════════════════════════════════════════
Loaded and PROVEN — a cycle ran under launchd during this install.

From now on there are three ways to know it is alive, in order of
convenience:

  phone   open Drive > CCC/Photos > _darkroom_status.txt
          (rewritten every cycle; a stale timestamp IS the alarm)
  mac     tail -f ~/CCC/Darkroom/logs/darkroom.log
  deep    ~/CCC/Darkroom/bin/darkroom_doctor.sh

New uploads now also TRIGGER a cycle within ~2 minutes (WatchPaths)
instead of waiting for the 15-minute timer. The Sunday learn job
doubles as a watchdog: if the main cycle's heartbeat goes stale, it
raises the alarm through the same three channels.

  stop everything:  ./install_launchd.sh --unload
════════════════════════════════════════════════════════════════
EOF
