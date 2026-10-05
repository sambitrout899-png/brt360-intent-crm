"""Field technician matching (INT-12) - HLD Layer 3."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.assurance import Technician


def match_technician(db: Session, tenant_id: int, required_skill: str, region: str) -> Technician | None:
    candidates = (
        db.query(Technician)
        .filter(Technician.tenant_id == tenant_id)
        .filter(Technician.status == "available")
        .filter(Technician.skills.ilike(f"%{required_skill}%"))
        .all()
    )
    same_region = [t for t in candidates if t.region == region]
    if same_region:
        return same_region[0]
    return candidates[0] if candidates else None
