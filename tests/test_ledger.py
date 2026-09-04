import hashlib

import pytest

from dwarpal.ledger import GENESIS, Ledger, canonical, event_hash


@pytest.fixture
def ledger(conn, clock):
    return Ledger(conn, clock)


def test_first_event_links_to_genesis(ledger):
    e = ledger.append("agent.registered", "merchant", {"agent_id": "agt_1"})
    assert e.seq == 1 and e.prev_hash == GENESIS
    body = {"seq": 1, "id": e.id, "ts": e.ts, "type": "agent.registered", "actor": "merchant",
            "session_id": None, "payload": {"agent_id": "agt_1"}}
    assert e.hash == hashlib.sha256((GENESIS + canonical(body)).encode()).hexdigest()
    assert e.hash == event_hash(GENESIS, body)


def test_chain_links_and_verifies(ledger):
    a = ledger.append("a", "x", {"n": 1})
    b = ledger.append("b", "x", {"n": 2}, session_id="cs_1")
    assert b.prev_hash == a.hash
    r = ledger.verify()
    assert r.ok and r.count == 2 and r.bad_seq is None
    assert ledger.head() == b.hash


def test_empty_ledger_verifies_with_genesis_head(ledger):
    assert ledger.head() == GENESIS
    r = ledger.verify()
    assert r.ok and r.count == 0


def test_events_filter_by_session(ledger):
    ledger.append("a", "x", {}, session_id="cs_1")
    ledger.append("b", "x", {}, session_id="cs_2")
    ledger.append("c", "x", {}, session_id="cs_1")
    assert [e.type for e in ledger.events(session_id="cs_1")] == ["a", "c"]
    assert [e.type for e in ledger.events()] == ["a", "b", "c"]
    assert [e.type for e in ledger.events(limit=2)] == ["a", "b"]


def test_tamper_breaks_verify_at_that_seq(ledger):
    ledger.append("a", "x", {"amount_paise": 100})
    ledger.append("b", "x", {"amount_paise": 200})
    ledger.append("c", "x", {"amount_paise": 300})
    ledger.tamper(2)
    r = ledger.verify()
    assert not r.ok and r.bad_seq == 2
    assert ledger.events()[1].payload["amount_paise"] == 2000


def test_tamper_requires_an_amount_field(ledger):
    ledger.append("a", "x", {"note": "nothing to tamper"})
    with pytest.raises(ValueError):
        ledger.tamper(1)
    with pytest.raises(ValueError):
        ledger.tamper(99)


def test_payload_is_normalised_before_hashing(ledger):
    e = ledger.append("a", "x", {"t": (1, 2)})  # tuple becomes list through JSON
    assert e.payload == {"t": [1, 2]}
    assert ledger.verify().ok


def test_receipt_mentions_session_events(ledger):
    ledger.append("session.created", "sessions", {"total_paise": 300000, "items": []}, session_id="cs_9")
    ledger.append(
        "gate.decision", "gate",
        {"verdict": "ALLOW", "rule_id": "ALLOW", "reason": "ok",
         "checks": [{"rule": "G00_WELL_FORMED", "ok": True, "detail": "fine"}]},
        session_id="cs_9",
    )
    ledger.append("payment.captured", "reconciler", {"razorpay_payment_id": "pay_1", "amount_paise": 300000},
                  session_id="cs_9")
    md = ledger.receipt("cs_9")
    assert "cs_9" in md and "G00_WELL_FORMED" in md and "pay_1" in md
    assert "Chain: verified" in md
    ledger.tamper(1)
    assert "Chain: BROKEN at seq 1" in ledger.receipt("cs_9")


def test_receipt_for_unknown_session_raises(ledger):
    with pytest.raises(ValueError):
        ledger.receipt("cs_missing")


def test_tamper_without_a_seq_picks_the_first_event_carrying_an_amount(ledger):
    ledger.append("agent.registered", "merchant", {"agent_id": "agt_1"})       # nothing to tamper
    ledger.append("session.created", "sessions", {"status": "ready"})          # nothing to tamper
    ledger.append("mandate.reserved", "sessions", {"amount_paise": 100})       # the first candidate
    ledger.append("payment.captured", "reconciler", {"amount_paise": 200})
    assert ledger.first_tamperable_seq() == 3
    assert ledger.tamper() == 3
    assert ledger.events()[2].payload["amount_paise"] == 1000
    r = ledger.verify()
    assert not r.ok and r.bad_seq == 3


def test_tamper_without_a_seq_raises_when_nothing_carries_an_amount(ledger):
    ledger.append("agent.registered", "merchant", {"agent_id": "agt_1"})
    assert ledger.first_tamperable_seq() is None
    with pytest.raises(ValueError):
        ledger.tamper()


# -- anchoring: the head (seq, hash) kept outside the ledger detects a shortened or re-hashed tail ----

def test_anchor_is_the_head_seq_and_hash(ledger):
    from dwarpal.ledger import Anchor, parse_anchor
    assert ledger.anchor() == Anchor(0, GENESIS)
    a = ledger.append("a", "x", {"n": 1})
    b = ledger.append("b", "x", {"n": 2})
    anchor = ledger.anchor()
    assert anchor == Anchor(2, b.hash)
    assert str(anchor) == f"2:{b.hash}"
    assert parse_anchor(str(anchor)) == anchor


def test_verify_with_anchor_passes_when_the_chain_still_contains_it(ledger):
    ledger.append("a", "x", {"n": 1})
    anchor = ledger.anchor()
    ledger.append("b", "x", {"n": 2})  # the chain may grow past the anchor
    r = ledger.verify(anchor=anchor)
    assert r.ok and r.count == 2 and "anchor" in r.detail


def test_verify_with_anchor_detects_a_truncated_tail(ledger):
    ledger.append("a", "x", {"n": 1})
    ledger.append("b", "x", {"n": 2})
    anchor = ledger.anchor()
    ledger.conn.execute("delete from ledger where seq = 2")
    assert ledger.verify().ok  # plain verification cannot see a cut tail...
    r = ledger.verify(anchor=anchor)  # ...the anchor can
    assert not r.ok and r.bad_seq == 2 and "shorter" in r.detail


def test_verify_with_anchor_detects_a_rewritten_tail(ledger):
    ledger.append("a", "x", {"n": 1})
    ledger.append("b", "x", {"n": 2})
    anchor = ledger.anchor()
    ledger.conn.execute("delete from ledger where seq = 2")
    ledger.append("b", "x", {"n": 999})  # same length, different history
    assert ledger.verify().ok
    r = ledger.verify(anchor=anchor)
    assert not r.ok and r.bad_seq == 2 and "different" in r.detail


def test_parse_anchor_rejects_garbage():
    from dwarpal.ledger import parse_anchor
    for bad in ("", "abc", "2", "x:" + "0" * 64, "2:nothex", "-1:" + "0" * 64, "2:" + "0" * 63):
        with pytest.raises(ValueError):
            parse_anchor(bad)
