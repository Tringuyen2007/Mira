#!/usr/bin/env python3
"""Development-only Mira REST/WS fixture. No third-party dependencies.

This is a provisional section-16 contract, NOT Tri's server or a clinical simulator.
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


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

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/ws/live", "/ws/pose", "/ws/events"):
            return self.websocket(path)
        if path == "/calibration/status":
            return self.reply({"board_refined": True, "cameras": [{"id": "A", "ok": True}, {"id": "B", "ok": True}]})
        if path == "/test/state":
            with STATE.lock:
                value = {"commands": list(STATE.commands), "connections": STATE.connections}
            return self.reply(value)
        if path.endswith("/progress"):
            return self.reply({"patient_id": "demo", "series": []})
        if path.endswith("/frames"):
            return self.reply([])
        if path.startswith("/prescriptions/"):
            return self.reply({"patient_id": "demo", "exercises": [{"id": "squat", "sets": 3, "reps": 10}]})
        if path.startswith("/sessions/"):
            return self.reply({"id": "mock-session", "status": "active"})
        if path == "/proposals":
            return self.reply([])
        self.reply({"error": "No mock route"}, 404)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 65536:
            return self.reply({"error": "Body too large"}, 413)
        try:
            body = json.loads(self.rfile.read(size)) if size else {}
        except ValueError:
            return self.reply({"error": "Invalid JSON"}, 400)
        path = urlparse(self.path).path
        if path == "/test/drop":
            with STATE.lock:
                clients = list(STATE.clients)
            for client in clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            return self.reply({"dropped": len(clients)})
        if path == "/sessions":
            return self.reply({"id": "mock-session", "status": "active"}, 201)
        if path == "/copilot/ask":
            return self.reply({"answer": "Mock answer", "charts": [], "sql": []})
        if (path.startswith("/sessions/") and path.rsplit("/", 1)[-1] in ("stop", "share", "cue")) or (
            path.startswith("/proposals/") and path.rsplit("/", 1)[-1] in ("approve", "edit", "dismiss")
        ):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.reply({"error": "No mock route", "body": body}, 404)

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
                    send({"type": "greeting", "audio_id": None, "text": "Connected to the development mock"})
                while not stopped.is_set():
                    with STATE.lock:
                        paused, rep = STATE.paused, STATE.rep
                        if path == "/ws/live" and tick % 20 == 0 and not paused:
                            STATE.rep = rep = (rep + 1) % 11
                    if path == "/ws/live":
                        send({"phase": "active", "exercise_id": "squat", "rep": rep, "reps_target": 10,
                              "countdown": None, "main_angle": round(65 + 10 * math.sin(tick / 10), 1),
                              "load_l_pct": 52, "flags": [], "cue_id": None, "paused": paused})
                    elif path == "/ws/pose":
                        send({"t": time.time(), "joints": [[0, i / 33, 0] for i in range(33)],
                              "confidence": [0.99] * 33, "load_l_pct": 52, "flags": []})
                    elif tick % 20 == 0:
                        send({"type": "cue", "id": "mock-cue", "text": "Keep your weight balanced"})
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
                    send({"type": "cue", "id": "command-received", "text": "Mock received: " + event.get("name", event.get("type", "unknown"))})
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
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mira development mock: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
