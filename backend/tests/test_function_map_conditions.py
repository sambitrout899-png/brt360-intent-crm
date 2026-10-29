"""Conditions on the Functional Map: a next step is offered - and an
automation runs - only when the link's condition holds for what the step did;
an "otherwise" link catches everything else."""

from test_function_map import MAP, _apply, _draft, _intent, _mine, _plan


def _with(draft, source, target, kind="next", when=None, why=""):
    draft["edges"] = [e for e in draft["edges"] if (e["source"], e["target"], e["kind"]) != (source, target, kind)]
    draft["edges"].append({"source": source, "target": target, "kind": kind, "why": why, "when": when})


def test_next_steps_follow_the_conditions_drawn_on_the_map(client):
    draft = _draft(_mine(client))
    draft["edges"] = [e for e in draft["edges"] if e["source"] != "INT-15"]
    _with(draft, "INT-15", "INT-46", when={"field": "entities.name", "operator": "contains", "value": "enterprise"}, why="Big one - open a lead")
    _with(draft, "INT-15", "INT-35", when={"operator": "otherwise"}, why="Set up the account")

    planned, vid = _plan(client, draft)
    texts = [c["text"] for c in planned["changes"] if c["kind"] == "condition"]
    assert any("only when the input name contains enterprise" in t for t in texts), texts
    assert any("otherwise" in t for t in texts)
    client.post(f"{MAP}/versions/{vid}/activate")

    links = {(e["source"], e["target"]): e for e in _mine(client)["edges"] if e["kind"] == "next"}
    assert links[("INT-15", "INT-46")]["when"] == {"field": "entities.name", "operator": "contains", "value": "enterprise"}
    assert any(d["kind"] == "conditioned" for d in _mine(client)["differences"])

    big = [n["code"] for n in _intent(client, "INT-15", {"name": "Acme Enterprise", "segment": "B2B"})["next_actions"]]
    small = [n["code"] for n in _intent(client, "INT-15", {"name": "Corner Shop", "segment": "B2B"})["next_actions"]]
    assert "INT-46" in big and "INT-35" not in big
    assert "INT-35" in small and "INT-46" not in small


def test_an_incomplete_condition_is_reported_and_saved_without_one(client):
    draft = _draft(_mine(client))
    _with(draft, "INT-15", "INT-46", when={"field": "nonsense", "operator": "eq", "value": "x"})
    planned, vid = _plan(client, draft)
    assert any("incomplete" in p["text"] for p in planned["problems"])
    client.post(f"{MAP}/versions/{vid}/activate")
    link = next(e for e in _mine(client)["edges"] if (e["source"], e["target"]) == ("INT-15", "INT-46"))
    assert "when" not in link


def test_an_automation_with_a_condition_runs_only_when_it_holds(client):
    from test_phase5_fulfillment import _launch_bundle

    _launch_bundle(client, name="Fiber 600 Bundle", price=600)
    draft = _draft(_mine(client))
    _with(draft, "INT-46", "INT-17", kind="auto", when={"field": "entities.source", "operator": "eq", "value": "referral"})
    planned, vid = _plan(client, draft)
    auto = next(c for c in planned["changes"] if c["kind"] == "automate")
    assert "only when the input source is referral" in auto["text"]
    client.post(f"{MAP}/versions/{vid}/activate")
    link = next(e for e in _mine(client)["edges"] if e["kind"] == "auto")
    assert link["when"] == {"field": "entities.source", "operator": "eq", "value": "referral"}

    def lead(source):
        party = _intent(client, "INT-15", {"name": f"Cond {source} Co", "segment": "B2B"})["result"]["party_id"]
        out = _intent(client, "INT-46", {"party_id": party, "products": ["Fiber 600 Bundle"], "source": source})
        return [t["intent"] for f in out["result"].get("flows_run", []) for t in f["trace"] if t["type"] == "action" and not t.get("skipped")]

    assert lead("referral") == ["INT-17"]
    assert lead("website") == []

    # changing the condition rewrites the same automation
    draft = _draft(_mine(client))
    for e in draft["edges"]:
        if e["kind"] == "auto":
            e["when"] = {"field": "entities.source", "operator": "eq", "value": "website"}
    applied = _apply(client, draft)["applied"]
    assert [c["kind"] for c in applied] == ["reautomate"]
    assert next(e for e in _mine(client)["edges"] if e["kind"] == "auto")["when"]["value"] == "website"
