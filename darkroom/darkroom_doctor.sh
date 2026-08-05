#!/bin/bash
# CCC Darkroom v5 — the 30-second diagnosis.
#
#   ~/CCC/Darkroom/bin/darkroom_doctor.sh
#
# Read-only. Checks every way this pipeline has ever actually failed, plus
# the ways it plausibly could, and prints PASS/WARN/FAIL per check with the
# exact fix command for anything broken. Exit code = number of FAILs.
#
# Every check here is a scar, not a hypothetical:
#   - the wrong-username plist (three silent outages before v5)
#   - agents present on disk but never loaded into launchd
#   - Drive unmounted / listing EDEADLK / cloud-only placeholder files
#   - a stale lock wedging every subsequent cycle
#   - the machine sleeping through its own timer

BIN="$HOME/CCC/Darkroom/bin"
STATE="$HOME/CCC/Darkroom/.state"
LOGS="$HOME/CCC/Darkroom/logs"
AGENTS="$HOME/Library/LaunchAgents"
FAILS=0; WARNS=0

say()  { printf '%s\n' "$1"; }
pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; WARNS=$((WARNS + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '      fix: %s\n' "$2"; FAILS=$((FAILS + 1)); }

say "=== CCC Darkroom doctor — $(date '+%Y-%m-%d %H:%M:%S') ==="
say "    user: $(whoami)   home: $HOME"
say ""

# --- 1. Are the agents installed, and do they point at THIS home? -----------
say "-- launchd agents on disk"
for p in com.ccc.darkroom.plist com.ccc.darkroom.learn.plist; do
  if [ ! -f "$AGENTS/$p" ]; then
    fail "$p is not installed in ~/Library/LaunchAgents" \
         "cd <update-folder>/darkroom && ./install_launchd.sh"
    continue
  fi
  if grep -q "__HOME__\|__DUMP__" "$AGENTS/$p"; then
    fail "$p still contains an unsubstituted template token" \
         "./install_launchd.sh (never hand-copy the template)"
    continue
  fi
  BADPATH="$(grep -o '/Users/[^/<]*' "$AGENTS/$p" | sort -u | grep -v "^$HOME\$" || true)"
  if [ -n "$BADPATH" ]; then
    # THE bug: a plist pointing into a home directory that is not this one.
    # launchd fires forever, finds nothing, and cannot even log the failure
    # because its log path is inside the nonexistent home too.
    fail "$p points at '$BADPATH' but this home is '$HOME' — launchd is firing into a void" \
         "./install_launchd.sh (regenerates from template with the real home)"
  else
    pass "$p installed, paths match this user"
  fi
done

# --- 2. Does launchd actually have them loaded? -----------------------------
say ""
say "-- launchd state"
LIST="$(launchctl list 2>/dev/null | grep -i ccc.darkroom || true)"
if [ -z "$LIST" ]; then
  fail "no darkroom jobs are loaded in launchd (installed != loaded; a reboot or 'Login Items & Extensions' toggle can drop them)" \
       "./install_launchd.sh"
else
  # launchctl list: PID  LastExitStatus  Label
  echo "$LIST" | while read -r pid code label; do
    if [ "$code" = "0" ] || [ "$pid" != "-" ]; then
      pass "$label loaded (pid=$pid last-exit=$code)"
    elif [ "$code" = "78" ] || [ "$code" = "127" ]; then
      fail "$label loaded but last exit was $code — its ProgramArguments path is wrong or missing"
    else
      warn "$label loaded, last exit was $code — check the log tail below"
    fi
  done
fi

# --- 3. Heartbeat -------------------------------------------------------------
say ""
say "-- heartbeat"
if [ -f "$STATE/heartbeat" ]; then
  AGE=$(( $(date +%s) - $(cat "$STATE/heartbeat" 2>/dev/null || echo 0) ))
  if [ "$AGE" -le 1200 ]; then
    pass "last cycle started ${AGE}s ago (expected < 900s + runtime)"
  elif [ "$AGE" -le 7200 ]; then
    warn "last cycle started $(( AGE / 60 ))m ago — one or two missed fires, watch the next 15 minutes"
  else
    fail "last cycle started $(( AGE / 3600 ))h ago — the timer is not firing" \
         "./install_launchd.sh, then check System Settings > General > Login Items & Extensions"
  fi
else
  fail "no heartbeat file — the v5 cycle has never run on this install" \
       "./install_launchd.sh (RunAtLoad fires a cycle immediately)"
fi

# --- 4. Stale lock ------------------------------------------------------------
if [ -d "$STATE/cycle.lock" ]; then
  LPID="$(cat "$STATE/cycle.lock/pid" 2>/dev/null || echo '')"
  if [ -n "$LPID" ] && kill -0 "$LPID" 2>/dev/null; then
    pass "a cycle is running right now (pid $LPID)"
  else
    warn "stale cycle lock present (the wrapper clears this itself on its next run)"
  fi
fi

# --- 5. Drive ----------------------------------------------------------------
say ""
say "-- Google Drive"
DUMP_LINE="$(grep '^DUMP=' "$BIN/darkroom_cycle.sh" 2>/dev/null | head -1)"
if [ -z "$DUMP_LINE" ]; then
  fail "darkroom_cycle.sh not installed in $BIN" "./install_darkroom.sh"
else
  eval "$DUMP_LINE"
  if echo "$DUMP" | grep -q "account@example.com"; then
    fail "the Drive account was never set — the cycle has no real dump path" \
         "./set_drive_account.sh <account>  (ls ~/Library/CloudStorage tells you the name)"
  elif [ ! -d "$DUMP" ]; then
    fail "dump folder not found: $DUMP" \
         "start Google Drive for Desktop and sign in"
  else
    LS_OK=0
    for i in 1 2 3; do ls "$DUMP" >/dev/null 2>&1 && { LS_OK=1; break; }; sleep 2; done
    if [ "$LS_OK" -eq 0 ]; then
      fail "_dump exists but cannot be listed — TCC permission block or a wedged Drive mount" \
           "System Settings > Privacy & Security > Files and Folders; or restart Drive for Desktop"
    else
      pass "dump reachable: $DUMP"
      # Inventory: images + placeholders per roll, so "uploaded but ignored"
      # is visible here instead of being a mystery.
      for d in "$DUMP"/*/; do
        [ -d "$d" ] || continue
        NAME="$(basename "$d")"
        LNAME="$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]')"
        case "$LNAME" in .*|_*|xarchive) continue ;; esac
        N=0; DATALESS=0
        while IFS= read -r -d '' f; do
          N=$((N + 1))
          [ "$(stat -f%b "$f" 2>/dev/null || echo 1)" -eq 0 ] && DATALESS=$((DATALESS + 1))
        done < <(find "$d" -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \
                   -o -iname "*.tif" -o -iname "*.tiff" -o -iname "*.webp" \) -print0 2>/dev/null)
        if [ "$N" -eq 0 ]; then
          warn "roll '$NAME': no image files (loose files in _dump root are ignored by design — images must be IN the roll folder)"
        elif [ "$DATALESS" -gt 0 ]; then
          warn "roll '$NAME': $N images, $DATALESS still cloud-only placeholders — the cycle defers until they land"
        else
          pass "roll '$NAME': $N images, fully local, ready"
        fi
      done
    fi
  fi
fi

# --- 6. Scripts parse ----------------------------------------------------------
say ""
say "-- installed scripts"
PYBAD=0
for f in "$BIN"/*.py; do
  [ -f "$f" ] || continue
  python3 -m py_compile "$f" 2>/dev/null || { fail "$(basename "$f") does not parse"; PYBAD=1; }
done
[ "$PYBAD" -eq 0 ] && pass "all python parses"
SHBAD=0
for f in "$BIN"/*.sh; do
  [ -f "$f" ] || continue
  bash -n "$f" 2>/dev/null || { fail "$(basename "$f") has a shell syntax error"; SHBAD=1; }
done
[ "$SHBAD" -eq 0 ] && pass "all shell parses"

# --- 7. Environment ------------------------------------------------------------
say ""
say "-- environment"
AVAIL_GB="$(df -g "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "$AVAIL_GB" ] && [ "$AVAIL_GB" -lt 5 ]; then
  fail "only ${AVAIL_GB}GB free on the boot volume — renders will start failing"
else
  pass "disk: ${AVAIL_GB:-?}GB free"
fi
SLEEPMIN="$(pmset -g 2>/dev/null | awk '$1 == "sleep" {print $2; exit}')"
if [ -n "$SLEEPMIN" ] && [ "$SLEEPMIN" != "0" ]; then
  warn "system sleep is ${SLEEPMIN} min — launchd timers do not fire while asleep. For an always-on pipeline:  sudo pmset -a sleep 0"
else
  pass "system sleep disabled — timers fire around the clock"
fi

# --- 8. Recent evidence ----------------------------------------------------------
say ""
say "-- last log lines"
tail -12 "$LOGS/darkroom.log" 2>/dev/null | sed 's/^/      /' || say "      (no log yet)"
if [ -n "${DUMP:-}" ] && [ -f "$(dirname "$DUMP")/_darkroom_status.txt" ]; then
  say ""
  say "-- phone-visible status file"
  head -4 "$(dirname "$DUMP")/_darkroom_status.txt" | sed 's/^/      /'
fi

say ""
if [ "$FAILS" -eq 0 ] && [ "$WARNS" -eq 0 ]; then
  say "=== ALL CLEAR ==="
elif [ "$FAILS" -eq 0 ]; then
  say "=== OK with $WARNS warning(s) — read them, most clear themselves ==="
else
  say "=== $FAILS FAILURE(S), $WARNS warning(s) — fix lines above, top to bottom ==="
fi
exit "$FAILS"
