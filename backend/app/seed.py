"""Seed the "default" tenant + its reference data: atomic products, bundle
compatibility rules, territory ownership, and field technician skill matrix.

HLD INT-01 precondition: "Underlying atomic products already exist in the
catalog; pricing guardrails and segment eligibility rules are defined as
reference data." Run with: python -m app.seed

Multi-tenant note: this seeds exactly one tenant (slug "default") so the
existing single-tenant deployment and the pre-multi-tenant test suite keep
working unchanged (app.tenancy falls back to this slug when no
X-Tenant-Slug header is sent). A second tenant is just another call to
get_or_create_tenant() with its own reference data - nothing here is
special-cased to "default" beyond being the fallback.
"""

from __future__ import annotations

from app.crm_core.approval_service import DEFAULT_SEED_TEMPLATES
from app.db import SessionLocal
from app.models.approval import ApprovalChainTemplate, ApprovalChainTier
from app.models.assurance import Technician
from app.models.catalog import BundleCompatibilityRule, ProductSpecification
from app.models.crm import TerritoryRule
from app.models.tenant import Tenant, TenantVertical
from app.tenancy import DEFAULT_TENANT_SLUG

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


def get_or_create_tenant(db, slug: str, name: str, vertical: TenantVertical = TenantVertical.both) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
    if tenant is None:
        tenant = Tenant(slug=slug, name=name, vertical=vertical)
        db.add(tenant)
        db.flush()
    return tenant


def seed_reference_data(db, tenant_id: int) -> None:
    existing = {p.name for p in db.query(ProductSpecification).filter(ProductSpecification.tenant_id == tenant_id).all()}
    for name, line in ATOMIC_PRODUCTS:
        if name not in existing:
            db.add(ProductSpecification(tenant_id=tenant_id, name=name, product_line=line, is_atomic=True))

    if db.query(BundleCompatibilityRule).filter(BundleCompatibilityRule.tenant_id == tenant_id).count() == 0:
        for primary, addon, max_addons in COMPATIBILITY_RULES:
            db.add(
                BundleCompatibilityRule(
                    tenant_id=tenant_id, primary_product_line=primary, addon_product_line=addon, max_addons=max_addons
                )
            )

    if db.query(TerritoryRule).filter(TerritoryRule.tenant_id == tenant_id).count() == 0:
        for region, owner_name in TERRITORY_RULES:
            db.add(TerritoryRule(tenant_id=tenant_id, region=region, owner_name=owner_name))

    if db.query(Technician).filter(Technician.tenant_id == tenant_id).count() == 0:
        for name, skills, region in TECHNICIANS:
            db.add(Technician(tenant_id=tenant_id, name=name, skills=skills, region=region, status="available"))

    if db.query(ApprovalChainTemplate).filter(ApprovalChainTemplate.tenant_id == tenant_id).count() == 0:
        for name, segment, min_pct, max_pct, tiers in DEFAULT_SEED_TEMPLATES:
            template = ApprovalChainTemplate(
                tenant_id=tenant_id, name=name, segment=segment, min_discount_pct=min_pct, max_discount_pct=max_pct
            )
            db.add(template)
            db.flush()
            for sequence, approver_tier in enumerate(tiers, start=1):
                db.add(ApprovalChainTier(template_id=template.id, sequence=sequence, approver_tier=approver_tier))


def seed() -> None:
    db = SessionLocal()
    try:
        tenant = get_or_create_tenant(db, DEFAULT_TENANT_SLUG, "Default Tenant")
        seed_reference_data(db, tenant.id)
        db.commit()
        print(
            f"Seeded tenant '{tenant.slug}' (id={tenant.id}) with {len(ATOMIC_PRODUCTS)} product specifications, "
            f"{len(COMPATIBILITY_RULES)} compatibility rules, {len(TERRITORY_RULES)} territory rules, "
            f"{len(TECHNICIANS)} technicians, {len(DEFAULT_SEED_TEMPLATES)} approval chain templates."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()
