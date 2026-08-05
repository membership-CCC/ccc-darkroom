#!/bin/bash
# CCC Darkroom v5 — wrapper for the weekly learn pass, doubling as a watchdog.
#
# Why a wrapper: the learn job runs on an INDEPENDENT launchd schedule
# (Sundays 20:00). Two jobs on separate schedules can watch each other in a
# way one job can never watch itself — if the main cycle's heartbeat has gone
# stale, this is a second, unrelated process that will notice and say so.
# The failure mode this catches is exactly the one that happened three times:
# the main agent dead or misconfigured, and nothing anywhere saying so.
#
# The watchdog does NOT try to repair the main job. Repairs that run
# unattended on a machine nobody is watching are how one broken thing becomes
# two. It reports — to the log, to a notification, and into the phone-visible
# status file — and leaves the fixing to darkroom_doctor.sh with a human
# behind it.

STATE="$HOME/CCC/Darkroom/.state"
BIN="$HOME/CCC/Darkroom/bin"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"CCC Darkroom\"" \
    >/dev/null 2>&1 || true
}

# --- watchdog: how old is the main cycle's heartbeat? -----------------------
HB="$STATE/heartbeat"
NOW="$(date +%s)"
if [ -f "$HB" ]; then
  AGE=$(( NOW - $(cat "$HB" 2>/dev/null || echo 0) ))
else
  AGE=-1
fi

# The cycle runs every 15 minutes; two hours of silence means at least seven
# consecutive missed fires. That is dead, not busy.
if [ "$AGE" -lt 0 ] || [ "$AGE" -gt 7200 ]; then
  if [ "$AGE" -lt 0 ]; then
    MSG="Watchdog: the main darkroom cycle has NO heartbeat at all — it has never run on this install."
  else
    MSG="Watchdog: the main darkroom cycle has not run in $(( AGE / 3600 ))h $(( (AGE % 3600) / 60 ))m. It should run every 15 minutes."
  fi
  echo "!! $MSG"
  notify "$MSG Run darkroom_doctor.sh"

  # Best effort: put it where the phone can see it. The dump path lives in
  # the account-patched cycle script; extract rather than duplicate it.
  DUMP_LINE="$(grep '^DUMP=' "$BIN/darkroom_cycle.sh" 2>/dev/null | head -1)"
  if [ -n "$DUMP_LINE" ]; then
    eval "$DUMP_LINE"
    PHOTOS="$(dirname "$DUMP")"
    if [ -d "$PHOTOS" ]; then
      {
        echo "CCC DARKROOM — $(date '+%a %d %b %Y, %H:%M:%S')"
        echo ""
        echo "PROBLEM: $MSG"
        echo ""
        echo "On Ada, run:  ~/CCC/Darkroom/bin/darkroom_doctor.sh"
      } > "$PHOTOS/_darkroom_status.txt" 2>/dev/null || true
    fi
  fi
else
  echo "watchdog: main cycle heartbeat is ${AGE}s old — healthy"
fi

# --- the actual learn pass ---------------------------------------------------
exec /usr/bin/python3 "$BIN/darkroom_learn.py" --write
