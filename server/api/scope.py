"""Which patient a request acts for (shared by /me and the care routes, so neither router imports the other)."""

from __future__ import annotations

from fastapi import HTTPException

from server import security


def patient_for(principal: security.Principal, patient_id: str | None) -> str:
    """Patients act as themselves; the admin token must name a patient with ?patient_id=."""
    if principal.scope == "patient":
        assert principal.patient_id is not None
        return principal.patient_id
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required with the API token")
    return patient_id
