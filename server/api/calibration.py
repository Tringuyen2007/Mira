"""Camera calibration status (§4.4). Placeholder: the vision team (Eric) fills in the content."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/calibration/status")
async def calibration_status() -> dict[str, Any]:
    return {"status": "unknown", "calibrated": False, "cameras": [], "errors": None,
            "note": "placeholder until the vision engine reports calibration"}
