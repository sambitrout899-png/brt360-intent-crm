"""The Integration Hub (app/hub/), end to end: real sockets against a fake partner system.

Inbound - other systems calling BRT 360: keys, scopes, REST, SOAP, WSDL, OpenAPI, sync, async, idempotency.
Outbound - BRT 360 calling them: all four patterns, retries, dead letters, breaker, auth, the SSRF wall.
Flows calling out and waiting for replies, webhooks, and BIM's EAI: source keys, transforms, change feed, reprocessing.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import text

from app import outbound_guard, ratelimit
from app.config import get_settings
from app.crm_core import bim_service
from app.hub import contracts, inbound, mapping, outbound, worker
from app.models.auth import User, UserRole
from app.models.crm import Party
from app.models.flows import FlowRun
from app.models.integration_hub import HubClient, HubConnection, HubMessage
from app.orchestration import flow_runs
from app.timeutils import utcnow

SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
WSDL = "{http://schemas.xmlsoap.org/wsdl/}"


# ---------------------------------------------------------------- the partner system


class Partner:
    """A real HTTP server: answers what the test scripts, and remembers what it was sent."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: list[tuple[int, str, dict]] = []
        self.default = (200, '{"ok": true, "reference": "R-1"}', {"Content-Type": "application/json"})
        partner = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                partner.requests.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                         "body": self.rfile.read(length).decode()})
                status, body, headers = partner.script.pop(0) if partner.script else partner.default
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                encoded = body.encode()
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _answer

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, status, body="", content_type="application/json", headers=None):
        self.script.append((status, body, {"Content-Type": content_type, **(headers or {})}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def partner(monkeypatch):
    server = Partner()
    monkeypatch.setattr(get_settings(), "integration_allow_private_cidrs", "127.0.0.0/8")
    monkeypatch.setattr(get_settings(), "integration_public_origin", "https://crm.example.com")
    outbound._tokens.clear()
    ratelimit._local.clear()
    yield server
    server.close()


def connection(client, partner, **over):
    body = {"code": "ERP", "name": "The ERP", "base_url": partner.url, "auth_type": "none", **over}
    resp = client.post("/api/integration/connections", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def operation(client, conn, code="SYNC_CUSTOMER", **over):
    body = {"connection_id": conn["id"], "code": code, "name": code.title(), "path": "/customers", **over}
    resp = client.post("/api/integration/operations", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def tenant_user_id(client):
    db = client.TestSessionLocal()
    try:
        return db.query(User).filter(User.role == UserRole.admin).order_by(User.id).first().id
    finally:
        db.close()


def api_client(client, code="SAP", **over):
    body = {"code": code, "name": code, "run_as_user_id": tenant_user_id(client), **over}
    resp = client.post("/api/integration/clients", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def service(client, code="NEW_PROSPECT", target="INT-15", **over):
    resp = client.post("/api/integration/services", json={"code": code, "name": code.title(), "target": target, **over})
    assert resp.status_code == 200, resp.text
    return resp.json()


def key_of(made):
    return {"X-API-Key": made["key"]}


def pump(client, rounds=1):
    total = 0
    for _ in range(rounds):
        total += worker.tick(client.TestSessionLocal, workers=1)
    return total


def message(client, **filters):
    rows = client.get("/api/integration/messages", params=filters).json()
    return rows[0] if rows else None


def make_due(client, message_id):
    db = client.TestSessionLocal()
    db.get(HubMessage, message_id).next_attempt_at = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    db.close()


def soap(operation_name, inner, header=""):
    head = f"<soap:Header>{header}</soap:Header>" if header else ""
    return (f'<soap:Envelope xmlns:soap="{SOAP11}" xmlns:t="urn:any">{head}<soap:Body><t:{operation_name}Request>{inner}'
            f"</t:{operation_name}Request></soap:Body></soap:Envelope>")


# ---------------------------------------------------------------- outbound: the four patterns


def test_sync_request_reply_calls_and_returns_the_mapped_answer(client, partner):
    conn = connection(client, partner, auth_type="bearer", secret="s3cret-token", headers={"X-Tenant": "acme"})
    op = operation(client, conn, method="POST", path="/customers/{{id}}", body_template='{"name": "{{name}}", "age": "{{age}}", "ref": "{{correlation_id}}"}',
                   reply_mapping={"erp_ref": "reference"})
    partner.answer(200, json.dumps({"reference": "ERP-77", "other": 1}))
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"id": "a/b", "name": 'Q"uote', "age": 30}}).json()
    assert done["status"] == "replied" and done["reply"] == {"erp_ref": "ERP-77"} and done["http_status"] == 200
    sent = partner.requests[-1]
    assert sent["path"] == "/customers/a%2Fb"  # a value can't change the path
    assert json.loads(sent["body"]) == {"name": 'Q"uote', "age": "30", "ref": done["correlation_id"]} or json.loads(sent["body"])["age"] == 30
    assert sent["headers"]["authorization"] == "Bearer s3cret-token" and sent["headers"]["x-tenant"] == "acme"
    assert sent["headers"]["x-correlation-id"] == done["correlation_id"] == sent["headers"]["idempotency-key"]
    log = client.get(f"/api/integration/messages/{message(client, direction='out')['id']}").json()
    assert log["status"] == "replied" and log["http_status"] == 200 and log["mode"] == "sync"
    assert "s3cret-token" not in json.dumps(log)  # the credential isn't in the log


def test_a_placeholder_alone_in_json_keeps_its_type_and_xml_values_are_escaped():
    body, kind = mapping.render_body('{"n": "{{n}}", "s": "x{{n}}", "nested": {"o": "{{o}}"}}', {"payload": {"n": 5, "o": {"a": 1}}})
    assert kind == "json" and json.loads(body) == {"n": 5, "s": "x5", "nested": {"o": {"a": 1}}}
    body, kind = mapping.render_body("<a>{{v}}</a>", {"payload": {"v": "</a><evil/>"}})
    assert kind == "xml" and "<evil" not in body and ET.fromstring(body).text == "</a><evil/>"


def test_sync_request_only_keeps_no_reply(client, partner):
    op = operation(client, connection(client, partner), pattern="request_only")
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"a": 1}}).json()
    assert done["status"] == "delivered" and "reply" not in done
    assert json.loads(partner.requests[-1]["body"]) == {"a": 1}


def test_sync_failure_says_why_and_is_logged(client, partner):
    op = operation(client, connection(client, partner))
    partner.answer(422, '{"message": "tax id invalid"}')
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()
    assert done["status"] == "failed" and "422" in done["error"] and "tax id invalid" in done["error"]
    assert message(client, status="failed")["http_status"] == 422


def test_async_request_only_is_queued_retried_and_dead_lettered(client, partner):
    op = operation(client, connection(client, partner), mode="async", pattern="request_only", max_attempts=3, backoff_seconds=1)
    queued = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"a": 1}}).json()
    assert queued["status"] == "queued" and partner.requests == []  # nothing sent until the worker runs

    partner.answer(503, "busy")
    assert pump(client) == 1
    row = message(client, direction="out")
    assert row["status"] == "queued" and row["attempts"] == 1 and "503" in row["error"] and row["next_attempt_at"]

    assert pump(client) == 0  # the backoff hasn't passed
    make_due(client, row["id"])
    partner.answer(200, "{}")
    assert pump(client) == 1
    row = message(client, direction="out")
    assert row["status"] == "delivered" and row["attempts"] == 2
    first, second = partner.requests
    assert first["headers"]["idempotency-key"] == second["headers"]["idempotency-key"] == queued["correlation_id"]  # a retry is the same request

    # a refusal is not retried: straight to the dead letters, where a person can replay it
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"b": 2}})
    partner.answer(400, '{"error": "bad"}')
    pump(client)
    dead = message(client, status="dead")
    assert dead["attempts"] == 1 and "400" in dead["error"]
    assert client.get("/api/integration/overview").json()["dead_letters"] == 1
    assert client.post(f"/api/integration/messages/{dead['id']}/replay").json()["status"] == "queued"
    partner.answer(200, "{}")
    pump(client)
    assert client.get(f"/api/integration/messages/{dead['id']}").json()["status"] == "delivered"


def test_retries_run_out_into_the_dead_letters(client, partner):
    op = operation(client, connection(client, partner), mode="async", pattern="request_only", max_attempts=2, backoff_seconds=1)
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.default = (500, "down", {})
    pump(client)
    make_due(client, message(client, direction="out")["id"])
    pump(client)
    assert message(client, direction="out")["status"] == "dead"


def test_retry_after_is_honoured(client, partner):
    op = operation(client, connection(client, partner), mode="async", pattern="request_only", backoff_seconds=1)
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.answer(429, "slow down", headers={"Retry-After": "600"})
    pump(client)
    row = message(client, direction="out")
    wait = dt.datetime.fromisoformat(row["next_attempt_at"]) - utcnow()
    assert dt.timedelta(minutes=9) < wait <= dt.timedelta(minutes=10)


def test_async_request_reply_gets_its_answer_from_the_response(client, partner):
    op = operation(client, connection(client, partner), mode="async", reply_mapping={"id": "data.id"})
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.answer(200, '{"data": {"id": 9}}')
    pump(client)
    row = client.get(f"/api/integration/messages/{message(client, direction='out')['id']}").json()
    assert row["status"] == "replied" and json.loads(row["reply"]) == {"id": 9}


def test_async_request_reply_by_callback_is_single_use(client, partner):
    op = operation(client, connection(client, partner), mode="async", reply_via="callback", body_template='{"reply_to": "{{reply_url}}", "id": "{{correlation_id}}"}')
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.answer(202, "")
    pump(client)
    row = message(client, direction="out")
    assert row["status"] == "awaiting_reply" and row["reply_deadline"]
    sent = json.loads(partner.requests[-1]["body"])
    assert sent["reply_to"].startswith("https://crm.example.com/api/gateway/callback/")
    token = sent["reply_to"].rsplit("/", 1)[1]

    assert client.post("/api/gateway/callback/not-the-token", json={"done": 1}).status_code == 404
    got = client.post(f"/api/gateway/callback/{token}", json={"done": 1})
    assert got.status_code == 200 and got.json()["correlation_id"] == sent["id"]
    assert message(client, direction="out")["status"] == "replied"
    assert client.post(f"/api/gateway/callback/{token}", json={"done": 2}).status_code == 409  # once

    db = client.TestSessionLocal()  # only the hash is kept: the database can't answer for the partner
    assert hashlib.sha256(token.encode()).hexdigest() == db.query(HubMessage).first().reply_token_hash
    db.close()


def test_a_reply_that_never_comes_times_out(client, partner):
    op = operation(client, connection(client, partner), mode="async", reply_via="callback")
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.answer(202, "")
    pump(client)
    db = client.TestSessionLocal()
    db.query(HubMessage).first().reply_deadline = utcnow() - dt.timedelta(minutes=1)
    db.commit()
    db.close()
    assert worker.expire_replies(client.TestSessionLocal) == 1
    assert message(client, direction="out")["status"] == "timed_out"


def test_a_rolled_back_transaction_sends_nothing(client, partner):
    op = operation(client, connection(client, partner), mode="async", pattern="request_only")
    db = client.TestSessionLocal()
    outbound.call(db, db.query(HubConnection).first().tenant_id, op["code"], {"a": 1})
    db.rollback()
    db.close()
    assert pump(client) == 0 and partner.requests == []


def test_the_circuit_breaker_stops_calling_a_failing_system(client, partner):
    conn = connection(client, partner)
    op = operation(client, conn)
    partner.default = (500, "down", {})
    for _ in range(outbound.BREAKER_FAILURES):
        client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    calls = len(partner.requests)
    assert calls == outbound.BREAKER_FAILURES
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()
    assert done["status"] == "failed" and "left alone" in done["error"] and len(partner.requests) == calls  # not called
    assert client.get("/api/integration/overview").json()["tripped_connections"][0]["code"] == "ERP"

    # queued work waits without using up its attempts
    async_op = operation(client, conn, code="QUEUED", mode="async", pattern="request_only")
    client.post(f"/api/integration/operations/{async_op['id']}/send", json={"payload": {}})
    pump(client)
    row = message(client, status="queued")
    assert row["attempts"] == 0 and "left alone" in row["error"] and len(partner.requests) == calls

    assert client.post(f"/api/integration/connections/{conn['id']}/reset-breaker").json()["circuit_open_until"] is None
    partner.default = (200, "{}", {})
    assert client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()["status"] == "replied"


def test_a_stuck_send_is_picked_up_after_its_lease(client, partner):
    op = operation(client, connection(client, partner), mode="async", pattern="request_only")
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    assert worker.claim(client.TestSessionLocal)  # a worker takes it... and dies
    assert worker.claim(client.TestSessionLocal) == []  # the lease holds it
    db = client.TestSessionLocal()
    db.query(HubMessage).first().locked_until = utcnow() - dt.timedelta(seconds=1)
    db.commit()
    db.close()
    pump(client)
    assert message(client, direction="out")["status"] == "delivered"


# ---------------------------------------------------------------- outbound: credentials


def test_oauth2_token_is_fetched_cached_and_refreshed_on_401(client, partner):
    conn = connection(client, partner, auth_type="oauth2", username="cid", secret="csecret", token_url=f"{partner.url}/token", oauth_scope="erp.write")
    op = operation(client, conn)
    partner.answer(200, '{"access_token": "T1", "expires_in": 3600}')
    partner.answer(200, "{}")
    partner.answer(200, "{}")
    for _ in range(2):
        assert client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()["status"] == "replied"
    token_call, call1, call2 = partner.requests
    assert token_call["path"] == "/token" and "grant_type=client_credentials" in token_call["body"] and "scope=erp.write" in token_call["body"]
    assert token_call["headers"]["authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()
    assert call1["headers"]["authorization"] == call2["headers"]["authorization"] == "Bearer T1"  # one token, two calls

    partner.answer(401, "expired")
    partner.answer(200, '{"access_token": "T2", "expires_in": 3600}')
    partner.answer(200, "{}")
    assert client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()["status"] == "replied"
    assert partner.requests[-1]["headers"]["authorization"] == "Bearer T2"


def test_hmac_signature_can_be_verified_by_the_receiver(client, partner):
    op = operation(client, connection(client, partner, auth_type="hmac", secret="shared"))
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"a": 1}})
    sent = partner.requests[-1]
    stamp = sent["headers"]["x-brt-timestamp"]
    expected = hmac.new(b"shared", stamp.encode() + b"." + sent["body"].encode(), hashlib.sha256).hexdigest()
    assert sent["headers"]["x-brt-signature"] == f"sha256={expected}"


def test_basic_and_api_key_auth(client, partner):
    basic = connection(client, partner, code="BASIC", auth_type="basic", username="u", secret="p")
    keyed = connection(client, partner, code="KEYED", auth_type="api_key", auth_header="X-Their-Key", secret="k123456789")
    client.post(f"/api/integration/operations/{operation(client, basic, code='BB')['id']}/send", json={"payload": {}})
    assert partner.requests[-1]["headers"]["authorization"] == "Basic " + base64.b64encode(b"u:p").decode()
    client.post(f"/api/integration/operations/{operation(client, keyed, code='KK')['id']}/send", json={"payload": {}})
    assert partner.requests[-1]["headers"]["x-their-key"] == "k123456789"


def test_secrets_are_encrypted_at_rest_and_never_returned(client, partner):
    made = connection(client, partner, auth_type="bearer", secret="plain-secret-value")
    assert "plain-secret-value" not in json.dumps(made) and made["has_secret"] and made["secret_hint"].endswith("alue")
    op = operation(client, client.get("/api/integration/connections").json()[0], mode="async", pattern="request_only")
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"pii": "sara@acme.com"}})
    db = client.TestSessionLocal()
    raw_secret = db.execute(text("SELECT secret FROM hub_connections")).scalar()
    raw_payload = db.execute(text("SELECT payload FROM hub_messages")).scalar()
    db.close()
    assert raw_secret.startswith("enc:v1:") and "plain-secret" not in raw_secret
    assert raw_payload.startswith("enc:v1:") and "sara" not in raw_payload
    # saving without the secret keeps the one held
    body = {k: made[k] for k in ("code", "name", "base_url", "auth_type")}
    assert client.put(f"/api/integration/connections/{made['id']}", json=body).json()["has_secret"] is True


# ---------------------------------------------------------------- outbound: SOAP


def test_soap_call_builds_the_envelope_and_reads_the_reply(client, partner):
    op = operation(client, connection(client, partner), protocol="soap", soap_action="urn:createCustomer", path="/soap", reply_mapping={"id": "CustomerId"})
    partner.answer(200, f'<s:Envelope xmlns:s="{SOAP11}"><s:Body><r:CreateResponse xmlns:r="urn:x"><r:CustomerId>C-9</r:CustomerId></r:CreateResponse></s:Body></s:Envelope>', "text/xml")
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"name": "A & B"}}).json()
    assert done["reply"] == {"id": "C-9"}
    sent = partner.requests[-1]
    assert sent["headers"]["soapaction"] == '"urn:createCustomer"' and sent["headers"]["content-type"].startswith("text/xml")
    body = ET.fromstring(sent["body"]).find(f"{{{SOAP11}}}Body")
    assert body[0].tag == "SYNC_CUSTOMERRequest" and body[0][0].text == "A & B"


def test_a_soap_fault_is_a_failure_and_whose_fault_decides_the_retry(client, partner):
    op = operation(client, connection(client, partner), protocol="soap", mode="async", pattern="request_only", backoff_seconds=1)
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    fault = f'<s:Envelope xmlns:s="{SOAP11}"><s:Body><s:Fault><faultcode>s:Server</faultcode><faultstring>Database down</faultstring></s:Fault></s:Body></s:Envelope>'
    partner.answer(500, fault, "text/xml")
    pump(client)
    row = message(client, direction="out")
    assert row["status"] == "queued" and "Database down" in row["error"]  # the server's fault: try again
    make_due(client, row["id"])
    partner.answer(500, fault.replace("s:Server", "s:Client").replace("Database down", "Bad customer"), "text/xml")
    pump(client)
    assert message(client, direction="out")["status"] == "dead"  # the sender's fault: retrying gives the same


def test_soap_12_uses_its_own_content_type(client, partner):
    op = operation(client, connection(client, partner), protocol="soap", soap_version="1.2", soap_action="urn:go")
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"a": 1}})
    sent = partner.requests[-1]
    assert sent["headers"]["content-type"].startswith("application/soap+xml") and 'action="urn:go"' in sent["headers"]["content-type"]
    assert "http://www.w3.org/2003/05/soap-envelope" in sent["body"]


# ---------------------------------------------------------------- the SSRF wall


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/", "http://localhost/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.17.0.1/",
                                 "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://0.0.0.0/"])
def test_private_and_metadata_addresses_are_refused_by_default(url):
    with pytest.raises(outbound.NotConfigured):
        outbound.check_destination(url)


def test_the_platform_can_allow_a_network_but_never_the_metadata_service(monkeypatch):
    monkeypatch.setattr(get_settings(), "integration_allow_private_cidrs", "10.20.0.0/16,169.254.0.0/16")
    assert outbound.check_destination("http://10.20.1.1/")[1] == "10.20.1.1"
    with pytest.raises(outbound.NotConfigured):
        outbound.check_destination("http://10.21.1.1/")
    with pytest.raises(outbound.NotConfigured):
        outbound.check_destination("http://169.254.169.254/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/", "http://user:pass@example.com/"])
def test_other_schemes_and_credentials_in_the_address_are_refused(url):
    with pytest.raises(outbound.NotConfigured):
        outbound.check_destination(url)


def test_a_private_name_in_dns_is_caught_when_called(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 80))])
    with pytest.raises(outbound.NotConfigured):
        outbound.check_destination("http://looks-public.example.com/")


def test_connections_refuse_bad_addresses_when_saved(client):
    for url in ("ftp://x/", "http://u:p@example.com/"):
        assert client.post("/api/integration/connections", json={"code": "X1", "name": "x", "base_url": url}).status_code == 400


def test_a_redirect_is_not_followed(client, partner):
    op = operation(client, connection(client, partner))
    partner.answer(302, "", headers={"Location": "http://169.254.169.254/"})
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()
    assert done["status"] == "failed" and "redirect" in done["error"] and len(partner.requests) == 1


def test_a_call_to_a_private_address_fails_cleanly_for_the_tenant(client, partner, monkeypatch):
    monkeypatch.setattr(get_settings(), "integration_allow_private_cidrs", "")
    op = operation(client, connection(client, partner))
    resp = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    assert resp.status_code == 400 and "private" in resp.json()["detail"] and partner.requests == []


# ---------------------------------------------------------------- inbound: keys and scopes


def test_no_key_or_a_wrong_key_is_refused(client, partner):
    service(client)
    url = "/api/gateway/services/NEW_PROSPECT"
    assert client.post(url, json={"name": "X"}).status_code == 401
    assert client.post(url, json={"name": "X"}, headers={"X-API-Key": "brt_nope_nope"}).status_code == 401
    made = api_client(client)
    bad = made["key"][:-4] + "XXXX"
    assert client.post(url, json={"name": "X"}, headers={"X-API-Key": bad}).status_code == 401
    assert "key" in made and made["key"].startswith("brt_")
    assert "key" not in client.get("/api/integration/clients").json()[0]  # shown once
    db = client.TestSessionLocal()
    assert made["key"] not in db.execute(text("SELECT key_hash FROM hub_clients")).scalar()
    db.close()


def test_a_key_is_presented_any_of_the_ways(client, partner):
    made = api_client(client)
    basic = base64.b64encode(f"{made['key_id']}:{made['key']}".encode()).decode()
    for headers in ({"X-API-Key": made["key"]}, {"Authorization": f"Bearer {made['key']}"}, {"Authorization": f"Basic {basic}"}):
        assert client.get("/api/gateway/ping", headers=headers).json()["client"] == "SAP"


def test_oauth2_client_credentials(client, partner):
    made = api_client(client)
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    good = f"grant_type=client_credentials&client_id={made['key_id']}&client_secret={made['key']}"
    assert client.post("/api/gateway/oauth/token", content=good.replace("client_credentials", "password"), headers=form).status_code == 400
    assert client.post("/api/gateway/oauth/token", content=good.replace(made["key"], "wrong"), headers=form).status_code == 401
    token = client.post("/api/gateway/oauth/token", content=good, headers=form).json()
    assert token["token_type"] == "Bearer" and token["expires_in"] > 0
    assert client.get("/api/gateway/ping", headers={"Authorization": f"Bearer {token['access_token']}"}).json()["client"] == "SAP"

    # switched off: the token it already holds stops working too
    body = {"code": "SAP", "name": "SAP", "run_as_user_id": made["run_as_user_id"], "is_active": False}
    client.put(f"/api/integration/clients/{made['id']}", json=body)
    assert client.get("/api/gateway/ping", headers={"Authorization": f"Bearer {token['access_token']}"}).status_code == 401


def test_an_integration_token_is_not_a_session(client, partner):
    from fastapi.testclient import TestClient

    from app.auth import decode_token
    from app.main import app

    made = api_client(client)
    db = client.TestSessionLocal()
    token = inbound.issue_token(db, made["key_id"], made["key"])[0]
    db.close()
    assert decode_token(token)["purpose"].startswith("integration:")
    # the real session check (conftest's override is lifted for this one call) refuses a one-purpose token
    from app.auth import get_current_user

    saved = app.dependency_overrides.pop(get_current_user)
    try:
        assert TestClient(app).get("/api/intents/recent", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = saved


def test_a_client_only_reaches_what_it_was_given(client, partner):
    service(client)
    service(client, code="OTHER")
    made = api_client(client, allowed_services=["NEW_PROSPECT"])
    assert client.post("/api/gateway/services/OTHER", json={"name": "X"}, headers=key_of(made)).status_code == 403
    listed = client.get("/api/gateway/services", headers=key_of(made)).json()
    assert [s["code"] for s in listed] == ["NEW_PROSPECT"]
    assert "OTHER" not in client.get("/api/gateway/soap?wsdl", headers=key_of(made)).text  # nor in its contracts
    assert "OTHER" not in json.dumps(client.get("/api/gateway/openapi.json", headers=key_of(made)).json())


def test_rate_limit_expiry_and_ip_allowlist(client, partner):
    limited = api_client(client, code="LIMITED", rate_per_minute=2)
    codes = [client.get("/api/gateway/ping", headers=key_of(limited)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]

    expired = api_client(client, code="OLD", expires_at=(utcnow() - dt.timedelta(days=1)).isoformat())
    assert client.get("/api/gateway/ping", headers=key_of(expired)).status_code == 401

    walled = api_client(client, code="WALLED", ip_allowlist="8.8.4.0/24")
    assert client.get("/api/gateway/ping", headers=key_of(walled)).status_code == 403
    allowed = {**key_of(walled), "X-Forwarded-For": "8.8.4.4, 10.0.0.2"}
    assert client.get("/api/gateway/ping", headers=allowed).status_code == 200
    # a caller can't talk its way in by writing the address itself: the proxy appends the real one to its right
    spoofed = {**key_of(walled), "X-Forwarded-For": "8.8.4.4, 1.1.1.1, 10.0.0.2"}
    assert client.get("/api/gateway/ping", headers=spoofed).status_code == 403

    assert client.post("/api/integration/clients", json={"code": "BAD", "name": "x", "run_as_user_id": limited["run_as_user_id"], "ip_allowlist": "nonsense"}).status_code == 400


def test_rotating_a_key_keeps_the_old_one_working_for_a_while(client, partner):
    made = api_client(client)
    fresh = client.post(f"/api/integration/clients/{made['id']}/rotate").json()
    assert fresh["key"] != made["key"] and fresh["key_id"] == made["key_id"] and fresh["rotating_until"]
    for key in (made["key"], fresh["key"]):
        assert client.get("/api/gateway/ping", headers={"X-API-Key": key}).status_code == 200
    client.post(f"/api/integration/clients/{made['id']}/revoke-previous")
    assert client.get("/api/gateway/ping", headers={"X-API-Key": made["key"]}).status_code == 401
    assert client.get("/api/gateway/ping", headers={"X-API-Key": fresh["key"]}).status_code == 200


def test_one_tenant_never_sees_anothers(client, partner):
    from app.seed import get_or_create_tenant, get_or_create_user

    db = client.TestSessionLocal()
    other = get_or_create_tenant(db, "other-co", "Other Co")
    user = get_or_create_user(db, tenant_id=other.id, email="a@other.co", password="x", name="Other", role=UserRole.admin)
    db.commit()
    other_id, other_user = other.id, user.id
    db.close()
    headers = {"X-Test-User-Id": str(other_user)}

    service(client)
    mine = api_client(client)
    theirs = client.post("/api/integration/clients", json={"code": "THEIRS", "name": "T", "run_as_user_id": other_user}, headers=headers).json()
    assert client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "X"}, headers=key_of(theirs)).status_code == 404
    assert "NEW_PROSPECT" not in client.get("/api/gateway/ping", headers=key_of(theirs)).json()["services"]

    called = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "Mine Co"}, headers=key_of(mine)).json()
    assert client.get(f"/api/gateway/messages/{called['correlation_id']}", headers=key_of(theirs)).status_code == 404
    assert client.get("/api/integration/messages", headers=headers).json() == []
    assert client.get(f"/api/integration/clients", headers=headers).json()[0]["code"] == "THEIRS"
    # a client can't be made to run as another organization's user
    assert client.post("/api/integration/clients", json={"code": "HIJACK", "name": "x", "run_as_user_id": mine["run_as_user_id"]}, headers=headers).status_code == 400
    # nor can another tenant's rows be touched by id
    assert client.put(f"/api/integration/clients/{mine['id']}", json={"code": "X1", "name": "x", "run_as_user_id": other_user}, headers=headers).status_code == 404
    assert client.post(f"/api/integration/clients/{mine['id']}/rotate", headers=headers).status_code == 404
    assert other_id


# ---------------------------------------------------------------- inbound: the work


def test_a_service_runs_the_intent_as_the_clients_user(client, partner):
    service(client, input_mapping=[{"source": "customer.fullName", "target": "name", "transform": "trim"}, {"target": "segment", "value": "B2B"}],
            fields=[{"name": "customer.fullName", "type": "string", "required": True}])
    made = api_client(client)
    done = client.post("/api/gateway/services/NEW_PROSPECT", json={"customer": {"fullName": "  Acme Fibre  "}}, headers=key_of(made))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "executed" and body["correlation_id"] and body["result"]["party_id"]
    db = client.TestSessionLocal()
    party = db.get(Party, body["result"]["party_id"])
    assert party.name == "Acme Fibre" and party.sys_created_by == made["run_as_user_id"]  # the audit trail says who
    log = db.execute(text("SELECT channel, user_id FROM intent_execution_log WHERE id = :i"), {"i": body["execution_id"]}).one()
    db.close()
    assert log[0] == "integration" and log[1] == made["run_as_user_id"]
    assert message(client, direction="in")["status"] == "succeeded"
    assert client.get(f"/api/gateway/messages/{body['correlation_id']}", headers=key_of(made)).json()["status"] == "succeeded"


def test_a_refused_intent_answers_422_with_the_reason_and_a_missing_field_400(client, partner):
    service(client, code="STRICT", fields=[{"name": "name", "required": True}])
    made = api_client(client)
    assert client.post("/api/gateway/services/STRICT", json={}, headers=key_of(made)).status_code == 400
    service(client, code="LOOSE")
    # the pipeline names what it still needs
    needs = client.post("/api/gateway/services/LOOSE", json={"irrelevant": "x"}, headers=key_of(made))
    assert needs.status_code == 422 and needs.json()["status"] != "executed" and needs.json()["missing_fields"]


def test_a_clients_user_who_left_stops_its_work_with_a_clear_answer(client, partner):
    service(client)
    made = api_client(client)
    db = client.TestSessionLocal()
    db.get(User, made["run_as_user_id"]).is_active = False
    db.commit()
    db.close()
    resp = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "X"}, headers=key_of(made))
    assert resp.status_code == 403 and "no longer active" in resp.json()["message"]


def test_async_call_answers_at_once_and_the_outcome_can_be_read_or_pushed(client, partner):
    op = operation(client, connection(client, partner), code="TELL_THEM", mode="async", pattern="request_only")
    service(client, callback_operation_id=op["id"])
    made = api_client(client)
    accepted = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "Later Co"}, headers={**key_of(made), "Prefer": "respond-async"})
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] == "accepted" and accepted.headers["location"] == body["status_url"]
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name == "Later Co").first() is None  # not done yet
    db.close()
    assert client.get(body["status_url"], headers=key_of(made)).json()["status"] == "queued"

    pump(client)
    final = client.get(body["status_url"], headers=key_of(made)).json()
    assert final["status"] == "succeeded" and final["result"]["result"]["party_id"]
    pump(client)  # the callback was queued too
    told = json.loads(partner.requests[-1]["body"])
    assert told["correlation_id"] == body["correlation_id"] and told["outcome"] == "succeeded" and told["service"] == "NEW_PROSPECT"


def test_mode_of_a_service_binds_the_caller(client, partner):
    service(client, code="ONLY_SYNC", mode="sync")
    service(client, code="ONLY_ASYNC", mode="async")
    made = api_client(client)
    assert client.post("/api/gateway/services/ONLY_SYNC", json={"name": "A"}, headers={**key_of(made), "Prefer": "respond-async"}).status_code == 400
    assert client.post("/api/gateway/services/ONLY_ASYNC", json={"name": "B"}, headers=key_of(made)).status_code == 202


def test_idempotency_key_does_the_work_once_and_replays_the_answer(client, partner):
    service(client)
    made = api_client(client)
    headers = {**key_of(made), "Idempotency-Key": "order-123"}
    first = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "Once Co"}, headers=headers)
    second = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "Once Co"}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.headers["idempotent-replayed"] == "true" and second.json()["result"] == first.json()["result"]
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name == "Once Co").count() == 1
    db.close()
    # another key, or another client, is another request
    other = client.post("/api/gateway/services/NEW_PROSPECT", json={"name": "Twice Co"}, headers={**key_of(made), "Idempotency-Key": "order-124"})
    assert "idempotent-replayed" not in other.headers


def test_dry_run_checks_and_keeps_nothing(client, partner):
    service(client)
    made = api_client(client)
    resp = client.post("/api/gateway/services/NEW_PROSPECT?dry_run=true", json={"name": "Ghost Co"}, headers=key_of(made))
    assert resp.status_code == 200 and resp.json()["dry_run"] is True
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name == "Ghost Co").count() == 0
    db.close()
    tried = client.post("/api/integration/services-try", json={"code": "NEW_PROSPECT", "payload": {"name": "Ghost 2"}}).json()
    assert tried["http_status"] == 200 and tried["answer"]["dry_run"] is True


def test_unknown_service_and_bad_body(client, partner):
    made = api_client(client)
    assert client.post("/api/gateway/services/NOPE", json={}, headers=key_of(made)).status_code == 404
    service(client)
    bad = client.post("/api/gateway/services/NEW_PROSPECT", content="{not json", headers={**key_of(made), "Content-Type": "application/json"})
    assert bad.status_code == 400


def test_service_setup_is_checked(client):
    bad = client.post("/api/integration/services", json={"code": "X1", "name": "x", "target": "INT-99999"})
    assert bad.status_code == 400
    assert client.post("/api/integration/services", json={"code": "X2", "name": "x", "target_type": "bim", "target": "NOPE"}).status_code == 400
    assert client.post("/api/integration/services", json={"code": "X3", "name": "x", "target": "INT-15", "mode": "maybe"}).status_code == 400
    assert client.post("/api/integration/services", json={"code": "X4", "name": "x", "target": "INT-15", "input_mapping": [{"target": "a", "transform": "nope"}]}).status_code == 400


# ---------------------------------------------------------------- inbound: contracts and SOAP


def test_openapi_describes_each_service(client, partner):
    service(client, fields=[{"name": "name", "type": "string", "required": True, "description": "Who"}, {"name": "employees", "type": "integer"}])
    made = api_client(client)
    doc = client.get("/api/gateway/openapi.json", headers=key_of(made)).json()
    assert doc["openapi"].startswith("3.0") and doc["servers"][0]["url"] == "https://crm.example.com"
    post = doc["paths"]["/api/gateway/services/NEW_PROSPECT"]["post"]
    schema = post["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["name"] and schema["properties"]["employees"]["type"] == "integer"
    assert {"200", "202", "401", "422"} <= set(post["responses"]) and post["operationId"] == "NEW_PROSPECT"
    assert client.get("/api/integration/contracts/openapi.json").json()["paths"]  # the admin's download


def test_wsdl_is_a_complete_document(client, partner):
    service(client, fields=[{"name": "name", "type": "string", "required": True}, {"name": "employees", "type": "integer"}])
    made = api_client(client)
    resp = client.get("/api/gateway/soap?wsdl", headers=key_of(made))
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/xml")
    root = ET.fromstring(resp.text)
    assert root.tag == f"{WSDL}definitions"
    assert [o.get("name") for o in root.find(f"{WSDL}portType")] == ["NEW_PROSPECT"]
    assert {b.get("name") for b in root.findall(f"{WSDL}binding")} == {"IntegrationSoap11", "IntegrationSoap12"}
    addresses = [p[0].get("location") for p in root.find(f"{WSDL}service")]
    assert addresses == ["https://crm.example.com/api/gateway/soap"] * 2
    xs = "{http://www.w3.org/2001/XMLSchema}"
    request = next(e for e in root.iter(f"{xs}element") if e.get("name") == "NEW_PROSPECTRequest")
    named = {e.get("name"): e for e in request.iter(f"{xs}element")}
    assert named["name"].get("minOccurs") == "1" and named["employees"].get("type") == "xs:integer"
    assert client.get("/api/integration/contracts/service.wsdl").status_code == 200
    assert client.get("/api/gateway/soap", headers=key_of(made)).status_code == 400  # without ?wsdl


def test_soap_call_runs_the_service(client, partner):
    service(client)
    made = api_client(client)
    resp = client.post("/api/gateway/soap", content=soap("NEW_PROSPECT", "<t:name>Soap Co</t:name>"), headers={**key_of(made), "Content-Type": "text/xml"})
    assert resp.status_code == 200, resp.text
    root = ET.fromstring(resp.text)
    answer = root.find(f"{{{SOAP11}}}Body")[0]
    assert answer.tag.endswith("NEW_PROSPECTResponse")
    values = {c.tag.rsplit("}")[-1]: c.text for c in answer}
    assert values["status"] == "executed" and values["correlationId"] and json.loads(values["resultJson"])["result"]["party_id"]
    assert message(client, direction="in")["protocol"] == "soap"


def test_soap_with_ws_security_username_token(client, partner):
    service(client)
    made = api_client(client)
    token = (f'<w:Security xmlns:w="{contracts.WSSE}"><w:UsernameToken><w:Username>{made["key_id"]}</w:Username>'
             f'<w:Password>{made["key"]}</w:Password></w:UsernameToken></w:Security>')
    resp = client.post("/api/gateway/soap", content=soap("NEW_PROSPECT", "<t:name>Secure Co</t:name>", token), headers={"Content-Type": "text/xml"})
    assert resp.status_code == 200, resp.text
    wrong = client.post("/api/gateway/soap", content=soap("NEW_PROSPECT", "<t:name>X</t:name>", token.replace(made["key"], "brt_x_y")), headers={"Content-Type": "text/xml"})
    assert wrong.status_code == 401 and "Fault" in wrong.text


def test_soap_problems_are_faults(client, partner):
    service(client)
    made = api_client(client)
    headers = {**key_of(made), "Content-Type": "text/xml"}
    unknown = client.post("/api/gateway/soap", content=soap("NOPE", ""), headers=headers)
    assert unknown.status_code == 500 and "faultstring" in unknown.text and "Client" in unknown.text
    malformed = client.post("/api/gateway/soap", content="<not-closed>", headers=headers)
    assert malformed.status_code == 500 and "Fault" in malformed.text
    # SOAP 1.2 faults have their own shape
    twelve = soap("NOPE", "").replace(SOAP11, contracts.SOAP12)
    answer = client.post("/api/gateway/soap", content=twelve, headers={**key_of(made), "Content-Type": "application/soap+xml"})
    assert answer.status_code == 400 and "soap:Sender" in answer.text and answer.headers["content-type"].startswith("application/soap+xml")
    # a refused intent reaches the caller as a fault that names the call
    needs = client.post("/api/gateway/soap", content=soap("NEW_PROSPECT", ""), headers=headers)
    assert needs.status_code == 500 and "correlation id" in needs.text


def test_soap_async(client, partner):
    service(client)
    made = api_client(client)
    resp = client.post("/api/gateway/soap", content=soap("NEW_PROSPECT", "<t:callMode>async</t:callMode><t:name>Soap Later</t:name>"), headers={**key_of(made), "Content-Type": "text/xml"})
    # 200, with status=accepted inside: a real toolkit (zeep) reads a 202 with a body as a fault
    assert resp.status_code == 200 and "<status>accepted</status>" in resp.text and "<httpStatus>202</httpStatus>" in resp.text
    pump(client)
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name == "Soap Later").count() == 1
    db.close()


@pytest.mark.parametrize("attack", [
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><a>&xxe;</a>',
    '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><a>&lol2;</a>',
])
def test_xxe_and_entity_bombs_are_refused(attack):
    with pytest.raises(ValueError):
        mapping.parse_xml(attack)


def test_xxe_through_the_gateway_is_a_fault_and_reads_nothing(client, partner):
    service(client)
    made = api_client(client)
    attack = (f'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><soap:Envelope xmlns:soap="{SOAP11}"><soap:Body>'
              '<NEW_PROSPECTRequest><name>&xxe;</name></NEW_PROSPECTRequest></soap:Body></soap:Envelope>')
    resp = client.post("/api/gateway/soap", content=attack, headers={**key_of(made), "Content-Type": "text/xml"})
    assert resp.status_code == 500 and "DOCTYPE" in resp.text and "root:" not in resp.text


def test_xml_body_on_the_rest_endpoint(client, partner):
    service(client)
    made = api_client(client)
    resp = client.post("/api/gateway/services/NEW_PROSPECT", content="<customer><name>Xml Co</name></customer>", headers={**key_of(made), "Content-Type": "application/xml"})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- flows


def _call_flow(client, node, after, edges, trigger="INT-15"):
    graph = {"nodes": [{"id": "s", "type": "start", "label": "Start", "config": {}}, node, *after],
             "edges": [{"id": "e1", "source": "s", "target": node["id"], "label": ""}, *edges]}
    flow = client.post("/api/admin/flows", json={"name": "Calls out", "entity_type": "party", "trigger_type": "intent",
                                                 "trigger_intent_type": trigger, "timing": "after", "graph": graph})
    assert flow.status_code == 201, flow.text
    activated = client.post(f"/api/admin/flows/{flow.json()['id']}/activate")
    assert activated.status_code == 200, activated.text
    return flow.json()


def _set_field(node_id, value):
    return {"id": node_id, "type": "set_field", "label": "Note", "config": {"field": "record.email", "value": value}}


def _end():
    return {"id": "end", "type": "end", "label": "End", "config": {}}


def _prospect(client, name="Flowing Co"):
    done = client.post("/api/intents", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": name}}}).json()
    assert done["status"] == "executed", done
    return done["result"]["party_id"]


def _email_of(client, party_id):
    db = client.TestSessionLocal()
    try:
        return db.get(Party, party_id).email
    finally:
        db.close()


def test_a_flow_calls_an_api_and_uses_the_reply(client, partner):
    operation(client, connection(client, partner), code="CHECK", reply_mapping={"email": "contact.email"})
    node = {"id": "c", "type": "call_api", "label": "Check", "config": {"operation": "CHECK", "payload": {"customer": "{{record.name}}"}}}
    _call_flow(client, node, [_set_field("n", "{{steps.c.output.reply.email}}"), _end()],
               [{"id": "e2", "source": "c", "target": "n", "label": ""}, {"id": "e3", "source": "n", "target": "end", "label": ""}])
    partner.answer(200, '{"contact": {"email": "found@acme.com"}}')
    party = _prospect(client)
    assert json.loads(partner.requests[-1]["body"]) == {"customer": "Flowing Co"}
    assert _email_of(client, party) == "found@acme.com"
    row = message(client, direction="out")
    assert row["source"].startswith("flow:") and row["status"] == "replied"


def test_a_failed_call_goes_down_the_error_output_and_is_still_logged(client, partner):
    operation(client, connection(client, partner), code="CHECK")
    node = {"id": "c", "type": "call_api", "label": "Check", "config": {"operation": "CHECK", "payload": {}}}
    _call_flow(client, node, [_set_field("n", "failed-{{error.code}}@x.com"), _end()],
               [{"id": "e2", "source": "c", "target": "n", "label": "error"}, {"id": "e3", "source": "n", "target": "end", "label": ""}])
    partner.answer(500, "down")
    party = _prospect(client)
    assert _email_of(client, party) == "failed-INTEGRATION_FAILED@x.com"


def test_async_call_from_a_flow_goes_out_only_if_the_flow_commits(client, partner):
    operation(client, connection(client, partner), code="TELL", mode="async", pattern="request_only")
    node = {"id": "c", "type": "call_api", "label": "Tell", "config": {"operation": "TELL", "payload": {"name": "{{record.name}}"}}}
    _call_flow(client, node, [_end()], [{"id": "e2", "source": "c", "target": "end", "label": ""}])
    _prospect(client, "Told Co")
    assert partner.requests == []
    pump(client)
    assert json.loads(partner.requests[-1]["body"]) == {"name": "Told Co"}


def test_rehearsals_and_simulations_never_call_out(client, partner):
    operation(client, connection(client, partner), code="CHECK")
    async_op = {"operation": "CHECK", "payload": {}}
    flow = _call_flow(client, {"id": "c", "type": "call_api", "label": "Check", "config": async_op}, [_end()], [{"id": "e2", "source": "c", "target": "end", "label": ""}])

    rehearsal = client.post("/api/intents/rehearse", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Ghost Co"}}}).json()
    assert rehearsal["status"] == "rehearsed"
    simulated = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"entities": {"name": "Ghost"}, "result": {}}).json()
    assert simulated["ok"] and simulated["run"]["trace"][-2]["output"]["simulated"] is True
    assert partner.requests == []
    assert pump(client) == 0
    with outbound_guard.simulation():
        assert outbound_guard.simulating()
    assert not outbound_guard.simulating()


def _wait_flow(client, reply_template="{{reply.ref}}@ok.com"):
    node = {"id": "c", "type": "call_api_wait", "label": "Provision", "config": {"operation": "PROVISION", "payload": {"name": "{{record.name}}"}, "expire_minutes": "60"}}
    return _call_flow(client, node, [_set_field("yes", reply_template), _set_field("no", "failed@x.com"), _set_field("late", "late@x.com"), _end()],
                      [{"id": "e2", "source": "c", "target": "yes", "label": "replied"}, {"id": "e3", "source": "c", "target": "no", "label": "failed"},
                       {"id": "e4", "source": "c", "target": "late", "label": "expired"}, {"id": "e5", "source": "yes", "target": "end", "label": ""},
                       {"id": "e6", "source": "no", "target": "end", "label": ""}, {"id": "e7", "source": "late", "target": "end", "label": ""}])


def _last_run(client):
    db = client.TestSessionLocal()
    try:
        return db.query(FlowRun).order_by(FlowRun.id.desc()).first()
    finally:
        db.close()


def test_a_flow_waits_for_the_callback_and_goes_on_with_the_reply(client, partner):
    operation(client, connection(client, partner), code="PROVISION", mode="async", reply_via="callback",
              body_template='{"name": "{{name}}", "callback": "{{reply_url}}"}', reply_mapping={"ref": "serviceId"})
    _wait_flow(client)
    party = _prospect(client, "Provisioned Co")
    run = _last_run(client)
    assert run.status == "waiting" and run.wait_kind == "reply" and flow_runs.describe(client.TestSessionLocal(), run)["kind"] == "reply"
    assert _email_of(client, party) is None

    partner.answer(202, "")
    pump(client)  # sent: the flow goes on waiting
    assert _last_run(client).status == "waiting"
    sent = json.loads(partner.requests[-1]["body"])
    assert sent["name"] == "Provisioned Co"

    reply = client.post(f"/api/gateway/callback/{sent['callback'].rsplit('/', 1)[1]}", json={"serviceId": "SVC-42"})
    assert reply.status_code == 200, reply.text
    assert _last_run(client).status == "completed"
    assert _email_of(client, party) == "SVC-42@ok.com"


def test_a_flow_waiting_on_a_response_reply_continues_when_the_worker_sends(client, partner):
    operation(client, connection(client, partner), code="PROVISION", mode="async", reply_mapping={"ref": "id"})
    _wait_flow(client)
    party = _prospect(client)
    partner.answer(200, '{"id": "ANSWERED"}')
    pump(client)
    assert _last_run(client).status == "completed" and _email_of(client, party) == "ANSWERED@ok.com"


def test_a_flow_goes_down_failed_when_delivery_is_dead(client, partner):
    operation(client, connection(client, partner), code="PROVISION", mode="async")
    _wait_flow(client)
    party = _prospect(client)
    partner.answer(400, '{"error": "no"}')
    pump(client)
    assert message(client, status="dead") and _last_run(client).status == "completed"
    assert _email_of(client, party) == "failed@x.com"


def test_a_flow_goes_down_expired_when_no_reply_comes_and_a_late_one_changes_nothing(client, partner):
    operation(client, connection(client, partner), code="PROVISION", mode="async", reply_via="callback", body_template='{"callback": "{{reply_url}}"}')
    _wait_flow(client)
    party = _prospect(client)
    partner.answer(202, "")
    pump(client)
    token = json.loads(partner.requests[-1]["body"])["callback"].rsplit("/", 1)[1]
    assert flow_runs.run_due(client.TestSessionLocal, now=utcnow() + dt.timedelta(minutes=61)) == 1
    assert _email_of(client, party) == "late@x.com"
    assert client.post(f"/api/gateway/callback/{token}", json={"serviceId": "TOO-LATE"}).status_code == 200  # kept on the message...
    assert _email_of(client, party) == "late@x.com"  # ...the flow has moved on


def test_a_rolled_back_flow_leaves_neither_run_nor_message(client, partner):
    operation(client, connection(client, partner), code="PROVISION", mode="async")
    _wait_flow(client)
    client.post("/api/intents/rehearse", json={"text": "x", "structured": {"intent_type": "INT-15", "entities": {"name": "Ghost"}}})
    assert pump(client) == 0 and message(client, direction="out") is None and _last_run(client) is None


def test_flow_validation_knows_the_new_steps(client, partner):
    graph = {"nodes": [{"id": "s", "type": "start", "label": "S", "config": {}}, {"id": "c", "type": "call_api", "label": "C", "config": {}}],
             "edges": [{"id": "e", "source": "s", "target": "c", "label": ""}]}
    flow = client.post("/api/admin/flows", json={"name": "Bad", "entity_type": "party", "trigger_type": "intent", "trigger_intent_type": "INT-15", "timing": "after", "graph": graph})
    errors = flow.json().get("validation_errors") if flow.status_code == 200 else flow.json()
    assert "operation" in json.dumps(errors)
    # a wait for a reply can't be in a flow that must answer at once
    node = {"id": "c", "type": "call_api_wait", "label": "C", "config": {"operation": "X"}}
    graph["nodes"][1] = node
    before = client.post("/api/admin/flows", json={"name": "Before", "entity_type": "party", "trigger_type": "intent", "trigger_intent_type": "INT-15", "timing": "before", "graph": graph})
    errors = before.json().get("validation_errors") if before.status_code == 200 else before.json()
    assert "can't wait" in json.dumps(errors)
    catalog = client.get("/api/admin/flows/catalog").json()
    assert {"call_api", "call_api_wait"} <= {t["type"] for t in catalog["node_types"]}


# ---------------------------------------------------------------- webhooks


def test_events_become_webhooks_once_each(client, partner):
    op = operation(client, connection(client, partner), code="HOOK", mode="async", pattern="request_only")
    _prospect(client, "Before Subscription")
    sub = client.post("/api/integration/subscriptions", json={"name": "Everything flow", "event_pattern": "*", "operation_id": op["id"]})
    assert sub.status_code == 200, sub.text
    assert pump(client) == 0  # what happened before it existed isn't sent

    _prospect(client, "After Subscription")
    assert pump(client) >= 1
    envelope = json.loads(partner.requests[-1]["body"])
    assert envelope["specversion"] == "1.0" and envelope["type"] and envelope["id"] and envelope["source"].startswith("brt360/tenant/")
    assert envelope["subscription"] == "Everything flow"
    count = len(partner.requests)
    assert pump(client) == 0 and len(partner.requests) == count  # once

    # the overlap behind the cursor doesn't send anything twice
    db = client.TestSessionLocal()
    from app.models.integration_hub import HubSubscription

    db.query(HubSubscription).first().last_event_id -= 1
    db.commit()
    db.close()
    assert pump(client) == 0 and len(partner.requests) == count


def test_a_subscription_only_takes_its_pattern_and_its_tenants_events(client, partner):
    op = operation(client, connection(client, partner), code="HOOK", mode="async", pattern="request_only")
    client.post("/api/integration/subscriptions", json={"name": "Nothing like this", "event_pattern": "no.such.event", "operation_id": op["id"]})
    _prospect(client)
    assert pump(client) == 0


# ---------------------------------------------------------------- BIM -> EAI


def interface(client, code="SAP-CUST", entity="prospect", **over):
    resp = client.post("/api/bim/interfaces", json={"code": code, "name": code, "entity": entity, **over})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_an_interface_publishes_itself_and_runs_with_a_key(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId")
    made = api_client(client)
    listed = {s["code"]: s for s in client.get("/api/gateway/services", headers=key_of(made)).json()}
    fields = {f["name"]: f for f in listed["SAP-CUST"]["fields"]}
    assert fields["SapId"]["required"] and fields["CustomerName"]["required"] and "brt_id" not in fields
    resp = client.post("/api/gateway/services/SAP-CUST", json={"SapId": "1000", "CustomerName": "No Token Co", "email": "a@b.co"}, headers=key_of(made))
    assert resp.status_code == 200, resp.text
    assert resp.json()["inserted"] == 1 and resp.json()["results"][0]["external_id"] == "1000"


def test_source_keys_issue_one_brt_id_and_update_it_ever_after(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId", source_system="sap")
    made = api_client(client)
    url = "/api/gateway/services/SAP-CUST"
    first = client.post(url, json={"SapId": "1000", "CustomerName": "First Name"}, headers=key_of(made)).json()
    code = first["results"][0]["brt_id"]
    assert first["inserted"] == 1 and code.startswith("PTY-")
    again = client.post(url, json={"SapId": "1000", "CustomerName": "Second Name"}, headers=key_of(made)).json()
    assert again["updated"] == 1 and again["results"][0]["brt_id"] == code
    assert client.get(f"/api/gateway/records/{code}", headers=key_of(made)).json()["name"] == "Second Name"
    found = client.get("/api/gateway/xref/sap/prospect/1000", headers=key_of(made)).json()
    assert found["brt_id"] == code
    assert client.get("/api/gateway/xref/sap/prospect/9999", headers=key_of(made)).status_code == 404
    # a missing id is that row's problem
    missing = client.post(url, json={"CustomerName": "No id"}, headers=key_of(made))
    assert missing.status_code == 422 and "SapId is missing" in missing.json()["results"][0]["errors"][0]


def test_one_batch_can_refer_to_its_own_records_by_source_id(client, partner):
    interface(client, code="SAP-CUST", mapping={"CustomerName": "name"}, key_field="SapId", source_system="SAP")
    interface(client, code="SAP-LEAD", entity="lead", key_field="LeadNo", source_system="SAP")
    made = api_client(client)
    customer = client.post("/api/gateway/services/SAP-CUST", json={"SapId": "C1", "CustomerName": "Parent Co"}, headers=key_of(made)).json()
    lead = client.post("/api/gateway/services/SAP-LEAD", json={"LeadNo": "L1", "party_external_id": "C1", "source": "partner"}, headers=key_of(made))
    assert lead.status_code == 200, lead.text
    record = client.get(f"/api/gateway/records/{lead.json()['results'][0]['brt_id']}", headers=key_of(made)).json()
    assert record["party_brt_id"] == customer["results"][0]["brt_id"]
    unknown = client.post("/api/gateway/services/SAP-LEAD", json={"LeadNo": "L2", "party_external_id": "NOPE", "source": "partner"}, headers=key_of(made))
    assert "has not loaded" in unknown.json()["results"][0]["errors"][0]


def test_a_dry_run_issues_no_lasting_link(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId")
    made = api_client(client)
    dry = client.post("/api/gateway/services/SAP-CUST?dry_run=true", json={"SapId": "D1", "CustomerName": "Ghost"}, headers=key_of(made))
    assert dry.status_code == 200 and dry.json()["inserted"] == 1
    assert client.get("/api/gateway/xref/SAP-CUST/prospect/D1", headers=key_of(made)).status_code == 404


def test_old_style_interfaces_still_need_their_brt_ids(client, partner):
    interface(client, mapping={"CustomerName": "name"})
    made = api_client(client)
    refused = client.post("/api/gateway/services/SAP-CUST", json={"CustomerName": "No id"}, headers=key_of(made))
    assert "brt_id is missing" in refused.json()["results"][0]["errors"][0]
    ids = client.post("/api/gateway/eai/SAP-CUST/brt-ids?count=1", headers=key_of(made)).json()["brt_ids"]
    ok = client.post("/api/gateway/services/SAP-CUST", json={"brt_id": ids[0], "CustomerName": "Has id"}, headers=key_of(made))
    assert ok.status_code == 200 and ok.json()["inserted"] == 1
    # the administrator's EAI route works as it did
    assert client.post("/api/bim/eai/SAP-CUST", json={"brt_id": ids[0], "CustomerName": "Updated"}).json()["updated"] == 1


def test_mapping_rules_transform_and_validate(client, partner):
    rules = [{"source": "customer.name", "target": "name", "transform": "title"}, {"source": "customer.mail", "target": "email", "transform": "lower"},
             {"source": "kind", "target": "party_type", "map": {"P": "prospect", "O": "organization"}, "default": "prospect"},
             {"source": "phone", "target": "phone", "transform": "digits"}, {"target": "segment", "value": "B2B"}]
    interface(client, mapping=rules, key_field="id", ignore_unmapped=True)
    made = api_client(client)
    resp = client.post("/api/gateway/services/SAP-CUST", headers=key_of(made), json={
        "id": "7", "customer": {"name": "ACME LTD", "mail": "SALES@ACME.COM"}, "kind": "O", "phone": "+91 (98) 7654-3210", "junk": "ignored", "more": {"a": 1}})
    assert resp.status_code == 200, resp.text
    record = client.get(f"/api/gateway/records/{resp.json()['results'][0]['brt_id']}", headers=key_of(made)).json()
    assert record["name"] == "Acme Ltd" and record["email"] == "sales@acme.com" and record["phone"] == "919876543210" and record["party_type"] == "organization"
    preview = client.post("/api/integration/mapping/preview", json={"mapping": rules, "sample": {"customer": {"name": "x y"}}, "keep_unmapped": False}).json()
    assert preview["ok"] and preview["result"]["name"] == "X Y" and preview["result"]["party_type"] == "prospect"
    bad = client.post("/api/bim/interfaces", json={"code": "BADRULE", "name": "x", "entity": "prospect", "mapping": [{"target": "a", "transform": "nope"}]})
    assert bad.status_code == 400 and "transform" in bad.json()["detail"]


def test_mapping_helpers():
    assert mapping.dig({"a": [{"b": 1}]}, "a[0].b") == 1 == mapping.dig({"a": [{"b": 1}]}, "a.0.b")
    assert mapping.apply({"A": 1, "B": 2}, {"A": "x"}) == {"x": 1, "b": 2}  # as before: the rest comes along
    assert mapping.apply({"A": 1, "B": 2}, {"A": "x"}, keep_unmapped=False) == {"x": 1}
    assert mapping.apply({"d": "31/12/2026"}, [{"source": "d", "target": "d", "transform": "date"}]) == {"d": "2026-12-31"}
    with pytest.raises(ValueError, match="is not a date"):
        mapping.apply({"d": "soon"}, [{"source": "d", "target": "d", "transform": "date"}])
    with pytest.raises(ValueError, match="required"):
        mapping.apply({}, [{"source": "gst", "target": "tax_id", "required": True}])
    assert mapping.to_dict(mapping.parse_xml("<a><b>1</b><b>2</b><c><d>x</d></c></a>")) == {"b": ["1", "2"], "c": {"d": "x"}}
    assert bim_service.records_in({"data": {"items": [{"a": 1}]}}, "data.items") == [{"a": 1}]
    assert bim_service.records_in({"items": [{"a": 1}]}) == [{"a": 1}]
    assert bim_service.records_in([{"a": 1}, "skip"]) == [{"a": 1}]
    assert bim_service.records_in({"a": 1}) == [{"a": 1}]
    assert bim_service.records_in(None) == []


def test_a_bad_row_is_kept_to_fix_and_reprocess(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId")
    made = api_client(client)
    resp = client.post("/api/gateway/services/SAP-CUST", headers=key_of(made), json={"records": [
        {"SapId": "1", "CustomerName": "Good Co"}, {"SapId": "2", "CustomerName": "=cmd|calc", "party_type": 1, "bogus_column": "x"}]})
    assert resp.status_code == 207 and resp.json()["inserted"] == 1 and resp.json()["failed"] == 1
    job_id = resp.json()["job_id"]
    job = client.get(f"/api/bim/jobs/{job_id}").json()
    assert job["can_reprocess"] and "bogus_column" in job["errors"][0]["errors"][0]

    sheet = client.get(f"/api/bim/jobs/{job_id}/errors.csv").text
    assert "bogus_column" in sheet and "'=cmd" in sheet  # a formula in the data doesn't run in Excel

    db = client.TestSessionLocal()
    assert "bogus_column" not in db.execute(text("SELECT failed_rows_json FROM bim_jobs WHERE id = :j"), {"j": job_id}).scalar()  # encrypted
    db.close()

    still = client.post(f"/api/bim/jobs/{job_id}/reprocess", json={})  # as it was: refused again
    assert still.json()["failed"] == 1 and still.json()["parent_job_id"] == job_id
    fixed = client.post(f"/api/bim/jobs/{still.json()['id']}/reprocess", json={"rows": [{"SapId": "2", "CustomerName": "Fixed Co"}]}).json()
    assert fixed["inserted"] == 1 and fixed["failed"] == 0
    assert client.post(f"/api/bim/jobs/{fixed['id']}/reprocess", json={}).status_code == 400  # nothing left


def test_the_change_feed_pages_through_what_changed(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId", source_system="SAP")
    made = api_client(client)
    first_page = client.get("/api/gateway/eai/SAP-CUST/changes", params={"limit": 500}, headers=key_of(made)).json()
    cursor = first_page["next_cursor"]
    for n in range(3):
        client.post("/api/gateway/services/SAP-CUST", json={"SapId": f"F{n}", "CustomerName": f"Feed {n}"}, headers=key_of(made))
    page = client.get("/api/gateway/eai/SAP-CUST/changes", params={"since": cursor, "limit": 2}, headers=key_of(made)).json()
    assert [r["name"] for r in page["records"]] == ["Feed 0", "Feed 1"] and page["has_more"]
    assert page["records"][0]["external_id"] == "F0"
    last = client.get("/api/gateway/eai/SAP-CUST/changes", params={"since": page["next_cursor"], "limit": 2}, headers=key_of(made)).json()
    assert [r["name"] for r in last["records"]] == ["Feed 2"] and not last["has_more"]
    assert client.get("/api/gateway/eai/SAP-CUST/changes", params={"since": "garbage"}, headers=key_of(made)).status_code == 400
    assert client.get("/api/gateway/eai/SAP-CUST/changes", params={"limit": 501}, headers=key_of(made)).status_code == 422


def test_records_and_ids_stay_within_a_clients_scope(client, partner):
    interface(client, code="CUST", mapping={"CustomerName": "name"}, key_field="SapId")
    interface(client, code="LEADS", entity="lead", key_field="LeadNo")
    whole = api_client(client, code="WHOLE")
    code = client.post("/api/gateway/services/CUST", json={"SapId": "S1", "CustomerName": "Scoped"}, headers=key_of(whole)).json()["results"][0]["brt_id"]
    only_leads = api_client(client, code="ONLYLEADS", allowed_services=["LEADS"])
    assert client.get(f"/api/gateway/records/{code}", headers=key_of(only_leads)).status_code == 404
    assert client.get("/api/gateway/xref/CUST/prospect/S1", headers=key_of(only_leads)).status_code == 404
    assert client.get("/api/gateway/eai/CUST/changes", headers=key_of(only_leads)).status_code == 403


def test_a_load_tells_the_source_how_it_went(client, partner):
    op = operation(client, connection(client, partner), code="ACK", mode="async", pattern="request_only")
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId", ack_operation_id=op["id"])
    made = api_client(client)
    client.post("/api/gateway/services/SAP-CUST?dry_run=true", json={"SapId": "9", "CustomerName": "Dry"}, headers=key_of(made))
    assert pump(client) == 0  # a dry run tells nobody
    client.post("/api/gateway/services/SAP-CUST", json={"records": [{"SapId": "1", "CustomerName": "A"}, {"CustomerName": "No id"}]}, headers=key_of(made))
    assert pump(client) == 1
    ack = json.loads(partner.requests[-1]["body"])
    assert ack["interface"] == "SAP-CUST" and ack["inserted"] == 1 and ack["failed"] == 1 and "SapId" in ack["errors"][0]["errors"][0]


def test_async_eai_load(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId")
    made = api_client(client)
    accepted = client.post("/api/gateway/services/SAP-CUST", json={"SapId": "A1", "CustomerName": "Later"}, headers={**key_of(made), "Prefer": "respond-async"})
    assert accepted.status_code == 202
    pump(client)
    final = client.get(accepted.json()["status_url"], headers=key_of(made)).json()
    assert final["status"] == "succeeded" and final["result"]["inserted"] == 1


def test_a_soap_load_with_repeated_records_and_a_csv_load(client, partner):
    interface(client, mapping={"CustomerName": "name"}, key_field="SapId")
    made = api_client(client)
    envelope = soap("SAP_CUST", "<t:record><t:SapId>S1</t:SapId><t:CustomerName>One</t:CustomerName></t:record>"
                                "<t:record><t:SapId>S2</t:SapId><t:CustomerName>Two</t:CustomerName></t:record>")
    resp = client.post("/api/gateway/soap", content=envelope, headers={**key_of(made), "Content-Type": "text/xml"})
    assert resp.status_code == 200, resp.text
    assert json.loads(ET.fromstring(resp.text).find(f"{{{SOAP11}}}Body")[0].find("resultJson").text)["inserted"] == 2
    upload = client.post("/api/gateway/services/SAP-CUST", content="SapId,CustomerName\nS3,Three\nS4,Four\n", headers={**key_of(made), "Content-Type": "text/csv"})
    assert upload.status_code == 200 and upload.json()["inserted"] == 2


def test_a_scheduled_pull_fetches_and_loads(client, partner):
    op = operation(client, connection(client, partner), code="FETCH", method="GET", path="/customers?since={{cursor}}")
    interface(client, code="PULLED", mapping={"Name": "name"}, key_field="Id", ignore_unmapped=True, pull_operation_id=op["id"], pull_every_minutes=30, pull_records_path="data.rows")
    partner.answer(200, json.dumps({"data": {"rows": [{"Id": "P1", "Name": "Pulled One", "extra": 1}, {"Id": "P2", "Name": "Pulled Two"}]}}))
    assert worker.run_pulls(client.TestSessionLocal) == 1
    assert partner.requests[-1]["path"] == "/customers?since="  # first time: everything
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name.in_(["Pulled One", "Pulled Two"])).count() == 2
    db.close()
    jobs = client.get("/api/bim/jobs").json()
    assert jobs[0]["channel"] == "pull" and jobs[0]["inserted"] == 2

    assert worker.run_pulls(client.TestSessionLocal) == 0  # not due for half an hour
    saved = client.get("/api/bim/interfaces").json()[0]
    assert saved["pull_cursor"].endswith("Z")
    db = client.TestSessionLocal()
    from app.models.bim import BimInterface

    db.query(BimInterface).first().pull_last_at = utcnow() - dt.timedelta(minutes=31)
    db.commit()
    db.close()
    partner.answer(200, '{"data": {"rows": []}}')
    assert worker.run_pulls(client.TestSessionLocal) == 1
    assert partner.requests[-1]["path"].startswith("/customers?since=20")  # only what changed since


def test_a_pull_that_fails_is_logged_and_tried_next_period(client, partner):
    op = operation(client, connection(client, partner), code="FETCH", method="GET")
    interface(client, code="PULLED", pull_operation_id=op["id"], pull_every_minutes=30)
    partner.answer(500, "down")
    assert worker.run_pulls(client.TestSessionLocal) == 0
    assert message(client, status="failed")["kind"] == "pull"
    assert client.get("/api/bim/interfaces").json()[0]["pull_cursor"] == ""  # the next good pull still covers it
    assert worker.run_pulls(client.TestSessionLocal) == 0  # not retried every few seconds


def test_a_reply_can_be_loaded_through_an_interface(client, partner):
    target = interface(client, code="ERP-CUST", mapping={"Name": "name"}, key_field="Id", ignore_unmapped=True)
    conn = connection(client, partner)
    op = operation(client, conn, code="PULL_NOW", mode="async", reply_interface_id=target["id"])
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    partner.answer(200, '{"items": [{"Id": "R1", "Name": "From Reply"}]}')
    pump(client)
    db = client.TestSessionLocal()
    assert db.query(Party).filter(Party.name == "From Reply").count() == 1
    db.close()


# ---------------------------------------------------------------- operations


def test_test_button_shows_the_exchange_and_masks_credentials(client, partner):
    conn = connection(client, partner, auth_type="bearer", secret="very-secret-token")
    op = operation(client, conn, mode="async", pattern="request_only")
    partner.answer(200, '{"pong": true}')
    done = client.post(f"/api/integration/operations/{op['id']}/test", json={"payload": {"ping": 1}}).json()
    assert done["ok"] and done["http_status"] == 200 and done["answer"] == {"pong": True} and "very-secret-token" not in json.dumps(done)
    assert partner.requests[-1]["headers"]["authorization"] == "Bearer very-secret-token"  # sent, but never shown
    partner.answer(500, "boom")
    failed = client.post(f"/api/integration/operations/{op['id']}/test", json={"payload": {}}).json()
    assert not failed["ok"] and failed["retryable"] and failed["response_body"] == "boom"


def test_operation_setup_is_checked(client, partner):
    conn = connection(client, partner)
    base = {"connection_id": conn["id"], "code": "X1", "name": "x"}
    assert client.post("/api/integration/operations", json={**base, "method": "TRACE"}).status_code == 400
    assert client.post("/api/integration/operations", json={**base, "mode": "sync", "reply_via": "callback"}).status_code == 400
    assert client.post("/api/integration/operations", json={**base, "connection_id": 99999}).status_code == 404
    assert client.post("/api/integration/operations", json={**base, "headers": "not json"}).status_code == 400
    operation(client, conn, code="DUP")
    assert client.post("/api/integration/operations", json={**base, "code": "dup"}).status_code == 400  # codes are one case


def test_a_switched_off_operation_holds_its_queue(client, partner):
    conn = connection(client, partner)
    op = operation(client, conn, mode="async", pattern="request_only")
    client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}})
    client.put(f"/api/integration/operations/{op['id']}", json={**{k: op[k] for k in ("connection_id", "code", "name", "path", "mode", "pattern")}, "is_active": False})
    pump(client)
    row = message(client, status="queued")
    assert row and "switched off" in row["error"] and partner.requests == [] and row["attempts"] == 0
    assert client.post(f"/api/integration/messages/{row['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/integration/messages/{row['id']}/cancel").status_code == 400


def test_old_messages_are_purged_but_dead_letters_kept_longer(client, partner):
    op = operation(client, connection(client, partner))
    for n in range(2):
        client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {"n": n}})
    db = client.TestSessionLocal()
    first, second = db.query(HubMessage).order_by(HubMessage.id).all()
    first.created_at = second.created_at = utcnow() - dt.timedelta(days=40)
    second.status = "dead"
    db.commit()
    db.close()
    assert worker.purge(client.TestSessionLocal) == 1
    assert message(client, status="dead")


def test_message_search_and_limits(client, partner):
    op = operation(client, connection(client, partner))
    done = client.post(f"/api/integration/operations/{op['id']}/send", json={"payload": {}}).json()
    assert client.get("/api/integration/messages", params={"q": done["correlation_id"][:12]}).json()
    assert client.get("/api/integration/messages", params={"q": "nothing-like-it"}).json() == []
    assert client.get("/api/integration/messages", params={"limit": 501}).status_code == 422
    assert client.get("/api/integration/messages/999999").status_code == 404
    assert client.get("/api/integration/overview").json()["totals"]["operations"] == 1


def test_non_admins_cant_touch_the_hub(client, partner):
    db = client.TestSessionLocal()
    admin = db.query(User).filter(User.role == UserRole.admin).first()
    agent = User(tenant_id=admin.tenant_id, email="agent@default.local", password_hash="x", name="Agent", role=UserRole.agent, is_active=True)
    db.add(agent)
    db.commit()
    agent_id = agent.id
    db.close()
    for path in ("/api/integration/clients", "/api/integration/connections", "/api/integration/messages", "/api/integration/overview"):
        assert client.get(path, headers={"X-Test-User-Id": str(agent_id)}).status_code == 403


def test_the_public_address_of_a_caller():
    assert inbound.client_ip({"x-forwarded-for": "8.8.8.8, 172.18.0.1"}, "172.18.0.2") == "8.8.8.8"
    assert inbound.client_ip({"x-forwarded-for": "1.1.1.1, 8.8.8.8, 172.18.0.1"}, "x") == "8.8.8.8"  # the forged left end is ignored
    assert inbound.client_ip({"x-forwarded-for": "10.0.0.9, 172.18.0.1"}, "x") == "10.0.0.9"  # all private: a partner on the network
    assert inbound.client_ip({}, "203.0.113.5") == "203.0.113.5"
    row = HubClient(ip_allowlist="203.0.113.0/24, 198.51.100.7")
    assert inbound._ip_allowed(row, "203.0.113.200") and inbound._ip_allowed(row, "198.51.100.7")
    assert not inbound._ip_allowed(row, "8.8.8.8") and not inbound._ip_allowed(row, "not-an-ip")
