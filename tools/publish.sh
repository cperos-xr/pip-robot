#!/bin/bash
# Publish a cleaned copy of this (private) robot repo to the public open-source repo. Run it on the owner's PC,
# never on the robot: the robot has no key for the public repo, so it cannot publish anything by accident.
#
# Why two repos: the robot commits and pushes by itself (RoboDev, self-tests, its manual). Its own repo stays private.
# The public repo only ever gets a snapshot that passed the privacy scan and that the owner looked at:
#
#   robot  --push-->  origin (PRIVATE, full history)  --tools/publish.sh-->  public repo (clean snapshots)
#
# One-time setup (on the PC):
#   git clone git@github.com:YOU/PUBLIC-REPO.git ../robot-public        # the public repo (may be empty)
#   git -C ../robot-public config user.name  "YOUR-GITHUB-NAME"
#   git -C ../robot-public config user.email "ID+YOUR-GITHUB-NAME@users.noreply.github.com"   # never your real email
#
# Usage:
#   tools/publish.sh                 prepare: export, scan, stage in ../robot-public, show what would change. Pushes nothing.
#   tools/publish.sh --push          the same, then commit and push (asks first)
#   tools/publish.sh --pull          bring changes made in the public repo (merged pull requests) into this checkout
# Options:  -s REV   publish this commit instead of HEAD      -d DIR   the public clone (default ../robot-public)
#           -m TEXT  commit message                           -y       do not ask before pushing
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PUB="${PUBLIC_DIR:-$HERE/../robot-public}"
REV=HEAD; MODE=prepare; MSG=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --push) MODE=push ;;
    --pull) MODE=pull ;;
    -s) REV="$2"; shift ;;
    -d) PUB="$2"; shift ;;
    -m) MSG="$2"; shift ;;
    -y) YES=1 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done
say() { printf '\n== %s\n' "$*"; }
die() { echo "publish: $*" >&2; exit 1; }

[ -d "$PUB/.git" ] || die "no public clone at $PUB (see the one-time setup at the top of this file)"
PUB="$(cd "$PUB" && pwd)"
[ "$PUB" != "$HERE" ] || die "the public clone must be a different folder than this repo"
BRANCH="$(git -C "$PUB" symbolic-ref --short HEAD 2>/dev/null || echo main)"

# ------------------------------------------------------------------------------------------------ --pull
if [ "$MODE" = pull ]; then
  git -C "$PUB" fetch -q origin
  git -C "$PUB" rev-parse -q --verify published >/dev/null || die "nothing was published from here yet (no 'published' tag in $PUB)"
  if git -C "$PUB" diff --quiet published "origin/$BRANCH"; then echo "the public repo has nothing new"; exit 0; fi
  say "changes made in the public repo since the last publish"
  git -C "$PUB" log --oneline "published..origin/$BRANCH"
  git -C "$PUB" diff --stat published "origin/$BRANCH"
  [ -z "$(git -C "$HERE" status --porcelain --untracked-files=no)" ] || die "commit or set aside your own changes in $HERE first"
  git -C "$PUB" diff --binary published "origin/$BRANCH" | git -C "$HERE" apply --3way --index \
    || die "some changes did not apply cleanly: fix the conflicts shown above (git status), then commit"
  git -C "$PUB" merge -q --ff-only "origin/$BRANCH"
  git -C "$PUB" tag -f published >/dev/null
  say "applied and staged in $HERE: look at 'git diff --cached', test, then commit and send it to the robot"
  exit 0
fi

# ------------------------------------------------------------------------------------------------ prepare (and push)
SHA="$(git -C "$HERE" rev-parse --short "$REV")" || die "no such commit: $REV"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
say "exporting $REV ($SHA): tracked files only (data/, secrets and untracked files are never included)"
git -C "$HERE" archive "$REV" | tar -x -C "$TMP"
echo "$(find "$TMP" -type f | wc -l) files"

say "privacy scan of exactly what would be published"
python3 "$HERE/tools/privacy_scan.py" --dir "$TMP" || die "the scan found something. Fix it in the private repo first; nothing was published."

say "do the Python files compile?"
python3 -m compileall -q "$TMP/robotd" "$TMP/mcp" "$TMP/tools" >/dev/null || die "a Python file does not compile"
echo "yes"

# the public clone becomes exactly the export (its .git stays)
EMAIL="$(git -C "$PUB" config user.email || true)"
case "$EMAIL" in
  *@users.noreply.github.com) ;;
  *) die "set a GitHub no-reply address in the public clone first (your real email would be public for good):
  git -C $PUB config user.email \"ID+NAME@users.noreply.github.com\"   (GitHub > Settings > Emails shows yours)" ;;
esac
if git -C "$PUB" rev-parse -q --verify "origin/$BRANCH" >/dev/null; then
  git -C "$PUB" fetch -q origin
  LOCAL="$(git -C "$PUB" rev-parse HEAD 2>/dev/null || true)"
  [ "$LOCAL" = "$(git -C "$PUB" rev-parse "origin/$BRANCH")" ] || \
    die "the public repo has changes that are not in this checkout yet (merged pull requests?). Run: tools/publish.sh --pull"
fi
find "$PUB" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$TMP/." "$PUB/"
git -C "$PUB" add -A
if git -C "$PUB" diff --cached --quiet; then echo; echo "nothing to publish: the public repo already has this"; exit 0; fi

say "what would change in the public repo"
git -C "$PUB" diff --cached --stat | tail -25
echo
echo "Look at it in full:  git -C $PUB diff --cached"

if [ "$MODE" != push ]; then
  say "prepared only, NOTHING was pushed. When you have looked at it:  tools/publish.sh --push"
  exit 0
fi

[ -n "$MSG" ] || MSG="Update from the robot ($(date +%Y-%m-%d))"
printf '%s\n' "$MSG" > "$TMP.msg"; trap 'rm -rf "$TMP" "$TMP.msg"' EXIT
python3 "$HERE/tools/privacy_scan.py" --msg "$TMP.msg" --quiet || die "the commit message has something private in it"
if [ "$YES" != 1 ]; then
  printf '\nPublish this to %s (branch %s)? It cannot be taken back once it is public. [type yes] ' "$(git -C "$PUB" remote get-url origin)" "$BRANCH"
  read -r ANSWER
  [ "$ANSWER" = yes ] || { echo "not pushed (the changes stay staged in $PUB)"; exit 0; }
fi
git -C "$PUB" commit -q -F "$TMP.msg"
git -C "$PUB" push -q origin "HEAD:$BRANCH"
git -C "$PUB" tag -f published >/dev/null
say "published $(git -C "$PUB" rev-parse --short HEAD) to $(git -C "$PUB" remote get-url origin)"
