"""Module boundaries: which module every file belongs to, and which other
modules it may use.

The code is laid out by layer (crm_core = services, experience = routes,
models = tables); the services it will split into later are cut by
business area. This map is that cut. Each file belongs to exactly one
module (first matching pattern wins); a module may import itself, the
chassis and the modules in its `uses`. Everything else is a boundary
crossing - the work list for splitting the module out. Intents and Flow
Studio reach domains through app/capabilities.py, not by import.

tests/test_boundaries.py fails on a crossing that isn't in
tests/boundary_exceptions.txt, and on an entry there that is gone (so the
list only shrinks). After removing a crossing on purpose:

    python -m app.boundaries --write

Patterns are dotted paths under app/, matched with fnmatch.
"""

from __future__ import annotations

import ast
import fnmatch
import pathlib
import sys
from dataclasses import dataclass

APP = pathlib.Path(__file__).resolve().parent
EXCEPTIONS = APP.parent / "tests" / "boundary_exceptions.txt"


@dataclass(frozen=True)
class Module:
    name: str
    files: tuple[str, ...]
    uses: tuple[str, ...] = ()


# The future services (architecture plan, section "Target services"), in
# matching order: a file goes to the first module with a matching pattern.
MODULES: tuple[Module, ...] = (
    # What every service gets: settings, database, token checks, tenant
    # scope, ids, metrics, events out, licensing, the capability registry.
    Module("chassis", (
        "config", "db", "auth", "tenancy", "rls", "observability", "timeutils", "brt_id",
        "capabilities", "boundaries", "licensing", "service", "ratelimit", "services", "",
        "data_intelligence.event_bus", "data_intelligence", "models.auth", "models.tenant",
        "schemas", "experience", "orchestration", "intent_engine", "crm_core", "integration",
        "experience.routes_modules",
    )),
    # Wires everything together - the one place allowed to import it all
    # (app.models gathers every table for the migrations).
    Module("assembly", ("main", "seed", "scheduler", "models")),
    Module("audit", ("data_intelligence.audit", "crm_core.audit_service")),
    Module("ai", ("crm_core.llm", "crm_core.ai_settings_service", "models.ai_settings", "services.ai_gateway")),
    Module("notification", (
        "crm_core.notice_service", "integration.notification_adapter", "experience.routes_notices",
    ), uses=("audit", "identity")),
    Module("identity", (
        "access", "crm_core.identity_service", "crm_core.access_service", "crm_core.org_policy",
        "models.identity", "models.org", "experience.routes_auth", "experience.routes_org",
        "experience.routes_tenants", "schemas.auth", "schemas.org", "orchestration.actions_identity",
    ), uses=("audit",)),
    # Page Designer, custom records and fields, the generic record APIs,
    # record lifecycles and approvals.
    Module("metadata", (
        "crm_core.page_service", "crm_core.custom_record_service", "crm_core.custom_field_service",
        "crm_core.attribute_service", "crm_core.record_*", "crm_core.lifecycle_service",
        "crm_core.approval_service", "models.pages", "models.custom_*", "models.attributes",
        "models.lifecycle", "models.approval", "experience.routes_pages", "experience.routes_custom_records",
        "experience.routes_records", "experience.routes_admin", "schemas.custom_records", "schemas.admin",
        "orchestration.actions_pages", "orchestration.actions_lifecycle",
    ), uses=("identity", "audit", "ai")),
    # Catalog, Product Modeller, configurator, pricing.
    Module("product", (
        "crm_core.catalog_service", "crm_core.product_*", "crm_core.configurator", "crm_core.price_formula",
        "crm_core.model_lifecycle", "crm_core.instance_service", "models.catalog", "models.product_*",
        "experience.routes_catalog", "experience.routes_product_*", "experience.routes_configurator",
        "schemas.catalog", "orchestration.actions", "orchestration.actions_config",
        "orchestration.actions_configurator", "integration.tmf620_adapter",
    ), uses=("metadata", "audit", "ai")),
    Module("bim", ("crm_core.bim_service", "models.bim", "experience.routes_bim"), uses=("product", "metadata", "ai")),
    Module("training", (
        "crm_core.training_*", "models.training", "experience.routes_training*", "schemas.training", "services.training*",
    ), uses=("identity", "notification", "metadata", "ai")),
    # Leads, accounts, opportunities, quotes.
    Module("crm", (
        "crm_core.lead_service", "crm_core.account_service", "crm_core.quote_*", "crm_core.customer_overview",
        "crm_core.dashboard_service", "models.crm", "models.accounts", "models.quote_docs",
        "experience.routes_crm", "schemas.crm", "orchestration.actions_l2q", "orchestration.actions_prospect",
        "orchestration.actions_sales", "integration.party_adapter", "integration.address_adapter",
        "integration.document_adapter",
    ), uses=("identity", "metadata", "product", "audit", "notification", "ai")),
    # Orders, fulfilment, inventory, field service, assets, tickets, SLAs,
    # communications billing - telecom-core until load or a team needs it split.
    Module("telecom", (
        "crm_core.order_service", "crm_core.om_service", "crm_core.b2c_order_service", "crm_core.abo_service",
        "crm_core.feasibility_service", "crm_core.ponr_service", "crm_core.fs_*", "crm_core.field_service",
        "crm_core.inventory_*", "crm_core.sla_service", "crm_core.ticket_service", "crm_core.comm_*",
        "models.orders", "models.order_mgmt", "models.fulfillment*", "models.inventory", "models.field_service",
        "models.abo", "models.feasibility", "models.ponr", "models.sla", "models.assurance", "models.comms",
        "experience.routes_order_mgmt", "experience.routes_fulfillment", "experience.routes_inventory*",
        "experience.routes_field_service", "experience.routes_comms",
        "orchestration.actions_o2a", "orchestration.actions_t2r", "orchestration.actions_om",
        "orchestration.actions_b2c", "orchestration.actions_fulfillment", "orchestration.actions_inventory",
        "orchestration.actions_feasibility", "orchestration.actions_offering_order", "orchestration.actions_site",
        "orchestration.actions_comm", "orchestration.fulfillment_*", "integration.*",
    ), uses=("crm", "product", "metadata", "identity", "audit", "notification", "ai")),
    Module("c360", ("c360.*", "c360", "models.c360", "experience.routes_c360", "orchestration.actions_c360"),
           uses=("crm", "telecom", "product", "metadata", "identity", "ai")),
    Module("analytics", ("analytics.*", "analytics", "models.analytics", "experience.routes_analytics"),
           uses=("crm", "telecom", "product", "c360", "metadata", "identity", "ai")),
    # Flow Studio: flows, processes, the function map, test runs.
    Module("flow", (
        "orchestration.flow_*", "orchestration.process_*", "orchestration.function_map",
        "crm_core.process_service", "crm_core.orchestration_mode_service", "crm_core.test_runner_service",
        "models.flows", "models.process", "models.orchestration_policy", "models.testing",
        "experience.routes_studio", "experience.routes_process", "experience.routes_function_map",
        "experience.routes_builder", "schemas.studio",
    ), uses=("metadata", "intent", "identity", "audit", "notification", "ai")),
    # The intent pipeline: understand, reason, plan, run (via the registry),
    # verify - and the agent and assistant built on it.
    Module("intent", (
        "intent_engine.*", "orchestration.*", "models.intent_*", "experience.routes_intent*",
        "experience.routes_agent", "experience.routes_assistant", "experience.routes_conflicts",
        "schemas.intent*",
    ), uses=("metadata", "identity", "audit", "notification", "ai")),
)

_BY_NAME = {m.name: m for m in MODULES}


def module_of(dotted: str) -> str | None:
    """'app.crm_core.bim_service' -> 'bim'."""
    rel = dotted.removeprefix("app.").removeprefix("app")
    for module in MODULES:
        if any(fnmatch.fnmatchcase(rel, pattern) for pattern in module.files):
            return module.name
    return None


def files() -> dict[str, pathlib.Path]:
    """Every module under app/, by dotted name."""
    out = {}
    for path in sorted(APP.rglob("*.py")):
        parts = path.relative_to(APP.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = path
    return out


def _imports(path: pathlib.Path, known: dict[str, pathlib.Path]) -> set[str]:
    """Every app.* module this file imports, at the top or inside functions.
    `from app.x import y` counts as app.x.y when that is a module."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith("app"):
            for alias in node.names:
                sub = f"{node.module}.{alias.name}"
                found.add(sub if sub in known else node.module)
    return found


def allowed(src: str, dst: str) -> bool:
    return src == dst or dst == "chassis" or src == "assembly" or dst in _BY_NAME[src].uses


def crossings() -> tuple[set[str], list[str]]:
    """({"app.x.file -> module", ...} that cross a boundary, [unmapped files])."""
    found, unmapped = set(), []
    known = files()
    for dotted, path in known.items():
        src = module_of(dotted)
        if src is None:
            unmapped.append(dotted)
            continue
        for target in _imports(path, known):
            dst = module_of(target)
            if dst is not None and not allowed(src, dst):
                found.add(f"{dotted} -> {dst}")
    return found, unmapped


def recorded() -> set[str]:
    if not EXCEPTIONS.exists():
        return set()
    lines = EXCEPTIONS.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def write() -> None:
    found, unmapped = crossings()
    if unmapped:
        sys.exit(f"Not in any module (add to MODULES): {', '.join(unmapped)}")
    header = (
        "# Boundary crossings that exist today (app/boundaries.py). Each line is a\n"
        "# file that imports a module it doesn't declare it uses. Remove lines by\n"
        "# removing the crossing; never add one by hand - rerun\n"
        "#   python -m app.boundaries --write\n"
    )
    EXCEPTIONS.write_text(header + "".join(f"{line}\n" for line in sorted(found)), encoding="utf-8")
    print(f"{len(found)} crossings written to {EXCEPTIONS}")


def summary() -> None:
    found, unmapped = crossings()
    pairs: dict[tuple[str, str], int] = {}
    for line in found:
        src_file, dst = line.split(" -> ")
        key = (module_of(src_file) or "?", dst)
        pairs[key] = pairs.get(key, 0) + 1
    for (src, dst), count in sorted(pairs.items(), key=lambda item: -item[1]):
        print(f"{src:>12} -> {dst:<12} {count:4} files")
    print(f"{len(found)} crossings; unmapped: {unmapped or 'none'}")


if __name__ == "__main__":
    write() if "--write" in sys.argv else summary()
