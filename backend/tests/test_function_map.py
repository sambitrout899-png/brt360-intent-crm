"""The Functional Map: BRT's vanilla map stays untouched and always available;
a tenant's map is its engines as they run, and changing the picture
re-orchestrates them - shown first, applied on consent, recorded."""

from test_access_guards import _as, _user

MAP = "/api/admin/function-map"


def _intent(client, code, entities):
    out = client.post("/api/intents", json={"text": code, "structured": {"intent_type": code, "entities": entities}}).json()
    assert out["status"] == "executed", out
    return out


def _mine(client):
    return client.get(MAP).json()


def _draft(m):
    """The picture as the editor sends it back."""
    return {"nodes": [{k: n[k] for k in ("code", "label", "lane", "active", "x", "dy")} for n in m["nodes"]],
            "edges": [{k: e.get(k) for k in ("source", "target", "kind", "why")} for e in m["edges"] if e["kind"] == "next" or e["active"]]}


def _node(draft, code):
    return next(n for n in draft["nodes"] if n["code"] == code)


def test_the_vanilla_map_has_every_function_point_and_the_standard_hand_offs(client):
    from app.intent_engine.intent_registry import BUILTIN

    van = client.get(f"{MAP}/vanilla").json()
    assert {n["code"] for n in van["nodes"]} == set(BUILTIN) and van["vanilla"]
    links = {(e["source"], e["target"]) for e in van["edges"]}
    assert {("INT-15", "INT-46"), ("INT-46", "INT-17"), ("INT-37", "INT-38"), ("INT-38", "INT-18"), ("INT-74", "INT-84")} <= links
    sto = next(p for p in van["processes"] if p["key"] == "b2b_sto")
    assert sto["steps"][0] == "INT-15" and sto["steps"][-1] == "INT-28"
    x = {n["code"]: n["x"] for n in van["nodes"]}
    assert x["INT-15"] < x["INT-46"] < x["INT-17"]  # drawn left to right along the hand-offs


def test_a_new_tenant_map_is_the_vanilla_map(client):
    mine = _mine(client)
    assert not mine["customized"] and mine["differences"] == []
    van = client.get(f"{MAP}/vanilla").json()
    assert {(e["source"], e["target"]) for e in mine["edges"]} == {(e["source"], e["target"]) for e in van["edges"]}


def test_map_includes_searchable_phrases_and_examples(client):
    response = client.put("/api/admin/intents/INT-18", json={"phrases": ["check connection readiness"]})
    assert response.status_code == 200
    catalog = client.get("/api/admin/intents").json()["intents"]
    nodes = {node["code"]: node for node in _mine(client)["nodes"]}
    assert "check connection readiness" in nodes["INT-18"]["phrases"]
    for intent in catalog:
        assert nodes[intent["code"]]["phrases"] == intent["phrases"]
        assert nodes[intent["code"]]["examples"] == intent["examples"]


def test_the_tenant_map_is_read_live_from_the_engines_and_vanilla_never_changes(client):
    client.put("/api/admin/intents/INT-18", json={"is_active": False, "label": "Site survey"})
    node = next(n for n in _mine(client)["nodes"] if n["code"] == "INT-18")
    assert not node["active"] and node["label"] == "Site survey"
    assert {d["kind"] for d in _mine(client)["differences"]} >= {"off", "renamed"}
    vanilla = next(n for n in client.get(f"{MAP}/vanilla").json()["nodes"] if n["code"] == "INT-18")
    assert vanilla["active"] and vanilla["label"] == "Check feasibility"


def test_changing_the_picture_is_shown_first_then_re_orchestrates_the_intents(client):
    draft = _draft(_mine(client))
    _node(draft, "INT-03")["active"] = False  # taken off the map
    _node(draft, "INT-13")["label"] = "KYC check"  # renamed
    _node(draft, "INT-13")["lane"] = "sales"  # moved to another area
    draft["edges"] = [e for e in draft["edges"] if (e["source"], e["target"]) != ("INT-15", "INT-46")]
    draft["edges"].append({"source": "INT-15", "target": "INT-35", "kind": "next", "why": "Make them an account"})

    planned = client.post(f"{MAP}/plan", json=draft).json()
    kinds = sorted(c["kind"] for c in planned["changes"])
    assert kinds == ["link", "move", "off", "rename", "unlink"] and planned["problems"] == []
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-03")["active"]  # a plan changes nothing

    applied = client.post(f"{MAP}/apply", json=draft).json()
    assert len(applied["applied"]) == 5
    intents = {i["code"]: i for i in client.get("/api/admin/intents").json()["intents"]}
    assert not intents["INT-03"]["is_active"]
    assert intents["INT-13"]["label"] == "KYC check" and intents["INT-13"]["category"] == "sales"
    mine = _mine(client)
    assert mine["customized"] and mine["saved_by"]

    # the next steps offered after an action follow the map
    out = _intent(client, "INT-15", {"name": "Map Co", "segment": "B2B"})
    offered = [n["code"] for n in out["next_actions"]]
    assert "INT-35" in offered and "INT-46" not in offered


def test_an_auto_link_becomes_an_automation_and_removing_it_switches_it_off(client):
    from test_phase5_fulfillment import _launch_bundle

    _launch_bundle(client, name="Fiber 500 Bundle", price=500)
    draft = _draft(_mine(client))
    draft["edges"].append({"source": "INT-46", "target": "INT-17", "kind": "auto", "why": ""})
    planned = client.post(f"{MAP}/plan", json=draft).json()
    auto = next(c for c in planned["changes"] if c["kind"] == "automate")
    assert "approve" in auto["warning"]  # it decides on its own - said before consent
    client.post(f"{MAP}/apply", json=draft)
    link = next(e for e in _mine(client)["edges"] if e["kind"] == "auto")
    assert (link["source"], link["target"], link["active"], link["by_map"]) == ("INT-46", "INT-17", True, True)

    party = _intent(client, "INT-15", {"name": "Auto Lead Co", "segment": "B2B"})["result"]["party_id"]
    lead = _intent(client, "INT-46", {"party_id": party, "products": ["Fiber 500 Bundle"]})
    ran = [t for f in lead["result"]["flows_run"] for t in f["trace"] if t["type"] == "action"]
    assert [t["intent"] for t in ran] == ["INT-17"] and not ran[0].get("skipped"), ran
    assert client.get(f"/api/records/lead/{lead['result']['lead_id']}").json()["built_in_status"] == "qualified"

    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if e["kind"] != "auto"]
    assert [c["kind"] for c in client.post(f"{MAP}/apply", json=draft).json()["applied"]] == ["unautomate"]
    flows = {f["id"]: f for f in client.get("/api/admin/flows").json()}
    assert flows[link["flow_id"]]["is_active"] is False  # kept, switched off


def test_what_cannot_be_automated_is_said_and_not_done(client):
    draft = _draft(_mine(client))
    draft["edges"] += [{"source": "INT-01", "target": "INT-02", "kind": "auto"},  # a product launch isn't about one record
                       {"source": "INT-15", "target": "INT-01", "kind": "auto"}]  # nor is launching a product
    planned = client.post(f"{MAP}/plan", json=draft).json()
    assert len(planned["problems"]) == 2 and not any(c["kind"] == "automate" for c in planned["changes"])
    client.post(f"{MAP}/apply", json=draft)
    assert not any(e["kind"] == "auto" for e in _mine(client)["edges"])


def test_restoring_vanilla_undoes_the_tenants_changes_on_consent(client):
    draft = _draft(_mine(client))
    _node(draft, "INT-03")["active"] = False
    _node(draft, "INT-13")["label"] = "KYC check"
    draft["edges"] = [e for e in draft["edges"] if e["source"] != "INT-15"]
    draft["edges"].append({"source": "INT-46", "target": "INT-17", "kind": "auto"})
    client.post(f"{MAP}/apply", json=draft)
    assert len(_mine(client)["differences"]) >= 4

    restore = client.get(f"{MAP}/restore").json()
    planned = client.post(f"{MAP}/plan", json=_draft(restore)).json()
    assert {c["kind"] for c in planned["changes"]} == {"on", "rename", "link", "unautomate"}
    client.post(f"{MAP}/apply", json=_draft(restore))
    assert _mine(client)["differences"] == []
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-13")["label"] == "Verify customer identity"


def test_every_save_is_kept_and_only_administrators_see_the_map(client):
    from app.models.intent_log import IntentExecutionLog
    from app.orchestration import function_map

    draft = _draft(_mine(client))
    _node(draft, "INT-15")["x"] += 40
    client.post(f"{MAP}/apply", json=draft)
    _node(draft, "INT-15")["x"] += 40
    out = client.post(f"{MAP}/apply", json=draft).json()
    assert out["applied"] == []  # only the layout changed
    assert next(n for n in out["map"]["nodes"] if n["code"] == "INT-15")["x"] == _node(draft, "INT-15")["x"]
    with client.TestSessionLocal() as db:  # a new row per save - nothing overwritten
        assert db.query(IntentExecutionLog).filter(IntentExecutionLog.intent_type == function_map.KIND).count() == 2
    rep = _user(client, "map-rep")
    for path in ("", "/vanilla", "/restore"):
        assert client.get(f"{MAP}{path}", headers=_as(rep)).status_code == 403
    assert client.post(f"{MAP}/apply", json=draft, headers=_as(rep)).status_code == 403
