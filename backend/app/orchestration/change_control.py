"""Change control: several administrators develop a tenant's configuration at
once without overwriting each other (models/change_control.py).

    workspace (one admin's piece of work)
       │ save ──► revision n of the object (a snapshot; live is untouched)
       │          + the object locked to the workspace - nobody else changes it
       ▼
    submitted ──► release (the root admin bundles submitted workspaces, and
                  optionally a Functional Map version) ──► simulate ──► deploy
                  (all or nothing; locks freed) ──► rollback (what each object was before)

An object type joins through an adapter (ADAPTERS): its live snapshot, a
transient object built from a snapshot (for the editors and simulations),
and applying a snapshot to live / restoring the one it replaced. Flow Studio
flows are the first; Page Designer, custom fields, record types and approval
rules follow the same pattern.

Outside a workspace an administrator can still change an object directly -
unless it is locked by someone's workspace, which is exactly what the lock is
for. Only the tenant's root administrator (Tenant.root_admin_user_id, else
its first administrator) deploys or rolls back a release, and activates a
Functional Map version.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.auth import User, UserRole
from app.models.change_control import ObjectLock, ObjectRevision, Release, Workspace
from app.timeutils import utcnow


class ChangeError(ValueError):
    """Not possible as things stand - locked, wrong state (409)."""


class NotRoot(PermissionError):
    """Only the tenant's root administrator may do this (403)."""


# ---------------------------------------------------------------- the root administrator


def root_admin_id(db: Session, tenant_id: int) -> int | None:
    from app.models.tenant import Tenant

    tenant = db.get(Tenant, tenant_id)
    if tenant is not None and tenant.root_admin_user_id:
        return tenant.root_admin_user_id
    first = (db.query(User.id).filter(User.tenant_id == tenant_id, User.role == UserRole.admin, User.is_active.is_(True))
             .order_by(User.id).first())
    return first[0] if first else None


def is_root(db: Session, user: User) -> bool:
    return user.tenant_id is not None and root_admin_id(db, user.tenant_id) == user.id


def require_root(db: Session, user: User, what: str = "do this") -> None:
    if not is_root(db, user):
        root = db.get(User, root_admin_id(db, user.tenant_id) or 0)
        raise NotRoot(f"Only the organization's root administrator{f' ({root.name})' if root else ''} can {what}")


def _name(db: Session, user_id: int | None) -> str | None:
    user = db.get(User, user_id) if user_id else None
    return user.name if user is not None else None


# ---------------------------------------------------------------- adapters


class FlowAdapter:
    """Flow Studio flows."""

    label = "Flow"
    FIELDS = ("name", "description", "entity_type", "trigger_type", "trigger_intent_type", "timing", "trigger_state",
              "trigger_phrases", "required_fields", "graph_json", "on_error", "is_active", "version")

    @staticmethod
    def live(db: Session, tenant_id: int, key: str):
        from app.models.flows import FlowDefinition

        row = db.get(FlowDefinition, int(key)) if key.isdigit() else None
        return row if row is not None and row.tenant_id == tenant_id else None

    @classmethod
    def snapshot(cls, row) -> dict[str, Any]:
        return {f: getattr(row, f) for f in cls.FIELDS}

    @classmethod
    def materialize(cls, tenant_id: int, key: str, snap: dict[str, Any]):
        """A flow built from a snapshot - never added to the session (the editor, a simulation)."""
        from app.models.flows import FlowDefinition

        flow = FlowDefinition(tenant_id=tenant_id, **{f: snap.get(f) for f in cls.FIELDS if f in snap})
        flow.id = display_id(key)
        flow.version = snap.get("version") or 1
        flow.updated_at = utcnow()
        return flow

    @classmethod
    def apply(cls, db: Session, tenant_id: int, key: str, snap: dict[str, Any]) -> str:
        from app.models.flows import FlowDefinition

        row = cls.live(db, tenant_id, key)
        if snap.get("deleted"):
            if row is not None:
                db.delete(row)
            return key
        if row is None:
            row = FlowDefinition(tenant_id=tenant_id)
            db.add(row)
        for f in cls.FIELDS:
            if f in snap and f != "version":
                setattr(row, f, snap[f])
        row.version = (row.version or 0) + 1
        db.flush()
        return str(row.id)

    @classmethod
    def restore(cls, db: Session, tenant_id: int, key: str, before: dict[str, Any] | None) -> None:
        """Back to what it was before a release: gone again if the release made it."""
        from app.models.flows import FlowDefinition

        row = cls.live(db, tenant_id, key)
        if before is None:
            if row is not None:
                db.delete(row)
            return
        if row is None:  # the release deleted it - back with its own number
            row = FlowDefinition(id=int(key), tenant_id=tenant_id)
            db.add(row)
        for f in cls.FIELDS:
            setattr(row, f, before.get(f))
        db.flush()


ADAPTERS: dict[str, Any] = {"flow": FlowAdapter}


def display_id(key: str) -> int:
    """A live object's own id; an object made in a workspace (new-<n>) shows as -n."""
    return -int(key[4:]) if key.startswith("new-") else int(key)


def key_of(object_id: int) -> str:
    return f"new-{-object_id}" if object_id < 0 else str(object_id)


# ---------------------------------------------------------------- workspaces


def _workspace(db: Session, tenant_id: int, workspace_id: int) -> Workspace:
    ws = db.get(Workspace, workspace_id)
    if ws is None or ws.tenant_id != tenant_id:
        raise LookupError("No such workspace")
    return ws


def workspace_out(db: Session, ws: Workspace) -> dict[str, Any]:
    revs = db.query(ObjectRevision).filter(ObjectRevision.workspace_id == ws.id, ObjectRevision.status != "discarded").all()
    latest: dict[tuple[str, str], ObjectRevision] = {}
    for r in sorted(revs, key=lambda r: r.id):
        latest[(r.object_type, r.object_key)] = r
    iso = lambda t: t.isoformat(timespec="seconds") if t else None  # noqa: E731
    return {"id": ws.id, "name": ws.name, "description": ws.description, "status": ws.status, "owner_id": ws.owner_id,
            "owner": _name(db, ws.owner_id), "created_at": iso(ws.created_at), "updated_at": iso(ws.updated_at),
            "submitted_at": iso(ws.submitted_at), "release_id": ws.release_id,
            "objects": [{"type": t, "key": k, "id": display_id(k), "kind": ADAPTERS[t].label if t in ADAPTERS else t,
                         "label": r.label, "revision": r.number, "change": "deleted" if json.loads(r.snapshot_json).get("deleted")
                         else "new" if r.base_json is None else "changed"} for (t, k), r in latest.items()]}


def create_workspace(db: Session, tenant_id: int, user: User, name: str, description: str = "") -> dict[str, Any]:
    now = utcnow()
    ws = Workspace(tenant_id=tenant_id, name=(name.strip() or "My changes")[:150], description=description[:2000], owner_id=user.id,
                   status="open", created_at=now, updated_at=now)
    db.add(ws)
    db.commit()
    return workspace_out(db, ws)


def workspaces(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    rows = db.query(Workspace).filter(Workspace.tenant_id == tenant_id).order_by(Workspace.id.desc()).all()
    return [workspace_out(db, ws) for ws in rows]


def working(db: Session, tenant_id: int, user: User, workspace_id: int | None) -> Workspace | None:
    """The workspace a request works in (the X-BRT-Workspace header): the
    caller's own, still open - or None (live)."""
    if not workspace_id:
        return None
    ws = _workspace(db, tenant_id, workspace_id)
    if ws.owner_id != user.id:
        raise ChangeError(f"“{ws.name}” is {_name(db, ws.owner_id)}'s workspace - work in one of your own")
    if ws.status != "open":
        raise ChangeError(f"“{ws.name}” is {ws.status} - reopen it or start another workspace")
    return ws


def submit(db: Session, tenant_id: int, user: User, workspace_id: int) -> dict[str, Any]:
    ws = working(db, tenant_id, user, workspace_id)
    if not workspace_out(db, ws)["objects"]:
        raise ChangeError("Nothing has been changed in this workspace yet")
    ws.status, ws.submitted_at, ws.updated_at = "submitted", utcnow(), utcnow()
    db.commit()
    return workspace_out(db, ws)


def reopen(db: Session, tenant_id: int, user: User, workspace_id: int) -> dict[str, Any]:
    ws = _workspace(db, tenant_id, workspace_id)
    if ws.owner_id != user.id or ws.status != "submitted":
        raise ChangeError("Only its owner can reopen a submitted workspace that isn't in a release")
    preparing = db.query(Release).filter(Release.tenant_id == tenant_id, Release.status == "draft").all()
    if any(ws.id in (r.workspace_ids or []) for r in preparing):
        raise ChangeError("It is in a release being prepared - ask the root administrator to take it out first")
    ws.status, ws.submitted_at, ws.updated_at = "open", None, utcnow()
    db.commit()
    return workspace_out(db, ws)


def discard(db: Session, tenant_id: int, user: User, workspace_id: int) -> dict[str, Any]:
    """Everything in it dropped and its locks freed - live never saw it."""
    ws = _workspace(db, tenant_id, workspace_id)
    if ws.owner_id != user.id and not is_root(db, user):
        raise ChangeError("Only its owner or the root administrator can discard a workspace")
    if ws.status not in ("open", "submitted"):
        raise ChangeError(f"It is already {ws.status}")
    db.query(ObjectRevision).filter(ObjectRevision.workspace_id == ws.id, ObjectRevision.status == "draft").update({"status": "discarded"})
    db.query(ObjectLock).filter(ObjectLock.workspace_id == ws.id).delete()
    ws.status, ws.updated_at = "discarded", utcnow()
    db.commit()
    return workspace_out(db, ws)


# ---------------------------------------------------------------- locks


def lock_of(db: Session, tenant_id: int, object_type: str, key: str) -> ObjectLock | None:
    return db.query(ObjectLock).filter(ObjectLock.tenant_id == tenant_id, ObjectLock.object_type == object_type,
                                       ObjectLock.object_key == key).first()


def lock_out(db: Session, lock: ObjectLock | None) -> dict[str, Any] | None:
    if lock is None:
        return None
    ws = db.get(Workspace, lock.workspace_id) if lock.workspace_id else None
    return {"user_id": lock.user_id, "user": _name(db, lock.user_id), "workspace_id": lock.workspace_id,
            "workspace": ws.name if ws else None, "since": lock.locked_at.isoformat(timespec="seconds")}


def _held_message(db: Session, lock: ObjectLock, what: str) -> str:
    info = lock_out(db, lock)
    where = f" in their workspace “{info['workspace']}”" if info["workspace"] else ""
    return f"{what} is locked - {info['user']} is working on it{where}. It can be changed again once that work is released or discarded."


def check_free(db: Session, tenant_id: int, object_type: str, key: str, user: User, workspace: Workspace | None, what: str) -> None:
    """Raises when someone else holds the object."""
    lock = lock_of(db, tenant_id, object_type, key)
    if lock is None:
        return
    if workspace is not None and lock.workspace_id == workspace.id:
        return
    if workspace is None and lock.workspace_id is None and lock.user_id == user.id:
        return
    raise ChangeError(_held_message(db, lock, what))


def claim(db: Session, tenant_id: int, object_type: str, key: str, user: User, workspace: Workspace | None, what: str) -> None:
    """Locks the object to the caller (and their workspace), unless someone else holds it."""
    check_free(db, tenant_id, object_type, key, user, workspace, what)
    if lock_of(db, tenant_id, object_type, key) is None:
        db.add(ObjectLock(tenant_id=tenant_id, object_type=object_type, object_key=key, workspace_id=workspace.id if workspace else None,
                          user_id=user.id, locked_at=utcnow()))
        db.flush()


def unlock(db: Session, tenant_id: int, object_type: str, key: str) -> None:
    db.query(ObjectLock).filter(ObjectLock.tenant_id == tenant_id, ObjectLock.object_type == object_type, ObjectLock.object_key == key).delete()


def break_lock(db: Session, tenant_id: int, user: User, object_type: str, key: str) -> None:
    """The root administrator frees a stale lock - the workspace keeps its drafts, recorded in the audit trail."""
    from app.data_intelligence import audit

    require_root(db, user, "break someone's lock")
    lock = lock_of(db, tenant_id, object_type, key)
    if lock is None:
        return
    log = audit.start_execution(db, tenant_id, f"[change control] lock broken on {object_type} {key} (held by {_name(db, lock.user_id)})", channel="admin")
    audit.complete(log, "executed")
    db.delete(lock)
    db.commit()


# ---------------------------------------------------------------- drafts (revisions)


def draft(db: Session, tenant_id: int, workspace: Workspace | None, object_type: str, key: str) -> dict[str, Any] | None:
    if workspace is None:
        return None
    rev = (db.query(ObjectRevision).filter(ObjectRevision.workspace_id == workspace.id, ObjectRevision.object_type == object_type,
                                           ObjectRevision.object_key == key, ObjectRevision.status == "draft")
           .order_by(ObjectRevision.id.desc()).first())
    return json.loads(rev.snapshot_json) if rev is not None else None


def drafts(db: Session, tenant_id: int, workspace: Workspace | None, object_type: str) -> dict[str, dict[str, Any]]:
    if workspace is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rev in (db.query(ObjectRevision).filter(ObjectRevision.workspace_id == workspace.id, ObjectRevision.object_type == object_type,
                                                ObjectRevision.status == "draft").order_by(ObjectRevision.id)):
        out[rev.object_key] = json.loads(rev.snapshot_json)
    return out


def save(db: Session, tenant_id: int, user: User, workspace: Workspace, object_type: str, key: str | None, snap: dict[str, Any],
         label: str) -> str:
    """A new revision of the object in the workspace (key None: an object made
    in it). The object is locked to the workspace. Its key comes back."""
    adapter = ADAPTERS[object_type]
    if key is not None:
        claim(db, tenant_id, object_type, key, user, workspace, f"{adapter.label} “{label}”")
    first = key is None or not (db.query(ObjectRevision.id).filter(ObjectRevision.tenant_id == tenant_id, ObjectRevision.workspace_id == workspace.id,
                                                                    ObjectRevision.object_type == object_type, ObjectRevision.object_key == key).first())
    base = None
    if key is not None and first:
        live = adapter.live(db, tenant_id, key)
        base = json.dumps(adapter.snapshot(live), default=str) if live is not None else None
    elif key is not None:
        base = (db.query(ObjectRevision.base_json).filter(ObjectRevision.workspace_id == workspace.id, ObjectRevision.object_type == object_type,
                                                          ObjectRevision.object_key == key).order_by(ObjectRevision.id).first() or (None,))[0]
    number = (db.query(func.max(ObjectRevision.number)).filter(ObjectRevision.tenant_id == tenant_id, ObjectRevision.object_type == object_type,
                                                               ObjectRevision.object_key == key).scalar() or 0) + 1 if key else 1
    rev = ObjectRevision(tenant_id=tenant_id, object_type=object_type, object_key=key or "pending", number=number, workspace_id=workspace.id,
                         label=label[:200], snapshot_json=json.dumps(snap, default=str), base_json=base, status="draft", author_id=user.id,
                         created_at=utcnow())
    db.add(rev)
    db.flush()
    if key is None:
        key = rev.object_key = f"new-{rev.id}"
        claim(db, tenant_id, object_type, key, user, workspace, f"{adapter.label} “{label}”")
    workspace.updated_at = utcnow()
    db.commit()
    return key


def history(db: Session, tenant_id: int, object_type: str, key: str) -> list[dict[str, Any]]:
    """Every revision of one object, newest first."""
    rows = (db.query(ObjectRevision).filter(ObjectRevision.tenant_id == tenant_id, ObjectRevision.object_type == object_type,
                                            ObjectRevision.object_key == key).order_by(ObjectRevision.id.desc()).all())
    return [{"revision": r.number, "status": r.status, "label": r.label, "author": _name(db, r.author_id), "workspace_id": r.workspace_id,
             "at": r.created_at.isoformat(timespec="seconds")} for r in rows]


# ---------------------------------------------------------------- releases


def _release(db: Session, tenant_id: int, release_id: int) -> Release:
    rel = db.get(Release, release_id)
    if rel is None or rel.tenant_id != tenant_id:
        raise LookupError("No such release")
    return rel


def release_out(db: Session, rel: Release) -> dict[str, Any]:
    iso = lambda t: t.isoformat(timespec="seconds") if t else None  # noqa: E731
    wss = [workspace_out(db, w) for w in db.query(Workspace).filter(Workspace.id.in_(rel.workspace_ids or [0])).order_by(Workspace.id)]
    return {"id": rel.id, "number": rel.number, "name": rel.name, "status": rel.status, "workspaces": wss, "map_version_id": rel.map_version_id,
            "created_by": _name(db, rel.created_by_id), "created_at": iso(rel.created_at), "deployed_by": _name(db, rel.deployed_by_id),
            "deployed_at": iso(rel.deployed_at), "rolled_back_at": iso(rel.rolled_back_at)}


def releases(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    return [release_out(db, r) for r in db.query(Release).filter(Release.tenant_id == tenant_id).order_by(Release.id.desc())]


def create_release(db: Session, tenant_id: int, user: User, name: str, workspace_ids: list[int], map_version_id: int | None) -> dict[str, Any]:
    require_root(db, user, "prepare a release")
    wss = [_workspace(db, tenant_id, w) for w in dict.fromkeys(workspace_ids)]
    not_ready = [w.name for w in wss if w.status != "submitted"]
    if not_ready:
        raise ChangeError(f"Only submitted workspaces go into a release - not yet: {', '.join(not_ready)}")
    if not wss and map_version_id is None:
        raise ChangeError("A release needs at least one submitted workspace or a map version")
    if map_version_id is not None:
        from app.orchestration import function_map

        v = function_map._get(db, tenant_id, map_version_id)
        if v.status == "active":
            raise ChangeError(f"Map version {v.number} is already active")
    number = (db.query(func.max(Release.number)).filter(Release.tenant_id == tenant_id).scalar() or 0) + 1
    rel = Release(tenant_id=tenant_id, number=number, name=(name.strip() or f"Release {number}")[:150], status="draft",
                  workspace_ids=[w.id for w in wss], map_version_id=map_version_id, created_by_id=user.id, created_at=utcnow(), undo_json="{}")
    db.add(rel)
    db.commit()
    return release_out(db, rel)


def _changes(db: Session, tenant_id: int, rel: Release) -> list[tuple[ObjectRevision, str, str]]:
    """(latest draft revision, object type, key) of every object the release carries."""
    latest: dict[tuple[str, str], ObjectRevision] = {}
    for rev in (db.query(ObjectRevision).filter(ObjectRevision.workspace_id.in_(rel.workspace_ids or [0]), ObjectRevision.status == "draft")
                .order_by(ObjectRevision.id)):
        latest[(rev.object_type, rev.object_key)] = rev
    return [(rev, t, k) for (t, k), rev in latest.items()]


def _apply_release(db: Session, tenant_id: int, rel: Release) -> list[dict[str, Any]]:
    undo = []
    for rev, object_type, key in _changes(db, tenant_id, rel):
        adapter = ADAPTERS[object_type]
        live = adapter.live(db, tenant_id, key)
        before = adapter.snapshot(live) if live is not None else None
        after_key = adapter.apply(db, tenant_id, key, json.loads(rev.snapshot_json))
        undo.append({"type": object_type, "key": after_key, "before": before, "label": rev.label})
    return undo


def simulate_release(db: Session, tenant_id: int, user: User, release_id: int) -> dict[str, Any]:
    """The release applied and checked with nothing kept: every changed flow
    validated, the map version's activation planned, then rolled back."""
    from app.orchestration import flow_engine, function_map, rehearsal

    require_root(db, user, "simulate a release")
    rel = _release(db, tenant_id, release_id)
    checks: list[dict[str, Any]] = []
    try:
        with rehearsal.capture(db) as rows:
            undo = _apply_release(db, tenant_id, rel)
            for u in undo:
                if u["type"] == "flow":
                    flow = FlowAdapter.live(db, tenant_id, u["key"])
                    errors = flow_engine.validate_graph(flow_engine.load_graph(flow), flow) if flow is not None and flow.is_active else []
                    checks.append({"object": f"Flow “{u['label']}”", "ok": not errors,
                                   "message": "; ".join(errors) if errors else ("deleted" if flow is None else "valid")})
            planned = function_map.plan_activation(db, tenant_id, rel.map_version_id) if rel.map_version_id else None
            db.flush()
        impact = rehearsal.summarize(db, rows, {})
    finally:
        db.rollback()
    problems = [c for c in checks if not c["ok"]] + [{"object": "Functional Map", "ok": False, "message": p["text"]}
                                                     for p in (planned or {}).get("problems", [])]
    return {"ok": not problems, "checks": checks, "map_plan": planned, "impact": impact, "problems": problems}


def deploy_release(db: Session, tenant_id: int, user: User, release_id: int) -> dict[str, Any]:
    """Every change of the release goes live together - or none does."""
    from app.data_intelligence import audit
    from app.orchestration import function_map

    require_root(db, user, "deploy a release")
    rel = _release(db, tenant_id, release_id)
    if rel.status != "draft":
        raise ChangeError(f"Release {rel.number} is already {rel.status.replace('_', ' ')}")
    try:
        undo = _apply_release(db, tenant_id, rel)
        for rev, _t, _k in _changes(db, tenant_id, rel):
            rev.status = "released"
        for ws in db.query(Workspace).filter(Workspace.id.in_(rel.workspace_ids or [0])):
            ws.status, ws.release_id, ws.updated_at = "released", rel.id, utcnow()
        db.query(ObjectLock).filter(ObjectLock.workspace_id.in_(rel.workspace_ids or [0])).delete(synchronize_session=False)
        active = function_map._active(db, tenant_id)
        rel.undo_json = json.dumps({"objects": undo, "map_before": active.id if active is not None else None}, default=str)
        rel.status, rel.deployed_by_id, rel.deployed_at = "deployed", user.id, utcnow()
        log = audit.start_execution(db, tenant_id, f"[release] {rel.number} “{rel.name}” deployed", channel="admin")
        audit.record_plan(log, [{"object": u["type"], "key": u["key"], "label": u["label"]} for u in undo])
        audit.complete(log, "executed")
        if rel.map_version_id:
            function_map.activate(db, tenant_id, rel.map_version_id, user.id)  # commits everything with the map, or rolls all back
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return release_out(db, rel)


def rollback_release(db: Session, tenant_id: int, user: User, release_id: int) -> dict[str, Any]:
    """The live configuration back to what it was before this release - its
    objects restored, the map version it replaced active again."""
    from app.data_intelligence import audit
    from app.orchestration import function_map

    require_root(db, user, "roll back a release")
    rel = _release(db, tenant_id, release_id)
    if rel.status != "deployed":
        raise ChangeError(f"Release {rel.number} is {rel.status.replace('_', ' ')} - only a deployed release rolls back")
    later = db.query(Release).filter(Release.tenant_id == tenant_id, Release.status == "deployed", Release.id > rel.id).first()
    if later is not None:
        raise ChangeError(f"Release {later.number} was deployed after it - roll that back first")
    undo = json.loads(rel.undo_json or "{}")
    try:
        for u in reversed(undo.get("objects", [])):
            ADAPTERS[u["type"]].restore(db, tenant_id, u["key"], u["before"])
        rel.status, rel.rolled_back_at = "rolled_back", utcnow()
        log = audit.start_execution(db, tenant_id, f"[release] {rel.number} “{rel.name}” rolled back", channel="admin")
        audit.complete(log, "executed")
        if rel.map_version_id:
            function_map.activate(db, tenant_id, undo.get("map_before"), user.id)
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return release_out(db, rel)
