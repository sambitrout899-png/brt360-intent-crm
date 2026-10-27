"""What a report says, in words.

narrate()  the facts, computed: the total, who leads and by how much, how
           concentrated it is, the trend and its unusual periods.
explain()  the tenant's AI model as an analyst: given those facts and the
           report's top aggregated rows (never record-level data), a
           headline, insights and recommended actions; the
           computed facts alone when no model is set up or it can't answer.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.analytics import ml


def _fmt(v: float | None, fmt: str | None) -> str:
    if v is None:
        return "-"
    if fmt == "pct":
        return f"{v:,.1f}%"
    if fmt == "money":
        return f"₹{v:,.0f}" if abs(v) >= 100 else f"₹{v:,.2f}"
    if fmt == "days":
        return f"{v:,.1f} days"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def narrate(result: dict[str, Any]) -> list[dict[str, str]]:
    cols = result.get("columns") or []
    dims = [c for c in cols if c["role"] == "dimension"]
    measures = [c for c in cols if c["role"] == "measure"]
    rows = result.get("rows") or []
    if not measures:
        return []
    m = measures[0]
    out: list[dict[str, str]] = []
    total = (result.get("totals") or {}).get(m["key"])
    additive = m.get("agg") in ("count", "sum", None) and not m.get("calc")
    out.append({"tone": "info", "text": f"{m['label']}: {_fmt(total, m.get('fmt'))} across {result.get('scanned', 0):,} records"
                + (f" in {result.get('groups', len(rows)):,} groups." if dims else ".")})
    if result.get("truncated"):
        out.append({"tone": "warn", "text": "Only the newest records were read - add a filter (e.g. a date range) for the full picture."})
    if not dims or not rows:
        return out
    d = dims[0]
    values = [(r[d["key"]], r.get(m["key"])) for r in rows if r.get(m["key"]) is not None]
    if not values:
        return out
    if d.get("grain") and d["grain"] not in ("weekday", "hour") and len(dims) == 1 and len(values) >= 3:
        labels, ys = [str(v[0]) for v in values], [float(v[1]) for v in values]
        first, last = ys[0], ys[-1]
        if first:
            change = 100 * (last - first) / abs(first)
            out.append({"tone": "good" if change > 0 else "bad" if change < 0 else "info",
                        "text": f"From {labels[0]} to {labels[-1]} it went {'up' if change >= 0 else 'down'} {abs(change):.0f}% ({_fmt(first, m.get('fmt'))} → {_fmt(last, m.get('fmt'))})."})
        peak = max(range(len(ys)), key=lambda i: ys[i])
        low = min(range(len(ys)), key=lambda i: ys[i])
        out.append({"tone": "info", "text": f"Highest in {labels[peak]} ({_fmt(ys[peak], m.get('fmt'))}), lowest in {labels[low]} ({_fmt(ys[low], m.get('fmt'))})."})
        slope, r2 = ml.linear_trend(ys)
        if r2 >= 0.5 and abs(slope) > 0:
            out.append({"tone": "good" if slope > 0 else "bad", "text": f"A steady {'rise' if slope > 0 else 'fall'} of about {_fmt(abs(slope), m.get('fmt'))} per {d['grain']} (fit R² {r2:.2f})."})
        for a in ml.anomalies(labels, ys)[:3]:
            out.append({"tone": "warn", "text": f"Unusual {a['kind']} in {a['period']}: {_fmt(a['value'], m.get('fmt'))} against a typical {_fmt(a['expected'], m.get('fmt'))}."})
        return out
    if additive and total:
        ranked = sorted(values, key=lambda v: -(v[1] or 0))
        lead_label, lead_value = ranked[0]
        share = 100 * lead_value / total if total else 0
        out.append({"tone": "info", "text": f"“{lead_label}” leads with {_fmt(lead_value, m.get('fmt'))} ({share:.0f}% of the total)."})
        if len(ranked) >= 5:
            top3 = 100 * sum(v for _, v in ranked[:3]) / total
            acc, n80 = 0.0, 0
            for _, v in ranked:
                acc += v
                n80 += 1
                if acc >= 0.8 * total:
                    break
            out.append({"tone": "warn" if top3 > 70 else "info",
                        "text": f"The top 3 make {top3:.0f}% of it; {n80} of {len(ranked)} groups make 80%" + (" - highly concentrated." if top3 > 70 else ".")})
    elif len(values) >= 2:
        ranked = sorted(values, key=lambda v: -(v[1] or 0))
        out.append({"tone": "info", "text": f"Highest: “{ranked[0][0]}” ({_fmt(ranked[0][1], m.get('fmt'))}); lowest: “{ranked[-1][0]}” ({_fmt(ranked[-1][1], m.get('fmt'))})."})
    return out


_PROMPT = (
    "You are a senior telecom business analyst. You are given computed facts about a report from the operator's CRM "
    "(revenue, churn, sales, service assurance, billing, usage). Explain what matters to a manager and what to do about it. "
    "Use only the facts given - never invent numbers. Answer with JSON only: "
    '{"headline": "<one sentence>", "insights": ["<2-5 short insights>"], "actions": ["<2-4 concrete recommended actions>"], '
    '"risks": ["<0-3 risks or caveats>"]}'
)


def explain(db: Session, tenant_id: int, title: str, facts: list[str], table: list[dict[str, Any]] | None = None,
            use_ai: bool = True) -> dict[str, Any]:
    from app.crm_core import llm

    facts = [f for f in facts if f][:30]
    rows = (table or [])[:25]
    target = llm.resolve(db, tenant_id) if use_ai else None
    if target is not None:
        try:
            with llm.budget(45):
                answer = llm.chat_json(target, _PROMPT, f"Report: {title}\nFacts:\n- " + "\n- ".join(facts)
                                       + (f"\nTop rows: {rows}" if rows else ""), temperature=0.2, max_tokens=700)
            return {"headline": str(answer.get("headline") or ""), "insights": [str(x) for x in answer.get("insights") or []][:6],
                    "actions": [str(x) for x in answer.get("actions") or []][:5], "risks": [str(x) for x in answer.get("risks") or []][:4],
                    "by": target.label}
        except llm.LlmUnavailable as exc:
            note = f"AI model not used ({exc})"
    else:
        note = "No AI model is set up (Admin → AI) - these are the computed facts"
    return {"headline": facts[0] if facts else title, "insights": facts[1:6], "actions": _rule_actions(" ".join(facts).lower()),
            "risks": [], "by": "rules", "note": note}


def _rule_actions(text: str) -> list[str]:
    out = []
    if "churn" in text:
        out.append("Open Predictions → Churn risk and call the high-risk customers with a retention offer before their contracts end.")
    if "overdue" in text or "collect" in text or "dso" in text:
        out.append("Prioritise collections on the 60+ day buckets; offer payment arrangements to long-standing customers.")
    if "ticket" in text or "mttr" in text or "sla" in text:
        out.append("Review the categories behind repeat faults and staff the queue for the peak periods.")
    if "concentrated" in text:
        out.append("Reduce concentration risk: grow the long tail with targeted bundles.")
    if "down" in text or "fall" in text or "drop" in text:
        out.append("Drill into the falling periods (click the bars) to see which records drove the drop.")
    if "up" in text or "rise" in text:
        out.append("Use Predictions → Forecast to plan capacity and targets for the next periods.")
    return out[:4] or ["Drill into the largest groups (click a bar) to see the records behind them."]
