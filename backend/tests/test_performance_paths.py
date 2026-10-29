"""What the load test found (scripts/load_test.py): the per-session read cache
(app/db.py cached), BRT_ID numbers without holding the register's row lock,
and BIM loads too large for one request running in the background."""

from app.crm_core import bim_service, custom_field_service
from app.db import cached
from app.models.custom_fields import CustomFieldDefinition
from app.models.tenant import Tenant


def test_cached_reads_once_until_a_row_of_its_model_changes(client):
    db = client.TestSessionLocal()
    try:
        tenant_id = db.query(Tenant.id).first()[0]
        loads = []

        def load():
            loads.append(1)
            return len(custom_field_service.list_definitions(db, tenant_id, "lead"))

        assert cached(db, ("n",), CustomFieldDefinition, load) == cached(db, ("n",), CustomFieldDefinition, load)
        assert len(loads) == 1  # once per session

        db.add(Tenant(name="Unrelated", slug="unrelated-cache"))
        db.flush()
        cached(db, ("n",), CustomFieldDefinition, load)
        assert len(loads) == 1  # a change to another model keeps it

        db.add(CustomFieldDefinition(tenant_id=tenant_id, entity_type="lead", key="cache_probe", label="Probe", data_type="text"))
        db.flush()
        assert cached(db, ("n",), CustomFieldDefinition, load) == _count(db, tenant_id)
        assert len(loads) == 2  # a change to its own model reloads it - with the new row

        savepoint = db.begin_nested()
        savepoint.commit()
        cached(db, ("n",), CustomFieldDefinition, load)
        assert len(loads) == 2  # a savepoint (one per BIM row) doesn't end it
        db.commit()
        cached(db, ("n",), CustomFieldDefinition, load)
        assert len(loads) == 3  # the transaction's end does
    finally:
        db.close()


def _count(db, tenant_id):
    return db.query(CustomFieldDefinition).filter_by(tenant_id=tenant_id, entity_type="lead", is_active=True).count()


def test_a_licence_change_is_seen_after_its_flush(client):
    from app import licensing

    db = client.TestSessionLocal()
    try:
        tenant = db.query(Tenant).first()
        before = licensing.enabled(db, tenant.id)
        tenant.licensed_modules = ["training_management"]
        db.flush()
        assert licensing.enabled(db, tenant.id) == {"training_management"} != before
    finally:
        db.rollback()
        db.close()


def test_a_large_bim_load_runs_in_the_background(client, monkeypatch):
    import app.db

    monkeypatch.setattr(bim_service, "SYNC_ROWS", 3)
    monkeypatch.setattr(app.db, "SessionLocal", client.TestSessionLocal)
    ids = client.post("/api/bim/brt-ids", json={"entity": "prospect", "count": 5}).json()["brt_ids"]
    csv = "\n".join(["brt_id,name,email,segment,tax_id,city"] + [f"{b},Big {k},big{k}@example.invalid,B2B,,Pune" for k, b in enumerate(ids)])
    queued = client.post("/api/bim/load", json={"entity": "prospect", "content": csv, "file_name": "big.csv"}).json()
    assert queued["status"] == "queued" and queued["total"] == 5 and queued["inserted"] == 0  # answered before loading
    done = client.get(f"/api/bim/jobs/{queued['id']}").json()  # the test client ran the background task after answering
    assert (done["status"], done["inserted"], done["failed"]) == ("completed", 5, 0)

    small = client.post("/api/bim/load", json={"entity": "prospect", "content": "\n".join(csv.splitlines()[:3]), "dry_run": True}).json()
    assert small["dry_run"] and small["status"] != "queued"  # small loads and dry runs still answer with the result
