#!/usr/bin/env bash
# Deploy the laptop's HEAD to Oracle, or roll Oracle back to an earlier commit. Run from the laptop.
#
#   ./deploy/deploy.sh                  push, then pull on Oracle and back up the books; nothing restarts
#   ./deploy/deploy.sh dashboard        … then restart dashboard.service and check its health
#   ./deploy/deploy.sh rollback [<commit>] [--yes]
#       Back to <commit>, by default the commit before the last deploy (data/deploy/history.jsonl
#       on Oracle). It shows the plan and asks first unless --yes is given. The tree is checked out
#       at <commit> (a detached HEAD: main keeps the rolled-back commit, so the next deploy
#       fast-forwards from it) and dashboard.service is restarted. The books are not touched:
#       their schema only ever grows (store/schema.py), so older code reads them as they are.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="oracle-instance"
DIR="~/stock-portfolio"
HEALTH_URL="http://127.0.0.1:8888/api/accounts"
PAGE_URL="http://127.0.0.1:8888/"          # served page = theme + shared fragments injected

fail() { echo "[deploy] FAIL: $*" >&2; exit 1; }

on_server() { ssh "$HOST" "cd $DIR && $1"; }

record() {
  local entry
  entry=$(printf '{"at": "%s", "kind": "%s", "service": "dashboard", "from": "%s", "to": "%s"}' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3")
  on_server "mkdir -p data/deploy && echo '$entry' >> data/deploy/history.jsonl" \
    || echo "[deploy] WARN: could not write data/deploy/history.jsonl"
}

restart_dashboard() {
  echo "[deploy] restarting dashboard.service..."
  ssh "$HOST" "sudo systemctl restart dashboard.service" || fail "dashboard restart failed — $1"
  ssh "$HOST" "for i in \$(seq 1 10); do curl -sf -o /dev/null $HEALTH_URL && curl -sf -o /dev/null $PAGE_URL && exit 0; sleep 2; done; exit 1" \
    || fail "dashboard health check failed after restart — $1"
  echo "[deploy] health OK ($HEALTH_URL, $PAGE_URL -> 200)"
}

server_head() { on_server 'git rev-parse HEAD'; }
server_branch() { on_server 'git symbolic-ref -q --short HEAD || true'; }

rollback() {
  local target="" yes=""
  for arg in "$@"; do
    case "$arg" in
      --yes) yes=1 ;;
      -*) fail "unknown option $arg" ;;
      *) target="$arg" ;;
    esac
  done
  local head
  head=$(server_head) || fail "cannot read Oracle's HEAD (nothing changed)"
  if [ -z "$target" ]; then
    target=$(on_server 'cat data/deploy/history.jsonl 2>/dev/null' \
      | python3 -c 'import json, sys
head = sys.argv[1]   # the last deploy that moved Oracle to where it is now
moves = [e for e in map(json.loads, filter(str.strip, sys.stdin))
         if e.get("kind") == "deploy" and e.get("to") == head and e.get("from") != head]
print(moves[-1]["from"] if moves else "")' "$head") || true
    [ -n "$target" ] || fail "no deploy recorded on Oracle: name the commit (nothing changed)"
  fi
  target=$(git rev-parse --verify "$target^{commit}") || fail "no commit $target here (nothing changed)"
  [ "$target" != "$head" ] || fail "Oracle is already at ${target:0:7} (nothing changed)"
  on_server "git cat-file -e $target^{commit}" || fail "Oracle does not have ${target:0:7}: push it first (nothing changed)"
  echo "[rollback] Oracle ${head:0:7} -> ${target:0:7}: $(git log -1 --format=%s "$target")"
  git diff --stat "$target" "$head" -- src deploy | tail -1 | sed 's/^/[rollback] /'
  if [ -z "$yes" ]; then
    read -r -p "[rollback] type 'yes' to check out ${target:0:7} and restart dashboard.service: " answer \
      || answer=""   # no terminal (end of input): not a yes
    [ "$answer" = "yes" ] || fail "rollback cancelled (nothing changed)"
  fi
  on_server "test -z \"\$(git status --porcelain --untracked-files=no)\"" \
    || fail "Oracle's tree has local changes (nothing changed)"
  on_server "git checkout -q --detach $target" || fail "checkout failed on Oracle (nothing changed)"
  record rollback "$head" "$target"
  restart_dashboard "the tree is at ${target:0:7}; fix and deploy again, or roll back to another commit"
  echo "[rollback] done: Oracle at ${target:0:7} (detached; the next ./deploy/deploy.sh returns to main)"
}

MODE="${1:-}"
case "$MODE" in
  rollback) shift; rollback "$@"; exit 0 ;;
  ""|dashboard) ;;
  *) fail "unknown mode '$MODE' (dashboard or rollback)" ;;
esac

echo "[deploy] pushing local git..."
git push 2>&1 | tail -3
LOCAL_HEAD=$(git rev-parse HEAD)

PREV_HEAD=$(server_head) || fail "cannot read Oracle's HEAD (nothing changed)"
BRANCH=$(server_branch)   # empty: a detached HEAD, left by a rollback

on_server 'git fetch -q origin' || fail "git fetch failed on Oracle (nothing changed)"
ORIGIN_MAIN=$(on_server 'git rev-parse refs/remotes/origin/main')
[ "$ORIGIN_MAIN" = "$LOCAL_HEAD" ] \
  || fail "origin/main is ${ORIGIN_MAIN:0:7}, this laptop's HEAD ${LOCAL_HEAD:0:7}: push main first (nothing changed)"
ROLLBACK="./deploy/deploy.sh rollback $PREV_HEAD"

echo "[deploy] moving Oracle to ${LOCAL_HEAD:0:7}..."
if [ -z "$BRANCH" ]; then
  # After a rollback main still names the rolled-back commit; go straight to the new one (no
  # moment on the broken code) if it descends from main, as a fast-forward pull would.
  on_server "git merge-base --is-ancestor refs/heads/main $LOCAL_HEAD && git checkout -q -B main $LOCAL_HEAD" \
    || fail "cannot leave the rolled-back state: main on Oracle is not an ancestor of ${LOCAL_HEAD:0:7} (nothing changed)"
else
  ssh "$HOST" "set -o pipefail; cd $DIR && git pull --ff-only 2>&1 | tail -5" \
    || fail "git pull failed on Oracle (nothing changed on server)"
fi
ssh "$HOST" 'set -o pipefail; cd ~/dotclaude && git pull --ff-only 2>&1 | tail -3' \
  || fail "dotclaude (secrets) pull failed on Oracle"

REMOTE_HEAD=$(server_head)
[ "$LOCAL_HEAD" = "$REMOTE_HEAD" ] \
  || fail "HEAD mismatch after pull: local=$LOCAL_HEAD oracle=$REMOTE_HEAD — rollback: $ROLLBACK"
echo "[deploy] HEAD verified: ${REMOTE_HEAD:0:7}"
record deploy "$PREV_HEAD" "$LOCAL_HEAD"

# Back up the books before anything restarts on the new code. Runs after the pull so the module
# exists on Oracle; a failure leaves the service untouched.
echo "[deploy] backing up the books on Oracle..."
on_server '.venv/bin/python -m src.core.backup --only portfolio news' \
  || fail "DB backup failed on Oracle (service not restarted) — rollback: $ROLLBACK"

# Config drift (warn only — never auto-apply): these are installed by hand.
ssh "$HOST" "sudo -n diff -q $DIR/deploy/nginx-portfolio.conf /etc/nginx/sites-available/portfolio >/dev/null 2>&1" \
  || echo "[deploy] WARN: nginx config drift (deployed != git) — see deploy/nginx-portfolio.conf"
ssh "$HOST" "diff -q $DIR/deploy/dashboard.service /etc/systemd/system/dashboard.service >/dev/null 2>&1" \
  || echo "[deploy] WARN: dashboard.service drift (installed != git) — see deploy/dashboard.service"
ssh "$HOST" "diff -q $DIR/deploy/logrotate-stock-portfolio /etc/logrotate.d/stock-portfolio >/dev/null 2>&1" \
  || echo "[deploy] WARN: logrotate drift (installed != git) — see deploy/logrotate-stock-portfolio"

if [ "$MODE" = "dashboard" ]; then
  restart_dashboard "rollback: $ROLLBACK"
elif git cat-file -e "$PREV_HEAD^{commit}" 2>/dev/null \
  && ! git diff --quiet "$PREV_HEAD" "$LOCAL_HEAD" -- src deploy/dashboard.service; then
  echo "[deploy] WARN: src/ changed but dashboard.service was not restarted: ./deploy/deploy.sh dashboard"
fi

echo "[deploy] done. deployed ${LOCAL_HEAD:0:7} (prev ${PREV_HEAD:0:7})"
