#!/bin/bash
# CCC Darkroom v4 — point every script at the real Google Drive account folder.
#
# Nine files hardcode the Drive path. Editing them by hand is the single most
# common way this deploy goes wrong, so it is one command instead.
#
#   ./set_drive_account.sh you@gmail.com
#   ./set_drive_account.sh --check          # show current state, change nothing
#
# The account folder name is whatever appears under ~/Library/CloudStorage/
# as GoogleDrive-<name>.
#
# Substitution is done in Python with exact string replacement, not sed or
# perl. Account names contain @ and . — perl interpolates @foo as an array
# and silently deletes it, which corrupts every path without erroring.
set -euo pipefail

BIN="$HOME/CCC/Darkroom/bin"
STAMP="$(date +%Y%m%d-%H%M%S)"

# Every installed file that embeds the Drive path.
TARGETS=(
  darkroom.py
  darkroom_curate.py
  darkroom_sheet.py
  darkroom_learn.py
  darkroom_intake.py
  tidy_dump.py
  analyze_run.py
  darkroom_cycle.sh
  retest_roll.sh
)

if [ ! -d "$BIN" ]; then
  echo "ERROR: $BIN not found — run ./install_darkroom.sh first."
  exit 1
fi

show_current() {
  echo "== Current Drive references in $BIN =="
  local found=0
  for f in "${TARGETS[@]}"; do
    if [ ! -f "$BIN/$f" ]; then printf "  %-22s MISSING\n" "$f"; continue; fi
    local line
    line="$(grep -n "GoogleDrive-" "$BIN/$f" | head -1 || true)"
    if [ -n "$line" ]; then
      printf "  %-22s %s\n" "$f" "$line"
      found=$((found + 1))
    else
      printf "  %-22s (no GoogleDrive- reference)\n" "$f"
    fi
  done
  echo "  $found of ${#TARGETS[@]} file(s) carry a Drive account name"
}

echo "== Available CloudStorage mounts =="
ls -1 "$HOME/Library/CloudStorage/" 2>/dev/null || echo "  (none — is Drive for Desktop installed and signed in?)"
echo ""

if [ $# -eq 0 ]; then
  show_current
  echo ""
  echo "usage: ./set_drive_account.sh <account-folder-name>"
  echo "   or: ./set_drive_account.sh --check"
  exit 1
fi

if [ "$1" = "--check" ]; then
  show_current
  exit 0
fi

ACCOUNT="$1"
# Tolerate someone pasting the whole folder name.
ACCOUNT="${ACCOUNT#GoogleDrive-}"
NEWPATH="$HOME/Library/CloudStorage/GoogleDrive-$ACCOUNT/My Drive/CCC/Photos"

echo "== Verifying the target path exists BEFORE editing anything =="
echo "  $NEWPATH"
if [ ! -d "$NEWPATH" ]; then
  echo "!! That path does not exist on this machine."
  echo "!! Nothing was changed. Check the mount list above and the CCC/Photos"
  echo "!! folder inside 'My Drive'. If Drive is streaming rather than synced,"
  echo "!! set CCC/Photos to Available Offline in Finder first."
  exit 2
fi
echo "  exists — proceeding"
echo ""

show_current
echo ""
echo "== Rewriting to GoogleDrive-$ACCOUNT =="

# Exact-string rewrite of the account segment only. Never touches the
# surrounding path. Reports every file it changed.
ACCOUNT="$ACCOUNT" BIN="$BIN" STAMP="$STAMP" python3 - "${TARGETS[@]}" <<'PY'
import os, re, shutil, sys

account = os.environ["ACCOUNT"]
bin_dir = os.environ["BIN"]
stamp   = os.environ["STAMP"]
pattern = re.compile(r"GoogleDrive-[^/\"']+")
new     = "GoogleDrive-" + account

changed = skipped = 0
for name in sys.argv[1:]:
    path = os.path.join(bin_dir, name)
    if not os.path.isfile(path):
        continue
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    found = pattern.findall(text)
    if not found:
        continue
    if set(found) == {new}:
        print(f"  {name} — already correct")
        skipped += 1
        continue
    before = next(l.strip() for l in text.splitlines() if "GoogleDrive-" in l)
    shutil.copy2(path, f"{path}.drivebak_{stamp}")
    updated = pattern.sub(new, text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    after = next(l.strip() for l in updated.splitlines() if "GoogleDrive-" in l)
    print(f"  {name}")
    print(f"    before: {before}")
    print(f"    after:  {after}")
    changed += 1

print(f"\n{changed} file(s) changed, {skipped} already correct.")
PY

echo ""
echo "== Verification — every reference, after the rewrite =="
BAD=0
for f in "${TARGETS[@]}"; do
  [ -f "$BIN/$f" ] || continue
  while IFS= read -r line; do
    printf "  %-22s %s\n" "$f" "$line"
    case "$line" in
      *"GoogleDrive-$ACCOUNT/"*) ;;
      *) echo "  !! ^ does not point at GoogleDrive-$ACCOUNT"; BAD=1 ;;
    esac
  done < <(grep -n "GoogleDrive-" "$BIN/$f" || true)
done

echo ""
if [ "$BAD" -ne 0 ]; then
  echo "!! Some references still point elsewhere. Backups are *.drivebak_$STAMP"
  exit 3
fi
echo "All references now point at GoogleDrive-$ACCOUNT."
echo "Backups (if any): $BIN/*.drivebak_$STAMP"
echo ""
echo "Next: python3 $BIN/darkroom_curate.py --selftest"
