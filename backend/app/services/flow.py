"""The flow service: Flow Studio (/api/admin/flows), paused runs and their
approvals (/api/admin/flow-runs, /api/flow-approvals), the Process Builder
and function map, approval chains (/api/approvals, /api/admin/approval-*),
and what shares their route files: state models, positions, assignment
rules, custom fields, price lists, process roles and quote document
templates. Its background job - continuing waits whose time has come -
runs in flow-worker (`python -m app.scheduler --service flow`)."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = (
    "/api/admin/flows", "/api/admin/flow-runs", "/api/flow-approvals", "/api/process-builder/", "/api/admin/function-map", "/api/admin/change",
    "/api/approvals", "/api/admin/approval-policy", "/api/admin/approval-rules", "/api/admin/approvals",
    "/api/admin/price-lists", "/api/admin/process-roles", "/api/admin/quote-document-templates",
    "/api/admin/state-models", "/api/admin/positions", "/api/admin/assignment-rules", "/api/admin/custom-fields",
)


def routers() -> list[APIRouter]:
    from app.experience import routes_builder, routes_change, routes_function_map, routes_process, routes_studio

    return [routes_studio.router, routes_studio.approvals_router, routes_process.admin_router, routes_process.router,
            routes_function_map.router, routes_builder.router, routes_change.router]
