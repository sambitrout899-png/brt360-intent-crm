"""The metadata service: Page Designer (/api/pages, /api/admin/pages), custom
record types and their records (/api/custom-records, /api/admin/record-types)
and the generic record APIs (/api/records). The rest of the admin settings
(routes_admin.py, shared by several modules) stays in the API until it is
divided by module."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = ("/api/pages/", "/api/admin/pages", "/api/custom-records/", "/api/admin/record-types", "/api/records/")


def routers() -> list[APIRouter]:
    from app.experience import routes_custom_records, routes_pages, routes_records

    return [routes_records.router, routes_custom_records.admin_router, routes_custom_records.router,
            routes_pages.router, routes_pages.admin_router]
