#!/bin/bash
# One-shot: create the GitHub repo and push this folder to it.
#
#   ./push_to_github.sh                       # public, repo named ccc-darkroom
#   ./push_to_github.sh my-repo-name          # different name
#   ./push_to_github.sh ccc-darkroom --private
#
# Safe to re-run: if the remote already exists it just pushes.
# Delete this script once the repo is up — it is scaffolding, not pipeline.
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd)"

REPO="${1:-ccc-darkroom}"
VIS="--public"
[ "${2:-}" = "--private" ] && VIS="--private"

echo "== 0. Preconditions =="
if ! command -v git >/dev/null; then
  echo "ERROR: git not found. Install Xcode Command Line Tools: xcode-select --install"
  exit 1
fi
if ! command -v gh >/dev/null; then
  echo "ERROR: the GitHub CLI (gh) is not installed."
  echo "       brew install gh"
  echo "       (or: https://cli.github.com)"
  exit 1
fi
echo "git: $(git --version)"
echo "gh:  $(gh --version | head -1)"

echo ""
echo "== 1. Which GitHub account is gh signed in as? =="
if ! gh auth status 2>&1; then
  echo ""
  echo "Not signed in. Run:  gh auth login"
  echo "Choose GitHub.com -> HTTPS -> authenticate in browser."
  echo "Make sure you pick the CCC account, not a personal one."
  exit 1
fi
echo ""
read -r -p "Is that the CCC account you want to publish under? [y/N] " ok
case "$ok" in
  [yY]*) ;;
  *) echo "Stopping. Run 'gh auth switch' or 'gh auth login' and try again."; exit 1 ;;
esac

echo ""
echo "== 2. Guard: no secrets about to be committed =="
if git check-ignore -q .anthropic_key 2>/dev/null; then :; fi
LEAKS="$(grep -rlE 'sk-ant-[A-Za-z0-9_-]{20,}' . --exclude-dir=.git 2>/dev/null || true)"
if [ -n "$LEAKS" ]; then
  echo "!! An API key pattern appears in these files. NOT pushing:"
  echo "$LEAKS"
  exit 1
fi
echo "no API-key patterns found"

echo ""
echo "== 3. Commit =="
[ -d .git ] || git init -q -b main
git add -A
if git diff --cached --quiet 2>/dev/null && git rev-parse HEAD >/dev/null 2>&1; then
  echo "nothing new to commit"
else
  git commit -q -m "CCC Darkroom v4 — v3 curator pipeline, deploy runbook, docs" \
    || echo "nothing new to commit"
fi
echo "tracked files: $(git ls-files | wc -l | tr -d ' ')"

echo ""
echo "== 4. Create and push =="
if git remote get-url origin >/dev/null 2>&1; then
  echo "remote 'origin' already set: $(git remote get-url origin)"
  git push -u origin main
else
  gh repo create "$REPO" $VIS --source=. --remote=origin --push \
    --description "Photograph curation and multi-format publishing pipeline for Catskills Cycling Club"
fi

echo ""
URL="$(gh repo view --json url -q .url 2>/dev/null || echo '')"
cat <<EOF
Done. $URL

Future you, or Claude, pulls it with:
  git clone $URL
  cd $(basename "$REPO")/darkroom && ./install_darkroom.sh
EOF
