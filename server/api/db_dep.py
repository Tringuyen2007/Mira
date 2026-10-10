"""FastAPI dependency for routes that need the database."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from server.db.pool import Database
from server.logging_setup import get_logger
from server.state import get_state

log = get_logger("api")

NO_DB_DETAIL = "database unavailable: TIGER_DSN is not set or DB_DISABLED=1"


def require_db() -> Database:
    db = get_state().db
    if db is None:
        raise HTTPException(status_code=503, detail=NO_DB_DETAIL)
    return db


async def db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A DBError escaping a route becomes 503 with a short, DSN-free reason."""
    log.warning("database error in request", extra={"path": request.url.path, "error": str(exc)})
    return JSONResponse(status_code=503, content={"detail": f"database unavailable: {exc}"})

