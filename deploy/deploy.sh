#!/usr/bin/env bash
# Deploy the laptop's HEAD to Oracle, or roll Oracle back to an earlier commit. Run from the laptop.
#
#   ./deploy/deploy.sh                  push, then pull on Oracle; nothing restarts
#   ./deploy/deploy.sh dashboard        … then restart the old app (dashboard.service) + health
#   ./deploy/deploy.sh autotrade        the new quant (quant.jsyoo.dev), in this order:
#       1. build web/ of the pushed commit OUTSIDE the live tree (git archive -> npm ci
#          --ignore-scripts -> npm run build) into web/releases/<id>. A failed build changes
#          nothing on the server: the live web/dist, code and database are untouched.
#       2. move Oracle's tree to the commit (git pull --ff-only; after a rollback, from the detached
#          HEAD straight to the new commit, which must descend from main)
#       3. back up the old app's books (src.core.backup, as before)
#       4. python -m autotrade.jobs backup --predeploy   -> data/backup/autotrade/
#          autotrade-predeploy-<UTC>.db; stop if it fails (nothing migrated)
#       5. python -m autotrade.jobs migrate              (the only command that migrates)
#       6. web/dist -> releases/<id>: one atomic rename; the previous releases stay (rollback) and
#          the new one carries their hashed assets (pages loaded before the switch keep working)
#       7. restart autotrade.service; /api/healthz (503 unless the schema matches), / and the
#          origin rule must answer as expected
#     Between steps 2 and 7 the running API still has the old code: once the database is
#     migrated (5) it answers 503 schema until the restart (7), and the 5-minute tick refuses to
#     run against a schema its code was not written for. A failure after step 2 prints the exact
#     rollback command.
#   ./deploy/deploy.sh rollback [<commit>] [--yes]
#       Back to <commit>, by default the commit before the last deploy (data/deploy/history.jsonl
#       on Oracle). It shows the plan first. When the autotrade database is at a newer schema than
#       that commit's code, it restores the newest predeploy backup at that commit's schema: what
#       was written since then (runs, orders, fills, snapshots, events, AI calls, logins, PIN and
#       lockout changes) leaves the live database, which is moved aside (never deleted), and every
#       session is closed. That restore asks you to type 'restore' unless --yes is given. Then:
#       ticks and the AI runner are held off (their locks), autotrade.service is stopped, the tree
#       is checked out at <commit> (a detached HEAD: main keeps the rolled-back commit, so the next
#       deploy fast-forwards from it), web/dist goes back to that commit's release (built first if
#       it is gone), autotrade.service is started again, and dashboard.service is restarted only if
#       the old app's code (src/, its unit) differs between the two commits.
#
# The server-side steps live in autotrade/api/release.py, which this script pipes to Oracle's
# Python (so a rollback runs this laptop's version, not the one of the commit it checks out).
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="oracle-instance"
HEALTH_URL="http://127.0.0.1:8888/api/accounts"
PAGE_URL="http://127.0.0.1:8888/"          # served page = theme + shared fragments injected
HELPER="autotrade/api/release.py"

fail() { echo "[deploy] FAIL: $*" >&2; exit 1; }

# A release.py command on Oracle; prints its JSON line.
helper() { ssh "$HOST" "cd ~/portfolio-dashboard && .venv/bin/python - $*" < "$HELPER"; }

# One field of a JSON object on stdin (booleans as true/false, null as nothing).
field() {
  python3 -c 'import json, sys
v = json.load(sys.stdin).get(sys.argv[1])
print("" if v is None else json.dumps(v) if isinstance(v, bool) else v)' "$1"
}

# The 'error' of a failed helper call, or the raw output.
why() { field error <<<"$1" 2>/dev/null || echo "$1"; }

record() {
  helper record "'$1'" >/dev/null || echo "[deploy] WARN: could not write data/deploy/history.jsonl"
}

restart_dashboard() {
  echo "[deploy] restarting dashboard.service..."
  ssh "$HOST" "sudo systemctl restart dashboard.service" || fail "dashboard restart failed — $1"
  ssh "$HOST" "for i in \$(seq 1 10); do curl -sf -o /dev/null $HEALTH_URL && curl -sf -o /dev/null $PAGE_URL && exit 0; sleep 2; done; exit 1" \
    || fail "dashboard health check failed after restart — $1"
  echo "[deploy] health OK ($HEALTH_URL, $PAGE_URL -> 200)"
}

autotrade_health() {
  local out
  out=$(helper health) || fail "autotrade health check failed: $(why "$out") — $1"
  echo "[deploy] health OK (autotrade :8890 $out)"
}

rollback() {
  local target="" yes="" plan out
  for arg in "$@"; do
    case "$arg" in
      --yes) yes=1 ;;
      -*) fail "unknown option $arg" ;;
      *) target="$arg" ;;
    esac
  done
  [ -z "$target" ] || target=$(git rev-parse --verify "$target^{commit}") || fail "no commit $target here"
  plan=$(helper rollback-plan ${target:+"$target"}) || fail "cannot roll back: $(why "$plan") (nothing changed)"
  python3 -c 'import json, sys
for line in json.load(sys.stdin)["say"]: print("[rollback] " + line)' <<<"$plan"
  local head to restore release
  head=$(field head <<<"$plan")
  to=$(field target <<<"$plan")
  restore=$(field restore <<<"$plan")
  release=$(field release <<<"$plan")
  if [ -n "$restore" ] && [ -z "$yes" ]; then
    read -r -p "[rollback] type 'restore' to replace the live autotrade DB with that backup: " answer \
      || answer=""   # no terminal (end of input): not a yes
    [ "$answer" = "restore" ] || fail "rollback cancelled (nothing changed)"
  fi
  if [ "$(field autotrade <<<"$plan")" = "true" ] && [ -z "$release" ]; then
    echo "[rollback] building web of ${to:0:7} (no release of it is left)..."
    out=$(helper web-build "$to") || fail "web build failed: $(why "$out") (nothing changed)"
    release=$(field release <<<"$out")
  fi
  echo "[rollback] holding off the jobs, stopping autotrade, checking out ${to:0:7}..."
  out=$(helper rollback-apply "$to" ${restore:+--restore "$restore"} ${release:+--release "$release"}) \
    || fail "rollback stopped: $(why "$out")"
  record "{\"kind\": \"rollback\", \"from\": \"$head\", \"to\": \"$to\", \"release\": \"$release\"}"
  local hint="the tree is at ${to:0:7}; fix and deploy again, or roll back to another commit"
  if [ "$(field wasActive <<<"$out")" = "true" ]; then
    echo "[rollback] starting autotrade.service..."
    ssh "$HOST" "sudo systemctl start autotrade.service" || fail "autotrade start failed — $hint"
    autotrade_health "$hint"
  fi
  if [ "$(field dashboardChanged <<<"$plan")" = "true" ]; then
    restart_dashboard "$hint"
  fi
  if [ -n "$restore" ]; then
    python3 -c 'import json, sys
print("[rollback] restored {backup} (schema {schema}); the replaced database is kept at"
      " {setAside}; {closedSessions} sessions closed: log in again, with the PIN of that"
      " backup".format(**json.load(sys.stdin)["restored"]))' <<<"$out"
  fi
  echo "[rollback] done: Oracle at ${to:0:7} (detached; the next ./deploy/deploy.sh returns to main)"
}

MODE="${1:-}"
case "$MODE" in
  rollback) shift; rollback "$@"; exit 0 ;;
  ""|dashboard*|autotrade) ;;  # dashboard* as before (qdd)
  *) fail "unknown service '$MODE' (dashboard, autotrade, or rollback)" ;;
esac
SERVICE="$MODE"

echo "[deploy] pushing local git..."
git push 2>&1 | tail -3
LOCAL_HEAD=$(git rev-parse HEAD)

STATUS=$(helper status) || fail "cannot read Oracle's state: $(why "$STATUS")"
PREV_HEAD=$(field head <<<"$STATUS")
BRANCH=$(field branch <<<"$STATUS")   # empty: a detached HEAD, left by a rollback

ssh "$HOST" 'git -C ~/portfolio-dashboard fetch -q origin' || fail "git fetch failed on Oracle (nothing changed)"
ORIGIN_MAIN=$(ssh "$HOST" 'git -C ~/portfolio-dashboard rev-parse refs/remotes/origin/main')
[ "$ORIGIN_MAIN" = "$LOCAL_HEAD" ] \
  || fail "origin/main is ${ORIGIN_MAIN:0:7}, this laptop's HEAD ${LOCAL_HEAD:0:7}: push main first (nothing changed)"
ROLLBACK="./deploy/deploy.sh rollback $PREV_HEAD"

RELEASE=""
if [ "$SERVICE" = "autotrade" ]; then
  echo "[deploy] building web of ${LOCAL_HEAD:0:7} outside the live tree..."
  BUILD=$(helper web-build "$LOCAL_HEAD") || fail "web build failed: $(why "$BUILD") (nothing changed on the server)"
  RELEASE=$(field release <<<"$BUILD")
  echo "[deploy] built web/releases/$RELEASE"
fi

echo "[deploy] moving Oracle to ${LOCAL_HEAD:0:7}..."
if [ -z "$BRANCH" ]; then
  # After a rollback main still names the rolled-back commit; go straight to the new one (no
  # moment on the broken code) if it descends from main, as a fast-forward pull would.
  ssh "$HOST" "cd ~/portfolio-dashboard && git merge-base --is-ancestor refs/heads/main $LOCAL_HEAD && git checkout -q -B main $LOCAL_HEAD" \
    || fail "cannot leave the rolled-back state: main on Oracle is not an ancestor of ${LOCAL_HEAD:0:7} (nothing changed)"
else
  ssh "$HOST" 'set -o pipefail; cd ~/portfolio-dashboard && git pull --ff-only 2>&1 | tail -5' \
    || fail "git pull failed on Oracle (nothing changed on server)"
fi
ssh "$HOST" 'set -o pipefail; cd ~/dotclaude && git pull --ff-only 2>&1 | tail -3' \
  || fail "dotclaude (secrets) pull failed on Oracle"

REMOTE_HEAD=$(ssh "$HOST" 'git -C ~/portfolio-dashboard rev-parse HEAD')
[ "$LOCAL_HEAD" = "$REMOTE_HEAD" ] \
  || fail "HEAD mismatch after pull: local=$LOCAL_HEAD oracle=$REMOTE_HEAD — rollback: $ROLLBACK"
echo "[deploy] HEAD verified: ${REMOTE_HEAD:0:7}"
record "{\"kind\": \"deploy\", \"service\": \"$SERVICE\", \"from\": \"$PREV_HEAD\", \"to\": \"$LOCAL_HEAD\", \"release\": \"$RELEASE\"}"

# Backup every book of the old app before anything restarts on the new code. Runs after the
# pull so the module exists on Oracle; a failure leaves the services untouched.
echo "[deploy] backing up the old app's DBs on Oracle..."
ssh "$HOST" 'cd ~/portfolio-dashboard && .venv/bin/python -m src.core.backup --only portfolio quant news' \
  || fail "DB backup failed on Oracle (service not restarted) — rollback: $ROLLBACK"

# Config drift (warn only — never auto-apply): nginx and the autotrade unit are installed by hand.
ssh "$HOST" 'sudo -n diff -q ~/portfolio-dashboard/deploy/nginx-portfolio.conf /etc/nginx/sites-available/portfolio >/dev/null 2>&1' \
  || echo "[deploy] WARN: nginx config drift (deployed != git) — review manually"
ssh "$HOST" 'test ! -e /etc/nginx/sites-available/quant || sudo -n diff -q ~/portfolio-dashboard/deploy/nginx-quant.conf /etc/nginx/sites-available/quant >/dev/null 2>&1' \
  || echo "[deploy] WARN: nginx quant config drift (deployed != git) — see deploy/nginx-quant.conf"
ssh "$HOST" 'test ! -e /etc/systemd/system/autotrade.service || diff -q ~/portfolio-dashboard/deploy/autotrade.service /etc/systemd/system/autotrade.service >/dev/null 2>&1' \
  || echo "[deploy] WARN: autotrade.service drift (installed != git) — see deploy/autotrade.service"

if [[ "$SERVICE" != dashboard* ]] \
  && git cat-file -e "$PREV_HEAD^{commit}" 2>/dev/null \
  && ! git diff --quiet "$PREV_HEAD" "$LOCAL_HEAD" -- src deploy/dashboard.service; then
  echo "[deploy] WARN: the old app's code (src/) changed but dashboard.service was not restarted: ./deploy/deploy.sh dashboard"
fi

if [ "$SERVICE" = "autotrade" ]; then
  echo "[deploy] predeploy backup of the autotrade DB..."
  BACKUP=$(ssh "$HOST" 'cd ~/portfolio-dashboard && .venv/bin/python -m autotrade.jobs backup --predeploy') \
    || fail "predeploy backup failed (nothing migrated, service not restarted) — rollback: $ROLLBACK"
  echo "[deploy] $BACKUP"
  echo "[deploy] migrating the autotrade DB..."
  ssh "$HOST" 'cd ~/portfolio-dashboard && .venv/bin/python -m autotrade.jobs migrate' \
    || fail "autotrade migration failed (service not restarted) — rollback: $ROLLBACK (restores the predeploy backup)"
  STATUS=$(helper status) || fail "cannot read Oracle's state after the migration — rollback: $ROLLBACK"
  python3 -c 'import json, sys
s = json.load(sys.stdin)["schema"]
sys.exit(0 if s["stored"] == s["code"] else f"schema {s} after migrate")' <<<"$STATUS" \
    || fail "the database is not at the code's schema after migrate — rollback: $ROLLBACK"
  echo "[deploy] switching web/dist to $RELEASE..."
  SWITCH=$(helper web-switch "$RELEASE") || fail "web switch failed: $(why "$SWITCH") — rollback: $ROLLBACK"
else
  # Ticks run whatever code is on disk: a pulled schema change pauses them (and the site answers
  # 503) until the autotrade deploy migrates.
  STATUS=$(helper status) || STATUS='{"schema": {}}'
  python3 -c 'import json, sys
s = json.load(sys.stdin)["schema"]
sys.exit(0 if s.get("stored") in (None, s.get("code")) else 1)' <<<"$STATUS" \
    || echo "[deploy] WARN: the autotrade schema changed: ticks refuse to run and quant answers 503 until ./deploy/deploy.sh autotrade"
fi

case "$SERVICE" in
  "") ;;
  dashboard*) restart_dashboard "rollback: $ROLLBACK" ;;
  autotrade)
    if ! ssh "$HOST" 'systemctl cat autotrade.service >/dev/null 2>&1'; then
      echo "[deploy] autotrade.service is not installed (see deploy/autotrade.service): code, DB and web are in place, nothing restarted"
    else
      echo "[deploy] restarting autotrade.service..."
      ssh "$HOST" "sudo systemctl restart autotrade.service" || fail "restart failed — rollback: $ROLLBACK"
      autotrade_health "rollback: $ROLLBACK"
    fi
    ;;
esac

echo "[deploy] done. deployed ${LOCAL_HEAD:0:7} (prev ${PREV_HEAD:0:7})"
