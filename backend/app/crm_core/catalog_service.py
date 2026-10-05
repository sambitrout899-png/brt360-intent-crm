"""Bundle & Offer Catalog service (BRM PDC-style) - HLD Layer 3.

Holds the actual business rules for composition validation, pricing and
eligibility. The Orchestrator (Layer 4) calls these functions as saga steps;
these functions never talk to the Integration layer directly except through
tmf620_adapter, keeping the TMF-shaped contract as the only seam.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.catalog import BundleCompatibilityRule, ProductSpecification


class CompositionError(ValueError):
    """Raised when a proposed bundle composition violates compatibility rules."""


class PricingError(ValueError):
    """Raised when a computed price would fall below the configured price floor."""


class MigrationPathRequiredError(ValueError):
    """Raised when retiring a bundle with no replacement specified (HLD hard rule)."""


@dataclass
class CompositionCheckResult:
    primary_line: str
    addon_lines: list[str]
    ok: bool
    reason: str = ""


def validate_composition(db: Session, tenant_id: int, product_names: list[str]) -> CompositionCheckResult:
    """Step: validate proposed bundle composition against compatibility rules.

    First product in the list is treated as the primary connectivity line;
    the rest are add-ons, per HLD INT-01 business rule ("one primary
    connectivity line + N add-ons"). Both ProductSpecification and
    BundleCompatibilityRule are tenant-configurable reference data - a
    telecom's product lines and bundling rules are its own, not shared
    across tenants.
    """
    if not product_names:
        return CompositionCheckResult("", [], False, "No products specified")

    specs = {
        s.name: s
        for s in db.query(ProductSpecification)
        .filter(ProductSpecification.tenant_id == tenant_id, ProductSpecification.name.in_(product_names))
        .all()
    }
    missing = [n for n in product_names if n not in specs]
    if missing:
        return CompositionCheckResult(product_names[0], product_names[1:], False, f"Unknown product(s): {missing}")

    primary_name, *addon_names = product_names
    primary_line = specs[primary_name].product_line
    addon_lines = [specs[n].product_line for n in addon_names]

    if len(addon_names) > 0:
        rules = (
            db.query(BundleCompatibilityRule)
            .filter(BundleCompatibilityRule.tenant_id == tenant_id, BundleCompatibilityRule.primary_product_line == primary_line)
            .all()
        )
        allowed_addon_lines = {r.addon_product_line for r in rules}
        max_addons = max((r.max_addons for r in rules), default=0)

        bad_addons = [line for line in addon_lines if line not in allowed_addon_lines]
        if bad_addons:
            return CompositionCheckResult(
                primary_line, addon_lines, False, f"Add-on line(s) not compatible with {primary_line}: {bad_addons}"
            )
        if len(addon_names) > max_addons:
            return CompositionCheckResult(
                primary_line, addon_lines, False, f"Too many add-ons: {len(addon_names)} > max {max_addons}"
            )

    return CompositionCheckResult(primary_line, addon_lines, True)


def compute_price(list_price: float, requested_discount_pct: float, discount_ceiling_pct: float, price_floor: float) -> float:
    """Step: compute price using PDC-style pricing (list price, discount ceiling)."""
    discount_pct = min(requested_discount_pct, discount_ceiling_pct)
    price = list_price * (1 - discount_pct / 100)
    if price < price_floor:
        raise PricingError(f"Computed price {price:.2f} is below the price floor {price_floor:.2f}")
    return round(price, 2)


def generate_eligibility(segment: str, channel: str, region: str = "ALL") -> dict[str, str]:
    """Step: generate eligibility & channel rules (B2B/B2C, region)."""
    return {"segment": segment, "channel": channel, "region": region}
