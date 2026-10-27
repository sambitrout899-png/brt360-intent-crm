"""Tenant inventory import. Endpoints and secrets are operator-managed environment variables."""
import os
from datetime import timedelta
import httpx
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import update
from app.models.feasibility import FeasibilityCheckType, InventorySyncConfig
from app.timeutils import utcnow

class InventoryItem(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=150)
    resource_type: str = Field(min_length=1, max_length=50)
    is_active: bool = True

SAMPLES = [dict(id=kind, name=name, resource_type=kind) for kind, name in [
    ("fiber", "Fiber Route Availability"), ("port", "Port Capacity"),
    ("switch", "Switch Capacity"), ("inventory", "Inventory on Hand")]]

def config(db, tenant_id):
    row = db.get(InventorySyncConfig, tenant_id)
    if row is None:
        row = InventorySyncConfig(tenant_id=tenant_id, enabled=False, interval_minutes=60)
        db.add(row)
        db.flush()
    return row

def endpoint(tenant_id):
    return os.environ.get(f"INVENTORY_CHECK_TYPES_URL_{tenant_id}", "")

def fetch(tenant_id):
    url = endpoint(tenant_id)
    if not url.startswith("https://"):
        raise ValueError("Configure an HTTPS inventory endpoint for this tenant on the server.")
    token = os.environ.get(f"INVENTORY_CHECK_TYPES_TOKEN_{tenant_id}")
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"} if token else {})
        response.raise_for_status()
        return response.json()

def sync(db, tenant_id, sample=False):
    # Validate the complete snapshot before changing any checks.
    raw = SAMPLES if sample else fetch(tenant_id)
    if not isinstance(raw, list) or len(raw) > 10000:
        raise ValueError("Inventory must return an array of at most 10,000 check types.")
    items = [InventoryItem.model_validate(item) for item in raw]
    if len({item.id for item in items}) != len(items):
        raise ValueError("Inventory returned duplicate check type IDs.")
    prefix = "sample:" if sample else "inventory:"
    existing = {r.external_id: r for r in db.query(FeasibilityCheckType).filter(
        FeasibilityCheckType.tenant_id == tenant_id,
        FeasibilityCheckType.external_id.startswith(prefix)).all()}
    created = updated = deactivated = 0
    for item in items:
        key = prefix + item.id
        row = existing.pop(key, None)
        if row is None:
            row = FeasibilityCheckType(tenant_id=tenant_id, external_id=key)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.name, row.resource_type, row.is_active = item.name, item.resource_type, item.is_active
    for row in existing.values():
        if row.is_active:
            row.is_active = False
            deactivated += 1
    if not sample:
        settings = config(db, tenant_id)
        settings.last_attempt_at = settings.last_success_at = utcnow()
        settings.last_error = None
    db.flush()
    return dict(created=created, updated=updated, deactivated=deactivated, sample=sample)

def run_due(session_factory):
    with session_factory() as db:
        ids = [r.tenant_id for r in db.query(InventorySyncConfig).filter_by(enabled=True).all()]
    for tenant_id in ids:
        with session_factory() as db:
            from app.licensing import enabled
            if "telecom_crm" not in enabled(db, tenant_id):
                continue
            row = db.get(InventorySyncConfig, tenant_id)
            now = utcnow()
            previous = row.last_attempt_at
            if previous and previous + timedelta(minutes=row.interval_minutes) > now:
                continue
            # Atomic claim avoids duplicate jobs across application workers.
            claimed = db.execute(update(InventorySyncConfig).where(
                InventorySyncConfig.tenant_id == tenant_id,
                InventorySyncConfig.enabled == True,
                InventorySyncConfig.last_attempt_at == previous,
            ).values(last_attempt_at=now)).rowcount
            db.commit()
            if not claimed:
                continue
            try:
                sync(db, tenant_id)
                db.commit()
            except Exception:
                db.rollback()
                row = db.get(InventorySyncConfig, tenant_id)
                row.last_error = "Inventory sync failed. Check endpoint, credentials and response format."
                db.commit()
