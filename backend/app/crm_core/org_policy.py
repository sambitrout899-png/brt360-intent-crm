"""Per-tenant access and approval switches (Admin -> Roles & Access).

Both default to off so a deployment changes nothing until an administrator
turns them on. Stored in the tenant's settings row (cm_settings.data_json,
key "org_policy") - no table of its own.

  require_role_and_position - every non-admin user must hold a role and a
      position: saving one without is refused, a user without a position
      sees only their own records (not the whole organization) and a user
      without a role gets no pages or intents.
  strict_approvals - an approval step is decided only by a holder of its
      position (or an administrator): a step whose position nobody holds
      waits for an administrator, and nobody approves their own request.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.comms import CmSetting

KEY = "org_policy"
DEFAULTS: dict[str, bool] = {"require_role_and_position": False, "strict_approvals": False}


def _row(db: Session, tenant_id: int) -> CmSetting | None:
    return db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()


def get(db: Session, tenant_id: int) -> dict[str, bool]:
    row = _row(db, tenant_id)
    try:
        stored = json.loads(row.data_json or "{}").get(KEY) if row else None
    except ValueError:
        stored = None
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        out.update({k: bool(v) for k, v in stored.items() if k in DEFAULTS})
    return out


def is_on(db: Session, tenant_id: int, key: str) -> bool:
    return get(db, tenant_id)[key]


def save(db: Session, tenant_id: int, data: dict[str, Any]) -> dict[str, bool]:
    unknown = [k for k in data if k not in DEFAULTS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
    row = _row(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    try:
        current = json.loads(row.data_json or "{}")
    except ValueError:
        current = {}
    current[KEY] = {**get(db, tenant_id), **{k: bool(v) for k, v in data.items()}}
    row.data_json = json.dumps(current)
    db.flush()
    if "strict_approvals" in data:
        from app.crm_core import approval_service

        # the Approvals page's "Who decides" settings, once saved, follow this switch
        approval_service.strict_switched(db, tenant_id, bool(data["strict_approvals"]))
    return get(db, tenant_id)
