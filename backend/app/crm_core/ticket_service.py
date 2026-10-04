"""Complaint classification + priority assignment (INT-10) - HLD Layer 3.

classify() is a deterministic keyword rule-set standing in for the HLD's
"complaint taxonomy model" NLU step - same role as classifier.py's rule-based
fallback: testable without a live model, swappable for a real one later.
"""

from __future__ import annotations

_SEVERITY_KEYWORDS: list[tuple[str, str]] = [
    ("down", "critical"),
    ("outage", "critical"),
    ("no service", "critical"),
    ("not working", "high"),
    ("slow", "medium"),
    ("intermittent", "medium"),
    ("billing", "low"),
    ("question", "low"),
]

_PRODUCT_LINE_KEYWORDS: list[tuple[str, str]] = [
    ("fiber", "connectivity"),
    ("internet", "connectivity"),
    ("voip", "voip"),
    ("call", "voip"),
    ("mobile", "mobile"),
    ("sim", "mobile"),
    ("bill", "billing"),
]

_SLA_PRIORITY_MATRIX: dict[str, dict[str, str]] = {
    "premium": {"critical": "P1", "high": "P1", "medium": "P2", "low": "P3"},
    "standard": {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"},
}


def classify(description: str) -> dict:
    lowered = description.lower()
    severity = next((sev for kw, sev in _SEVERITY_KEYWORDS if kw in lowered), "medium")
    product_line = next((line for kw, line in _PRODUCT_LINE_KEYWORDS if kw in lowered), "general")
    category = f"{product_line}_{severity}"
    return {"category": category, "severity": severity, "product_line": product_line}


def assign_priority(sla_class: str, severity: str) -> str:
    matrix = _SLA_PRIORITY_MATRIX.get(sla_class, _SLA_PRIORITY_MATRIX["standard"])
    return matrix.get(severity, "P3")
