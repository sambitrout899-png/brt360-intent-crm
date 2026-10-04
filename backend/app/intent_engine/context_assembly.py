"""Context Assembly Service (HLD 4.2).

Fans out to the systems of record relevant to the resolved intent and
returns one normalized context object, before any plan is proposed. HLD
specifies a parallel-fetch aggregator with a 60-120s short-lived cache to
avoid redundant calls within one intent execution; Sprint 1's "systems of
record" are all local-DB reads (no external OSS/BRM yet), so the cache here
is a simple in-process TTL dict - the interface is what matters for later
sprints, when some of these fetches become real outbound calls.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.integration import tmf620_adapter
from app.models.catalog import BundleCompatibilityRule, ProductOffering

_TTL_SECONDS = 90
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fetch) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def assemble_context(db: Session, intent_type: str, entities: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {"intent_type": intent_type}

    if intent_type == "INT-01":
        segment = entities.get("segment", "B2C")
        context["compatibility_rules"] = _cached(
            "compat_rules",
            lambda: [
                {"primary": r.primary_product_line, "addon": r.addon_product_line, "max_addons": r.max_addons}
                for r in db.query(BundleCompatibilityRule).all()
            ],
        )
        context["existing_offerings_in_segment"] = [
            o.name for o in db.query(ProductOffering).filter(ProductOffering.segment == segment).all()
        ]

    elif intent_type in ("INT-02", "INT-03"):
        offering_name = entities.get("offering_name", "")
        current = tmf620_adapter.get_active_offering_by_name(db, offering_name) if offering_name else None
        context["current_offering"] = (
            {
                "id": current.id,
                "version": current.version,
                "price": current.price,
                "segment": current.segment,
                "composition": current.composition,
            }
            if current
            else None
        )
        if intent_type == "INT-03":
            replacement_name = entities.get("replacement_offering_name")
            replacement = tmf620_adapter.get_active_offering_by_name(db, replacement_name) if replacement_name else None
            context["replacement_offering"] = {"id": replacement.id, "name": replacement.name} if replacement else None

    return context
