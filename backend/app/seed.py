"""Seed reference data: atomic products, bundle compatibility rules,
territory ownership, and field technician skill matrix.

HLD INT-01 precondition: "Underlying atomic products already exist in the
catalog; pricing guardrails and segment eligibility rules are defined as
reference data." Run with: python -m app.seed
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models.assurance import Technician
from app.models.catalog import BundleCompatibilityRule, ProductSpecification
from app.models.crm import TerritoryRule

ATOMIC_PRODUCTS = [
    ("Fiber100", "connectivity"),
    ("Fiber500", "connectivity"),
    ("Fiber1000", "connectivity"),
    ("VOIP", "voip"),
    ("MobileLine", "mobile"),
    ("StaticIP", "addon"),
    ("CloudBackup", "addon"),
    ("SecuritySuite", "addon"),
]

COMPATIBILITY_RULES = [
    # primary_line, addon_line, max_addons
    ("connectivity", "voip", 1),
    ("connectivity", "mobile", 3),
    ("connectivity", "addon", 5),
]

TERRITORY_RULES = [
    # region, owner_name
    ("North", "Asha Mehta"),
    ("South", "Ravi Kumar"),
    ("ALL", "Unassigned Pool"),
]

TECHNICIANS = [
    # name, skills (comma-separated), region
    ("Dev Prakash", "fiber_install,network", "North"),
    ("Simran Kaur", "fiber_install,network,mobile", "South"),
    ("general", "general", "ALL"),
]


def seed() -> None:
    db = SessionLocal()
    try:
        existing = {p.name for p in db.query(ProductSpecification).all()}
        for name, line in ATOMIC_PRODUCTS:
            if name not in existing:
                db.add(ProductSpecification(name=name, product_line=line, is_atomic=True))

        if db.query(BundleCompatibilityRule).count() == 0:
            for primary, addon, max_addons in COMPATIBILITY_RULES:
                db.add(
                    BundleCompatibilityRule(
                        primary_product_line=primary, addon_product_line=addon, max_addons=max_addons
                    )
                )

        if db.query(TerritoryRule).count() == 0:
            for region, owner_name in TERRITORY_RULES:
                db.add(TerritoryRule(region=region, owner_name=owner_name))

        if db.query(Technician).count() == 0:
            for name, skills, region in TECHNICIANS:
                db.add(Technician(name=name, skills=skills, region=region, status="available"))

        db.commit()
        print(
            f"Seeded {len(ATOMIC_PRODUCTS)} product specifications, {len(COMPATIBILITY_RULES)} compatibility rules, "
            f"{len(TERRITORY_RULES)} territory rules, {len(TECHNICIANS)} technicians."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
