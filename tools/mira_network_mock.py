#!/usr/bin/env python3
"""Development-only Mira REST/WS fixture. No third-party dependencies.

Prefer Tri's real server with its mock engine (no keys or database needed):
    MIRA_SOURCE=mock DB_DISABLED=1 MIRA_API_TOKEN=<token> make run
This fixture remains for outage tests (/test/drop). It enforces the same auth as the
server (Bearer header on REST except /health, ?token= on WebSockets) and answers REST
with the server's own samples from server/samples/.
Bind localhost by default; pass --host 0.0.0.0 for a phone on a trusted LAN.
"""
import argparse
import base64
import hashlib
import json
import math
import socket
import struct
import threading
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SAMPLES = Path(__file__).resolve().parents[1] / "server" / "samples"
TOKEN = ""  # set in __main__; required like the real server's MIRA_API_TOKEN


def sample(name):
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def token_ok(given):
    return bool(TOKEN) and secrets.compare_digest(given.encode(), TOKEN.encode())


class MockState:
    def __init__(self):
        self.lock = threading.Lock()
        self.paused = False
        self.rep = 0
        self.commands = []
        self.clients = set()
        self.connections = 0


STATE = MockState()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and token_ok(auth[len("Bearer "):]):
            return True
        self.reply(sample("rest_error_401"), 401)
        return False

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in ("/ws/live", "/ws/pose", "/ws/events"):
            if not token_ok(parse_qs(url.query).get("token", [""])[0]):
                return self.reply({"detail": "forbidden"}, 403)  # the server closes with 1008 → HTTP 403
            return self.websocket(path)
        if path == "/health":
            return self.reply(sample("rest_get_health"))
        if path == "/test/state":
            with STATE.lock:
                value = {"commands": list(STATE.commands), "connections": STATE.connections}
            return self.reply(value)
        if not self.authorized():
            return None
        routes = {"/calibration/status": "rest_get_calibration_status", "/proposals": "rest_get_proposals",
                  "/alerts": "rest_get_alerts", "/notifications/count": "rest_get_notifications_count"}
        if path in routes:
            return self.reply(sample(routes[path]))
        if path.endswith("/progress"):
            return self.reply(sample("rest_get_patient_progress"))
        if path.endswith("/frames"):
            return self.reply(sample("rest_get_session_frames_lttb"))
        if path.startswith("/prescriptions/"):
            return self.reply(sample("rest_get_prescriptions"))
        if path.startswith("/sessions/"):
            return self.reply(sample("rest_get_session"))
        self.reply(sample("rest_error_404"), 404)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 65536:
            return self.reply({"error": "Body too large"}, 413)
        try:
            body = json.loads(self.rfile.read(size)) if size else {}
        except ValueError:
            return self.reply({"error": "Invalid JSON"}, 400)
        path = urlparse(self.path).path
        if path != "/test/drop" and not self.authorized():
            return None
        if path == "/test/drop":
            with STATE.lock:
                clients = list(STATE.clients)
            for client in clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            return self.reply({"dropped": len(clients)})
        routes = {"/sessions": "rest_post_sessions", "/copilot/ask": "rest_post_copilot_ask"}
        if path in routes:
            return self.reply(sample(routes[path]))
        last = path.rsplit("/", 1)[-1]
        if path.startswith("/sessions/") and last in ("stop", "share"):
            return self.reply(sample(f"rest_post_session_{last}"))
        if path.startswith("/proposals/") and last in ("approve", "edit", "dismiss"):
            return self.reply(sample("rest_post_proposal_approve"))
        if path.startswith("/alerts/") and last == "resolve":
            return self.reply(sample("rest_post_alert_resolve"))
        self.reply(sample("rest_error_404"), 404)

    def websocket(self, path):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            return self.reply({"error": "WebSocket upgrade required"}, 400)
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        stopped = threading.Event()
        send_lock = threading.Lock()

        def send(payload, opcode=1):
            if not isinstance(payload, bytes):
                payload = json.dumps(payload).encode()
            size = len(payload)
            header = bytes([0x80 | opcode, size]) if size < 126 else bytes([0x80 | opcode, 126]) + struct.pack("!H", size)
            with send_lock:
                self.connection.sendall(header + payload)

        def stream():
            tick = 0
            try:
                if path == "/ws/events":
                    send({"type": "greeting", "text": "Connected to the development mock", "audio_url": None})
                while not stopped.is_set():
                    with STATE.lock:
                        paused, rep = STATE.paused, STATE.rep
                        if path == "/ws/live" and tick % 20 == 0 and not paused:
                            STATE.rep = rep = (rep + 1) % 11
                    if path == "/ws/live":
                        send({"t": time.time(), "phase": "paused" if paused else "active", "exercise_id": "squat",
                              "rep": rep, "reps_target": 10, "set": 1, "sets_target": 3, "countdown": None,
                              "main_angle": round(65 + 10 * math.sin(tick / 10), 1), "load_l_pct": 52,
                              "load_conf": 0.95, "hr": 100, "flags": [], "cue_id": None, "paused": paused})
                    elif path == "/ws/pose":
                        send({"t": time.time(), "joints": [[0, i / 33, 0] for i in range(33)],
                              "confidence": [0.99] * 33, "load_l_pct": 52, "flags": []})
                    elif tick % 20 == 0:
                        send({"type": "cue", "id": "mock-cue", "text": "Keep your weight balanced", "audio_url": None})
                    tick += 1
                    stopped.wait(0.05 if path == "/ws/pose" else 0.1)
            except OSError:
                stopped.set()

        def read_exact(size):
            data = self.rfile.read(size)
            if len(data) != size:
                raise EOFError()
            return data

        with STATE.lock:
            STATE.clients.add(self.connection)
            STATE.connections += 1
        producer = threading.Thread(target=stream, daemon=True)
        producer.start()
        try:
            while not stopped.is_set():
                first, second = read_exact(2)
                opcode, size = first & 15, second & 127
                if size == 126:
                    size = struct.unpack("!H", read_exact(2))[0]
                elif size == 127:
                    size = struct.unpack("!Q", read_exact(8))[0]
                if size > 65536 or not second & 128 or not first & 128:
                    break  # Development mock accepts masked, unfragmented frames only.
                mask = read_exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(read_exact(size)))
                if opcode == 8:
                    send(data, 8)
                    break
                if opcode == 9:
                    send(data, 10)
                elif opcode == 1 and path == "/ws/events":
                    event = json.loads(data)
                    with STATE.lock:
                        STATE.commands.append(event)
                        STATE.commands[:] = STATE.commands[-100:]
                        if event.get("type") == "command":
                            name = event.get("name")
                            if name in ("pause", "stop"):
                                STATE.paused = True
                            elif name == "start":
                                STATE.paused = False
                            elif name == "skip":
                                STATE.rep = 0
                    if event.get("type") in ("command", "hr", "wrist", "watch_button", "device_health"):
                        send({**event, "t": time.time()})  # the server echoes accepted client messages
        except (OSError, EOFError, ValueError):
            pass
        finally:
            stopped.set()
            producer.join(timeout=1)
            with STATE.lock:
                STATE.clients.discard(self.connection)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    TOKEN = os.environ.get("MIRA_API_TOKEN", "")
    if not TOKEN:
        parser.error("set MIRA_API_TOKEN (the same token the app uses), like the real server")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mira development mock: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
