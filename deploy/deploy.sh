#!/usr/bin/env bash
# Run from laptop to deploy latest code to Oracle.
# Usage: ./deploy/deploy.sh [service_name]
#   - no args: just git pull on Oracle (no restart)
#   - service_name: git pull + systemctl restart <service> + health check
set -euo pipefail

HOST="oracle-instance"
SERVICE="${1:-}"
HEALTH_URL="http://127.0.0.1:8888/api/accounts"

fail() { echo "[deploy] FAIL: $*" >&2; exit 1; }

echo "[deploy] pushing local git..."
git push 2>&1 | tail -3

LOCAL_HEAD=$(git rev-parse HEAD)
PREV_HEAD=$(ssh "$HOST" 'git -C ~/portfolio-dashboard rev-parse HEAD') \
  || fail "cannot read HEAD on Oracle"

echo "[deploy] pulling on Oracle..."
ssh "$HOST" 'set -o pipefail; cd ~/portfolio-dashboard && git pull --ff-only 2>&1 | tail -5' \
  || fail "git pull failed on Oracle (nothing changed on server)"
ssh "$HOST" 'set -o pipefail; cd ~/dotclaude && git pull --ff-only 2>&1 | tail -3' \
  || fail "dotclaude (secrets) pull failed on Oracle"

REMOTE_HEAD=$(ssh "$HOST" 'git -C ~/portfolio-dashboard rev-parse HEAD')
[ "$LOCAL_HEAD" = "$REMOTE_HEAD" ] \
  || fail "HEAD mismatch after pull: local=$LOCAL_HEAD oracle=$REMOTE_HEAD"
echo "[deploy] HEAD verified: ${REMOTE_HEAD:0:7}"

# Backup every book before anything restarts on the new code. Runs after the
# pull so the module exists on Oracle; a failure leaves the service untouched.
ROLLBACK="ssh $HOST 'git -C ~/portfolio-dashboard reset --hard $PREV_HEAD' && ./deploy/deploy.sh $SERVICE"
echo "[deploy] backing up DBs on Oracle..."
ssh "$HOST" 'cd ~/portfolio-dashboard && .venv/bin/python -m src.core.backup --only portfolio quant news' \
  || fail "DB backup failed on Oracle (service not restarted) — rollback: $ROLLBACK"

# nginx config drift check (warn only — never auto-apply)
ssh "$HOST" 'sudo -n diff -q ~/portfolio-dashboard/deploy/nginx-portfolio.conf /etc/nginx/sites-available/portfolio >/dev/null 2>&1' \
  || echo "[deploy] WARN: nginx config drift (deployed != git) — review manually"

if [ -n "$SERVICE" ]; then
  echo "[deploy] restarting $SERVICE..."
  ssh "$HOST" "sudo systemctl restart $SERVICE" \
    || fail "restart failed — rollback: $ROLLBACK"
  case "$SERVICE" in
    dashboard*)
      echo "[deploy] health check..."
      ssh "$HOST" "for i in \$(seq 1 10); do curl -sf -o /dev/null $HEALTH_URL && exit 0; sleep 2; done; exit 1" \
        || fail "health check failed after restart — rollback: $ROLLBACK"
      echo "[deploy] health OK ($HEALTH_URL -> 200)"
      ;;
  esac
fi

echo "[deploy] done. deployed ${LOCAL_HEAD:0:7} (prev ${PREV_HEAD:0:7})"
