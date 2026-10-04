"""Discount Approval Matrix (INT-06) - HLD 5.2 Master & Reference Data.

Bands below are illustrative, exactly as the HLD itself flags in Appendix B
("must be replaced with [client]'s actual authority matrix before UAT") -
kept here as a single named constant so that replacement is a one-line change.
"""

from __future__ import annotations

# (discount_pct ceiling, approver tier) - first matching ceiling wins.
DISCOUNT_MATRIX: list[tuple[float, str]] = [
    (10.0, "Auto-Approved"),
    (20.0, "Regional Sales Head"),
    (30.0, "VP Sales"),
    (100.0, "CFO"),
]


def determine_approver_tier(discount_pct: float) -> str:
    for ceiling, tier in DISCOUNT_MATRIX:
        if discount_pct <= ceiling:
            return tier
    return "CFO"
