"""Training policy: org settings (ORG-010), the permission matrix of BRD
section 4 (ORG-005), data scoping (ORG-006), the append-only audit log
(ORG-012, NFR-011), public-link rate limiting (NFR-003) and time zones.

Every training service goes through `guard` - one policy check - and every
query is filtered by the caller's tenant (BR-ORG-01, NFR-001)."""
from __future__ import annotations

import contextvars
import copy
import datetime as dt
import functools
import logging
import time
from collections import defaultdict, deque
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

from app.config import get_settings
from app.licensing import require_module
from app.models.auth import User, UserRole
from app.models.org import Department
from app.models.training import TrainingAudit, TrainingMember, TrainingSettings
from app.timeutils import utcnow

ROLES = ("admin", "hr", "facilitator", "executive", "participant")

# ---------------------------------------------------------------- settings (ORG-010)

DEFAULTS: dict = {
    "brand": {"logo_url": "", "primary_color": ""},
    "timezone": "UTC",
    "anonymity_default": "identifiable",    # feedback forms: identifiable | anonymous (ASM-020)
    "pass_mark": 70,                        # learning outcome achievement (10.3)
    "retention_months": 36,                 # NFR-004 purge
    "min_group_size": 5,                    # ASM-023 / BR-ASM-04 (never below 3)
    "show_own_scores": False,               # ASM-016
    "exclude_late_pre": True,               # BR-ASM-03
    "pre_grace_min": 15,                    # ASM-012
    "post_window_days": 7,                  # ASM-012
    "checkin_before_min": 30,               # SES-009
    "checkin_after_min": 60,
    "expiring_days": 60,                    # FAC-002
    "rating_threshold": 3.5,                # FAC-005
    "travel_buffer_min": 30,                # BR-SES-02
    "quiet_hours": {"enabled": True, "start": 21, "end": 8},  # WFA-005
    "reminders": {                          # section 8 schedule
        "pre_1_h": 48, "pre_2_h": 4, "session_day_h": 2, "post_send_min": 15,
        "post_1_h": 48, "post_2_before_close_h": 24, "cert_days": [60, 14],
    },
    "channels": {"email": True, "sms": False, "whatsapp": False},
    "qa_rubric": ["Preparation", "Delivery", "Engagement", "Content accuracy", "Time management"],
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def settings(db, tenant_id: int, session=None) -> dict:
    """The org's settings over the defaults, and a session's own overrides over both."""
    row = db.query(TrainingSettings).filter_by(tenant_id=tenant_id).first()
    data = _merge(DEFAULTS, row.data if row else {})
    if session is not None and getattr(session, "settings", None):
        data = _merge(data, session.settings)
    return data


def validate_settings(data: dict) -> dict:
    clean = _merge(DEFAULTS, data)
    problems = []
    if not 0 <= float(clean["pass_mark"]) <= 100:
        problems.append("pass_mark must be 0-100")
    if int(clean["min_group_size"]) < 3:
        problems.append("min_group_size cannot be below 3 (ASM-023)")
    if clean["anonymity_default"] not in ("identifiable", "anonymous"):
        problems.append("anonymity_default must be identifiable or anonymous")
    for key in ("retention_months", "pre_grace_min", "post_window_days", "checkin_before_min", "checkin_after_min", "expiring_days"):
        if int(clean[key]) < 0:
            problems.append(f"{key} cannot be negative")
    try:
        ZoneInfo(clean["timezone"])
    except Exception:
        problems.append("timezone is not a known IANA time zone")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    return clean


# ---------------------------------------------------------------- roles and the permission matrix

# c create, r read, u update, d delete/archive, p publish/retire. "own" limits
# (a facilitator's own sessions, a participant's own registration) are applied
# by the service functions on top of this table.
POLICY: dict[str, dict[str, str]] = {
    "settings":         {"admin": "cru", "hr": "r", "executive": "r"},
    "locations":        {"admin": "crud", "hr": "r", "facilitator": "r", "executive": "r"},
    "people":           {"admin": "crud", "hr": "cru"},
    "programs":         {"admin": "crud", "hr": "r", "facilitator": "r", "executive": "r", "participant": "r"},
    "versions":         {"admin": "crup", "hr": "r", "facilitator": "r", "executive": "r"},
    "sessions":         {"admin": "crud", "hr": "cru", "facilitator": "ru", "executive": "r", "participant": "r"},
    "registrations":    {"admin": "crud", "hr": "crud", "facilitator": "r", "executive": "r", "participant": "cru"},
    "attendance":       {"admin": "crud", "hr": "ru", "facilitator": "cru", "executive": "r", "participant": "c"},
    "certifications":   {"admin": "crud", "hr": "r", "facilitator": "r", "executive": "r"},
    "qa_reviews":       {"admin": "crud", "hr": "r", "facilitator": "r", "executive": "r"},
    "templates":        {"admin": "crud", "hr": "r", "facilitator": "r"},
    "responses":        {"admin": "r", "hr": "r", "facilitator": "r", "participant": "cr"},
    "analytics":        {"admin": "r", "hr": "r", "facilitator": "r", "executive": "r", "participant": "r"},
    "notify_templates": {"admin": "crud", "hr": "r"},
    "notifications":    {"admin": "crud", "hr": "cru"},
    "audit":            {"admin": "r"},
}


def member(db, user: User) -> TrainingMember | None:
    return db.query(TrainingMember).filter_by(tenant_id=user.tenant_id, user_id=user.id).first()


def roles(db, user: User) -> set[str]:
    """Effective roles - the union of everything the user holds (section 4)."""
    require_module(db, user.tenant_id, "training_management")
    if user.role == UserRole.admin:
        return {"admin"}
    row = member(db, user)
    if row is None:
        return {"participant"}
    held = {row.role, *(row.roles or [])}
    return {r for r in held if r in ROLES} or {"participant"}


def primary(held: set[str]) -> str:
    return next(r for r in ROLES if r in held)


def can(held: set[str], resource: str, op: str) -> bool:
    table = POLICY.get(resource, {})
    return any(op in table.get(r, "") for r in held)


def guard(db, user: User, resource: str, op: str) -> set[str]:
    held = roles(db, user)
    if not can(held, resource, op):
        raise HTTPException(403, "Your training role does not allow this action")
    return held


# ---------------------------------------------------------------- data scoping (ORG-006)


def _descendants(db, tenant_id: int, roots: set[int]) -> set[int]:
    rows = db.query(Department.id, Department.parent_department_id).filter_by(tenant_id=tenant_id).all()
    children: dict[int, list[int]] = defaultdict(list)
    for did, parent in rows:
        if parent is not None:
            children[parent].append(did)
    out, stack = set(), list(roots)
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(children.get(current, []))
    return out


def scope(db, user: User, held: set[str] | None = None):
    """None = the whole org; else (location ids, department ids incl. children).
    Only HR and Executive roles are scoped, and only when an Org Admin assigned
    a scope; an admin is never scoped."""
    held = held if held is not None else roles(db, user)
    if "admin" in held or not ({"hr", "executive"} & held):
        return None
    row = member(db, user)
    if row is None or not (row.scope_location_ids or row.scope_department_ids):
        return None
    return set(row.scope_location_ids or []), _descendants(db, user.tenant_id, set(row.scope_department_ids or []))


def in_scope(limits, location_id, department_id) -> bool:
    if limits is None:
        return True
    locations, departments = limits
    return (location_id in locations) or (department_id in departments)


# ---------------------------------------------------------------- audit (ORG-012)

request_ip: contextvars.ContextVar[str] = contextvars.ContextVar("training_request_ip", default="")


def remember_ip(request: Request) -> None:
    request_ip.set(request.client.host if request.client else "")


def audit(db, tenant_id: int, actor_id: int | None, action: str, entity: str, entity_id, before=None, after=None) -> None:
    db.add(TrainingAudit(tenant_id=tenant_id, actor_id=actor_id, action=action, entity=entity,
                         entity_id=str(entity_id or ""), before=_jsonable(before), after=_jsonable(after),
                         ip=request_ip.get(), at=utcnow()))


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def snapshot(row, fields=None) -> dict:
    cols = [c.name for c in row.__table__.columns if not c.name.startswith("sys_") and c.name != "brt_id"]
    return {c: _jsonable(getattr(row, c)) for c in cols if fields is None or c in fields}


# ---------------------------------------------------------------- rate limiting (NFR-003)

_hits: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = 30  # requests per minute per IP on public links


@functools.lru_cache
def _redis_client(url: str):
    import redis

    return redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)


def _shared_hits(ip: str) -> int | None:
    """This minute's hits from `ip` counted across every worker (Redis), or
    None without Redis - or when it can't be reached: the limit then falls
    back to each worker's own count rather than turning people away."""
    url = get_settings().redis_url
    if not url:
        return None
    try:
        key = f"brt360:ratelimit:public:{ip}:{int(time.time() // 60)}"
        pipe = _redis_client(url).pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)
        hits, _ = pipe.execute()
        return int(hits)
    except Exception:  # noqa: BLE001 - a limiter outage must not take the public links down
        logging.getLogger(__name__).warning("Rate limiter: Redis unavailable, using the per-worker count", exc_info=True)
        return None


def rate_limit(request: Request) -> None:
    """30 requests a minute per client IP on public token endpoints. With
    REDIS_URL set the count is shared by every worker (a hard limit);
    without it each worker process keeps its own (decision D-07: with N
    gunicorn workers the effective ceiling is N x 30)."""
    ip = request.client.host if request.client else "?"
    shared = _shared_hits(ip)
    if shared is not None:
        if shared > RATE_LIMIT:
            raise HTTPException(429, "Too many requests - please wait a minute and try again")
        remember_ip(request)
        return
    now = time.monotonic()
    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(429, "Too many requests - please wait a minute and try again")
    window.append(now)
    remember_ip(request)


# ---------------------------------------------------------------- time zones


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def to_local(value: dt.datetime | None, tz: str | None) -> str | None:
    """A stored naive-UTC time as an ISO string in `tz`."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc).astimezone(zone(tz)).isoformat()


def as_utc(value: dt.datetime) -> dt.datetime:
    """Aware -> naive UTC (what the database stores); naive is taken as UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
