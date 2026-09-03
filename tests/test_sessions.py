import pytest

from agentgate.sessions import CANCELED, COMPLETED, NOT_READY, PENDING, READY, SessionError

SHOES = [{"id": "prod_shoes", "quantity": 1}]
WATCH = [{"id": "prod_watch", "quantity": 1}]


def types(world, sid):
    return [e.type for e in world.ledger.events(session_id=sid)]


def create(world, items=SHOES, key="k1"):
    return world.sessions.create(world.agent, items, key, "hash-" + key)


def pending(world, items=SHOES, key="k1", ckey="c1"):
    s = create(world, items, key)
    return world.sessions.complete(world.agent, s["id"], ckey)


# -- create / update ------------------------------------------------------------------------------

def test_create_allowed(world):
    s = create(world)
    assert s["id"].startswith("cs_") and s["status"] == READY and s["currency"] == "INR"
    assert s["decision"]["verdict"] == "ALLOW" and s["totals"]["total_paise"] == 249900
    assert s["line_items"][0]["title"] == "Trail Running Shoes" and s["line_items"][0]["line_total_paise"] == 249900
    assert s["offers"] and all(o["price_paise"] <= 400000 - 249900 for o in s["offers"])
    assert s["payment"] is None and s["mandate_id"] == world.mandate.id and s["agent_id"] == world.agent.id
    assert s["messages"] == [] and s["attempt"] == 0 and s["links"]["trail"].endswith(f"/{s['id']}/trail")
    assert types(world, s["id"]) == ["session.created", "gate.decision", "crosssell.offered"]


def test_create_denied(world):
    s = create(world, WATCH)
    assert s["status"] == NOT_READY and s["offers"] == []
    msg = s["messages"][0]
    assert msg["type"] == "error" and msg["code"] == "policy_denied" and msg["rule_id"] == "G06_MERCHANT_CATEGORY"
    assert "electronics" in msg["text"]
    assert types(world, s["id"]) == ["session.created", "gate.decision"]


def test_create_is_idempotent_per_agent_and_key(world):
    a = create(world, key="same")
    b = create(world, key="same")
    assert a["id"] == b["id"] and len(world.sessions.list()) == 1
    with pytest.raises(SessionError) as ei:
        world.sessions.create(world.agent, WATCH, "same", "other-hash")
    assert ei.value.status_code == 409 and ei.value.code == "request_not_idempotent"
    other, _ = world.agents.register("other")
    world.mandates.create(other.id, 400000, 800000, 2000000, [], world.clock.now + 86400)
    c = world.sessions.create(other, SHOES, "same", "hash-same")
    assert c["id"] != a["id"]


def test_update_replans_from_denied(world):
    s = create(world, WATCH)
    s2 = world.sessions.update(world.agent, s["id"], SHOES)
    assert s2["id"] == s["id"] and s2["status"] == READY and s2["messages"] == []
    t = types(world, s["id"])
    assert t[:2] == ["session.created", "gate.decision"]
    assert "session.updated" in t and t.count("gate.decision") == 2 and t[-1] == "crosssell.offered"


def test_update_accepting_offer_logs_acceptance(world):
    s = create(world)
    offer_id = s["offers"][0]["id"]
    s2 = world.sessions.update(world.agent, s["id"], SHOES + [{"id": offer_id, "quantity": 1}])
    assert s2["status"] == READY and {l["id"] for l in s2["line_items"]} == {"prod_shoes", offer_id}
    accepted = [e for e in world.ledger.events(session_id=s["id"]) if e.type == "crosssell.accepted"]
    assert accepted and accepted[0].payload["offer_ids"] == [offer_id]
    assert all(o["id"] != offer_id for o in s2["offers"])


def test_update_in_wrong_state(world):
    p = pending(world)
    with pytest.raises(SessionError) as ei:
        world.sessions.update(world.agent, p["id"], SHOES)
    assert ei.value.status_code == 409 and ei.value.code == "wrong_state"


def test_other_agent_and_unknown_session_are_404(world):
    s = create(world)
    other, _ = world.agents.register("other")
    with pytest.raises(SessionError) as ei:
        world.sessions.get(other, s["id"])
    assert ei.value.status_code == 404 and ei.value.code == "not_found"
    with pytest.raises(SessionError):
        world.sessions.get(world.agent, "cs_missing")


# -- complete -------------------------------------------------------------------------------------

def test_complete_allowed_reserves_and_creates_link(world):
    s = create(world)
    c = world.sessions.complete(world.agent, s["id"], "c1")
    assert c["status"] == PENDING and c["attempt"] == 1
    pay = c["payment"]
    assert pay["provider"] == "razorpay" and pay["method"] == "payment_link" and pay["status"] == "pending"
    assert pay["url"].startswith("https://") and pay["amount_paise"] == 249900 and pay["link_id"] == "plink_fake001"
    assert pay["expires_at"] == world.clock.now + 1200
    r = world.mandates.open_for(s["id"])
    assert r and r.amount_paise == 249900 and r.mandate_id == world.mandate.id
    t = types(world, s["id"])
    assert t[-2:] == ["mandate.reserved", "payment.link.created"] and t.count("gate.decision") == 2
    assert world.payments.created[0].link_id == "plink_fake001"
    assert world.payments.links["plink_fake001"]["req"].reference_id == f"{s['id']}-1"


def test_complete_when_not_ready(world):
    s = create(world, WATCH)
    with pytest.raises(SessionError) as ei:
        world.sessions.complete(world.agent, s["id"], "c1")
    assert ei.value.status_code == 409 and ei.value.code == "wrong_state"
    assert world.payments.created == []


def test_complete_denied_by_authoritative_gate_run(world):
    s = create(world)
    world.mandates.revoke(world.mandate.id)
    with pytest.raises(SessionError) as ei:
        world.sessions.complete(world.agent, s["id"], "c1")
    assert ei.value.status_code == 409 and ei.value.code == "policy_denied"
    assert ei.value.extra["rule_id"] == "G02_MANDATE_ACTIVE"
    g = world.sessions.get(world.agent, s["id"])
    assert g["status"] == NOT_READY and g["messages"][0]["rule_id"] == "G02_MANDATE_ACTIVE"
    assert world.mandates.open_for(s["id"]) is None and world.payments.created == []


def test_complete_provider_error_releases_and_stays_ready(world):
    s = create(world)
    world.payments.fail_create = True
    with pytest.raises(SessionError) as ei:
        world.sessions.complete(world.agent, s["id"], "c1")
    assert ei.value.status_code == 502 and ei.value.code == "payment_provider_error"
    g = world.sessions.get(world.agent, s["id"])
    assert g["status"] == READY and g["payment"] is None
    assert world.mandates.open_for(s["id"]) is None
    t = types(world, s["id"])
    assert "provider.error" in t and t[-1] == "mandate.released"
    world.payments.fail_create = False
    assert world.sessions.complete(world.agent, s["id"], "c2")["status"] == PENDING


def test_complete_replay_returns_same_link(world):
    s = create(world)
    a = world.sessions.complete(world.agent, s["id"], "c1")
    b = world.sessions.complete(world.agent, s["id"], "c1")
    assert a["payment"]["link_id"] == b["payment"]["link_id"] and len(world.payments.created) == 1
    with pytest.raises(SessionError) as ei:
        world.sessions.complete(world.agent, s["id"], "c2")
    assert ei.value.code == "wrong_state"


# -- reconcile: capture ---------------------------------------------------------------------------

def test_reconcile_paid_completes_and_commits(world):
    p = pending(world)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == COMPLETED and r["payment"]["status"] == "captured"
    assert r["attempts"] == [{"razorpay_payment_id": "pay_plink_fake001_ok", "status": "captured",
                              "amount_paise": 249900, "error_code": None, "error_description": None, "attempt": 1}]
    assert [x.state for x in world.mandates.reservations_for(p["id"])] == ["committed"]
    t = types(world, p["id"])
    assert t[-3:] == ["payment.captured", "mandate.committed", "session.completed"]
    n = len(t)
    assert world.sessions.reconcile(p["id"])["status"] == COMPLETED
    assert len(types(world, p["id"])) == n


def test_get_triggers_reconcile_only_when_asked(world):
    p = pending(world)
    assert world.sessions.get(world.agent, p["id"], reconcile=False)["status"] == PENDING
    assert world.sessions.get(world.agent, p["id"])["status"] == COMPLETED


def test_reconcile_pending_adds_nothing(world):
    world.payments.outcomes = ["pending"]
    p = pending(world)
    n = len(types(world, p["id"]))
    assert world.sessions.reconcile(p["id"])["status"] == PENDING
    assert len(types(world, p["id"])) == n


def test_reconcile_all_returns_touched_sessions(world):
    world.payments.outcomes = ["paid", "pending"]
    a = pending(world, key="k1", ckey="c1")
    b = pending(world, key="k2", ckey="c2")
    touched = world.sessions.reconcile_all()
    assert touched == [a["id"]]
    assert world.sessions.get_any(a["id"])["status"] == COMPLETED
    assert world.sessions.get_any(b["id"])["status"] == PENDING


# -- reconcile: failure recovery ------------------------------------------------------------------

def test_failed_first_attempt_retries_with_fresh_link(world):
    world.payments.outcomes = ["failed", "paid"]
    p = pending(world)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == PENDING and r["attempt"] == 2 and r["payment"]["link_id"] == "plink_fake002"
    assert r["attempts"][0]["status"] == "failed" and r["attempts"][0]["attempt"] == 1
    t = types(world, p["id"])
    i = t.index("payment.failed")
    assert t[i:] == ["payment.failed", "gate.decision", "payment.link.cancelled", "payment.retry", "payment.link.created"]
    assert world.payments.cancelled == ["plink_fake001"]
    r2 = world.sessions.reconcile(p["id"])
    assert r2["status"] == COMPLETED and len(r2["attempts"]) == 2 and r2["attempts"][1]["attempt"] == 2
    assert [x.state for x in world.mandates.reservations_for(p["id"])] == ["committed"]


def test_failed_twice_abandons_and_releases(world):
    world.payments.outcomes = ["failed", "failed"]
    p = pending(world)
    world.sessions.reconcile(p["id"])
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == CANCELED and r["attempt"] == 2 and r["payment"]["status"] == "abandoned"
    assert [x.state for x in world.mandates.reservations_for(p["id"])] == ["released"]
    t = types(world, p["id"])
    assert t.count("payment.failed") == 2
    assert t[-4:] == ["payment.link.cancelled", "mandate.released", "payment.abandoned", "session.canceled"]
    abandoned = [e for e in world.ledger.events(session_id=p["id"]) if e.type == "payment.abandoned"][0]
    assert abandoned.payload["attempts"] == 2 and abandoned.payload["reason"] == "payment_failed"


def test_retry_denied_by_gate_abandons(world):
    world.payments.outcomes = ["failed"]
    p = pending(world)
    world.mandates.revoke(world.mandate.id)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == CANCELED
    abandoned = [e for e in world.ledger.events(session_id=p["id"]) if e.type == "payment.abandoned"][0]
    assert "G02_MANDATE_ACTIVE" in abandoned.payload["reason"]
    assert [x.state for x in world.mandates.reservations_for(p["id"])] == ["released"]


def test_expired_link_counts_as_failed_attempt(world):
    world.payments.outcomes = ["expired", "paid"]
    p = pending(world)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == PENDING and r["attempt"] == 2
    retry = [e for e in world.ledger.events(session_id=p["id"]) if e.type == "payment.retry"][0]
    assert retry.payload["reason"] == "link_expired"


def test_link_ttl_elapsed_counts_as_failed_attempt(world):
    world.payments.outcomes = ["pending", "paid"]
    p = pending(world)
    world.clock.tick(1201)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == PENDING and r["attempt"] == 2
    assert world.sessions.reconcile(p["id"])["status"] == COMPLETED


def test_poll_error_is_recorded_and_session_stays_pending(world):
    world.payments.outcomes = ["error"]
    p = pending(world)
    r = world.sessions.reconcile(p["id"])
    assert r["status"] == PENDING
    assert types(world, p["id"])[-1] == "provider.error"


# -- cancel ---------------------------------------------------------------------------------------

def test_cancel_by_agent(world):
    p = pending(world)
    c = world.sessions.cancel(world.agent, p["id"])
    assert c["status"] == CANCELED and c["payment"]["status"] == "cancelled"
    assert world.payments.cancelled == ["plink_fake001"]
    assert [x.state for x in world.mandates.reservations_for(p["id"])] == ["released"]
    assert types(world, p["id"])[-3:] == ["payment.link.cancelled", "mandate.released", "session.canceled"]
    with pytest.raises(SessionError) as ei:
        world.sessions.cancel(world.agent, p["id"])
    assert ei.value.status_code == 409 and ei.value.code == "wrong_state"
    s2 = create(world, key="k2")
    assert world.sessions.cancel(world.agent, s2["id"])["status"] == CANCELED
    assert len(world.payments.cancelled) == 1


def test_cancel_when_provider_cancel_fails(world):
    p = pending(world)
    world.payments.fail_cancel = True
    c = world.sessions.cancel(world.agent, p["id"])  # a late capture exists: the honest outcome is completed
    assert c["status"] == COMPLETED
    assert "payment.link.cancel_failed" not in types(world, p["id"])
    world.payments.outcomes = ["pending"]
    p2 = pending(world, key="k2", ckey="c2")
    with pytest.raises(SessionError) as ei:
        world.sessions.cancel(world.agent, p2["id"])
    assert ei.value.status_code == 502 and ei.value.code == "cancel_failed"
    assert world.sessions.get(world.agent, p2["id"], reconcile=False)["status"] == PENDING
    assert types(world, p2["id"])[-1] == "payment.link.cancel_failed"


# -- accounting across sessions, trail, merchant views --------------------------------------------

def test_spend_accounting_across_sessions(world):
    world.mandate = world.mandates.create(world.agent.id, 400000, 800000, 300000, [], world.clock.now + 86400)
    p = pending(world)
    world.sessions.reconcile(p["id"])
    s2 = create(world, key="k2")
    assert s2["status"] == NOT_READY and s2["messages"][0]["rule_id"] == "G12_TOTAL_CAP"


def test_reserved_but_unpaid_spend_counts_too(world):
    world.mandate = world.mandates.create(world.agent.id, 400000, 400000, 2000000, [], world.clock.now + 86400)
    world.payments.outcomes = ["pending"]
    pending(world)
    s2 = create(world, key="k2")
    assert s2["status"] == NOT_READY and s2["messages"][0]["rule_id"] == "G11_DAILY_CAP"


def test_trail(world):
    s = create(world)
    t = world.sessions.trail(world.agent, s["id"])
    assert t["session_id"] == s["id"] and t["events"][0]["type"] == "session.created"
    assert t["events"][0]["hash"] and t["verify"]["ok"] and t["head"] == world.ledger.head()


def test_list_get_any_and_merchant_cancel(world):
    s = create(world)
    assert [x["id"] for x in world.sessions.list()] == [s["id"]]
    assert world.sessions.get_any("cs_nope") is None
    assert world.sessions.cancel_by_merchant(s["id"])["status"] == CANCELED
    assert types(world, s["id"])[-1] == "session.canceled"
