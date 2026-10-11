"""Start or stop a Mira session the way the iOS app does, without the phone.

    .venv/bin/python tools/demo_session.py start [--host http://127.0.0.1:8000] [--patient maya] [--exercise squat]
    .venv/bin/python tools/demo_session.py stop

Uses MIRA_API_TOKEN from the environment or .env to mint a pairing code, pairs a "cli" device (a patient-scoped
token, which is what arms the remote camera), and POSTs /sessions. The patient token is cached in .demo_patient_token
so `stop` and later starts reuse it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".demo_patient_token"


def api_token() -> str:
    tok = os.environ.get("MIRA_API_TOKEN", "")
    if not tok and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("MIRA_API_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    if not tok:
        sys.exit("MIRA_API_TOKEN is not set (env or .env)")
    return tok


def patient_token(c: httpx.Client, patient: str) -> str:
    if CACHE.exists():
        cached = CACHE.read_text().strip()
        if cached and c.get("/me", headers={"Authorization": f"Bearer {cached}"}).status_code == 200:
            return cached
    code = c.post(f"/patients/{patient}/pairing-code", headers={"Authorization": f"Bearer {api_token()}"})
    code.raise_for_status()
    pair = c.post("/pair", json={"code": code.json()["code"], "device_name": "demo-cli",
                                 "profile": {"name": "Maya Ellis", "height_cm": 163, "mass_kg": 70}})
    pair.raise_for_status()
    token = pair.json()["token"]
    CACHE.write_text(token)
    return token


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("start", "stop"))
    ap.add_argument("--host", default=os.environ.get("MIRA_HOST_HTTP", "http://127.0.0.1:8000"))
    ap.add_argument("--patient", default="maya")
    ap.add_argument("--exercise", default="squat")
    args = ap.parse_args()
    with httpx.Client(base_url=args.host, timeout=20) as c:
        pt = patient_token(c, args.patient)
        auth = {"Authorization": f"Bearer {pt}"}
        if args.action == "stop":
            me = c.get("/me", headers=auth).json()
            sid = (me.get("session") or {}).get("session_id") or (me.get("live") or {}).get("session_id")
            if not sid:
                r = c.get("/live/media", headers={"Authorization": f"Bearer {api_token()}"})
                sid = r.json().get("session_id") if r.status_code == 200 else None
            if not sid:
                print("no running session"); return
            r = c.post(f"/sessions/{sid}/stop", headers=auth); print("stop", r.status_code, r.json()); return
        r = c.post("/sessions", json={"patient_id": args.patient, "exercise_id": args.exercise}, headers=auth)
        if r.status_code != 200:
            sys.exit(f"start failed: {r.status_code} {r.text}")
        sid = r.json()["session_id"]
        from urllib.parse import urlsplit
        parts = urlsplit(args.host)
        print("session id :", sid)
        print("WHIP URL   :", f"{parts.scheme}://{parts.hostname}:8889/mira/{sid}/whip")
        print("dashboard  :", f"{args.host}/dashboard/")


if __name__ == "__main__":
    main()
