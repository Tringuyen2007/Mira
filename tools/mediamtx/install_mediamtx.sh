#!/usr/bin/env bash
# Install/upgrade MediaMTX on the Mira Vultr box and point the Mira server at it. Run FROM YOUR LAPTOP:
#
#   tools/mediamtx/install_mediamtx.sh            # uses MIRA_HOST / MIRA_SSH_KEY like deploy_vultr.sh
#
# What it does on the box: downloads the MediaMTX release for linux/amd64 into /usr/local/bin, installs
# tools/mediamtx/mediamtx.yml (+ systemd unit) under /opt/mira/mediamtx, opens 8889/tcp and 8189/udp+tcp in ufw
# if ufw is active, sets MIRA_WHEP_URL in /opt/mira/app/.env (never printed) and restarts both services.
set -euo pipefail

VERSION=${MEDIAMTX_VERSION:-v1.21.2}
HOST=${MIRA_HOST:-45.76.255.253}
USER_AT=${MIRA_SSH_USER:-root}@$HOST
KEY=${MIRA_SSH_KEY:-$HOME/.ssh/mira_vultr}
HERE=$(cd "$(dirname "$0")" && pwd)
SSH=(ssh -i "$KEY" -o BatchMode=yes "$USER_AT")

scp -q -i "$KEY" "$HERE/mediamtx.yml" "$HERE/mediamtx.service" "$USER_AT:/tmp/"
"${SSH[@]}" bash -s <<EOS
set -euo pipefail
if ! /usr/local/bin/mediamtx --version 2>/dev/null | grep -q "$VERSION"; then
  cd /tmp && curl -fsSL -o mediamtx.tgz "https://github.com/bluenviron/mediamtx/releases/download/$VERSION/mediamtx_${VERSION}_linux_amd64.tar.gz"
  tar -xzf mediamtx.tgz mediamtx && install -m 755 mediamtx /usr/local/bin/mediamtx && rm -f mediamtx mediamtx.tgz
fi
mkdir -p /opt/mira/mediamtx
sed "s/^webrtcAdditionalHosts:.*/webrtcAdditionalHosts: [$HOST]/" /tmp/mediamtx.yml > /opt/mira/mediamtx/mediamtx.yml
chown -R mira:mira /opt/mira/mediamtx
install -m 644 /tmp/mediamtx.service /etc/systemd/system/mediamtx.service
rm -f /tmp/mediamtx.yml /tmp/mediamtx.service
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 8889/tcp >/dev/null; ufw allow 8189/udp >/dev/null; ufw allow 8189/tcp >/dev/null
fi
ENV=/opt/mira/app/.env
grep -q '^MIRA_WHEP_URL=' \$ENV && sed -i "s#^MIRA_WHEP_URL=.*#MIRA_WHEP_URL=http://$HOST:8889/mira/{session_id}/whep#" \$ENV \
  || echo "MIRA_WHEP_URL=http://$HOST:8889/mira/{session_id}/whep" >> \$ENV
systemctl daemon-reload
systemctl enable --now mediamtx >/dev/null
systemctl restart mediamtx
systemctl restart mira
sleep 3
systemctl is-active mediamtx mira
curl -fsS http://127.0.0.1:8000/health
echo
EOS
echo "MediaMTX $VERSION is up on $HOST: WHIP http://$HOST:8889/mira/<session_id>/whip, WHEP http://$HOST:8889/mira/<session_id>/whep"
