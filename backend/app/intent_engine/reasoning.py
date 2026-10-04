"""Reasoning & Planning Engine (HLD 4.3).

Per the implementation plan's design decision: the orchestration *steps* for
INT-01/02/03 are deterministic (see orchestration/actions.py), taken directly
from HLD Section 7. This module's job is the part that genuinely benefits
from reasoning: deciding whether the classified intent has enough to safely
auto-execute, and producing the confidence + rationale that AI Governance
(HLD 8.5) requires to accompany every action.

Plans at or above auto_execute_confidence_threshold (app.config) with no
missing required fields execute automatically; everything else is returned
with status "needs_review" for a human to approve before the Orchestrator
is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import get_settings


@dataclass
class ReasoningResult:
    confidence: float
    rationale: str
    auto_execute: bool
    missing_fields: list[str]


_REQUIRED_FIELDS: dict[str, list[str]] = {
    "INT-01": ["name", "composition", "list_price"],
    "INT-02": ["offering_name"],
    "INT-03": ["offering_name", "replacement_offering_name"],
}


def _missing_fields(intent_type: str, entities: dict[str, Any]) -> list[str]:
    required = _REQUIRED_FIELDS.get(intent_type, [])
    missing = []
    for field_name in required:
        value = entities.get(field_name)
        if value is None or value == "" or value == []:
            missing.append(field_name)
    return missing


def _context_missing(intent_type: str, context: dict[str, Any]) -> list[str]:
    missing = []
    if intent_type in ("INT-02", "INT-03") and context.get("current_offering") is None:
        missing.append("current_offering (not found or not active)")
    if intent_type == "INT-03" and context.get("replacement_offering") is None:
        missing.append("replacement_offering (not found or not active)")
    return missing


def plan(
    intent_type: str | None,
    entities: dict[str, Any],
    context: dict[str, Any],
    classifier_confidence: float,
    classifier_rationale: str,
) -> ReasoningResult:
    settings = get_settings()

    if intent_type is None:
        return ReasoningResult(0.0, "Could not resolve a known intent from the input.", False, ["intent_type"])

    missing = _missing_fields(intent_type, entities) + _context_missing(intent_type, context)
    confidence = classifier_confidence
    if missing:
        confidence = min(confidence, 0.5)

    auto_execute = confidence >= settings.auto_execute_confidence_threshold and not missing

    rationale_parts = [classifier_rationale]
    if missing:
        rationale_parts.append(f"Missing/unresolved: {', '.join(missing)} - routed to human review.")
    elif auto_execute:
        rationale_parts.append(
            f"Confidence {confidence:.2f} meets the auto-execute threshold "
            f"({settings.auto_execute_confidence_threshold}); executing the fixed {intent_type} step sequence."
        )
    else:
        rationale_parts.append(
            f"Confidence {confidence:.2f} below the auto-execute threshold "
            f"({settings.auto_execute_confidence_threshold}); routed to human review."
        )

    return ReasoningResult(confidence, " ".join(rationale_parts), auto_execute, missing)
