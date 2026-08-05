#!/bin/bash
# CCC Darkroom — one full unattended cycle: curate, render, contact sheet.
# This is what launchd runs, NOT darkroom.py on its own — the renderer alone
# would silently fall back to v2 orientation rules with no plan present.
#
# v5 REDESIGN — after the automation died silently three separate times.
#
# The post-mortem on all three: the failure itself was cheap, the SILENCE was
# expensive. A launchd agent pointing at a home directory that doesn't exist
# fails every 15 minutes forever, writes no log (its log path is inside the
# nonexistent home too), and looks exactly like "working" from a phone.
#
# So v5 has one governing rule, enforced in this file:
#
#     EVERY failure mode either self-heals, retries next cycle, or announces
#     itself somewhere John actually looks.
#
# The announcement channel is `_darkroom_status.txt` at the root of the Drive
# Photos folder. Drive syncs it to the phone, so the health of a launchd job
# on a headless Mac mini is readable from a pocket with no new infrastructure.
# Every cycle rewrites it — a stale timestamp IS the alarm. macOS
# notifications fire too, but those need eyes on the Mac; the file does not.

BIN="$HOME/CCC/Darkroom/bin"
STATE="$HOME/CCC/Darkroom/.state"
LOGS="$HOME/CCC/Darkroom/logs"
mkdir -p "$STATE" "$LOGS"

# set_drive_account.sh rewrites the account name on this line. Keep the format.
DUMP="$HOME/Library/CloudStorage/GoogleDrive-account@example.com/My Drive/CCC/Photos/_dump"

# ---------------------------------------------------------------------------
# Reporting. None of these may ever kill the run — reporting failure is
# tolerable, failing silently because the REPORTER crashed would be farce.
# ---------------------------------------------------------------------------
notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"CCC Darkroom\"" \
    >/dev/null 2>&1 || true
}

status_write() {   # $1 = headline; rest of the file is standing instructions
  local photos; photos="$(dirname "$DUMP")"
  [ -d "$photos" ] || return 0
  {
    echo "CCC DARKROOM — $(date '+%a %d %b %Y, %H:%M:%S')"
    echo ""
    echo "$1"
    echo ""
    echo "----------------------------------------------------------"
    echo "Every automated cycle rewrites this file. If the timestamp"
    echo "above is old while uploads are waiting in _dump, the"
    echo "automation is down. On Ada, run:"
    echo ""
    echo "    ~/CCC/Darkroom/bin/darkroom_doctor.sh"
    echo ""
    echo "and it will say exactly what is wrong."
  } > "$photos/_darkroom_status.txt" 2>/dev/null || true
}

heartbeat() { date +%s > "$STATE/heartbeat" 2>/dev/null || true; }

fail_loud() {      # terminal problem for THIS cycle — say it three ways
  echo "!! $1"
  status_write "PROBLEM: $1"
  notify "Darkroom: $1"
}

# The heartbeat moves at the START of the cycle, deliberately. It answers
# "is launchd running me at all?" — the question the last three failures
# could not answer. Whether the cycle then SUCCEEDED is the status file's job.
heartbeat

# ---------------------------------------------------------------------------
# Log hygiene. launchd appends forever; a year of 15-minute cycles is a log
# nobody can tail. Rotate above 5MB. (Truncation leaves the current run's fd
# at its old offset — a one-run sparse gap, harmless, and only on the rare
# run that rotates.)
# ---------------------------------------------------------------------------
LOGF="$LOGS/darkroom.log"
if [ -f "$LOGF" ] && [ "$(stat -f%z "$LOGF" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  cp "$LOGF" "$LOGF.1" 2>/dev/null && : > "$LOGF" || true
  echo "(rotated log to darkroom.log.1)"
fi

# ---------------------------------------------------------------------------
# One cycle at a time. mkdir is atomic on every filesystem this touches;
# flock is not on macOS. (On 2 Aug two overlapped cycles double-processed a
# roll; both derive sequence numbers from the same state file.)
# ---------------------------------------------------------------------------
LOCK="$STATE/cycle.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
    echo "=== another cycle is already running (pid $(cat "$LOCK/pid")) — skipping ==="
    exit 0
  fi
  echo "clearing a stale lock from a previous run"
  rm -rf "$LOCK"
  mkdir "$LOCK" || { fail_loud "could not take the cycle lock"; exit 1; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM

echo "=== darkroom cycle $(date '+%Y-%m-%d %H:%M:%S') ==="

# ---------------------------------------------------------------------------
# Find Drive — self-heal, then wait, then say so.
#
# 1. If the configured path is gone but a GoogleDrive-* mount exists with the
#    right shape, use it and warn: an account rename must not kill the
#    pipeline, it should degrade to a warning in the log.
# 2. Drive for Desktop mounts late after login. Wait up to 2 minutes — the
#    lock prevents pileup and a 15-minute cadence absorbs the stall.
# 3. Still nothing: that is a phone-visible PROBLEM, not a silent exit 0.
# ---------------------------------------------------------------------------
if [ ! -d "$DUMP" ]; then
  for cand in "$HOME/Library/CloudStorage/"GoogleDrive-*"/My Drive/CCC/Photos/_dump"; do
    if [ -d "$cand" ]; then
      echo "WARN: configured dump path missing; self-healed to: $cand"
      DUMP="$cand"
      break
    fi
  done
fi
tries=0
while [ ! -d "$DUMP" ] && [ "$tries" -lt 8 ]; do
  sleep 15; tries=$((tries + 1))
done
if [ ! -d "$DUMP" ]; then
  fail_loud "Google Drive is not mounted. Is Drive for Desktop running and signed in?"
  exit 0
fi

# TCC / permission probe. From a launchd context, macOS can deny reads under
# ~/Library/CloudStorage even when Terminal reads it fine — and the pipeline
# would see an empty world, not an error. This mount has also thrown EDEADLK
# on readdir before, so listing gets three tries before it counts as real.
ls_ok=0
for i in 1 2 3; do
  if ls "$DUMP" >/dev/null 2>&1; then ls_ok=1; break; fi
  sleep 5
done
if [ "$ls_ok" -eq 0 ]; then
  fail_loud "Cannot list the _dump folder. Check System Settings > Privacy & Security > Files and Folders (python3 and bash need access), or Drive is wedged."
  exit 0
fi

# ---------------------------------------------------------------------------
# Roll readiness. Two Drive-specific traps:
#
#  DATALESS  Drive can show a file that is a cloud placeholder (0 allocated
#            blocks). The pipeline would read a stub or stall. Nudge the
#            first megabyte to trigger materialization, defer the roll.
#  MID-SYNC  A roll uploaded from a phone arrives file by file. Curating half
#            a roll splits it into two rolls forever. If the roll's
#            name+size signature changes across a 3-second gap, defer.
#
# Deferring is FREE because the timer returns in 15 minutes. Processing a
# half-synced roll is not. A roll deferred >12 cycles (~3h) is flagged in the
# status file as stuck instead of silently deferring forever. This matters
# double from here on: 35mm lab scans are 50-100MB TIFFs that sync slowly.
# ---------------------------------------------------------------------------
roll_signature() {
  find "$1" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
    -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.webp" \) \
    -exec stat -f "%N:%z" {} \; 2>/dev/null | sort | /sbin/md5 -q 2>/dev/null || echo "sig-err"
}

roll_ready() {     # 0 ready | 1 defer   (prints its own reasons)
  local roll="$1" name sig1 sig2 f blocks
  name="$(basename "$roll")"

  sig1="$(roll_signature "$roll")"
  sleep 3
  sig2="$(roll_signature "$roll")"
  if [ "$sig1" != "$sig2" ]; then
    echo "-- $name is still syncing (contents changing) — deferring"
    return 1
  fi

  while IFS= read -r -d '' f; do
    blocks="$(stat -f%b "$f" 2>/dev/null || echo 1)"
    if [ "$blocks" -eq 0 ] && [ "$(stat -f%z "$f" 2>/dev/null || echo 0)" -gt 0 ]; then
      echo "-- $name has cloud-only placeholders ($(basename "$f")) — nudging download, deferring"
      head -c 1048576 "$f" >/dev/null 2>&1 || true
      return 1
    fi
  done < <(find "$roll" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
             -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.webp" \) -print0 2>/dev/null)
  return 0
}

defer_mark() {     # $1 roll name -> echoes defer count after increment
  local key n
  key="$STATE/defer.$(/sbin/md5 -q -s "$1" 2>/dev/null || echo x)"
  n=$(($(cat "$key" 2>/dev/null || echo 0) + 1))
  echo "$n" > "$key" 2>/dev/null || true
  echo "$n"
}
defer_clear() {
  rm -f "$STATE/defer.$(/sbin/md5 -q -s "$1" 2>/dev/null || echo x)" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Curate every pending, READY roll. Several can queue between runs, and an
# uncurated roll would render on the old shape-only rules.
# ---------------------------------------------------------------------------
shopt -s nullglob
DEFERRED=0; CURATED=0; STUCK=""
for roll in "$DUMP"/*/; do
  name="$(basename "$roll")"
  lname="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
  case "$lname" in .*|_*|xarchive) continue ;; esac
  if ! ls "$roll"*.[jJ][pP][gG] "$roll"*.[jJ][pP][eE][gG] "$roll"*.[pP][nN][gG] \
        "$roll"*.[tT][iI][fF] "$roll"*.[tT][iI][fF][fF] "$roll"*.[wW][eE][bB][pP] \
        >/dev/null 2>&1; then
    continue
  fi

  if ! roll_ready "$roll"; then
    DEFERRED=$((DEFERRED + 1))
    n="$(defer_mark "$name")"
    if [ "$n" -gt 12 ]; then
      STUCK="$STUCK $name(${n}x)"
      echo "!! $name has been deferring for $n cycles — flagging as stuck"
    fi
    continue
  fi
  defer_clear "$name"

  if [ -f "$roll/_curation.json" ]; then
    echo "-- $name already curated, skipping judgment"
    continue
  fi
  echo "-- curating $name"
  # A curation failure must not strand the roll: the renderer still handles
  # it on the old rules — worse output, not lost work.
  python3 "$BIN/darkroom_curate.py" --dir "$roll" -q \
    || echo "!! curation failed for $name — rendering on v2 rules instead"
  CURATED=$((CURATED + 1))
done

# ---------------------------------------------------------------------------
# Render + sheets, with the outcome measured rather than assumed: the merge
# consumes dump folders, so rolls(before) - rolls(after) = rolls processed.
# ---------------------------------------------------------------------------
count_rolls() {
  local c=0 d lname
  for d in "$DUMP"/*/; do
    [ -d "$d" ] || continue
    lname="$(basename "$d" | tr '[:upper:]' '[:lower:]')"
    case "$lname" in .*|_*|xarchive) continue ;; esac
    c=$((c + 1))
  done
  echo "$c"
}

BEFORE="$(count_rolls)"
echo "-- rendering"
RENDER_OK=1
python3 "$BIN/darkroom.py" || RENDER_OK=0

echo "-- contact sheets"
SHEET_OK=1
python3 "$BIN/darkroom_sheet.py" --all || SHEET_OK=0
AFTER="$(count_rolls)"
DONE=$((BEFORE - AFTER)); [ "$DONE" -lt 0 ] && DONE=0

# ---------------------------------------------------------------------------
# Tell the phone what happened. Exactly one status per cycle; worst news wins.
# ---------------------------------------------------------------------------
if [ "$RENDER_OK" -eq 0 ]; then
  fail_loud "The renderer exited with an error — see logs/darkroom.log on Ada."
elif [ -n "$STUCK" ]; then
  status_write "PROBLEM: roll(s) stuck syncing for hours:$STUCK. Drive may have stalled — open Drive for Desktop on Ada and check its sync status."
  notify "Darkroom: a roll appears stuck in sync"
elif [ "$DONE" -gt 0 ]; then
  MSG="OK — processed $DONE roll(s) this cycle."
  [ "$DEFERRED" -gt 0 ] && MSG="$MSG $DEFERRED still syncing, will retry."
  [ "$SHEET_OK" -eq 0 ] && MSG="$MSG (contact sheet step reported an error — log has details)"
  status_write "$MSG"
elif [ "$DEFERRED" -gt 0 ]; then
  status_write "OK — $DEFERRED roll(s) still syncing from Drive, will retry in 15 min."
elif [ "$CURATED" -gt 0 ]; then
  # Curated but nothing merged away: the renderer ran clean yet left the
  # rolls in place. Legal (e.g. everything HELD), but worth distinguishing
  # from idle — "I did work and here it still is" is not "no work".
  status_write "OK — curated $CURATED roll(s); renderer left them in _dump (all frames held, or see log)."
else
  status_write "OK — idle, no new work. Watching _dump."
fi

echo "=== cycle done ==="
