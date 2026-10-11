#!/usr/bin/env bash
# One-laptop Mira demo: server + video relay + vision agent + publisher page, all on this machine.
#
#   tools/demo_local.sh [agent args...]        e.g.  tools/demo_local.sh --exercise squat --reps 3 --height-cm 170
#   tools/demo_local.sh --synthetic            no camera: scripted squat
#
# Needs: repo root, .venv with server/requirements.txt + vision/requirements.txt, a .env with TIGER_DSN (pairing and
# the caseload need the database), and optionally `mediamtx` on PATH (brew install mediamtx) for camera video.
# Then: open http://127.0.0.1:8000/dashboard/ (API token from .env, therapist id okafor) and start a session with
#   .venv/bin/python tools/demo_session.py start      (prints the session id and WHIP URL; `stop` ends it)
# Ctrl-C stops everything. Nothing here touches the Vultr box.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing (copy .env.example and set TIGER_DSN + MIRA_API_TOKEN)"; exit 2; }
PY=${PY:-.venv/bin/python}
PORT=${MIRA_PORT:-8000}
set -a; source .env; set +a
# The local demo always uses the database (pairing + caseload need it) and never places real calls unless asked.
export MIRA_SOURCE=remote DB_DISABLED=${DEMO_DB_DISABLED:-0} CALLS_DISABLED=${DEMO_CALLS_LIVE:+0}
export CALLS_DISABLED=${CALLS_DISABLED:-1}
export MIRA_VISION_TOKEN=${MIRA_VISION_TOKEN:-local-demo-vision-$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')}
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1)
RUN=$(mktemp -d); PIDS=()
cleanup() { echo; echo "stopping demo"; for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$RUN"; }
trap cleanup EXIT INT TERM

if command -v mediamtx >/dev/null; then
  sed "s#http://127.0.0.1:8000/media/auth#http://127.0.0.1:$PORT/media/auth#; s/^webrtcAdditionalHosts:.*/webrtcAdditionalHosts: [$LAN_IP]/" \
    tools/mediamtx/mediamtx.yml > "$RUN/mediamtx.yml"
  mediamtx "$RUN/mediamtx.yml" > "$RUN/mediamtx.log" 2>&1 & PIDS+=($!)
  export MIRA_WHEP_URL="http://$LAN_IP:8889/mira/{session_id}/whep"
  echo "video relay: WHIP http://$LAN_IP:8889/mira/<session_id>/whip"
else
  echo "mediamtx not installed: 3D only, no camera video (brew install mediamtx)"
fi

$PY -m uvicorn server.main:app --host 0.0.0.0 --port "$PORT" --workers 1 > "$RUN/server.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 40); do curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://127.0.0.1:$PORT/health" || { echo "server failed to start:"; tail -20 "$RUN/server.log"; exit 1; }; echo

(cd tools && python3 -m http.server 8077 --bind 127.0.0.1 > /dev/null 2>&1) & PIDS+=($!)
echo "dashboard : http://$LAN_IP:$PORT/dashboard/   (API token from .env, therapist id okafor)"
echo "LCD       : http://$LAN_IP:$PORT/lcd-web/"
echo "phone     : server address http://$LAN_IP:$PORT  (Settings -> Developer connection tools)"
echo "publisher : http://127.0.0.1:8077/whip_publish.html  (opens itself when a session starts)"
echo "start/stop: .venv/bin/python tools/demo_session.py start | stop"
echo "logs      : $RUN"
echo

exec $PY -m vision.agent --host "http://127.0.0.1:$PORT" --token "$MIRA_VISION_TOKEN" \
  --whip-url "http://127.0.0.1:8889/mira/{session_id}/whip" \
  --whip-cmd "open http://127.0.0.1:8077/whip_publish.html?autostart=1&token=$MIRA_VISION_TOKEN&url={whip_url}" \
  "$@"
