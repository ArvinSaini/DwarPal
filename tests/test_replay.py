import json

from agentgate.ledger import canonical
from agentgate.replay import ReplayReport, render_report, replay

SHOES = [{"id": "prod_shoes", "quantity": 1}]
WATCH = [{"id": "prod_watch", "quantity": 1}]


def exercise(world):
    """Create denied, update, complete, failed payment then paid, a review, and a refund."""
    world.payments.outcomes = ["failed", "paid"]
    s = world.sessions.create(world.agent, WATCH, "k1", "h1")
    world.sessions.update(world.agent, s["id"], SHOES)
    world.sessions.complete(world.agent, s["id"], "c1")
    world.sessions.reconcile(s["id"])  # failed -> retry decision -> fresh link
    world.sessions.reconcile(s["id"])  # paid
    world.sessions.refund(s["id"], 500, "goodwill", "gw-1")
    world.policies.set(dict(world.policies.get(), review_above_paise=200000))
    r = world.sessions.create(world.agent, SHOES, "k2", "h2")
    world.sessions.approve_review(r["id"], "fine")
    return s["id"], r["id"]


def test_replay_reproduces_every_recorded_decision(world):
    exercise(world)
    report = replay(world.ledger)
    assert isinstance(report, ReplayReport)
    assert report.decisions == 7 and report.identical == report.decisions and report.divergences == []
    assert report.skipped == 0
    md = render_report(report)
    assert "identical" in md


def test_replay_detects_a_changed_recorded_input(world):
    exercise(world)
    row = world.conn.execute("select seq, payload from ledger where type = 'gate.decision' order by seq limit 1").fetchone()
    payload = json.loads(row["payload"])
    payload["input"]["items"][0]["quantity"] = 3  # the recorded reasoning no longer matches its input
    world.conn.execute("update ledger set payload = ? where seq = ?", (canonical(payload), row["seq"]))
    report = replay(world.ledger)
    assert report.identical == report.decisions - 1
    d = report.divergences[0]
    assert d["seq"] == row["seq"] and d["type"] == "gate.decision" and d["field"] in ("total_paise", "verdict", "reason", "checks")


def test_replay_skips_decisions_without_recorded_input(world):
    world.ledger.append("gate.decision", "gate", {"verdict": "ALLOW", "rule_id": "ALLOW", "reason": "legacy"})
    report = replay(world.ledger)
    assert report.decisions == 0 and report.skipped == 1


def test_replay_of_refund_decisions(world):
    exercise(world)
    row = world.conn.execute("select seq, payload from ledger where type = 'refund.decision' order by seq desc limit 1").fetchone()
    payload = json.loads(row["payload"])
    payload["input"]["amount_paise"] = 10 ** 9
    world.conn.execute("update ledger set payload = ? where seq = ?", (canonical(payload), row["seq"]))
    report = replay(world.ledger)
    assert any(d["seq"] == row["seq"] and d["type"] == "refund.decision" for d in report.divergences)
