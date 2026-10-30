"""Change control: administrators develop in workspaces - a flow they change
is a draft there and locked to them, nobody else can overwrite it - and the
organization's root administrator alone deploys a release (all or nothing)
and rolls it back. Functional Map versions get revisions and the same locks."""

from app.auth import hash_password
from app.models.auth import User, UserRole
from app.models.tenant import Tenant

FLOWS = "/api/admin/flows"
CHANGE = "/api/admin/change"
MAP = "/api/admin/function-map"


def _admin(client, name):
    with client.TestSessionLocal() as db:
        tid = db.query(Tenant).filter(Tenant.slug == "default").one().id
        user = User(tenant_id=tid, email=f"{name}@change.test", password_hash=hash_password("x" * 8), name=name, role=UserRole.admin)
        db.add(user)
        db.commit()
        return user.id


def _as(user_id, workspace=None):
    return {"X-Test-User-Id": str(user_id), **({"X-BRT-Workspace": str(workspace)} if workspace else {})}


def _root(client):
    return client.get(f"{CHANGE}/me").json()["root_admin"]["id"]


def _graph(message="hello"):
    return {"nodes": [{"id": "n1", "type": "start", "label": "Start", "config": {}},
                      {"id": "n2", "type": "notify", "label": "Tell", "config": {"channel": "popup", "audience": "me", "message": message}},
                      {"id": "n3", "type": "end", "label": "End", "config": {}}],
            "edges": [{"id": "e1", "source": "n1", "target": "n2", "label": ""}, {"id": "e2", "source": "n2", "target": "n3", "label": ""}]}


def _flow(name, message="hello"):
    return {"name": name, "entity_type": "party", "trigger_type": "intent", "trigger_intent_type": "INT-15", "timing": "after", "graph": _graph(message)}


def _message(flow):
    return next(n for n in flow["graph"]["nodes"] if n["type"] == "notify")["config"]["message"]


def _workspace(client, user, name):
    out = client.post(f"{CHANGE}/workspaces", json={"name": name}, headers=_as(user))
    assert out.status_code == 200, out.text
    return out.json()["id"]


def test_the_root_administrator_is_the_first_admin_and_can_hand_it_over(client):
    root = _root(client)
    other = _admin(client, "Second Admin")
    assert client.get(f"{CHANGE}/me").json()["is_root"] is True
    assert client.get(f"{CHANGE}/me", headers=_as(other)).json()["is_root"] is False
    assert client.put(f"{CHANGE}/root-admin", json={"user_id": root}, headers=_as(other)).status_code == 403
    assert client.put(f"{CHANGE}/root-admin", json={"user_id": other}).status_code == 200
    assert client.get(f"{CHANGE}/me", headers=_as(other)).json()["is_root"] is True


def test_workspace_drafts_are_invisible_to_live_and_locked_against_everyone_else(client):
    root, b = _root(client), _admin(client, "Admin B")
    live = client.post(FLOWS, json=_flow("Welcome", "live text")).json()
    a_ws = _workspace(client, root, "A - welcome changes")

    # A changes the live flow and makes a new one - in the workspace only
    changed = client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "draft text"), headers=_as(root, a_ws)).json()
    assert changed["draft"] and _message(changed) == "draft text"
    made = client.post(FLOWS, json=_flow("Brand new"), headers=_as(root, a_ws)).json()
    assert made["id"] < 0 and made["draft"]
    assert _message(client.get(f"{FLOWS}/{live['id']}").json()) == "live text"  # live untouched
    assert "Brand new" not in {f["name"] for f in client.get(FLOWS).json()}
    mine = {f["name"]: f for f in client.get(FLOWS, headers=_as(root, a_ws)).json()}
    assert _message(mine["Welcome"]) == "draft text" and mine["Brand new"]["draft"]

    # the flow is locked to A's workspace: B can neither change it live nor in a workspace of their own
    held = client.get(f"{FLOWS}/{live['id']}").json()["locked_by"]
    assert held["user_id"] == root and held["workspace"] == "A - welcome changes"
    assert client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "B was here"), headers=_as(b)).status_code == 409
    assert client.post(f"{FLOWS}/{live['id']}/deactivate", headers=_as(b)).status_code == 409
    b_ws = _workspace(client, b, "B - other work")
    blocked = client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "B again"), headers=_as(b, b_ws))
    assert blocked.status_code == 409 and "locked" in blocked.json()["detail"]
    assert client.get(FLOWS, headers=_as(b, a_ws)).status_code == 409  # nobody works in someone else's workspace

    # every save is a revision
    client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "draft text 2"), headers=_as(root, a_ws))
    revisions = client.get(f"{FLOWS}/{live['id']}/history").json()["revisions"]
    assert [r["revision"] for r in revisions] == [2, 1]

    # discarding frees the lock - B can work on it now
    client.post(f"{CHANGE}/workspaces/{a_ws}/discard", headers=_as(root))
    assert client.get(f"{FLOWS}/{live['id']}").json()["locked_by"] is None
    assert client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "B's text"), headers=_as(b, b_ws)).status_code == 200


def test_only_the_root_administrator_releases_and_a_release_rolls_back(client):
    b = _admin(client, "Admin B")
    live = client.post(FLOWS, json=_flow("Welcome", "live text")).json()
    ws = _workspace(client, b, "B - welcome rework")
    client.put(f"{FLOWS}/{live['id']}", json=_flow("Welcome", "released text"), headers=_as(b, ws))
    client.post(FLOWS, json=_flow("Released new flow"), headers=_as(b, ws))
    deleted = client.post(FLOWS, json=_flow("Old flow")).json()
    client.delete(f"{FLOWS}/{deleted['id']}", headers=_as(b, ws))
    listed = next(w for w in client.get(f"{CHANGE}/workspaces").json() if w["id"] == ws)
    assert sorted(o["change"] for o in listed["objects"]) == ["changed", "deleted", "new"]

    assert client.post(f"{CHANGE}/releases", json={"workspace_ids": [ws]}).status_code == 409  # not submitted yet
    client.post(f"{CHANGE}/workspaces/{ws}/submit", headers=_as(b))
    assert client.post(f"{CHANGE}/releases", json={"workspace_ids": [ws]}, headers=_as(b)).status_code == 403  # not the root admin
    rel = client.post(f"{CHANGE}/releases", json={"name": "Welcome rework", "workspace_ids": [ws]}).json()

    sim = client.post(f"{CHANGE}/releases/{rel['id']}/simulate").json()
    assert sim["ok"], sim
    assert _message(client.get(f"{FLOWS}/{live['id']}").json()) == "live text"  # the simulation kept nothing
    assert client.post(f"{CHANGE}/releases/{rel['id']}/deploy", headers=_as(b)).status_code == 403

    done = client.post(f"{CHANGE}/releases/{rel['id']}/deploy").json()
    assert done["status"] == "deployed" and done["deployed_by"]
    names = {f["name"]: f for f in client.get(FLOWS).json()}
    assert _message(names["Welcome"]) == "released text" and "Released new flow" in names and "Old flow" not in names
    assert names["Welcome"]["locked_by"] is None  # released - free again
    assert next(w for w in client.get(f"{CHANGE}/workspaces").json() if w["id"] == ws)["status"] == "released"

    back = client.post(f"{CHANGE}/releases/{rel['id']}/rollback").json()
    assert back["status"] == "rolled_back"
    names = {f["name"]: f for f in client.get(FLOWS).json()}
    assert _message(names["Welcome"]) == "live text" and "Released new flow" not in names and "Old flow" in names


def _picture(client, vid, user, workspace=None):
    shown = client.get(f"{MAP}/versions/{vid}", headers=_as(user, workspace)).json()
    return {"nodes": [{k: n[k] for k in ("code", "label", "lane", "active", "x", "dy")} for n in shown["nodes"]],
            "edges": [{k: e.get(k) for k in ("source", "target", "kind", "why")} for e in shown["edges"] if e["kind"] == "next"]}


def test_map_versions_made_in_a_workspace_are_locked_hidden_and_released_with_it(client):
    b = _admin(client, "Admin B")
    ws = _workspace(client, b, "B - map rework")
    made = client.post(f"{MAP}/versions", json={"source": "active", "name": "B's map"}, headers=_as(b, ws)).json()["version"]
    vid = made["id"]
    assert made["workspace"] == "B - map rework" and made["locked_by"]["workspace"] == "B - map rework"

    # B works on it in the workspace; outside it, even B can't change it
    draft = _picture(client, vid, b, ws)
    for n in draft["nodes"]:
        if n["code"] == "INT-03":
            n["active"] = False
    assert client.put(f"{MAP}/versions/{vid}", json=draft, headers=_as(b, ws)).status_code == 200
    outside = client.put(f"{MAP}/versions/{vid}", json=draft, headers=_as(b))
    assert outside.status_code == 409 and "select it" in outside.json()["detail"]

    # to everyone else it is B's draft: not listed, not readable, not changeable, not revisable
    assert vid not in [v["id"] for v in client.get(f"{MAP}/versions").json()["versions"]]
    assert client.get(f"{MAP}/versions/{vid}").status_code == 404
    assert client.post(f"{MAP}/versions/{vid}/revise").status_code == 404
    assert client.put(f"{MAP}/versions/{vid}", json=draft).status_code == 409
    # and it goes live only with the workspace's release - not by a direct activation
    blocked = client.post(f"{MAP}/versions/{vid}/activate")
    assert blocked.status_code == 409 and "release" in blocked.json()["detail"]

    listed = next(w for w in client.get(f"{CHANGE}/workspaces").json() if w["id"] == ws)
    assert [o["kind"] for o in listed["objects"]] == ["Map version"]
    client.post(f"{CHANGE}/workspaces/{ws}/submit", headers=_as(b))
    rel = client.post(f"{CHANGE}/releases", json={"name": "Map rework", "workspace_ids": [ws]}).json()
    assert rel["map_version_id"] == vid  # carried by the workspace
    assert client.post(f"{CHANGE}/releases/{rel['id']}/simulate").json()["ok"]
    client.post(f"{CHANGE}/releases/{rel['id']}/deploy")
    listed = client.get(f"{MAP}/versions").json()
    assert listed["active_id"] == vid
    assert not next(n for n in client.get(MAP).json()["nodes"] if n["code"] == "INT-03")["active"]

    client.post(f"{CHANGE}/releases/{rel['id']}/rollback")  # the map before the release runs again
    assert client.get(f"{MAP}/versions").json()["on_original"]
    assert next(n for n in client.get(MAP).json()["nodes"] if n["code"] == "INT-03")["active"]


def test_a_discarded_workspace_takes_its_map_versions_with_it(client):
    b = _admin(client, "Admin B")
    ws = _workspace(client, b, "B - throwaway")
    vid = client.post(f"{MAP}/versions", json={"source": "original"}, headers=_as(b, ws)).json()["version"]["id"]
    revised = client.post(f"{MAP}/versions/{vid}/revise", headers=_as(b, ws)).json()["version"]
    assert revised["workspace_id"] == ws
    client.post(f"{CHANGE}/workspaces/{ws}/discard", headers=_as(b))
    assert client.get(f"{MAP}/versions/{vid}", headers=_as(b)).status_code == 404
    assert client.get(f"{MAP}/versions/{revised['id']}", headers=_as(b)).status_code == 404


def test_map_versions_revise_lock_and_only_the_root_administrator_activates(client):
    b = _admin(client, "Admin B")
    v1 = client.post(f"{MAP}/versions", json={"source": "active", "name": "Sales tweaks"}).json()["version"]
    mine = client.get(f"{MAP}/versions/{v1['id']}").json()
    draft = {"nodes": [{k: n[k] for k in ("code", "label", "lane", "active", "x", "dy")} for n in mine["nodes"]], "edges": []}
    assert client.put(f"{MAP}/versions/{v1['id']}", json=draft).status_code == 200  # the root admin now holds v1
    assert client.put(f"{MAP}/versions/{v1['id']}", json=draft, headers=_as(b)).status_code == 409  # B can't overwrite it
    assert client.get(f"{MAP}/versions/{v1['id']}").json()["version"]["locked_by"]["user"]

    revised = client.post(f"{MAP}/versions/{v1['id']}/revise", headers=_as(b)).json()["version"]
    assert (revised["label"], revised["status"], revised["parent_id"]) == ("v1.1", "draft", v1["id"])
    assert client.put(f"{MAP}/versions/{revised['id']}", json=draft, headers=_as(b)).status_code == 200  # B's own revision

    assert client.post(f"{MAP}/versions/{revised['id']}/activate", headers=_as(b)).status_code == 403  # only the root admin
    assert client.post(f"{MAP}/original/activate", headers=_as(b)).status_code == 403
    out = client.post(f"{MAP}/versions/{revised['id']}/activate").json()
    assert out["active_id"] == revised["id"]
    active = next(v for v in out["versions"] if v["id"] == revised["id"])
    assert active["label"] == "v1.1" and active["locked_by"] is None
    assert client.post(f"{MAP}/versions/{v1['id']}/revise").json()["version"]["label"] == "v1.2"
