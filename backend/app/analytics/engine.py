"""The query engine: slice and dice any entity of the reporting model.

    spec = {"entity": "order",
            "dimensions": [{"field": "created_at", "grain": "month"}, {"field": "customer_profile.segment"}],
            "measures":   [{"field": "monthly_price", "agg": "sum", "calc": "running"}, {"agg": "count"}],
            "filters":    [{"field": "status", "op": "in", "value": ["active", "changed"]},
                           {"field": "created_at", "op": "last_n_days", "value": 365}],
            "sort": [{"key": "sum(monthly_price)", "dir": "desc"}], "top": {"n": 10}, "limit": 1000}

The rows a spec needs are read once (plain columns pushed down to SQL as far
as they go: the tenant, what the user may see, plain filters), then grouped,
aggregated and calculated here - computed fields, custom fields and the
calculations work the same on every database. Every result says how many
records it read; a read cut short at MAX_ROWS says so.

records() is the most granular level: the records behind any cell (drill
down), any columns, sorted and paged - and exported as CSV.
"""
from __future__ import annotations

import csv
import datetime as dt
import enum
import io
import math
import statistics
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.analytics import semantic
from app.analytics.semantic import AGGREGATIONS, GRAINS, Field, Model

MAX_ROWS = 200_000
MAX_GROUPS = 5_000
BLANK = "(blank)"
CALCS = ("pct_total", "running", "diff", "pct_change", "moving_avg", "rank")
_WEEKDAYS = ("1 Mon", "2 Tue", "3 Wed", "4 Thu", "5 Fri", "6 Sat", "7 Sun")


# ---------------------------------------------------------------- values


def plain(v: Any) -> Any:
    """Enums as their values (str-enums too: str(OrderStatus.active) is "OrderStatus.active"), everything else as is."""
    return v.value if isinstance(v, enum.Enum) else v


def _as_datetime(v: Any) -> dt.datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, dt.date):
        return dt.datetime.combine(v, dt.time())
    try:
        return dt.datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def grain_value(v: Any, grain: str) -> str | None:
    d = _as_datetime(v)
    if d is None:
        return None
    if grain == "year":
        return f"{d.year}"
    if grain == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if grain == "month":
        return f"{d.year}-{d.month:02d}"
    if grain == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if grain == "day":
        return d.date().isoformat()
    if grain == "weekday":
        return _WEEKDAYS[d.weekday()]
    if grain == "hour":
        return f"{d.hour:02d}:00"
    return d.date().isoformat()


def period_start(label: str, grain: str) -> dt.date:
    if grain == "year":
        return dt.date(int(label), 1, 1)
    if grain == "quarter":
        y, q = label.split("-Q")
        return dt.date(int(y), (int(q) - 1) * 3 + 1, 1)
    if grain == "month":
        y, m = label.split("-")
        return dt.date(int(y), int(m), 1)
    if grain == "week":
        y, w = label.split("-W")
        return dt.date.fromisocalendar(int(y), int(w), 1)
    return dt.date.fromisoformat(label)


def next_period(label: str, grain: str, steps: int = 1) -> str:
    d = period_start(label, grain)
    for _ in range(steps):
        if grain == "year":
            d = dt.date(d.year + 1, 1, 1)
        elif grain == "quarter":
            m = d.month + 3
            d = dt.date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
        elif grain == "month":
            d = dt.date(d.year + d.month // 12, d.month % 12 + 1, 1)
        elif grain == "week":
            d = d + dt.timedelta(days=7)
        else:
            d = d + dt.timedelta(days=1)
    return grain_value(d, grain) or ""


def _num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None if v is None or v == "" else float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def aggregate(values: list[Any], agg: str) -> float | None:
    if agg == "count":
        return float(len(values))
    if agg == "count_distinct":
        return float(len({v for v in values if v is not None}))
    nums = [n for n in (_num(v) for v in values) if n is not None]
    if not nums:
        return None if agg != "sum" else 0.0
    if agg == "sum":
        return round(sum(nums), 4)
    if agg == "avg":
        return round(sum(nums) / len(nums), 4)
    if agg == "min":
        return min(nums)
    if agg == "max":
        return max(nums)
    if agg == "median":
        return round(statistics.median(nums), 4)
    if agg == "p90":
        s = sorted(nums)
        return s[min(len(s) - 1, int(math.ceil(0.9 * len(s))) - 1)]
    if agg == "stddev":
        return round(statistics.pstdev(nums), 4) if len(nums) > 1 else 0.0
    raise ValueError(f"Unknown aggregation “{agg}”")


# ---------------------------------------------------------------- filters


def _bounds(op: str, value: Any) -> tuple[dt.datetime | None, dt.datetime | None]:
    """A relative date filter as [start, end)."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    today = dt.datetime.combine(now.date(), dt.time())
    if op == "last_n_days":
        return today - dt.timedelta(days=int(value or 30) - 1), None
    if op == "next_n_days":
        return today, today + dt.timedelta(days=int(value or 30) + 1)
    if op == "today":
        return today, today + dt.timedelta(days=1)
    if op in ("this_month", "last_month"):
        start = today.replace(day=1)
        if op == "last_month":
            end = start
            start = (start - dt.timedelta(days=1)).replace(day=1)
            return start, end
        return start, None
    if op in ("this_quarter",):
        return today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1), None
    if op in ("this_year", "last_year"):
        start = today.replace(month=1, day=1)
        return (start.replace(year=start.year - 1), start) if op == "last_year" else (start, None)
    raise ValueError(op)


RELATIVE = ("last_n_days", "next_n_days", "today", "this_month", "last_month", "this_quarter", "this_year", "last_year")
OPERATORS = ("eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "between", "contains", "not_contains", "starts",
             "is_blank", "not_blank", "is_true", "is_false", "before", "after", *RELATIVE)


def _cmp(a: Any, b: Any) -> tuple[Any, Any]:
    """Two values made comparable: numbers as numbers, dates as dates, else text."""
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None and not isinstance(a, (dt.date, dt.datetime)):
        return na, nb
    da, db_ = _as_datetime(a), _as_datetime(b)
    if da is not None and db_ is not None:
        return da, db_
    return str(a).lower(), str(b).lower()


def matches(value: Any, op: str, target: Any) -> bool:
    value = plain(value)
    if op == "is_blank":
        return value is None or value == ""
    if op == "not_blank":
        return not (value is None or value == "")
    if op == "is_true":
        return bool(value) and str(value).lower() not in ("false", "0", "no")
    if op == "is_false":
        return not value or str(value).lower() in ("false", "0", "no")
    if op in RELATIVE:
        d = _as_datetime(value)
        if d is None:
            return False
        start, end = _bounds(op, target)
        return (start is None or d >= start) and (end is None or d < end)
    if value is None:
        return op in ("neq", "not_in", "not_contains")
    if op in ("in", "not_in"):
        items = target if isinstance(target, list) else [t.strip() for t in str(target).split(",")]
        hit = any(_eq(value, t) for t in items)
        return hit if op == "in" else not hit
    if op == "eq":
        return _eq(value, target)
    if op == "neq":
        return not _eq(value, target)
    if op == "contains":
        return str(target).lower() in str(value).lower()
    if op == "not_contains":
        return str(target).lower() not in str(value).lower()
    if op == "starts":
        return str(value).lower().startswith(str(target).lower())
    if op == "between":
        lo, hi = (target or [None, None])[:2]
        return (lo in (None, "") or _cmp(value, lo)[0] >= _cmp(value, lo)[1]) and (hi in (None, "") or _cmp(value, hi)[0] <= _cmp(value, hi)[1])
    a, b = _cmp(value, target)
    try:
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b, "before": a < b, "after": a > b}[op]
    except TypeError:
        return False
    except KeyError:
        raise ValueError(f"Unknown filter operator “{op}”") from None


def _eq(value: Any, target: Any) -> bool:
    if isinstance(value, bool):
        return value == (str(target).lower() in ("true", "1", "yes"))
    a, b = _cmp(value, target)
    return a == b


def _sql_filter(f: Field, op: str, target: Any) -> Any | None:
    """The same filter in SQL, when it is a plain column a database compares the same way (never enums or computed fields)."""
    col = f.column
    if col is None or f.values is not None:
        return None
    if op in RELATIVE and f.kind == "date":
        start, end = _bounds(op, target)
        parts = [col >= start] if start else []
        if end:
            parts.append(col < end)
        return and_(*parts) if parts else None
    if f.kind == "number":
        n = _num(target) if op not in ("between", "in", "not_in") else None
        if op in ("gt", "gte", "lt", "lte") and n is not None:
            return {"gt": col > n, "gte": col >= n, "lt": col < n, "lte": col <= n}[op]
        if op == "between" and isinstance(target, list):
            lo, hi = (_num(t) for t in (target + [None, None])[:2])
            return and_(*([col >= lo] if lo is not None else []), *([col <= hi] if hi is not None else [])) if lo is not None or hi is not None else None
        return None
    if f.kind == "date" and op in ("before", "after", "between"):
        if op == "between" and isinstance(target, list):
            lo, hi = (_as_datetime(t) for t in (target + [None, None])[:2])
            if hi is not None and len(str(target[1])) <= 10:
                hi = hi + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
            return and_(*([col >= lo] if lo else []), *([col <= hi] if hi else [])) if lo or hi else None
        d = _as_datetime(target)
        return None if d is None else (col < d if op == "before" else col > d)
    if f.kind == "dimension" and op in ("is_blank", "not_blank"):
        return or_(col.is_(None), col == "") if op == "is_blank" else and_(col.is_not(None), col != "")
    return None


# ---------------------------------------------------------------- reading


def _needs(m: Model, keys: Iterable[str]) -> list[str]:
    """Every field these keys need, plain columns first."""
    seen: dict[str, None] = {}

    def visit(k: str) -> None:
        if k in seen:
            return
        f = m.field(k)
        for dep in f.needs:
            visit(dep)
        seen[k] = None

    for k in keys:
        visit(k)
    return list(seen)


def _scope_column(m: Model) -> Any | None:
    _etype, key = m.entity.scope
    if not key:
        return None
    alias, _, name = key.rpartition(".")
    return getattr(m.aliases[alias], name)


def _tenant_column(m: Model) -> Any:
    for key in ["", *(j.key for j in m.entity.joins)]:
        alias = m.aliases[key]
        if hasattr(alias, "tenant_id"):
            return alias.tenant_id
    raise ValueError(f"{m.entity.label} has no tenant column")


_NO_VALUE = ("is_blank", "not_blank", "is_true", "is_false", "today", "this_month", "last_month", "this_quarter", "this_year", "last_year")


def _complete(f: dict[str, Any]) -> bool:
    """A filter still being set up (no value chosen yet) filters nothing, rather than everything."""
    op, v = f.get("op", "eq"), f.get("value")
    if op in _NO_VALUE:
        return True
    if op == "between":
        return isinstance(v, list) and any(x not in (None, "") for x in v)
    if isinstance(v, list):
        return bool(v)
    return v not in (None, "")


def read(db: Session, tenant_id: int, access: Any, m: Model, keys: Iterable[str], filters: list[dict[str, Any]],
         max_rows: int = MAX_ROWS, order_desc: bool = True) -> tuple[list[dict[str, Any]], bool]:
    """The records (as {field key: value}) the user may see, filtered."""
    from app.crm_core import access_service

    filters = [f for f in filters or [] if f.get("field") and _complete(f)]
    for f in filters:
        if f.get("op", "eq") not in OPERATORS:
            raise ValueError(f"Unknown filter operator “{f.get('op')}”")
    from app.analytics import services

    keys = _needs(m, services.required(m.entity.key, [*keys, *(f["field"] for f in filters)]))
    sql_keys = [k for k in keys if m.fields[k].column is not None]
    # an empty select still needs a column: the record id
    cols = [m.fields[k].column.label(f"c{i}") for i, k in enumerate(sql_keys)] or [m.aliases[""].id.label("c0")]
    stmt = select(*cols).select_from(m.aliases[""])
    for j in m.entity.joins:
        stmt = stmt.outerjoin(m.aliases[j.key], j.on(m.aliases))
    stmt = stmt.where(_tenant_column(m) == tenant_id)
    if access is not None and not access.unrestricted:
        etype, _key = m.entity.scope
        scope = _scope_column(m)
        if not etype or scope is None:
            return [], False  # nothing to scope it by - only an unrestricted view sees it
        ids = access_service.visible_ids(db, access, etype)
        if ids is not None:
            stmt = stmt.where(scope.in_(ids or {-1}))
    python_filters = []
    for f in filters:
        clause = _sql_filter(m.fields[f["field"]], f.get("op", "eq"), f.get("value"))
        if clause is not None:
            stmt = stmt.where(clause)
        else:
            python_filters.append(f)
    stmt = stmt.order_by(m.aliases[""].id.desc() if order_desc else m.aliases[""].id).limit(max_rows + 1)
    raw = db.execute(stmt).all()
    truncated = len(raw) > max_rows
    rows = [{k: plain(r[i]) for i, k in enumerate(sql_keys)} for r in raw[:max_rows]]
    # a completed order's service lives on as an asset: its rows take the service's state (analytics/services.py)
    services.apply(db, tenant_id, m.entity.key, rows)
    _load_custom(db, tenant_id, m, [k for k in keys if m.fields[k].custom], rows)
    for k in keys:
        f = m.fields[k]
        if f.fn is not None:
            for r in rows:
                try:
                    r[k] = f.fn(r)
                except Exception:  # noqa: BLE001 - a computed value that can't be computed is blank
                    r[k] = None
    for f in python_filters:
        op, target = f.get("op", "eq"), f.get("value")
        rows = [r for r in rows if matches(r.get(f["field"]), op, target)]
    return rows, truncated


def _load_custom(db: Session, tenant_id: int, m: Model, keys: list[str], rows: list[dict[str, Any]]) -> None:
    if not keys or not rows:
        return
    from app.models.custom_fields import CustomFieldValue

    by_field: dict[int, list[str]] = defaultdict(list)
    for k in keys:
        by_field[m.fields[k].custom[1]].append(k)
    id_keys = {m.fields[k].custom[0] for k in keys}
    ids = {r.get(ik) for r in rows for ik in id_keys} - {None}
    values: dict[tuple[int, int], str | None] = {}
    id_list = list(ids)
    for i in range(0, len(id_list), 900):
        for v in db.query(CustomFieldValue).filter(CustomFieldValue.tenant_id == tenant_id, CustomFieldValue.field_id.in_(list(by_field)),
                                                   CustomFieldValue.entity_id.in_(id_list[i:i + 900])).all():
            values[(v.field_id, v.entity_id)] = v.value
    for k in keys:
        id_key, field_id, data_type = m.fields[k].custom
        for r in rows:
            raw = values.get((field_id, r.get(id_key)))
            r[k] = _num(raw) if m.fields[k].kind == "number" else (str(raw).lower() in ("true", "1", "yes")) if m.fields[k].kind == "bool" and raw is not None else raw


# ---------------------------------------------------------------- the pivot


def dim_key(d: dict[str, Any]) -> str:
    return f"{d['field']}:{d['grain']}" if d.get("grain") else f"{d['field']}:bins" if d.get("bins") else d["field"]


def measure_key(ms: dict[str, Any]) -> str:
    base = "count" if ms.get("agg", "count") == "count" and not ms.get("field") else f"{ms.get('agg', 'sum')}({ms.get('field')})"
    return f"{base}~{ms['calc']}" if ms.get("calc") else base


def validate(m: Model, spec: dict[str, Any]) -> None:
    for d in spec.get("dimensions") or []:
        f = m.field(d["field"])
        if d.get("grain") and d["grain"] not in GRAINS:
            raise ValueError(f"Unknown date grain “{d['grain']}”")
        if d.get("grain") and f.kind != "date":
            raise ValueError(f"“{f.label}” is not a date - it can't be sliced by {d['grain']}")
    for ms in spec.get("measures") or []:
        agg = ms.get("agg", "count")
        if agg not in AGGREGATIONS:
            raise ValueError(f"Unknown aggregation “{agg}”")
        if ms.get("field"):
            f = m.field(ms["field"])
            if agg not in ("count", "count_distinct") and f.kind not in ("number", "bool"):  # a yes/no averages to a rate
                raise ValueError(f"“{f.label}” is not a number - it can only be counted")
        elif agg != "count":
            raise ValueError(f"{agg} needs a field")
        if ms.get("calc") and ms["calc"] not in CALCS:
            raise ValueError(f"Unknown calculation “{ms['calc']}”")
    for f in spec.get("filters") or []:
        m.field(f["field"])


def _bin_edges(values: list[float], bins: int) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [lo, hi]
    step = (hi - lo) / bins
    mag = 10 ** math.floor(math.log10(step))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= step)  # round steps: 0-50, 50-100 ...
    start = math.floor(lo / step) * step
    edges = [start]
    while edges[-1] < hi:
        edges.append(round(edges[-1] + step, 10))
    return edges


def _fmt_edge(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}"


def _bin_label(v: Any, edges: list[float]) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if len(edges) < 2 or edges[0] == edges[-1]:
        return _fmt_edge(edges[0]) if edges else str(n)
    for lo, hi in zip(edges, edges[1:]):
        if n < hi or hi == edges[-1]:
            return f"{_fmt_edge(lo)} – {_fmt_edge(hi)}"
    return f"{_fmt_edge(edges[-2])} – {_fmt_edge(edges[-1])}"


def bucket(row: dict[str, Any], d: dict[str, Any], edges: dict[str, list[float]]) -> Any:
    v = row.get(d["field"])
    if d.get("grain"):
        out = grain_value(v, d["grain"])
    elif d.get("bins"):
        out = _bin_label(v, edges[dim_key(d)])
    elif isinstance(v, (dt.datetime, dt.date)):
        out = grain_value(v, "day")
    elif isinstance(v, bool):
        out = "Yes" if v else "No"
    else:
        out = v
    return BLANK if out is None or out == "" else out


def _sort_key(v: Any) -> tuple:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, v, "")
    if v == BLANK:
        return (2, 0, "")
    s = str(v)
    head = s.split(" – ")[0].replace(",", "")
    try:
        return (0, float(head), s)
    except ValueError:
        return (1, 0, s.lower())


def query(db: Session, tenant_id: int, access: Any, spec: dict[str, Any]) -> dict[str, Any]:
    m = semantic.model(db, tenant_id, spec.get("entity", ""))
    dims = [d for d in spec.get("dimensions") or [] if d.get("field")]
    measures = spec.get("measures") or [{"agg": "count"}]
    validate(m, {**spec, "measures": measures, "dimensions": dims})
    keys = [d["field"] for d in dims] + [ms["field"] for ms in measures if ms.get("field")]
    rows, truncated = read(db, tenant_id, access, m, keys, spec.get("filters") or [])

    edges: dict[str, list[float]] = {}
    for d in dims:
        if d.get("bins"):
            vals = [n for n in (_num(r.get(d["field"])) for r in rows) if n is not None]
            edges[dim_key(d)] = _bin_edges(vals, max(2, min(50, int(d["bins"])))) if vals else [0.0, 0.0]

    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(bucket(r, d, edges) for d in dims)].append(r)

    def measure_values(members: list[dict[str, Any]]) -> dict[str, float | None]:
        out = {}
        for ms in measures:
            agg = ms.get("agg", "count")
            vals = members if agg == "count" and not ms.get("field") else [r.get(ms["field"]) for r in members]
            if agg == "count" and ms.get("field"):
                vals = [v for v in vals if v is not None and v != ""]
            out[measure_key(ms)] = aggregate(vals, agg)
        return out

    first_m = measure_key(measures[0])
    # top N of the first dimension (by the first measure); the rest folded into "Other"
    top = spec.get("top") or {}
    if dims and top.get("n"):
        by_first: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for g, members in groups.items():
            by_first[g[0]].extend(members)
        ranked = sorted(by_first, key=lambda v: measure_values(by_first[v])[first_m] or 0, reverse=top.get("dir", "desc") != "asc")
        keep = set(ranked[: int(top["n"])])
        if len(ranked) > len(keep):
            folded: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
            for g, members in groups.items():
                if g[0] in keep:
                    folded[g].extend(members)
                elif top.get("other", True):
                    folded[("Other", *g[1:])].extend(members)
            groups = folded

    out_rows = []
    for g, members in groups.items():
        row = {dim_key(d): g[i] for i, d in enumerate(dims)}
        row.update(measure_values(members))
        row["__count"] = len(members)
        out_rows.append(row)

    dkeys = [dim_key(d) for d in dims]
    sort = spec.get("sort") or []
    if sort:
        for s in reversed(sort):
            key = s.get("key")
            if key not in (*dkeys, *(measure_key(x) for x in measures)):
                continue
            rev = s.get("dir", "asc") == "desc"
            out_rows.sort(key=lambda r, k=key: (r.get(k) is None, _sort_key(r.get(k)) if k in dkeys else (r.get(k) or 0)), reverse=rev)
    elif dims and (dims[0].get("grain") or dims[0].get("bins")):
        out_rows.sort(key=lambda r: tuple(_sort_key(r[k]) for k in dkeys))
    elif dims:
        out_rows.sort(key=lambda r: (-(r.get(first_m) or 0), *(_sort_key(r[k]) for k in dkeys)))

    _calculations(out_rows, measures, dkeys)
    limit = max(1, min(int(spec.get("limit") or MAX_GROUPS), MAX_GROUPS))
    totals = measure_values(rows)
    columns = [{"key": dim_key(d), "label": _dim_label(m, d), "role": "dimension", "field": d["field"], "grain": d.get("grain"),
                "bins": d.get("bins"), "edges": edges.get(dim_key(d)), "kind": m.fields[d["field"]].kind} for d in dims]
    columns += [{"key": measure_key(ms), "label": ms.get("label") or _measure_label(m, ms), "role": "measure", "field": ms.get("field"),
                 "agg": ms.get("agg", "count"), "calc": ms.get("calc"),
                 "fmt": "pct" if ms.get("calc") in ("pct_total", "pct_change") else
                 None if ms.get("agg") in ("count", "count_distinct") or not ms.get("field") else m.fields[ms["field"]].fmt} for ms in measures]
    return {"entity": m.entity.key, "entity_label": m.entity.label, "columns": columns, "rows": out_rows[:limit],
            "groups": len(out_rows), "scanned": len(rows), "truncated": truncated, "totals": totals,
            "record_type": m.entity.record_type}


def _dim_label(m: Model, d: dict[str, Any]) -> str:
    base = d.get("label") or m.fields[d["field"]].label
    return f"{base} ({d['grain']})" if d.get("grain") and not d.get("label") else base


_AGG_LABEL = {"count": "Count", "count_distinct": "Distinct", "sum": "Total", "avg": "Average", "min": "Min", "max": "Max",
              "median": "Median", "p90": "90th pct", "stddev": "Std dev"}
_CALC_LABEL = {"pct_total": "% of total", "running": "running total", "diff": "change", "pct_change": "% change",
               "moving_avg": "3-period avg", "rank": "rank"}


def _measure_label(m: Model, ms: dict[str, Any]) -> str:
    agg = ms.get("agg", "count")
    base = f"Number of {m.entity.label.lower()}" if agg == "count" and not ms.get("field") else \
        f"{_AGG_LABEL[agg]} {m.fields[ms['field']].label.lower()}" if ms.get("field") else _AGG_LABEL[agg]
    return f"{base} - {_CALC_LABEL[ms['calc']]}" if ms.get("calc") else base


def _calculations(rows: list[dict[str, Any]], measures: list[dict[str, Any]], dkeys: list[str]) -> None:
    """Table calculations, along the rows' order - within each value of the first dimension when there are two."""
    for ms in measures:
        calc = ms.get("calc")
        if not calc:
            continue
        key = measure_key(ms)
        partitions: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            partitions[r[dkeys[1]] if len(dkeys) > 1 and calc != "pct_total" else None].append(r)
        for part in partitions.values():
            vals = [r.get(key) for r in part]
            if calc == "pct_total":
                total = sum(v or 0 for v in vals)
                for r, v in zip(part, vals):
                    r[key] = round(100 * (v or 0) / total, 2) if total else None
            elif calc == "running":
                acc = 0.0
                for r, v in zip(part, vals):
                    acc += v or 0
                    r[key] = round(acc, 4)
            elif calc in ("diff", "pct_change"):
                prev = None
                for r, v in zip(part, vals):
                    if prev is None or v is None:
                        r[key] = None
                    else:
                        r[key] = round(v - prev, 4) if calc == "diff" else (round(100 * (v - prev) / abs(prev), 2) if prev else None)
                    prev = v
            elif calc == "moving_avg":
                for i, r in enumerate(part):
                    window = [x for x in vals[max(0, i - 2): i + 1] if x is not None]
                    r[key] = round(sum(window) / len(window), 4) if window else None
            elif calc == "rank":
                order = sorted(range(len(vals)), key=lambda i: -(vals[i] or 0))
                for rank, i in enumerate(order, 1):
                    part[i][key] = rank


# ---------------------------------------------------------------- the most granular level


def default_columns(m: Model) -> list[str]:
    plain_fields = [k for k, f in m.fields.items() if f.column is not None and "." not in k and not k.startswith("sys_")]
    first = ["id"] + [k for k in plain_fields if k != "id"][:9]
    extra = [k for k in ("customer.name", "customer_profile.segment") if k in m.fields]
    computed = [k for k, f in m.fields.items() if f.fn is not None][:3]
    return list(dict.fromkeys(first + extra + computed))


def records(db: Session, tenant_id: int, access: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """The records behind a report or one of its cells: every column asked for, sorted and paged."""
    m = semantic.model(db, tenant_id, spec.get("entity", ""))
    columns = [c for c in spec.get("columns") or default_columns(m) if c in m.fields]
    drill = [d for d in spec.get("drill") or [] if d.get("field")]
    filters = list(spec.get("filters") or [])
    for d in drill:
        m.field(d["field"])
    rows, truncated = read(db, tenant_id, access, m, [*columns, *(d["field"] for d in drill)], filters)
    for d in drill:
        rows = [r for r in rows if str(bucket(r, d, {dim_key(d): d.get("edges") or []})) == str(d.get("value"))]
    sort = spec.get("sort") or {}
    if sort.get("key") in m.fields:
        rows.sort(key=lambda r: _record_sort_key(r.get(sort["key"])), reverse=sort.get("dir") == "desc")
    offset = max(0, int(spec.get("offset") or 0))
    limit = max(1, min(int(spec.get("limit") or 100), 2000 if spec.get("export") else 500))
    page = rows[offset: offset + limit] if not spec.get("export") else rows[:MAX_ROWS]
    return {"entity": m.entity.key, "entity_label": m.entity.label, "record_type": m.entity.record_type,
            "columns": [{"key": c, "label": m.fields[c].label, "kind": m.fields[c].kind, "fmt": m.fields[c].fmt} for c in columns],
            "rows": [{c: _json(r.get(c)) for c in columns} | {"__id": r.get("id")} for r in page],
            "total": len(rows), "offset": offset, "truncated": truncated}


def _record_sort_key(v: Any) -> tuple:
    if v is None or v == "":
        return (1, (2, 0, ""))
    if isinstance(v, (dt.datetime, dt.date)):
        return (0, (1, 0, v.isoformat()))
    return (0, _sort_key(v))


def _json(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return plain(v)


def to_csv(columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c["label"] for c in columns])
    for r in rows:
        w.writerow(["" if r.get(c["key"]) is None else r.get(c["key"]) for c in columns])
    return buf.getvalue()


# ---------------------------------------------------------------- time series (for the forecasts)


def series(db: Session, tenant_id: int, access: Any, entity: str, date_field: str | None, measure: dict[str, Any],
           grain: str = "month", filters: list[dict[str, Any]] | None = None, fill_to_now: bool = True) -> list[tuple[str, float]]:
    """One measure per period, oldest first, with the empty periods filled in (0 for counts and sums, else carried)."""
    m = semantic.model(db, tenant_id, entity)
    date_field = date_field or m.entity.date
    result = query(db, tenant_id, access, {"entity": entity, "dimensions": [{"field": date_field, "grain": grain}],
                                           "measures": [measure], "filters": filters or [], "limit": MAX_GROUPS})
    dk, mk = dim_key({"field": date_field, "grain": grain}), measure_key(measure)
    points = {r[dk]: r[mk] for r in result["rows"] if r[dk] != BLANK}
    if not points:
        return []
    labels = sorted(points, key=lambda p: period_start(p, grain))
    last = grain_value(dt.datetime.now(dt.timezone.utc), grain) if fill_to_now else labels[-1]
    if fill_to_now and period_start(last, grain) < period_start(labels[-1], grain):
        last = labels[-1]
    zero_fill = measure.get("agg", "count") in ("count", "count_distinct", "sum")
    out, label, prev = [], labels[0], 0.0
    guard = 0
    while guard < 5000:
        v = points.get(label)
        v = (0.0 if zero_fill else prev) if v is None else v
        out.append((label, float(v or 0.0)))
        prev = v or 0.0
        if label == last:
            break
        label = next_period(label, grain)
        guard += 1
    return out
