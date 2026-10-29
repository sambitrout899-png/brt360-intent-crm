"""The notification service: the notices bar and message boxes (/api/notices),
admins' broadcast messages (/api/admin/broadcasts) and the delivery log
(/api/admin/notification-deliveries). Its worker (notification-worker,
`python -m app.scheduler --service notification`) delivers the notifications
the platform records (app/crm_core/notification_delivery.py)."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = ("/api/notices", "/api/admin/broadcasts", "/api/admin/notification-deliveries")


def routers() -> list[APIRouter]:
    from app.experience import routes_notices

    return [routes_notices.router, routes_notices.admin_router, routes_notices.deliveries_router]
