"""The Configure 360 agent: plain words in, a checked layout change out.

    "add a billing pane with revenue trend and overdue invoices as a donut,
     connect our network system, make it compact with Vi red"

becomes a short list of operations (add_pane, add_widget, connect,
set_theme, ...). Operations - never free-form JSON - are the only thing
either engine may produce, and apply_ops() is the only thing that turns
them into a layout, which then passes catalog.normalize_layout(). So the
LLM (used when the tenant has an AI key) can only pick from the same safe
moves the rule engine makes, and anything it invents is dropped with a note.

Nothing is saved here: plan() returns the proposed layout, a plain-English
change list and any systems to connect - the studio previews it, the
INT-33 intent saves it (see orchestration/actions_c360.py).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from app.c360 import catalog, connectors
from app.c360.catalog import DATASETS, DOMAINS, RECIPES, TEMPLATES, THEMES
from app.c360.connectors import ConnectorSpec

OPS = (
    "apply_template", "ensure_domains", "add_pane", "remove_pane", "update_pane", "move_pane", "layout_columns",
    "add_widget", "remove_widget", "update_widget", "set_theme", "set_header_kpis", "set_component_audience", "connect", "undo",
)

_DOMAIN_WORDS: dict[str, tuple[str, ...]] = {
    "sales": ("sales", "pipeline", "deal", "deals", "opportunit", "selling", "commercial"),
    "service": ("service desk", "servicedesk", "service", "ticket", "support", "care", "complaint", "assurance", "itsm", "incident"),
    "billing": ("billing", "bss", "invoice", "revenue", "payment", "finance", "charging", "bills", "spend"),
    "network": ("network", "oss", "nms", "noc", "latency", "uptime", "link", "bandwidth"),
    "inventory": ("inventory", "asset", "device", "cpe", "equipment", "hardware"),
}
_EXTERNAL_DOMAINS = ("billing", "service", "network", "inventory")
_COLORS = {
    "vi red": "#e60000", "vodafone red": "#e60000", "red": "#e60000", "crimson": "#dc2626", "blue": "#3b82f6",
    "sky": "#38bdf8", "navy": "#1e3a8a", "teal": "#14b8a6", "cyan": "#22d3ee", "green": "#22c55e", "emerald": "#10b981",
    "purple": "#a855f7", "violet": "#8b5cf6", "lavender": "#a78bfa", "pink": "#ec4899", "magenta": "#d946ef",
    "orange": "#f97316", "amber": "#f59e0b", "yellow": "#eab308", "gold": "#f59e0b", "indigo": "#6366f1",
    "grey": "#94a3b8", "gray": "#94a3b8", "white": "#f8fafc",
}
_PRESET_WORDS = {
    "daylight": ("light mode", "light theme", "daylight", "bright", "white background", "light background", "make it light"),
    "midnight": ("midnight", "dark mode", "dark theme", "make it dark"),
    "ocean": ("ocean", "sea", "marine", "deep blue"),
    "aurora": ("aurora", "northern lights", "neon", "vibrant"),
    "graphite": ("graphite", "charcoal", "black theme", "monochrome", "minimal"),
}
_TYPE_WORDS = {
    "donut": ("donut", "doughnut", "pie", "ring"),
    "line": ("line chart", "line graph", "as a line", "trend line", "line"),
    "area": ("area chart", "area"),
    "bar": ("bar chart", "bars", "bar", "column chart", "histogram"),
    "gauge": ("gauge", "dial", "meter", "speedometer"),
    "table": ("table", "list", "grid"),
    "kpi": ("kpi", "number", "tile", "counter", "big number", "metric card"),
}
_ADD_VERBS = r"\b(add|show|include|display|put|want|need|give|create|plot|chart|graph|track|see|bring|place|insert|with)\b"
_REMOVE_VERBS = r"\b(remove|delete|hide|drop|get rid of|take out|take away|without|no more)\b"
_PANE_NOUNS = r"\b(pane|panes|panel|panels|section|sections|column|columns|card|cards|block|blocks|area|tab)\b"
_URL = re.compile(r"https?://[^\s,;\"'<>]+", re.I)


# ---------------------------------------------------------------- helpers


def _domains_in(text: str) -> list[str]:
    found = []
    for domain, words in _DOMAIN_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}", text) for w in words):
            found.append(domain)
    # "service accounts" is inventory, not the service desk
    if "service" in found and re.search(r"service accounts?", text) and not re.search(r"service desk|ticket|support|care", text):
        found.remove("service")
        if "inventory" not in found:
            found.append("inventory")
    return found


def find_pane(layout: dict[str, Any], ref: Any) -> dict[str, Any] | None:
    if not ref:
        return None
    ref_s = str(ref).strip().lower()
    for p in layout["panes"]:
        if p["id"].lower() == ref_s or p["title"].lower() == ref_s:
            return p
    for p in sorted(layout["panes"], key=lambda p: -len(p["title"])):
        if len(p["title"]) > 2 and p["title"].lower() in ref_s:
            return p
    for p in layout["panes"]:
        if p["domain"] == ref_s:
            return p
    domains = _domains_in(ref_s)
    for d in domains:
        for p in layout["panes"]:
            if p["domain"] == d:
                return p
    return None


def find_widget(layout: dict[str, Any], ref: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not ref:
        return None
    ref_s = str(ref).strip().lower()
    for p in layout["panes"]:
        for w in p["widgets"]:
            if ref_s in (w["id"].lower(), (w["recipe"] or "").lower(), w["title"].lower()):
                return p, w
    for p in layout["panes"]:
        for w in p["widgets"]:
            if w["title"].lower() in ref_s or (w["recipe"] and any(k in ref_s for k in RECIPES[w["recipe"]].keywords if len(k) > 4)):
                return p, w
    return None


def _shape(recipe_or_widget: dict[str, Any]) -> str:
    t, m = recipe_or_widget["type"], recipe_or_widget.get("metric") or {}
    if t in ("records", "insights", "table"):
        return t
    if t in catalog.SCALAR_TYPES:
        return "scalar"
    if m.get("x"):
        return "trend"
    return "category"


_SHAPE_TYPES = {"scalar": ("kpi", "gauge"), "trend": ("line", "area", "bar"), "category": ("bar", "donut", "table"), "table": ("table",)}


def _retype(widget: dict[str, Any], new_type: str) -> bool:
    shape = _shape(widget)
    if new_type not in _SHAPE_TYPES.get(shape, ()):
        return False
    widget["type"] = new_type
    return True


def _unique_key(base: str, taken: set[str]) -> str:
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


# ---------------------------------------------------------------- applying operations


def apply_ops(
    layout: dict[str, Any], ops: list[dict[str, Any]], existing: list[ConnectorSpec] | None = None
) -> dict[str, Any]:
    lay = copy.deepcopy(layout)
    existing = existing or []
    changes: list[str] = []
    notes: list[str] = []
    drafts: list[dict[str, Any]] = []
    undo = False
    taken = {s.key for s in existing}

    def add_pane(domain: str, title: str | None = None, width: str | None = None, recipes: list[str] | None = None, at: int | None = None) -> dict[str, Any]:
        pane = catalog.make_pane(domain, title, width or "half", recipes)
        if len(lay["panes"]) >= catalog.MAX_PANES:
            notes.append(f"There are already {catalog.MAX_PANES} panes - remove one before adding {pane['title']}.")
            return pane
        if at is None:
            lay["panes"].append(pane)
        else:
            lay["panes"].insert(at, pane)
        changes.append(f"Added the {pane['title']} pane" + (f" with {', '.join(w['title'] for w in pane['widgets'])}" if pane["widgets"] else ""))
        return pane

    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind not in OPS:
            notes.append(f"Skipped an unknown step '{kind}'.")
            continue

        if kind == "undo":
            undo = True
            changes.append("Went back to the previous version")

        elif kind == "apply_template":
            key = op.get("template")
            if key not in TEMPLATES:
                notes.append(f"No layout template called '{key}'.")
                continue
            lay = catalog.template_layout(key, theme=lay["theme"])
            lay["header"] = layout["header"]
            lay["component_audiences"] = layout.get("component_audiences", {})
            changes.append(f"Switched to the “{TEMPLATES[key]['label']}” layout")

        elif kind == "ensure_domains":
            already = []
            for d in op.get("domains") or []:
                if d in DOMAINS and not any(p["domain"] == d for p in lay["panes"]):
                    add_pane(d)
                elif d in DOMAINS:
                    already.append(DOMAINS[d]["label"])
            if already:
                notes.append(f"{', '.join(already)} {'is' if len(already) == 1 else 'are'} already on the page.")

        elif kind == "add_pane":
            domain = op.get("domain") if op.get("domain") in DOMAINS else "custom"
            recipes = [r for r in op.get("recipes") or [] if r in RECIPES] or None
            at = 0 if op.get("position") in ("top", "first", 0) else None
            add_pane(domain, op.get("title"), op.get("width"), recipes, at)

        elif kind == "remove_pane":
            pane = find_pane(lay, op.get("pane"))
            if pane is None:
                notes.append(f"Couldn't find a pane called '{op.get('pane')}'.")
                continue
            lay["panes"].remove(pane)
            changes.append(f"Removed the {pane['title']} pane")

        elif kind == "update_pane":
            pane = find_pane(lay, op.get("pane"))
            if pane is None:
                notes.append(f"Couldn't find a pane called '{op.get('pane')}'.")
                continue
            if op.get("title"):
                changes.append(f"Renamed {pane['title']} to {op['title']}")
                pane["title"] = str(op["title"])[:60]
            if op.get("width") in catalog.WIDTHS:
                pane["width"] = op["width"]
                changes.append(f"{pane['title']} is now {op['width'].replace('_', ' ')} width")
            if isinstance(op.get("color"), str) and re.match(r"^#[0-9a-fA-F]{6}$", op["color"]):
                pane["color"] = op["color"]
                changes.append(f"Recoloured {pane['title']}")
            if op.get("icon"):
                pane["icon"] = str(op["icon"])[:8]
            if "collapsed" in op:
                pane["collapsed"] = bool(op["collapsed"])
            if "audience" in op:
                pane["audience"] = catalog.normalize_audience(op["audience"])
                changes.append(f"Updated the customer audience for {pane['title']}")

        elif kind == "move_pane":
            pane = find_pane(lay, op.get("pane"))
            if pane is None:
                notes.append(f"Couldn't find a pane called '{op.get('pane')}'.")
                continue
            lay["panes"].remove(pane)
            to = op.get("to")
            if to in ("top", "first", "start", 0):
                idx = 0
            elif isinstance(to, int):
                idx = max(0, min(len(lay["panes"]), to))
            elif isinstance(to, str) and to.startswith(("before:", "after:")):
                other = find_pane(lay, to.split(":", 1)[1])
                idx = (lay["panes"].index(other) + (1 if to.startswith("after:") else 0)) if other else len(lay["panes"])
            else:
                idx = len(lay["panes"])
            lay["panes"].insert(idx, pane)
            changes.append(f"Moved {pane['title']} to position {idx + 1}")

        elif kind == "layout_columns":
            width = {1: "full", 2: "half", 3: "third"}.get(int(op.get("columns") or 2), "half")
            for p in lay["panes"]:
                p["width"] = width
            changes.append(f"Arranged the panes in {op.get('columns')} column{'s' if int(op.get('columns') or 2) > 1 else ''}")

        elif kind == "add_widget":
            recipe = RECIPES.get(op.get("recipe") or "")
            if recipe is None:
                notes.append(f"No chart called '{op.get('recipe')}' in the library.")
                continue
            pane = find_pane(lay, op.get("pane")) or next((p for p in lay["panes"] if p["domain"] == recipe.domain), None)
            if pane is None:
                pane = add_pane(recipe.domain, recipes=[])
            widget = recipe.widget()
            if op.get("type") and op["type"] != widget["type"] and not _retype(widget, op["type"]):
                notes.append(f"{recipe.title} can't be drawn as a {op['type']} - kept it as a {widget['type']}.")
            if any(w["recipe"] == recipe.id and w["type"] == widget["type"] for w in pane["widgets"]):
                notes.append(f"{recipe.title} is already in {pane['title']}.")
                continue
            if op.get("title"):
                widget["title"] = str(op["title"])[:80]
            if len(pane["widgets"]) >= catalog.MAX_WIDGETS:
                notes.append(f"{pane['title']} is full ({catalog.MAX_WIDGETS} widgets).")
                continue
            pane["widgets"].append(widget)
            changes.append(f"Added {widget['title']} ({widget['type']}) to {pane['title']}")

        elif kind == "remove_widget":
            found = find_widget(lay, op.get("widget"))
            if found is None:
                notes.append(f"Couldn't find a widget called '{op.get('widget')}'.")
                continue
            pane, widget = found
            pane["widgets"].remove(widget)
            changes.append(f"Removed {widget['title']} from {pane['title']}")

        elif kind == "update_widget":
            found = find_widget(lay, op.get("widget"))
            if found is None:
                notes.append(f"Couldn't find a widget called '{op.get('widget')}'.")
                continue
            pane, widget = found
            if op.get("type") and op["type"] != widget["type"]:
                old = widget["type"]
                if _retype(widget, op["type"]):
                    changes.append(f"{widget['title']} is now a {op['type']} instead of a {old}")
                else:
                    notes.append(f"{widget['title']} can't be drawn as a {op['type']}.")
            if op.get("title"):
                changes.append(f"Renamed {widget['title']} to {op['title']}")
                widget["title"] = str(op["title"])[:80]
            if "source" in op:
                widget["source"] = op["source"] or None
                changes.append(f"{widget['title']} now reads from {op['source'] or 'the default source'}")
            if "audience" in op:
                widget["audience"] = catalog.normalize_audience(op["audience"])
                changes.append(f"Updated the customer audience for {widget['title']}")

        elif kind == "set_component_audience":
            component = op.get("component")
            if isinstance(component, str) and (component in catalog.COMPONENTS or component in {"kpi:" + k for k in catalog.HEADER_KPI_CHOICES}):
                lay.setdefault("component_audiences", {})[component] = catalog.normalize_audience(op.get("audience"))
                changes.append(f"Updated the customer audience for {component}")

        elif kind == "set_theme":
            theme = lay["theme"]
            said = []
            if op.get("preset") in THEMES:
                theme["preset"] = op["preset"]
                said.append(f"{THEMES[op['preset']]['label']} theme")
            if isinstance(op.get("accent"), str) and re.match(r"^#[0-9a-fA-F]{6}$", op["accent"]):
                theme["accent"] = op["accent"]
                said.append(f"accent {op['accent']}")
            for key, allowed, noun in (("card_style", catalog.CARD_STYLES, "cards"), ("density", catalog.DENSITIES, "spacing"),
                                       ("header_style", catalog.HEADER_STYLES, "header")):
                if op.get(key) in allowed:
                    theme[key] = op[key]
                    said.append(f"{op[key]} {noun}")
            if said:
                changes.append("Look & feel: " + ", ".join(said))

        elif kind == "set_header_kpis":
            kpis = list(lay["header"]["kpis"])
            if isinstance(op.get("set"), list):
                kpis = [k for k in op["set"] if k in catalog.HEADER_KPI_CHOICES]
            for k in op.get("add") or []:
                if k in catalog.HEADER_KPI_CHOICES and k not in kpis:
                    kpis.append(k)
            kpis = [k for k in kpis if k not in (op.get("remove") or [])][:6]
            if kpis != lay["header"]["kpis"]:
                lay["header"]["kpis"] = kpis
                changes.append("Headline numbers: " + ", ".join(RECIPES[k].title for k in kpis))

        elif kind == "connect":
            domain = op.get("domain")
            if domain not in connectors.DOMAIN_DATASETS:
                notes.append(f"Don't know what kind of system '{domain}' is.")
                continue
            url = op.get("base_url") or ""
            kind_ = "rest" if url else (op.get("kind") if op.get("kind") in connectors.KINDS else "demo")
            reuse = next((s for s in existing if s.domain == domain and s.kind == kind_ and (not url or s.base_url == url)), None)
            reuse = reuse or next((d for d in drafts if d["domain"] == domain), None)
            if reuse is None:
                name = op.get("name") or (connectors.DEMO_NAMES[domain] if kind_ == "demo" else f"{connectors.domain_label(domain)} system")
                key = _unique_key(connectors.slug(name), taken)
                taken.add(key)
                draft = {"key": key, "name": name, "domain": domain, "kind": kind_, "base_url": url,
                         "endpoints": connectors.demo_endpoints(domain) if kind_ == "demo" else
                         {d: {"path": connectors.DEFAULT_PATHS[d], "records_path": "", "field_map": {}} for d in connectors.DOMAIN_DATASETS[domain]}}
                drafts.append(draft)
                key_used, name_used = key, name
                changes.append(f"Connected {name}" + (f" at {url}" if url else " - simulated data until you add the real address"))
            else:
                key_used = reuse.key if isinstance(reuse, ConnectorSpec) else reuse["key"]
                name_used = reuse.name if isinstance(reuse, ConnectorSpec) else reuse["name"]
            served = set(connectors.DOMAIN_DATASETS[domain])
            if not any(p["domain"] == domain for p in lay["panes"]):
                add_pane(domain)
            rewired = 0
            for p in lay["panes"]:
                for w in p["widgets"]:
                    if w["dataset"] in served and (p["domain"] == domain or not DATASETS[w["dataset"]].internal) and w["source"] != key_used:
                        w["source"] = key_used
                        rewired += 1
            if rewired:
                changes.append(f"{rewired} widget{'s' if rewired > 1 else ''} now read from {name_used}")

    return {"layout": catalog.normalize_layout(lay), "changes": changes, "notes": notes, "connectors": drafts, "undo": undo}


# ---------------------------------------------------------------- rule engine


def _match_recipes(text: str, domains: list[str]) -> list[tuple[str, int]]:
    scored = []
    for r in RECIPES.values():
        score = 0
        for kw in (*r.keywords, r.title.lower()):
            if re.search(rf"\b{re.escape(kw)}", text):
                score = max(score, len(kw) + (3 if r.domain in domains else 0))
        if score:
            scored.append((r.id, score))
    scored.sort(key=lambda s: -s[1])
    if not scored:
        return []
    top = scored[0][1]
    picked: list[tuple[str, int]] = []
    for rid, score in scored:
        if score < top * 0.55 or len(picked) >= 4:
            break
        # one widget per dataset+shape unless clearly asked
        if any(RECIPES[p].dataset == RECIPES[rid].dataset and _shape(RECIPES[p].widget()) == _shape(RECIPES[rid].widget()) for p, _ in picked):
            continue
        picked.append((rid, score))
    return picked


def _type_word(text: str) -> str | None:
    best, pos = None, 10_000
    for wtype, words in _TYPE_WORDS.items():
        for w in words:
            m = re.search(rf"\b{re.escape(w)}\b", text)
            if m and m.start() < pos:
                best, pos = wtype, m.start()
    return best


def _color(text: str) -> str | None:
    m = re.search(r"#[0-9a-fA-F]{6}\b", text)
    if m:
        return m.group(0)
    for name in sorted(_COLORS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text):
            return _COLORS[name]
    return None


def _rules(instruction: str, layout: dict[str, Any], brand_color: str | None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    # Keep URLs whole while splitting the request into clauses at . ; then also plus
    urls = _URL.findall(instruction)
    masked = _URL.sub(lambda m: f" URL{urls.index(m.group(0))} ", instruction)
    clauses = [c.strip() for c in re.split(r"(?<!\d)[.;\n]+|\bthen\b|\balso\b|\bplus\b", masked, flags=re.I) if c.strip()]
    clauses = [re.sub(r"URL(\d+)", lambda m: urls[int(m.group(1))], c) for c in clauses]
    for raw in clauses:
        t = " " + raw.lower() + " "
        url_match = _URL.search(raw)
        url = url_match.group(0).rstrip(".") if url_match else None
        t_no_url = _URL.sub(" ", t)
        domains = _domains_in(t_no_url)
        handled = connected = False

        if re.search(r"\bundo\b|\bgo back\b|\brevert\b|\bprevious version\b", t):
            ops.append({"op": "undo"})
            continue
        if re.search(r"\b(reset|start over|start again|default layout|from scratch)\b", t):
            ops.append({"op": "apply_template", "template": catalog.DEFAULT_TEMPLATE})
            continue
        for key, pattern in (("classic", r"\b(classic|old view|original view|record view)\b"),
                             ("sales_focus", r"\b(account manager|sales focus|sales[- ]first|for sales reps?)\b"),
                             ("service_focus", r"\b(customer care|care agent|service focus|service[- ]first|for (support|care) agents?)\b")):
            if re.search(pattern, t) and re.search(r"\b(layout|template|view|style|page|like|for)\b", t):
                ops.append({"op": "apply_template", "template": key})
                handled = True
        if handled:
            continue

        # --- connect external systems
        if re.search(r"\b(connect|integrat\w*|hook up|plug in|pull (data )?from|fetch from|sync|wire up|link (to|up|our|the)|bring (in )?(the )?data|api|apis|endpoint)\b", t_no_url):
            targets = [d for d in domains if d in _EXTERNAL_DOMAINS]
            if not targets and re.search(r"\b(all|every|each|respective|different|those)\b.*\bsystems?\b|\bsystems\b", t_no_url):
                targets = list(_EXTERNAL_DOMAINS)
            for d in targets:
                ops.append({"op": "connect", "domain": d, "base_url": url if len(targets) == 1 else "",
                            "kind": "demo" if re.search(r"\b(demo|sample|test|fake|simulat\w*|mock)\b", t) or not url else "rest"})
            if targets:
                handled = connected = True
                if url and len(targets) > 1:
                    ops.append({"op": "_note", "text": "I used demo systems because one address was given for several systems - set each address in Data sources."})
                t_no_url = re.sub(r"\b(billing|bss|service desk|service|network|oss|inventory)\b", " ", t_no_url)

        # --- look & feel
        theme_op: dict[str, Any] = {}
        for preset, words in _PRESET_WORDS.items():
            if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
                theme_op["preset"] = preset
        if re.search(r"\b(compact|dense|tight|condensed)\b", t):
            theme_op["density"] = "compact"
        elif re.search(r"\b(spacious|roomy|comfortable|airy)\b", t):
            theme_op["density"] = "comfortable"
        if re.search(r"\b(glass|frosted|translucent)\b", t):
            theme_op["card_style"] = "glass"
        elif re.search(r"\b(solid|filled)\b", t):
            theme_op["card_style"] = "solid"
        elif re.search(r"\b(outline|outlined|flat|borders? only)\b", t):
            theme_op["card_style"] = "outline"
        if re.search(r"\b(compact|small|slim) header\b", t):
            theme_op["header_style"] = "compact"
        elif re.search(r"\b(hero|big|large) header\b", t):
            theme_op["header_style"] = "hero"
        color = _color(t_no_url)
        pane_for_color = find_pane(layout, t) if re.search(_PANE_NOUNS, t) else None
        if color and pane_for_color is not None and not re.search(r"\b(theme|accent|brand|whole|page|everything)\b", t):
            ops.append({"op": "update_pane", "pane": pane_for_color["id"], "color": color})
            handled = True
        elif color and not re.search(_ADD_VERBS + r".*\b(chart|graph|widget)\b", t):
            theme_op["accent"] = color
        if re.search(r"\b(our|my|the|tenant|company)? ?brand(ing)?( colou?rs?)?\b", t) and "accent" not in theme_op and brand_color:
            theme_op["accent"] = brand_color
        if theme_op:
            ops.append({"op": "set_theme", **theme_op})
            handled = True
        if connected:
            continue

        # --- columns / widths
        m = re.search(r"\b(one|1|single|two|2|three|3)[- ]columns?\b", t)
        if m:
            ops.append({"op": "layout_columns", "columns": {"one": 1, "1": 1, "single": 1, "two": 2, "2": 2, "three": 3, "3": 3}[m.group(1)]})
            handled = True
        if re.search(r"\bside by side\b", t) and domains:
            for d in domains:
                pane = find_pane(layout, d)
                if pane:
                    ops.append({"op": "update_pane", "pane": pane["id"], "width": "half" if len(domains) <= 2 else "third"})
            handled = True
        wm = re.search(r"\b(full[- ]width|full|wider|wide|half[- ]width|half|two[- ]thirds|narrow|third)\b", t)
        if wm and re.search(r"\b(make|set|resize|width|wide|wider|narrow)\b", t) and not m:
            pane = find_pane(layout, t)
            if pane:
                word = wm.group(1).replace("-", " ")
                width = "full" if word.startswith("full") or word in ("wider", "wide") else "two_thirds" if "two" in word else "third" if word in ("narrow", "third") else "half"
                ops.append({"op": "update_pane", "pane": pane["id"], "width": width})
                handled = True

        # --- rename
        rm = re.search(r"\brename (?:the )?(.+?) (?:pane |section |panel |widget |chart )?(?:to|as) [\"'“]?(.+?)[\"'”]?\s*$", raw, re.I)
        if rm:
            target, new = rm.group(1), rm.group(2).strip()
            pane = find_pane(layout, target)
            if pane:
                ops.append({"op": "update_pane", "pane": pane["id"], "title": new})
            elif find_widget(layout, target):
                ops.append({"op": "update_widget", "widget": target, "title": new})
            continue

        # --- move
        mv = re.search(r"\b(move|put|place|bring)\b(.+?)\b(to the top|on top|at the top|first|to the bottom|at the bottom|last|to the end|above|below|before|after)\b(.*)", t)
        if mv and find_pane(layout, mv.group(2)):
            pane = find_pane(layout, mv.group(2))
            where = mv.group(3)
            if where in ("above", "before", "below", "after"):
                other = find_pane(layout, mv.group(4))
                to: Any = f"{'before' if where in ('above', 'before') else 'after'}:{other['id']}" if other else "bottom"
            else:
                to = "top" if any(w in where for w in ("top", "first")) else "bottom"
            ops.append({"op": "move_pane", "pane": pane["id"], "to": to})
            continue

        # --- headline KPIs
        if re.search(r"\b(header|headline|top|banner|hero)\b", t) and re.search(r"\b(kpis?|metrics?|numbers?|tiles?|figures?)\b", t):
            picked = [rid for rid, _ in _match_recipes(t, domains) if rid in catalog.HEADER_KPI_CHOICES]
            if picked:
                key = "remove" if re.search(_REMOVE_VERBS, t) else "add"
                ops.append({"op": "set_header_kpis", key: picked})
                continue

        # --- remove
        if re.search(_REMOVE_VERBS, t):
            recipes = _match_recipes(t, domains)
            removed = False
            for rid, _ in recipes:
                if find_widget(layout, rid):
                    ops.append({"op": "remove_widget", "widget": rid})
                    removed = True
            if not removed:
                for d in domains or []:
                    pane = find_pane(layout, d)
                    if pane:
                        ops.append({"op": "remove_pane", "pane": pane["id"]})
                        removed = True
                if not removed:
                    pane = find_pane(layout, t)
                    if pane:
                        ops.append({"op": "remove_pane", "pane": pane["id"]})
                        removed = True
            if removed:
                continue

        # --- change a chart's type ("show tickets by priority as a bar chart" when it already exists)
        wtype = _type_word(t)
        recipes = _match_recipes(t, domains)
        if wtype and re.search(r"\b(as|into|to|instead|switch|change|turn|make)\b", t):
            changed = False
            for rid, _ in recipes:
                found = find_widget(layout, rid)
                if found and found[1]["type"] != wtype:
                    ops.append({"op": "update_widget", "widget": found[1]["id"], "type": wtype})
                    changed = True
            if changed:
                continue

        # --- whole domains as panes ("sales, service, network and billing on one page")
        everything = re.search(r"\b(everything|all (the )?(panes|domains|views|data)|full 360|complete (view|picture)|single page|one page|unified|all in one)\b", t)
        wants_panes = re.search(_PANE_NOUNS, t) or everything or (len(domains) >= 2 and not recipes)
        if not handled and wants_panes and (domains or everything) and (len(domains) >= 2 or everything or not recipes):
            targets = domains if domains and not (everything and len(domains) < 2) else list(_EXTERNAL_DOMAINS) + ["sales"]
            ops.append({"op": "ensure_domains", "domains": targets})
            if everything and not any(p["domain"] == "custom" for p in layout["panes"]):
                ops.append({"op": "add_pane", "domain": "custom", "title": "Needs attention", "width": "full", "recipes": ["custom.insights"], "position": "top"})
            handled = True
            continue

        # --- add charts
        if recipes and (re.search(_ADD_VERBS, t) or not handled):
            target = None
            pm = re.search(r"\b(?:to|in|into|on|under)\s+(?:the\s+)?(.+?)\s+" + _PANE_NOUNS, t)
            if pm and find_pane(layout, pm.group(1)):
                target = find_pane(layout, pm.group(1))["id"]
            for rid, _ in recipes:
                op: dict[str, Any] = {"op": "add_widget", "recipe": rid}
                if target:
                    op["pane"] = target
                if wtype and len(recipes) == 1:
                    op["type"] = wtype
                ops.append(op)
            continue

        if domains and re.search(_ADD_VERBS, t) and not handled:
            ops.append({"op": "ensure_domains", "domains": domains})
    return ops


# ---------------------------------------------------------------- LLM engine

_LLM_PROMPT = """You configure the Customer 360 page of a telecom CRM for a tenant administrator.
Turn the admin's request into a JSON list of operations. Use ONLY these operations:
- {"op":"apply_template","template":<one of TEMPLATES>}
- {"op":"ensure_domains","domains":[sales|service|billing|network|inventory|custom]}
- {"op":"add_pane","domain":..,"title"?:..,"width"?:third|half|two_thirds|full,"recipes"?:[recipe ids],"position"?:"top"}
- {"op":"remove_pane","pane":<pane id or title>}
- {"op":"update_pane","pane":..,"title"?:..,"width"?:..,"color"?:"#rrggbb","audience"?:{...}}
- {"op":"move_pane","pane":..,"to":"top"|"bottom"|"before:<pane>"|"after:<pane>"}
- {"op":"layout_columns","columns":1|2|3}
- {"op":"add_widget","recipe":<recipe id>,"pane"?:<pane id>,"type"?:<widget type>,"title"?:..}
- {"op":"remove_widget","widget":<widget id or title>}
- {"op":"update_widget","widget":..,"type"?:..,"title"?:..,"audience"?:{...}}
- {"op":"set_component_audience","component":<standard component key or kpi:recipe id>,"audience":{...}}
Audience shape: {"enabled":true,"customer_types":["account","prospect"],"segments":["B2B","B2C"]}.
Use subsets to target customer types and segments; both dimensions must match. Preserve existing audiences unless asked to change them.
- {"op":"set_theme","preset"?:<THEMES>,"accent"?:"#rrggbb","card_style"?:glass|solid|outline,"density"?:comfortable|compact,"header_style"?:hero|compact}
- {"op":"set_header_kpis","add"?:[recipe ids],"remove"?:[recipe ids]}
- {"op":"connect","domain":billing|service|network|inventory,"base_url"?:"https://...","name"?:..} (no base_url = a demo system)
- {"op":"undo"}
Widget types: kpi, gauge (single numbers); bar, donut (categories); line, area, bar (monthly trends); table.
Answer strictly: {"ops":[...], "reply":"one friendly sentence on what you did"}."""


def _llm(target: Any, instruction: str, layout: dict[str, Any], existing: list[ConnectorSpec], brand: str | None) -> tuple[list[dict[str, Any]], str]:
    from app.crm_core import llm

    context = {
        "TEMPLATES": list(TEMPLATES),
        "standard_components": catalog.COMPONENTS,
        "THEMES": list(THEMES),
        "recipes": [f"{r.id}: {r.title} ({r.type}, {r.domain})" for r in RECIPES.values()],
        "current_layout": catalog.describe(layout),
        "connected_systems": [f"{s.name} ({s.domain}, {s.kind})" for s in existing],
        "tenant_brand_color": brand,
    }
    parsed = llm.chat_json(target, _LLM_PROMPT, f"{json.dumps(context)}\n\nRequest: {instruction}")
    ops = parsed.get("ops")
    return (ops if isinstance(ops, list) else []), str(parsed.get("reply") or "")


# ---------------------------------------------------------------- entry points


def plan(
    instruction: str,
    layout: dict[str, Any],
    existing: list[ConnectorSpec] | None = None,
    target: Any = None,
    brand_color: str | None = None,
) -> dict[str, Any]:
    existing = existing or []
    ops: list[dict[str, Any]] = []
    reply, engine = "", "rules"
    if target is not None:
        try:
            ops, reply = _llm(target, instruction, layout, existing, brand_color)
            ops = [o for o in ops if isinstance(o, dict)]
            engine = "ai"
        except Exception:  # noqa: BLE001 - fall back to the rule engine
            ops = []
    if not ops:
        ops, engine = _rules(instruction, layout, brand_color), "rules"
    extra_notes = [o["text"] for o in ops if o.get("op") == "_note"]
    ops = [o for o in ops if o.get("op") != "_note"]
    result = apply_ops(layout, ops, existing)
    result["notes"] = extra_notes + result["notes"]
    result.update(
        ops=ops,
        engine=engine,
        understood=bool(result["changes"]),
        reply=reply or (("Done: " + "; ".join(result["changes"]) + ".") if result["changes"] else
                        "I couldn't turn that into a change yet - try “add a billing pane with revenue trend” or “connect our network system”."),
        suggestions=suggest(result["layout"], existing + [_spec(d) for d in result["connectors"]]),
    )
    return result


def _spec(draft: dict[str, Any]) -> ConnectorSpec:
    return ConnectorSpec(key=draft["key"], name=draft["name"], domain=draft["domain"], kind=draft["kind"],
                         base_url=draft.get("base_url", ""), endpoints=draft.get("endpoints", {}))


def suggest(layout: dict[str, Any], specs: list[ConnectorSpec]) -> list[str]:
    """Next things worth asking for, given what the page has now."""
    out: list[str] = []
    domains = {p["domain"] for p in layout["panes"]}
    served = {d for s in specs if s.is_active for d in s.endpoints}
    missing_systems = []
    for p in layout["panes"]:
        for w in p["widgets"]:
            ds = DATASETS.get(w["dataset"] or "")
            if ds and not ds.internal and ds.key not in served:
                missing_systems.append(ds.domain)
    for d in dict.fromkeys(missing_systems):
        out.append(f"Connect our {connectors.domain_label(d).lower()} system")
    for d in ("sales", "service", "billing", "network", "inventory"):
        if d not in domains:
            out.append(f"Add a {d} pane")
    if "custom" not in domains:
        out.append("Put AI insights at the top")
    if layout["theme"]["preset"] != "daylight":
        out.append("Switch to a light theme")
    else:
        out.append("Go back to the dark theme")
    if layout["theme"]["density"] != "compact":
        out.append("Make it compact")
    out.append("Show tickets by priority as a bar chart")
    out.append("Make billing full width and move it to the top")
    return out[:6]
