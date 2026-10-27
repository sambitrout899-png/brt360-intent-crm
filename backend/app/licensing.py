"""Tenant entitlements are enforced separately from user permissions."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.db import get_db
from app.models.auth import User
from app.models.tenant import Tenant

MODULES = {
    'telecom_crm': {'label': 'Telecom CRM', 'kind': 'functional', 'description': 'Customers, sales, products, orders, billing and field service'},
    'training_management': {'label': 'Training & Learning', 'kind': 'functional', 'description': 'Programs, sessions, assessments, feedback and learning outcomes'},
    'flow_studio': {'label': 'Flow Studio', 'kind': 'primitive', 'description': 'Design and automate reusable workflows'},
    'page_designer': {'label': 'Page Designer', 'kind': 'primitive', 'description': 'Configure forms, layouts and validation'},
    'custom_development': {'label': 'Custom Development', 'kind': 'primitive', 'description': 'Custom intents, fields and process building'},
}
LEGACY_MODULES = ['telecom_crm', 'flow_studio', 'page_designer', 'custom_development']
TELECOM_PAGES = {'dashboard', 'mywork', 'customer', 'pipeline', 'orders', 'tickets', 'field', 'inventory', 'catalog', 'analytics'}


def validate_modules(values):
    unknown = set(values) - MODULES.keys()
    if unknown:
        raise HTTPException(422, f'Unknown modules: {", ".join(sorted(unknown))}')
    return sorted(set(values))


def enabled(db, tenant_id):
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    if tenant is None:
        return set()
    return set(LEGACY_MODULES if tenant.licensed_modules is None else tenant.licensed_modules)


def require_module(db, tenant_id, module):
    if module not in enabled(db, tenant_id):
        raise HTTPException(403, f'{MODULES[module]["label"]} is not licensed for this organization')


def licensed_pages(db, user, pages):
    modules = enabled(db, user.tenant_id)
    return [p for p in pages if (p not in TELECOM_PAGES or 'telecom_crm' in modules)
            and (not p.startswith('training') or 'training_management' in modules)
            and (p != 'records' or 'custom_development' in modules)]


def intent_module(code):
    if code == 'INT-103':
        return 'page_designer'
    if (code or '').startswith('TRN-'):
        return 'training_management'
    if (code or '').startswith('INT-'):
        return 'telecom_crm'
    return 'custom_development'


# Router ownership is explicit; shared identity, organization and runtime primitives
# stay available. Authoring tools require their own entitlement.
TELECOM_ROUTERS = {'routes_catalog', 'routes_crm', 'routes_records', 'routes_c360',
    'routes_product_config', 'routes_configurator', 'routes_product_model', 'routes_bim',
    'routes_field_service', 'routes_order_mgmt', 'routes_comms', 'routes_inventory_sync', 'routes_fulfillment', 'routes_inventory', 'routes_analytics'}


def route_module(request):
    path = request.url.path
    owner = getattr(request.scope.get('endpoint'), '__module__', '').rsplit('.', 1)[-1]
    if path.startswith('/api/admin/flows'):
        return 'flow_studio'
    if path.startswith('/api/admin/pages'):
        return 'page_designer'
    if path.startswith(('/api/admin/custom-fields', '/api/admin/record-types', '/api/custom-records', '/api/admin/intents', '/api/process-builder',
                        '/api/admin/function-map', '/api/assistant/scratch')):
        return 'custom_development'
    if owner == 'routes_admin' and not path.startswith(('/api/admin/users', '/api/admin/ai-settings')):
        return 'telecom_crm'
    if path.startswith(('/api/admin/price-lists', '/api/admin/process-roles')):
        return 'telecom_crm'
    if owner in {'routes_function_map', 'routes_builder', 'routes_conflicts'}:
        return 'custom_development'
    if path in ('/api/intents/lookup', '/api/pages/offerings') or path.startswith('/api/pages/lead/'):
        return 'telecom_crm'
    if path.startswith('/api/audit'):
        return None
    if owner in TELECOM_ROUTERS:
        return 'telecom_crm'
    return None


def license_guard(module):
    def check(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        require_module(db, user.tenant_id, module)
    return check


def include_module_router(app, router):
    from fastapi import APIRouter
    from fastapi.routing import APIRoute
    for route in router.routes:
        module = None
        if isinstance(route, APIRoute):
            request = Request({'type': 'http', 'path': route.path, 'headers': [], 'endpoint': route.endpoint})
            module = route_module(request)
        # include_router wraps the app's lifespan around each included router's
        # (empty) one; with a router per route that nests ~500 deep and startup
        # dies with RecursionError. Keep the app's own lifespan instead.
        lifespan = app.router.lifespan_context
        app.include_router(APIRouter(routes=[route]), dependencies=[Depends(license_guard(module))] if module else [])
        app.router.lifespan_context = lifespan

# Functional ownership is independent of authoring-tool entitlements.
TELECOM_ENTITIES = {'party', 'lead', 'opportunity', 'quote', 'order', 'ticket', 'work_order',
                    'offering', 'product', 'service_account', 'billing_account'}


def entity_available(db, tenant_id, entity):
    if not entity or entity == '@entity_type':
        return True
    module = 'training_management' if entity.startswith('training_') else 'telecom_crm'
    return module in enabled(db, tenant_id)


def require_entity(db, tenant_id, entity):
    if not entity_available(db, tenant_id, entity):
        raise HTTPException(403, 'This record type is not available in the selected modules')


def intent_available(db, tenant_id, code, seen=None):
    if not code:
        return True
    if intent_module(code) not in enabled(db, tenant_id):
        return False
    if code.startswith('FLOW-'):
        from app.models.flows import FlowDefinition
        try:
            flow = db.get(FlowDefinition, int(code[5:]))
        except ValueError:
            return False
        seen = set(seen or ())
        if code in seen:
            return False
        seen.add(code)
        return bool(flow and flow.tenant_id == tenant_id and flow_available(db, tenant_id, flow, seen))
    return True


def flow_available(db, tenant_id, flow, seen=None):
    import json
    if not entity_available(db, tenant_id, flow.entity_type):
        return False
    if flow.trigger_intent_type and not intent_available(db, tenant_id, flow.trigger_intent_type, seen):
        return False
    try:
        graph = json.loads(flow.graph_json or '{}')
    except ValueError:
        return False
    return all(intent_available(db, tenant_id, (n.get('config') or {}).get('intent'), seen)
               for n in graph.get('nodes', []) if n.get('type') == 'action')
