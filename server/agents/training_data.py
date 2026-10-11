"""Training-data registry for the rep-counting vision contract.

The MVP forbids storing video.  This module imports only provenance and
approved skeleton/rep metadata into Tiger Data; an approved downloader may be
added later for pose-only source files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from server.db import writes
from server.db.pool import Database


DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "data" / "training" / "exercise_datasets.json"
REQUIRED_FIELDS = {"id", "name", "source_url", "license", "approval", "exercises", "raw_media_allowed"}


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    """Load and validate provenance records without downloading any dataset."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("training manifest must be a list")
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or REQUIRED_FIELDS - item.keys():
            raise ValueError("each dataset needs id, provenance, approval and raw-media policy")
        if item["id"] in seen:
            raise ValueError(f"duplicate dataset id: {item['id']}")
        if item["raw_media_allowed"] is not False:
            raise ValueError(f"{item['id']}: Mira training registry must not permit raw media")
        if item["approval"] not in {"approved", "license_review_required"}:
            raise ValueError(f"{item['id']}: invalid approval state")
        seen.add(item["id"])
    return raw


async def register_manifest(db: Database | None, path: Path = DEFAULT_MANIFEST) -> int:
    """Upsert source records in Tiger.  No remote request and no patient data."""
    records = load_manifest(path)
    failed: list[str] = []
    for record in records:
        if not await writes.upsert_training_dataset(db, record):
            failed.append(record["id"])
    if failed:
        raise RuntimeError(f"Tiger did not register training dataset(s): {', '.join(failed)}")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="register Mira pose-only training data provenance in Tiger")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dsn", help="Tiger owner DSN (defaults to TIGER_DSN)")
    parser.add_argument("--check", action="store_true", help="validate the manifest without database writes")
    args = parser.parse_args(argv)
    records = load_manifest(args.manifest)
    if args.check:
        print(f"valid: {len(records)} training dataset records")
        return 0
    dsn = args.dsn
    if not dsn:
        from server.config import cfg
        dsn = cfg.TIGER_DSN
    if not dsn:
        parser.error("pass --dsn or set TIGER_DSN; use --check to validate without Tiger")
    async def run() -> int:
        db = Database(dsn)
        await db.open()
        try:
            return await register_manifest(db, args.manifest)
        finally:
            await db.close()
    print(f"registered {asyncio.run(run())} training dataset records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
