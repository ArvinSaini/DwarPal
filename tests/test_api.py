import hashlib
import hmac
import json

import pytest

from tests.conftest import make_client

SHOES = [{"id": "prod_shoes", "quantity": 1}]
WATCH = [{"id": "prod_watch", "quantity": 1}]


def create(client, items, key="k1"):
    return client.post("/agent/v1/checkout_sessions", json={"items": items}, headers={"Idempotency-Key": key})


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# -- discovery, health, feed ----------------------------------------------------------------------

def test_well_known_needs_no_auth(app_client):
    r = app_client.get("/.well-known/agent-commerce.json", headers={"Authorization": ""})
    assert r.status_code == 200
    doc = r.json()
    assert doc["merchant_id"] == "trail-and-turf" and doc["api_version"] == "2026-09-03"
    assert doc["checkout_url"].endswith("/agent/v1/checkout_sessions") and doc["feed_url"].endswith("/agent/v1/products")
    assert doc["payment_rails"] == ["razorpay:payment_link"] and doc["auth"] == "bearer"
    assert "footwear" in doc["policy"]["allowed_categories"]


def test_health(app_client):
    r = app_client.get("/health", headers={"Authorization": ""})
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["ledger_ok"] is True


def test_products_feed_and_filters(app_client):
    r = app_client.get("/agent/v1/products")
    assert r.status_code == 200 and r.json()["count"] == 10
    assert set(r.json()["items"][0]) >= {"id", "title", "price_paise", "currency", "availability", "category", "tags"}
    assert [p["id"] for p in app_client.get("/agent/v1/products?category=footwear").json()["items"]] == ["prod_shoes"]
    assert app_client.get("/agent/v1/products?q=bottle").json()["count"] == 1


def test_missing_and_invalid_auth(app_client):
    r = app_client.get("/agent/v1/products", headers={"Authorization": ""})
    assert r.status_code == 401 and r.json() == {"type": "unauthorized", "code": "missing_api_key",
                                                 "message": r.json()["message"]}
    r = app_client.get("/agent/v1/products", headers={"Authorization": "Bearer agk_nope"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_api_key"
    r = app_client.get("/agent/v1/products", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401 and r.json()["code"] == "missing_api_key"


def test_request_id_is_echoed(app_client):
    r = app_client.get("/agent/v1/products", headers={"Request-Id": "req-123"})
    assert r.headers["Request-Id"] == "req-123"


# -- create ---------------------------------------------------------------------------------------

def test_create_requires_idempotency_key(app_client):
    r = app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES})
    assert r.status_code == 400
    assert r.json()["type"] == "invalid_request" and r.json()["code"] == "missing"
    assert r.json()["param"] == "Idempotency-Key"


def test_create_rejects_bad_bodies(app_client):
    r = create(app_client, "x")
    assert r.status_code == 400 and r.json()["code"] == "invalid" and r.json()["param"] == "items"
    r = app_client.post("/agent/v1/checkout_sessions", json={}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 400 and r.json()["code"] == "missing" and r.json()["param"] == "items"
    r = app_client.post("/agent/v1/checkout_sessions", content=b"not json",
                        headers={"Idempotency-Key": "k", "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "invalid"
    r = app_client.post("/agent/v1/checkout_sessions", json=[1, 2], headers={"Idempotency-Key": "k"})
    assert r.status_code == 400


def test_create_allowed_and_denied(app_client):
    r = create(app_client, SHOES)
    assert r.status_code == 201
    s = r.json()
    assert s["status"] == "ready_for_payment" and s["offers"] and s["decision"]["verdict"] == "ALLOW"
    assert s["totals"]["total_paise"] == 249900 and s["links"]["trail"].endswith(f"/{s['id']}/trail")
    r = create(app_client, WATCH, key="k2")
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "not_ready_for_payment" and d["messages"][0]["rule_id"] == "G06_MERCHANT_CATEGORY"


def test_create_is_idempotent(app_client):
    a = create(app_client, SHOES, "same").json()
    b = create(app_client, SHOES, "same")
    assert b.status_code == 201 and b.json()["id"] == a["id"]
    r = create(app_client, WATCH, "same")
    assert r.status_code == 409 and r.json()["code"] == "request_not_idempotent"


# -- update / complete / get / trail / cancel -----------------------------------------------------

def test_full_flow(app_client, world):
    s = create(app_client, SHOES).json()
    sid = s["id"]
    offer_id = s["offers"][0]["id"]
    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}",
                        json={"items": SHOES + [{"id": offer_id, "quantity": 1}]})
    assert r.status_code == 200 and len(r.json()["line_items"]) == 2 and r.json()["status"] == "ready_for_payment"

    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete")
    assert r.status_code == 400 and r.json()["param"] == "Idempotency-Key"

    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    assert r.status_code == 200
    p = r.json()
    assert p["status"] == "payment_pending" and p["payment"]["url"].startswith("https://")
    assert p["payment"]["amount_paise"] == 249900 + 49900
    r2 = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    assert r2.status_code == 200 and r2.json()["payment"]["link_id"] == p["payment"]["link_id"]
    assert len(world.payments.created) == 1

    r = app_client.get(f"/agent/v1/checkout_sessions/{sid}")
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert r.json()["payment"]["status"] == "captured"

    r = app_client.get(f"/agent/v1/checkout_sessions/{sid}/trail")
    assert r.status_code == 200
    t = r.json()
    assert t["verify"]["ok"] and t["events"][0]["type"] == "session.created"
    assert [e["type"] for e in t["events"]][-3:] == ["payment.captured", "mandate.committed", "session.completed"]

    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/cancel")
    assert r.status_code == 409 and r.json()["code"] == "wrong_state"


def test_cancel_pending_session(app_client, world):
    world.payments.outcomes = ["pending"]
    sid = create(app_client, SHOES).json()["id"]
    app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "canceled" and r.json()["payment"]["status"] == "cancelled"


def test_complete_denied_returns_409_with_rule(app_client, world):
    sid = create(app_client, SHOES).json()["id"]
    world.mandates.revoke(world.mandate.id)
    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    assert r.status_code == 409
    assert r.json()["type"] == "policy_denied" and r.json()["rule_id"] == "G02_MANDATE_ACTIVE"
    assert app_client.get(f"/agent/v1/checkout_sessions/{sid}").json()["status"] == "not_ready_for_payment"


def test_complete_from_denied_session_is_wrong_state(app_client):
    sid = create(app_client, WATCH).json()["id"]
    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    assert r.status_code == 409 and r.json()["code"] == "wrong_state"


def test_provider_error_is_502(app_client, world):
    sid = create(app_client, SHOES).json()["id"]
    world.payments.fail_create = True
    r = app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    assert r.status_code == 502 and r.json()["code"] == "payment_provider_error"


def test_other_agent_cannot_see_session(app_client, world):
    sid = create(app_client, SHOES).json()["id"]
    _, key = world.agents.register("other")
    r = app_client.get(f"/agent/v1/checkout_sessions/{sid}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert app_client.get("/agent/v1/checkout_sessions/cs_nope").status_code == 404


def test_revoked_agent_is_denied_by_the_gate_not_by_auth(app_client, world):
    world.agents.revoke(world.agent.id)
    assert app_client.get("/agent/v1/products").status_code == 200
    r = create(app_client, SHOES)
    assert r.status_code == 201 and r.json()["messages"][0]["rule_id"] == "G01_AGENT_ACTIVE"


# -- webhook --------------------------------------------------------------------------------------

def test_webhook_signature_dedupe_and_reconcile(app_client, world):
    sid = create(app_client, SHOES).json()["id"]
    app_client.post(f"/agent/v1/checkout_sessions/{sid}/complete", headers={"Idempotency-Key": "c1"})
    body = json.dumps({"event": "payment.captured",
                       "payload": {"payment": {"entity": {"id": "pay_x", "notes": {"session_id": sid}}}}}).encode()
    headers = {"Content-Type": "application/json", "X-Razorpay-Event-Id": "evt_1", "X-Razorpay-Signature": "bad"}
    r = app_client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"

    headers["X-Razorpay-Signature"] = sign(body, "whsec")
    r = app_client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["session_id"] == sid
    assert world.sessions.get_any(sid)["status"] == "completed"
    assert any(e.type == "webhook.received" for e in world.ledger.events(session_id=sid))

    r = app_client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "duplicate"


def test_webhook_without_secret_is_disabled(world):
    client = make_client(world, razorpay_webhook_secret=None)
    r = client.post("/webhooks/razorpay", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 503 and r.json()["code"] == "webhooks_disabled"


def test_webhook_unknown_session_is_acknowledged(app_client, world):
    body = json.dumps({"event": "payment.failed",
                       "payload": {"payment": {"entity": {"id": "pay_y", "notes": {"session_id": "cs_ghost"}}}}}).encode()
    headers = {"Content-Type": "application/json", "X-Razorpay-Event-Id": "evt_2",
               "X-Razorpay-Signature": sign(body, "whsec")}
    r = app_client.post("/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_requires_review_is_visible_to_the_agent(app_client, world):
    world.policies.set(dict(world.policies.get(), review_above_paise=200000))
    s = create(app_client, SHOES).json()
    assert s["status"] == "requires_review" and s["messages"][0]["code"] == "requires_review"
    r = app_client.post(f"/agent/v1/checkout_sessions/{s['id']}/complete", headers={"Idempotency-Key": "c1"})
    assert r.status_code == 409 and r.json()["code"] == "requires_review"
    world.sessions.approve_review(s["id"], "ok")
    assert app_client.get(f"/agent/v1/checkout_sessions/{s['id']}").json()["status"] == "ready_for_payment"
    doc = app_client.get("/.well-known/agent-commerce.json").json()
    assert "requires_review" in doc["session_statuses"]


# -- the trail carries an anchor the agent can keep --------------------------------------------------

def test_trail_and_health_carry_the_ledger_head_as_an_anchor(app_client, world):
    from dwarpal.ledger import parse_anchor
    sid = create(app_client, SHOES).json()["id"]
    head = app_client.get(f"/agent/v1/checkout_sessions/{sid}/trail").json()["ledger_head"]
    assert head == {"seq": world.ledger.count(), "hash": world.ledger.head()}
    assert world.ledger.verify(anchor=parse_anchor(f"{head['seq']}:{head['hash']}")).ok
    assert app_client.get("/health", headers={"Authorization": ""}).json()["ledger_head"] == head


# -- request signing: an agent with a public key must sign every request -------------------------------

def signed_headers(private_b64, method, target, body=b"", ts=None, nonce=None, clock=None):
    import uuid

    from dwarpal.signing import HEADER_NONCE, HEADER_SIGNATURE, HEADER_TIMESTAMP, sign
    ts = clock.now if ts is None else ts
    nonce = nonce or uuid.uuid4().hex
    return {HEADER_TIMESTAMP: str(ts), HEADER_NONCE: nonce,
            HEADER_SIGNATURE: sign(private_b64, ts, nonce, method, target, body)}


@pytest.fixture
def signer(world):
    from dwarpal.signing import generate_keypair
    private, public = generate_keypair()
    agent, key = world.agents.register("signer", public_key=public)
    world.mandates.create(agent.id, 400000, 800000, 2000000, [], world.clock.now + 7 * 86400)
    client = make_client(world)
    client.headers.update({"Authorization": f"Bearer {key}"})
    return client, private, agent


def test_signing_agent_must_sign_every_request(signer, world):
    client, private, _ = signer
    r = client.get("/agent/v1/products")
    assert r.status_code == 401 and r.json()["code"] == "signature_required"
    target = "/agent/v1/products?q=bottle"  # the query string is part of what is signed
    r = client.get(target, headers=signed_headers(private, "GET", target, clock=world.clock))
    assert r.status_code == 200 and r.json()["count"] == 1
    body = json.dumps({"items": SHOES}).encode()
    h = signed_headers(private, "POST", "/agent/v1/checkout_sessions", body, clock=world.clock)
    r = client.post("/agent/v1/checkout_sessions", content=body,
                    headers={**h, "Idempotency-Key": "s1", "Content-Type": "application/json"})
    assert r.status_code == 201 and r.json()["status"] == "ready_for_payment"


def test_replay_stale_wrong_key_wrong_body_and_garbage_are_refused(signer, world):
    from dwarpal.signing import generate_keypair
    client, private, _ = signer
    target = "/agent/v1/products"
    h = signed_headers(private, "GET", target, clock=world.clock)
    assert client.get(target, headers=h).status_code == 200
    r = client.get(target, headers=h)  # the very same request again
    assert r.status_code == 401 and r.json()["code"] == "replayed_nonce"
    r = client.get(target, headers=signed_headers(private, "GET", target, ts=world.clock.now - 301, clock=world.clock))
    assert r.status_code == 401 and r.json()["code"] == "stale_timestamp"
    r = client.get(target, headers=signed_headers(private, "GET", target, ts=world.clock.now + 301, clock=world.clock))
    assert r.status_code == 401 and r.json()["code"] == "stale_timestamp"
    other_private, _ = generate_keypair()
    r = client.get(target, headers=signed_headers(other_private, "GET", target, clock=world.clock))
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"
    h = signed_headers(private, "GET", target, clock=world.clock)
    h["X-Agent-Signature"] = "AAAA" + h["X-Agent-Signature"][4:]
    r = client.get(target, headers=h)
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"
    h = signed_headers(private, "GET", target, clock=world.clock)
    h["X-Agent-Timestamp"] = "yesterday"
    r = client.get(target, headers=h)
    assert r.status_code == 401 and r.json()["code"] == "malformed_signature"
    body, other = json.dumps({"items": SHOES}).encode(), json.dumps({"items": WATCH}).encode()
    h = signed_headers(private, "POST", "/agent/v1/checkout_sessions", other, clock=world.clock)
    r = client.post("/agent/v1/checkout_sessions", content=body,
                    headers={**h, "Idempotency-Key": "s2", "Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"  # a signature covers one body only


def test_bearer_only_agents_are_unaffected_and_discovery_advertises_signing(app_client):
    assert app_client.get("/agent/v1/products").status_code == 200
    doc = app_client.get("/.well-known/agent-commerce.json", headers={"Authorization": ""}).json()
    assert doc["auth"] == "bearer"
    rs = doc["request_signing"]
    assert rs["alg"] == "ed25519" and rs["max_skew_s"] == 300
    assert rs["headers"] == ["X-Agent-Timestamp", "X-Agent-Nonce", "X-Agent-Signature"]
    assert "sha256(body)" in rs["canonical"]
