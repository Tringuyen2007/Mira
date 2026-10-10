"""Compress finished pose_frames chunks one day at a time, proving each one lossless (§5.8, §5.10 check 6).

usage: python -m server.db.compress_history "$DSN"
For each 1-day chunk: fingerprint (rows + hash) → convert_to_columnstore → fingerprint; logs a
`compress_verified` row in data_checks and prints the overall before/after ratio.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from server.agents.data_guard import DataGuard
from server.db import queries
from server.db.pool import Database


def _mb(n: int | None) -> str:
    return f"{n / 1e6:.1f} MB" if n else "?"


async def compress_history(dsn: str, min_age_days: int = 1, verbose: bool = True) -> dict[str, Any]:
    db = Database(dsn, min_size=1, max_size=2)
    await db.open()
    try:
        results = await DataGuard(db).compress_finished_chunks(min_age_days)
        for r in results:
            d = r["details"]
            if verbose:
                print(f"  compress_verified | {r['status']} | {d['range_start'][:10]}: {d['rows']:,} rows, "
                      f"fingerprint {'match' if d['fingerprint_match'] else 'MISMATCH'}, "
                      f"{_mb(d['bytes_before'])} → {_mb(d['bytes_after'])}")
        stats = await queries.compression(db)
    finally:
        await db.close()
    return {"chunks": len(results), "failed": sum(r["status"] != "ok" for r in results), **stats}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m server.db.compress_history <dsn>", file=sys.stderr)
        return 2
    out = asyncio.run(compress_history(argv[1]))
    print(f"compressed {out['chunks']} chunks ({out['failed']} failed); "
          f"{_mb(out['before_bytes'])} → {_mb(out['after_bytes'])}, ratio {out['ratio']}")
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
