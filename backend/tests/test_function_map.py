"""The Functional Map: BRT's vanilla map stays untouched and always available;
a tenant's map is its engines as they run - read-only - and a tenant changes
it by activating a version, which re-orchestrates the engines - shown first,
applied on consent, recorded."""

from test_access_guards import _as, _user

MAP = "/api/admin/function-map"


def _plan(client, draft):
    """A new draft version holding this picture, and what activating it would change."""
    made = client.post(f"{MAP}/versions", json={"source": "active"})
    assert made.status_code == 200, made.text
    vid = made.json()["version"]["id"]
    saved = client.put(f"{MAP}/versions/{vid}", json=draft)
    assert saved.status_code == 200, saved.text
    return client.post(f"{MAP}/versions/{vid}/plan").json(), vid


def _apply(client, draft):
    """Saves the picture as a version and activates it."""
    vid = _plan(client, draft)[1]
    out = client.post(f"{MAP}/versions/{vid}/activate")
    assert out.status_code == 200, out.text
    return out.json()


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

    planned, vid = _plan(client, draft)
    kinds = sorted(c["kind"] for c in planned["changes"])
    assert kinds == ["link", "move", "off", "rename", "unlink"] and planned["problems"] == []
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-03")["active"]  # a saved draft and a plan change nothing

    applied = client.post(f"{MAP}/versions/{vid}/activate").json()
    assert len(applied["applied"]) == 5 and applied["active_id"] == vid
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
    planned, vid = _plan(client, draft)
    auto = next(c for c in planned["changes"] if c["kind"] == "automate")
    assert "approve" in auto["warning"]  # it decides on its own - said before consent
    client.post(f"{MAP}/versions/{vid}/activate")
    link = next(e for e in _mine(client)["edges"] if e["kind"] == "auto")
    assert (link["source"], link["target"], link["active"], link["by_map"]) == ("INT-46", "INT-17", True, True)

    party = _intent(client, "INT-15", {"name": "Auto Lead Co", "segment": "B2B"})["result"]["party_id"]
    lead = _intent(client, "INT-46", {"party_id": party, "products": ["Fiber 500 Bundle"]})
    ran = [t for f in lead["result"]["flows_run"] for t in f["trace"] if t["type"] == "action"]
    assert [t["intent"] for t in ran] == ["INT-17"] and not ran[0].get("skipped"), ran
    assert client.get(f"/api/records/lead/{lead['result']['lead_id']}").json()["built_in_status"] == "qualified"

    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if e["kind"] != "auto"]
    assert [c["kind"] for c in _apply(client, draft)["applied"]] == ["unautomate"]
    flows = {f["id"]: f for f in client.get("/api/admin/flows").json()}
    assert flows[link["flow_id"]]["is_active"] is False  # kept, switched off


def test_what_cannot_be_automated_is_said_and_not_done(client):
    draft = _draft(_mine(client))
    draft["edges"] += [{"source": "INT-01", "target": "INT-02", "kind": "auto"},  # a product launch isn't about one record
                       {"source": "INT-15", "target": "INT-01", "kind": "auto"}]  # nor is launching a product
    planned, vid = _plan(client, draft)
    assert len(planned["problems"]) == 2 and not any(c["kind"] == "automate" for c in planned["changes"])
    client.post(f"{MAP}/versions/{vid}/activate")
    assert not any(e["kind"] == "auto" for e in _mine(client)["edges"])


def test_restoring_vanilla_undoes_the_tenants_changes_on_consent(client):
    draft = _draft(_mine(client))
    _node(draft, "INT-03")["active"] = False
    _node(draft, "INT-13")["label"] = "KYC check"
    draft["edges"] = [e for e in draft["edges"] if e["source"] != "INT-15"]
    draft["edges"].append({"source": "INT-46", "target": "INT-17", "kind": "auto"})
    vid = _apply(client, draft)["active_id"]
    assert len(_mine(client)["differences"]) >= 4

    planned = client.post(f"{MAP}/original/plan").json()
    assert {c["kind"] for c in planned["changes"]} == {"on", "rename", "link", "unautomate"}
    back = client.post(f"{MAP}/original/activate").json()
    assert back["on_original"] and back["active_id"] is None
    assert _mine(client)["differences"] == []
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-13")["label"] == "Verify customer identity"
    assert next(v for v in back["versions"] if v["id"] == vid)["status"] == "retired"  # kept, to activate again
    again = client.post(f"{MAP}/versions/{vid}/activate").json()
    assert again["active_id"] == vid and next(n for n in _mine(client)["nodes"] if n["code"] == "INT-13")["label"] == "KYC check"


def test_every_save_is_kept_and_only_administrators_see_the_map(client):
    from app.models.intent_log import IntentExecutionLog
    from app.orchestration import function_map

    draft = _draft(_mine(client))
    _node(draft, "INT-15")["x"] += 40
    _apply(client, draft)
    _node(draft, "INT-15")["x"] += 40
    out = _apply(client, draft)
    assert out["applied"] == []  # only the layout changed
    assert next(n for n in out["map"]["nodes"] if n["code"] == "INT-15")["x"] == _node(draft, "INT-15")["x"]
    with client.TestSessionLocal() as db:  # a new row per activation - nothing overwritten
        assert db.query(IntentExecutionLog).filter(IntentExecutionLog.intent_type == function_map.KIND).count() == 2
    rep = _user(client, "map-rep")
    for path in ("", "/vanilla", "/versions"):
        assert client.get(f"{MAP}{path}", headers=_as(rep)).status_code == 403
    assert client.post(f"{MAP}/versions", json={"source": "active"}, headers=_as(rep)).status_code == 403
    assert client.post(f"{MAP}/original/activate", headers=_as(rep)).status_code == 403


def test_a_rewired_step_overrides_the_built_in_outcome_rule(client):
    """Feasibility passed used to go to "Create a quote" whatever the map said;
    once the map in use re-wires it, the map decides - a failure keeps its own path."""
    from test_sales_to_order import _launch, _opportunity, run

    def at_presales(company):
        opp = _opportunity(client, company)
        run(client, "INT-37", opportunity_id=opp, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "100 Mbps", "Contract term": "36 months"})
        assert run(client, "INT-38", opportunity_id=opp, address="12 King Fahd Road")["result"]["stage"] == "in_feasibility"
        return opp

    _launch(client)
    offered = lambda body: [a["code"] for a in body["next_actions"]]  # noqa: E731
    before = run(client, "INT-18", opportunity_id=at_presales("Before Co"), decision="feasible")
    assert before["result"]["presales_outcome"] == "passed" and offered(before) == ["INT-05"]  # BRT's original: the quote

    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if e["source"] != "INT-18"]
    draft["edges"].append({"source": "INT-18", "target": "INT-24", "kind": "next", "why": "Feasible - order it straight away"})
    _apply(client, draft)

    passed = run(client, "INT-18", opportunity_id=at_presales("After Co"), decision="feasible")
    assert offered(passed) == ["INT-24"], passed["next_actions"]
    failed = run(client, "INT-18", opportunity_id=at_presales("Failing Co"), decision="not_feasible", notes="No duct")
    assert offered(failed) == ["INT-37"]  # not feasible still goes back to reconfiguring


def test_skipping_the_quote_after_feasibility_is_followed_everywhere(client):
    """Feasibility -> Order an offering (no quote): what's next, the opportunity's
    buttons and Customer 360's suggestions follow the map, prefilled from the deal;
    a condition can test the opportunity's lead (a parent record)."""
    from test_sales_to_order import _launch, _opportunity, run

    _launch(client)
    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if e["source"] != "INT-18"]
    draft["edges"].append({"source": "INT-18", "target": "INT-106", "kind": "next", "why": "Feasible - order it, no quote"})
    _apply(client, draft)

    opp = _opportunity(client, "Skip Co")
    run(client, "INT-37", opportunity_id=opp, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "100 Mbps", "Contract term": "36 months"})
    run(client, "INT-38", opportunity_id=opp, address="12 King Fahd Road")
    passed = run(client, "INT-18", opportunity_id=opp, decision="feasible")
    nxt = passed["next_actions"]
    assert [a["code"] for a in nxt] == ["INT-106"], nxt
    assert nxt[0]["entities"]["offering_name"] == "Fiber 900" and nxt[0]["entities"]["party_id"]

    buttons = {a["intent"] for a in client.get(f"/api/records/opportunity/{opp}").json()["actions"]}
    assert "INT-106" in buttons and "INT-05" not in buttons, buttons  # the record's buttons follow the map
    party = nxt[0]["entities"]["party_id"]
    codes = [a["code"] for a in client.get(f"/api/customers/{party}/overview").json()["next_actions"]]
    assert "INT-106" in codes and "INT-05" not in codes, codes  # and so does Customer 360

    # a condition on a parent record: offered only when the opportunity's lead came by referral
    draft = _draft(_mine(client))
    for e in draft["edges"]:
        if e["source"] == "INT-18":
            e["when"] = {"field": "parent.lead.source", "operator": "eq", "value": "referral"}
    planned, vid = _plan(client, draft)
    assert any("the lead's source is referral" in c["text"] for c in planned["changes"]), planned["changes"]
    assert not planned["problems"]
    client.post(f"{MAP}/versions/{vid}/activate")

    def feasible(company, source):
        lead = client.post("/api/intents", json={"text": f"Qualify this lead. company_name: {company}, source: {source}, region: North"}).json()
        o = lead["result"]["opportunity_id"]
        run(client, "INT-37", opportunity_id=o, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "100 Mbps", "Contract term": "36 months"})
        run(client, "INT-38", opportunity_id=o, address="12 King Fahd Road")
        return [a["code"] for a in run(client, "INT-18", opportunity_id=o, decision="feasible")["next_actions"]]

    assert feasible("Referred Co", "referral") == ["INT-106"]  # the lead (a parent record) came by referral
    assert "INT-106" not in feasible("Web Co", "website")


def test_the_map_in_use_and_the_original_cannot_be_changed_directly(client):
    draft = _draft(_mine(client))
    _node(draft, "INT-03")["active"] = False
    for path in ("/apply", "/plan", ""):  # the old direct save is gone
        assert client.post(f"{MAP}{path}", json=draft).status_code in (404, 405)
    assert client.put(MAP, json=draft).status_code in (404, 405)
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-03")["active"]


def test_versions_are_drafts_until_activated_and_only_drafts_change(client):
    made = client.post(f"{MAP}/versions", json={"source": "original", "name": "Leaner sales"}).json()
    vid, ver = made["version"]["id"], made["version"]
    assert (ver["status"], ver["name"], ver["based_on"], ver["number"]) == ("draft", "Leaner sales", "BRT original", 1)
    draft = _draft(made)
    _node(draft, "INT-03")["active"] = False
    saved = client.put(f"{MAP}/versions/{vid}", json={**draft, "notes": "no product retirement"}).json()
    assert not _node(_draft(saved), "INT-03")["active"] and saved["version"]["notes"] == "no product retirement"
    assert next(n for n in _mine(client)["nodes"] if n["code"] == "INT-03")["active"]  # a draft drives nothing

    second = client.post(f"{MAP}/versions", json={"source": vid}).json()["version"]  # a copy of a version
    assert second["based_on"] == "version 1" and second["number"] == 2
    listed = client.get(f"{MAP}/versions").json()
    assert [v["number"] for v in listed["versions"]] == [2, 1] and listed["on_original"]

    client.post(f"{MAP}/versions/{vid}/activate")
    assert not next(n for n in _mine(client)["nodes"] if n["code"] == "INT-03")["active"]
    # the active version is read-only: changing it means a new version
    assert client.put(f"{MAP}/versions/{vid}", json={"name": "Renamed"}).status_code == 409
    assert client.delete(f"{MAP}/versions/{vid}").status_code == 409
    assert client.post(f"{MAP}/versions/{vid}/activate").status_code == 409
    assert client.delete(f"{MAP}/versions/{second['id']}").status_code == 204
    assert client.get(f"{MAP}/versions/{second['id']}").status_code == 404
    assert client.post(f"{MAP}/versions", json={"source": "nonsense"}).status_code == 409


def test_a_tenants_versions_are_its_own(client):
    from app.auth import hash_password
    from app.models.auth import User, UserRole
    from app.models.tenant import Tenant

    vid = client.post(f"{MAP}/versions", json={"source": "active"}).json()["version"]["id"]
    with client.TestSessionLocal() as db:
        tenant = Tenant(name="Other Telco", slug="other-telco")
        db.add(tenant)
        db.flush()
        admin = User(tenant_id=tenant.id, email="admin@other-telco.test", password_hash=hash_password("x" * 8), name="Their Admin", role=UserRole.admin)
        db.add(admin)
        db.commit()
        other = admin.id
    assert client.get(f"{MAP}/versions/{vid}", headers=_as(other)).status_code == 404
    assert client.post(f"{MAP}/versions/{vid}/activate", headers=_as(other)).status_code == 404
    assert client.get(f"{MAP}/versions", headers=_as(other)).json()["versions"] == []


def test_brt_releases_reach_a_customized_map_without_undoing_its_changes(client):
    import json

    from app.models.function_map import FunctionMapVersion
    from app.orchestration import function_map

    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if (e["source"], e["target"]) != ("INT-37", "INT-38")]  # the tenant's own change
    vid = _plan(client, draft)[1]
    # pretend the version was made before BRT released INT-15 -> INT-46, and that BRT
    # has since retired a hand-off INT-15 -> INT-03 the version still has
    with client.TestSessionLocal() as db:
        v = db.get(FunctionMapVersion, vid)
        pic = json.loads(v.picture_json)
        pic["base"] = [p for p in pic["base"] if p != ["INT-15", "INT-46"]] + [["INT-15", "INT-03"]]
        pic["edges"] = [e for e in pic["edges"] if (e["source"], e["target"]) != ("INT-15", "INT-46")]
        pic["edges"].append({"source": "INT-15", "target": "INT-03", "kind": "next", "why": ""})
        v.picture_json = json.dumps(pic)
        db.commit()
    shown = client.get(f"{MAP}/versions/{vid}").json()
    links = {(e["source"], e["target"]) for e in shown["edges"] if e["kind"] == "next"}
    assert ("INT-15", "INT-46") in links  # BRT's new hand-off came in
    assert ("INT-15", "INT-03") not in links  # the retired one went
    assert ("INT-37", "INT-38") not in links  # the tenant's own removal stays
    assert len(shown["release_notes"]) == 2

    edges, notes = function_map.release_merge([{"source": "A", "target": "B", "kind": "next"}], None)
    assert edges == [{"source": "A", "target": "B", "kind": "next"}] and notes == []  # an old map without a base is left alone


def test_a_map_arranged_before_versions_becomes_the_active_version_1(client):
    from app.data_intelligence import audit
    from app.orchestration import function_map

    with client.TestSessionLocal() as db:  # a map saved the old way (no versions yet)
        from app.models.tenant import Tenant

        tid = db.query(Tenant).filter(Tenant.slug == "default").one().id
        log = audit.start_execution(db, tid, "[functional map] 0 change(s)", channel="function_map")
        log.intent_type = function_map.KIND
        audit.record_context(log, {"version": 1, "edges": function_map._vanilla_edges()[:5], "positions": {}})
        audit.complete(log, "executed")
        db.commit()
    listed = client.get(f"{MAP}/versions").json()
    assert len(listed["versions"]) == 1 and listed["versions"][0]["status"] == "active" and not listed["on_original"]
    assert listed["active_id"] == listed["versions"][0]["id"]
    assert len(client.get(f"{MAP}/versions").json()["versions"]) == 1  # made once
