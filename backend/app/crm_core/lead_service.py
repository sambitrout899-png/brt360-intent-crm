"""Lead qualification logic (INT-04) - HLD Layer 3."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models.crm import TerritoryRule

_SIZE_BANDS = ["small", "medium", "large"]
_RISK_BANDS = ["low", "medium", "high"]


def enrich_lead(company_name: str) -> dict:
    """Deterministic stand-in for a real firmographic/credit-risk enrichment
    API (HLD 'external enrichment adapter') - same company name always
    enriches the same way, which keeps this testable without a live vendor.
    """
    digest = int(hashlib.sha256(company_name.strip().lower().encode()).hexdigest(), 16)
    return {
        "size_band": _SIZE_BANDS[digest % 3],
        "credit_risk": _RISK_BANDS[(digest // 3) % 3],
    }


def score_lead(enrichment: dict) -> float:
    """Stand-in propensity model: bigger company, lower risk -> higher score."""
    base = {"small": 0.4, "medium": 0.6, "large": 0.85}[enrichment["size_band"]]
    penalty = {"low": 0.0, "medium": 0.1, "high": 0.25}[enrichment["credit_risk"]]
    return round(max(0.0, min(1.0, base - penalty)), 2)


def assign_territory_owner(db: Session, region: str) -> str:
    rule = db.query(TerritoryRule).filter(TerritoryRule.region == region).first()
    if rule is None:
        rule = db.query(TerritoryRule).filter(TerritoryRule.region == "ALL").first()
    return rule.owner_name if rule else "Unassigned"
