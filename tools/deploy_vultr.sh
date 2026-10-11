#!/usr/bin/env bash
# Deploy a git ref of this repo to the Mira Vultr box (systemd unit `mira`, app in /opt/mira/app).
#
#   tools/deploy_vultr.sh [ref]            # default ref: origin/main
#
# Env: MIRA_HOST (default 45.76.255.253), MIRA_SSH_KEY (default ~/.ssh/mira_vultr), MIRA_SSH_USER (default root).
# The box keeps its own /opt/mira/app/.env (never overwritten or printed), .venv, logs and generated data
# (data/audio, data/ai_cache, data/recordings). Only tracked files of <ref> are shipped (git archive), so
# untracked local files and secrets can't leak.
set -euo pipefail

REF=${1:-origin/main}
HOST=${MIRA_HOST:-45.76.255.253}
USER_AT=${MIRA_SSH_USER:-root}@$HOST
KEY=${MIRA_SSH_KEY:-$HOME/.ssh/mira_vultr}
APP=/opt/mira/app
SSH=(ssh -i "$KEY" -o BatchMode=yes "$USER_AT")

git rev-parse --verify --quiet "$REF^{commit}" >/dev/null || { echo "unknown ref: $REF" >&2; exit 2; }
SHA=$(git rev-parse --short "$REF")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar "$REF" | tar -x -C "$STAGE"
echo "$SHA" > "$STAGE/.deployed_ref"

echo "deploying $REF ($SHA) to $HOST"
rsync -az --delete -e "ssh -i $KEY -o BatchMode=yes" \
  --exclude=.env --exclude=.venv/ --exclude=logs/ --exclude=.pytest_cache/ --exclude=__pycache__/ \
  --exclude=data/audio/ --exclude=data/ai_cache/ --exclude=data/recordings/ \
  "$STAGE/" "$USER_AT:$APP/"

"${SSH[@]}" bash -s <<EOF
set -euo pipefail
chown -R mira:mira $APP
sudo -u mira $APP/.venv/bin/pip install -q -r $APP/server/requirements.txt
# The LCD kiosk needs its own least-privilege token; create one once, never print it.
if ! grep -q '^MIRA_DEVICE_TOKEN=.\+' $APP/.env; then
  sed -i '/^MIRA_DEVICE_TOKEN=/d' $APP/.env
  echo "MIRA_DEVICE_TOKEN=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> $APP/.env
  echo "generated MIRA_DEVICE_TOKEN in $APP/.env"
fi
# Idempotent schema migrations (server/db/migrations) against the box's TIGER_DSN (read from its .env).
(cd $APP && sudo -u mira $APP/.venv/bin/python -m server.db.migrate) || echo "WARNING: migrations failed; the server will retry at start"
systemctl restart mira
for i in \$(seq 1 20); do curl -fsS http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/health; echo
systemctl is-active mira
EOF
echo "deployed $SHA"
