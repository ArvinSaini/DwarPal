from agentgate.catalog import Product

SHOES = [{"id": "prod_shoes", "quantity": 1}]


def login(client):
    r = client.get("/dashboard/login?token=merchant-secret", follow_redirects=False)
    assert r.status_code == 303 and "merchant_session" in r.headers.get("set-cookie", "")
    return client


def test_unauthenticated_redirects_to_login(app_client):
    r = app_client.get("/dashboard/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/dashboard/login")
    assert app_client.get("/dashboard/products", follow_redirects=False).status_code == 303


def test_login_wrong_token_shows_error_and_overview_renders(app_client):
    r = app_client.get("/dashboard/login?token=wrong")
    assert r.status_code == 200 and "not recognised" in r.text
    login(app_client)
    r = app_client.get("/dashboard/")
    assert r.status_code == 200 and "Trail &amp; Turf" in r.text and "Ledger" in r.text
    assert app_client.get("/static/style.css").status_code == 200
    r = app_client.get("/dashboard/logout", follow_redirects=False)
    assert r.status_code == 303
    assert app_client.get("/dashboard/", follow_redirects=False).status_code == 303


def test_products_page_and_enrichment_approval(app_client, world):
    login(app_client)
    world.catalog.upsert(Product(id="prod_raw", title="Mystery Running Socks", description="socks", price_paise=100))
    r = app_client.get("/dashboard/products")
    assert r.status_code == 200 and "Mystery Running Socks" in r.text and "uncategorised" in r.text
    r = app_client.post("/dashboard/products/enrich", follow_redirects=False)
    assert r.status_code == 303 and "1" in r.headers["location"]
    r = app_client.get("/dashboard/products")
    assert "Pending proposals" in r.text and "apparel" in r.text
    enr = [e for e in r.text.split('name="enrichment_id" value="') if e][1:]
    enrichment_id = enr[0].split('"')[0]
    r = app_client.post(f"/dashboard/enrichments/{enrichment_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert world.catalog.get("prod_raw").category == "apparel"
    assert world.ledger.events()[-1].type == "catalog.enrichment.approved"
    r = app_client.post("/dashboard/enrichments/enr_nope/reject", follow_redirects=False)
    assert r.status_code == 303 and "not+found" in r.headers["location"] or "not%20found" in r.headers["location"]


def test_sync_without_razorpay_keys_reports_error(app_client):
    login(app_client)
    r = app_client.post("/dashboard/products/sync", follow_redirects=False)
    assert r.status_code == 303 and "Razorpay" in r.headers["location"] or "razorpay" in r.headers["location"].lower()


def test_agents_register_and_revoke(app_client, world):
    login(app_client)
    r = app_client.post("/dashboard/agents", data={"name": "dash-bot", "per_txn": "2500", "daily": "5000",
                                                    "total": "20000", "categories": "footwear, apparel", "days": "7"})
    assert r.status_code == 200 and "agk_" in r.text and "shown once" in r.text
    agent = world.agents.all()[-1]
    assert agent.name == "dash-bot"
    m = world.mandates.active_for(agent.id, world.clock.now)
    assert m.per_txn_cap_paise == 250000 and m.categories == ["footwear", "apparel"]
    assert {e.type for e in world.ledger.events()} >= {"agent.registered", "mandate.created"}
    r = app_client.post(f"/dashboard/agents/{agent.id}/revoke", follow_redirects=False)
    assert r.status_code == 303
    assert world.agents.get(agent.id).status == "revoked"
    assert world.ledger.events()[-1].type == "agent.revoked"
    r = app_client.post("/dashboard/agents", data={"name": "", "per_txn": "x", "daily": "1", "total": "1"})
    assert r.status_code == 200 and "error" in r.text.lower()


def test_policy_edit(app_client, world):
    login(app_client)
    r = app_client.get("/dashboard/policy")
    assert r.status_code == 200 and "max_order_paise" in r.text
    r = app_client.post("/dashboard/policy", data={"json": "{not json"})
    assert r.status_code == 200 and "error" in r.text.lower()
    assert world.policies.get()["max_order_paise"] == 500000
    r = app_client.post("/dashboard/policy", data={"json": '{"max_order_paise": 1, "allowed_categories": ["x"], '
                                                          '"blocked_skus": [], "max_qty_per_line": 2, '
                                                          '"in_stock_only": false}'}, follow_redirects=False)
    assert r.status_code == 303
    assert world.policies.get()["max_order_paise"] == 1
    assert world.ledger.events()[-1].type == "policy.updated"


def test_sessions_pages_and_merchant_cancel(app_client, world):
    login(app_client)
    s = app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES}, headers={"Idempotency-Key": "k"}).json()
    r = app_client.get("/dashboard/sessions")
    assert r.status_code == 200 and s["id"] in r.text and "ready_for_payment" in r.text
    r = app_client.get(f"/dashboard/sessions/{s['id']}")
    assert r.status_code == 200 and "G00_WELL_FORMED" in r.text and "G13_SESSION_STATE" in r.text
    r = app_client.post(f"/dashboard/sessions/{s['id']}/cancel", follow_redirects=False)
    assert r.status_code == 303
    assert world.sessions.get_any(s["id"])["status"] == "canceled"
    r = app_client.post(f"/dashboard/sessions/{s['id']}/cancel", follow_redirects=False)
    assert r.status_code == 303 and "already" in r.headers["location"]
    assert app_client.get("/dashboard/sessions/cs_nope").status_code == 404


def test_ledger_page_verify_tamper_and_receipt(app_client, world):
    login(app_client)
    s = app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES}, headers={"Idempotency-Key": "k"}).json()
    r = app_client.get("/dashboard/ledger")
    assert r.status_code == 200 and "verified" in r.text and "session.created" in r.text
    r = app_client.post("/dashboard/ledger/verify")
    assert r.status_code == 200 and "verified" in r.text
    r = app_client.get(f"/dashboard/ledger/receipt/{s['id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "Receipt for session" in r.text and "Chain: verified" in r.text
    world.ledger.tamper(1)
    r = app_client.post("/dashboard/ledger/verify")
    assert "BROKEN" in r.text
    assert app_client.get("/dashboard/ledger/receipt/cs_nope").status_code == 404


def test_review_queue_in_dashboard(app_client, world):
    login(app_client)
    world.policies.set(dict(world.policies.get(), review_above_paise=200000))
    s = app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES}, headers={"Idempotency-Key": "k"}).json()
    r = app_client.get("/dashboard/")
    assert "requires_review" in r.text and "awaiting review" in r.text
    r = app_client.get(f"/dashboard/sessions/{s['id']}")
    assert "Approve" in r.text and "Decline" in r.text and "G14_REVIEW_THRESHOLD" in r.text
    r = app_client.post(f"/dashboard/sessions/{s['id']}/approve", data={"note": "fine"}, follow_redirects=False)
    assert r.status_code == 303
    assert world.sessions.get_any(s["id"])["status"] == "ready_for_payment"
    r = app_client.post(f"/dashboard/sessions/{s['id']}/decline", data={"note": "x"}, follow_redirects=False)
    assert r.status_code == 303 and ("wrong" in r.headers["location"] or "is%20" in r.headers["location"])


def test_refund_from_dashboard(app_client, world):
    login(app_client)
    s = app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES}, headers={"Idempotency-Key": "k"}).json()
    app_client.post(f"/agent/v1/checkout_sessions/{s['id']}/complete", headers={"Idempotency-Key": "c"})
    app_client.get(f"/agent/v1/checkout_sessions/{s['id']}")
    r = app_client.get(f"/dashboard/sessions/{s['id']}")
    assert "Refund" in r.text
    r = app_client.post(f"/dashboard/sessions/{s['id']}/refund",
                        data={"amount": "5", "reason": "goodwill", "reference": "gw-1"}, follow_redirects=False)
    assert r.status_code == 303
    assert world.sessions.get_any(s["id"])["refunds"][0]["amount_paise"] == 500
    r = app_client.post(f"/dashboard/sessions/{s['id']}/refund",
                        data={"amount": "99999", "reason": "too much", "reference": "gw-2"}, follow_redirects=False)
    assert r.status_code == 303 and "RF02" in r.headers["location"]
    r = app_client.post(f"/dashboard/sessions/{s['id']}/refund",
                        data={"amount": "abc", "reason": "x", "reference": "gw-3"}, follow_redirects=False)
    assert r.status_code == 303 and "number" in r.headers["location"]


def test_ledger_replay_button(app_client, world):
    login(app_client)
    app_client.post("/agent/v1/checkout_sessions", json={"items": SHOES}, headers={"Idempotency-Key": "k"})
    r = app_client.post("/dashboard/ledger/replay")
    assert r.status_code == 200 and "identical" in r.text
