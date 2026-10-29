"""The core service: what isn't split out - sign-in and users, organization and
access, CRM, Customer 360, orders, fulfilment, inventory, field service,
communications, analytics, admin settings and licensing (telecom-core, in
the plan's words: split further only where load or a team needs it).

In production the api container runs it like any other service
(BRT_SERVICE=core, app/services/split_app.py) and the web container sends it
every /api path no other service claims. app/main.py - everything in one
app - is for development and the tests.
"""

from __future__ import annotations

from fastapi import APIRouter


def routers() -> list[APIRouter]:
    from app.experience import (
        routes_admin,
        routes_analytics,
        routes_auth,
        routes_c360,
        routes_comms,
        routes_crm,
        routes_field_service,
        routes_fulfillment,
        routes_inventory,
        routes_inventory_sync,
        routes_modules,
        routes_order_mgmt,
        routes_org,
        routes_tenants,
    )

    return [
        routes_auth.router, routes_crm.router, routes_admin.router, routes_tenants.router,
        routes_org.router, routes_org.access_router, routes_c360.router, routes_c360.admin_router,
        routes_field_service.router, routes_order_mgmt.router, routes_order_mgmt.admin_router,
        routes_fulfillment.router, routes_fulfillment.admin_router, routes_inventory.router, routes_inventory.presales_router,
        routes_comms.router, routes_comms.admin_router, routes_analytics.router, routes_inventory_sync.router, routes_modules.router,
    ]
