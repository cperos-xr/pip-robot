#!/bin/bash
# Publish RoboOS on its own: a snapshot of roboos/, the screen firmware and roboos-repo/ (its README, installer, CI)
# to the RoboOS repo, for people without the robot. Run on the owner's PC, never on the robot. Same safety as
# tools/publish.sh: tracked files only (git archive), privacy scan, Python compiles, the owner looks before pushing.
#
#   robot repo (roboos/ is the source)  --tools/publish_roboos.sh-->  RoboOS repo (clean snapshots)
#
# What goes where:   roboos/ -> roboos/      firmware/cyd_dashboard -> firmware/cyd_dashboard
#                    roboos-repo/* -> /      knowledge/manual/screen.md -> docs/screen.md
#                    tools/roboos_smoke_test.py -> tools/smoke_test.py      LICENSE -> LICENSE
#
# One-time setup (on the PC):
#   git clone https://github.com/YOU/roboos.git ../roboos-public
#   git -C ../roboos-public config user.email "ID+YOUR-GITHUB-NAME@users.noreply.github.com"   # never your real email
#
# Usage:
#   tools/publish_roboos.sh            prepare in ../roboos-public and show what would change. Pushes nothing.
#   tools/publish_roboos.sh --push     the same, then commit and push (asks first)
# Options:  -s REV  publish this commit (default HEAD)    -d DIR  the RoboOS clone    -m TEXT  message    -y  no asking
#
# Pull requests merged in the RoboOS repo: bring them into roboos/ here first (they show as commits after the tag
# "published"); the script refuses to publish over them, because a snapshot would undo them.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PUB="${ROBOOS_PUBLIC_DIR:-$HERE/../roboos-public}"
REV=HEAD; MODE=prepare; MSG=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --push) MODE=push ;;
    -s) REV="$2"; shift ;;
    -d) PUB="$2"; shift ;;
    -m) MSG="$2"; shift ;;
    -y) YES=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done
say() { printf '\n== %s\n' "$*"; }
die() { echo "publish_roboos: $*" >&2; exit 1; }

[ -d "$PUB/.git" ] || die "no RoboOS clone at $PUB (see the one-time setup at the top of this file)"
PUB="$(cd "$PUB" && pwd)"
[ "$PUB" != "$HERE" ] || die "the RoboOS clone must be a different folder than this repo"
BRANCH="$(git -C "$PUB" symbolic-ref --short HEAD 2>/dev/null || echo main)"
SHA="$(git -C "$HERE" rev-parse --short "$REV")" || die "no such commit: $REV"
git -C "$HERE" cat-file -e "$REV:roboos/__init__.py" 2>/dev/null || die "$REV has no roboos/ package"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP" "$TMP.msg"' EXIT
say "exporting RoboOS from $REV ($SHA): tracked files only"
mkdir -p "$TMP/x" "$TMP/out/docs" "$TMP/out/tools"
git -C "$HERE" archive "$REV" roboos roboos-repo firmware/cyd_dashboard knowledge/manual/screen.md \
  tools/roboos_smoke_test.py LICENSE .gitattributes | tar -x -C "$TMP/x"
cp -a "$TMP/x/roboos" "$TMP/out/roboos"
mkdir -p "$TMP/out/firmware" && cp -a "$TMP/x/firmware/cyd_dashboard" "$TMP/out/firmware/"
cp -a "$TMP/x/roboos-repo/." "$TMP/out/"
cp "$TMP/x/knowledge/manual/screen.md" "$TMP/out/docs/screen.md"
cp "$TMP/x/tools/roboos_smoke_test.py" "$TMP/out/tools/smoke_test.py"
cp "$TMP/x/LICENSE" "$TMP/out/LICENSE"
[ -f "$TMP/x/.gitattributes" ] && cp "$TMP/x/.gitattributes" "$TMP/out/.gitattributes"
echo "$(find "$TMP/out" -type f | wc -l) files"

say "RoboOS must not import the robot"
if grep -rnE "^\s*(from|import)\s+robotd" "$TMP/out/roboos"; then die "roboos/ imports robotd (see above)"; fi
echo "it does not"

say "privacy scan of exactly what would be published"
python3 "$HERE/tools/privacy_scan.py" --dir "$TMP/out" || die "the scan found something. Fix it in the robot repo first; nothing was published."

say "does the Python compile?"
python3 -m compileall -q "$TMP/out/roboos" "$TMP/out/tools" >/dev/null || die "a Python file does not compile"
find "$TMP/out" -name __pycache__ -prune -exec rm -rf {} +       # the check above wrote them; never publish them
echo "yes"

EMAIL="$(git -C "$PUB" config user.email || true)"
case "$EMAIL" in
  *@users.noreply.github.com) ;;
  *) die "set a GitHub no-reply address in the RoboOS clone first: git -C $PUB config user.email \"ID+NAME@users.noreply.github.com\"" ;;
esac
git -C "$PUB" fetch -q origin 2>/dev/null || true
if git -C "$PUB" rev-parse -q --verify "origin/$BRANCH" >/dev/null; then
  [ "$(git -C "$PUB" rev-parse HEAD)" = "$(git -C "$PUB" rev-parse "origin/$BRANCH")" ] || git -C "$PUB" merge -q --ff-only "origin/$BRANCH" \
    || die "the RoboOS clone has local commits that are not on GitHub: sort them out first"
  if git -C "$PUB" rev-parse -q --verify published >/dev/null && ! git -C "$PUB" diff --quiet published "origin/$BRANCH" -- ; then
    git -C "$PUB" log --oneline "published..origin/$BRANCH"
    die "the RoboOS repo has changes made there (above). Bring them into roboos/ here first, then: git -C $PUB tag -f published origin/$BRANCH"
  fi
fi
find "$PUB" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$TMP/out/." "$PUB/"
git -C "$PUB" add -A
git -C "$PUB" ls-files -z '*.sh' | xargs -0 -r git -C "$PUB" update-index --chmod=+x   # Windows copies drop the x bit
if git -C "$PUB" diff --cached --quiet; then echo; echo "nothing to publish: the RoboOS repo already has this"; exit 0; fi

say "what would change in the RoboOS repo"
git -C "$PUB" diff --cached --stat | tail -25
echo; echo "Look at it in full:  git -C $PUB diff --cached"
if [ "$MODE" != push ]; then
  say "prepared only, NOTHING was pushed. When you have looked at it:  tools/publish_roboos.sh --push"
  exit 0
fi

[ -n "$MSG" ] || MSG="RoboOS from the Pip robot ($(date +%Y-%m-%d), $SHA)"
printf '%s\n' "$MSG" > "$TMP.msg"
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
