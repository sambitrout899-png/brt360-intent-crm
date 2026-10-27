"""The explicit data contract between a triggering intent and its flows."""
import datetime as dt
import enum


def record_models():
    from app.crm_core.lifecycle_service import ENTITY_MODELS
    from app.models.catalog import ProductOffering
    return {"customer": ("party_id", ENTITY_MODELS["party"]),
            **{key: (f"{key}_id", model) for key, model in ENTITY_MODELS.items() if key != "party"},
            "product": ("product_offering_id", ProductOffering)}


def columns(model):
    return [c for c in model.__table__.c if c.name != "tenant_id" and not c.name.startswith("sys_")
            and not any(word in c.name for word in ("password", "secret", "token"))]


def snapshot(row):
    def plain(value):
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, (dt.datetime, dt.date)):
            return value.isoformat()
        return value
    return {c.name: plain(getattr(row, c.name)) for c in columns(type(row))}


def contract(db, tenant_id, inputs, outputs):
    # Keep originals intact; outputs take precedence only in the combined handoff.
    parameters = {**inputs, **outputs}
    records = {}
    for name, (key, model) in record_models().items():
        raw = parameters.get(key)
        row = None
        if raw not in (None, ""):
            query = db.query(model).filter(model.tenant_id == tenant_id)
            if str(raw).isdigit():
                row = query.filter(model.id == int(raw)).first()
            elif isinstance(raw, str):
                row = query.filter(model.brt_id == raw).first()
        elif name == "product" and parameters.get("offering_name"):
            # Do not silently choose among different product versions.
            rows = db.query(model).filter(model.tenant_id == tenant_id, model.name == parameters["offering_name"]).limit(2).all()
            row = rows[0] if len(rows) == 1 else None
        if row is not None:
            records[name] = snapshot(row)
    # Namespaced records cannot overwrite an intent's own named parameters.
    return {"parameters": parameters, "records": records}


def record_fields(known_keys):
    result = []
    for name, (key, model) in record_models().items():
        if key not in known_keys and not (name == "product" and "offering_name" in known_keys):
            continue
        label = "Customer / Prospect" if name == "customer" else name.title()
        for c in columns(model):
            result.append({"path": f"flow.inputs.records.{name}.{c.name}", "key": c.name,
                           "label": f"{label} → {c.name.replace('_', ' ').title()}",
                           "data_type": "number" if c.type.python_type in (int, float) else "text",
                           "source": "result", "values": [], "settable": False, "recent": []})
    return result
