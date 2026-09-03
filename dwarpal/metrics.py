"""Batch metrics: scripted sessions against the real gate, real state machine and fake payments.

What this proves: across N sessions with a seeded mix of allowed carts, refused carts, failed first
payments and abandoned payments, no mandate is ever over-committed, every denial names a rule, every
failure ends in a retry or a release, and the ledger chain verifies. What it does not prove: anything
about a particular LLM (the cross-sell picker and the buyer are scripted here).
"""
from __future__ import annotations

import argparse
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dwarpal.agents import AgentStore
from dwarpal.catalog import Catalog, seed
from dwarpal.crosssell import FakePicker
from dwarpal.db import connect, init_db, now_utc_day_bounds
from dwarpal.ledger import Ledger
from dwarpal.mandates import MandateStore
from dwarpal.money import rupees
from dwarpal.payments import FakePayments
from dwarpal.policy import PolicyStore
from dwarpal.sessions import CANCELED, COMPLETED, NOT_READY, PENDING, READY, SessionError, SessionService

SCENARIO_WEIGHTS = [("allowed_paid", 0.55), ("refused", 0.25), ("payfail_then_paid", 0.15), ("abandoned", 0.05)]
MANDATE_DAYS = 7

ALLOWED_CARTS = [
    [{"id": "prod_shoes", "quantity": 1}],
    [{"id": "prod_shoes", "quantity": 1}, {"id": "prod_socks", "quantity": 1}],
    [{"id": "prod_mat", "quantity": 1}, {"id": "prod_bands", "quantity": 1}],
    [{"id": "prod_tee", "quantity": 1}, {"id": "prod_cap", "quantity": 1}],
    [{"id": "prod_bottle", "quantity": 1}, {"id": "prod_gel", "quantity": 1}],
    [{"id": "prod_socks", "quantity": 2}, {"id": "prod_cap", "quantity": 1}],
    [{"id": "prod_tee", "quantity": 1}],
    [{"id": "prod_socks", "quantity": 2}, {"id": "prod_tee", "quantity": 1}],
]
CART_CATEGORIES = {"prod_shoes": "footwear", "prod_socks": "apparel", "prod_tee": "apparel", "prod_mat": "fitness",
                   "prod_bands": "fitness", "prod_cap": "accessories", "prod_bottle": "accessories",
                   "prod_gel": "fitness"}


def allowed_cart_for(mandate, rng: random.Random) -> list[dict]:
    """A cart the agent's mandate categories permit, so an 'allowed' scenario really means allowed."""
    carts = ALLOWED_CARTS
    if mandate.categories:
        carts = [c for c in ALLOWED_CARTS if all(CART_CATEGORIES[i["id"]] in mandate.categories for i in c)] or carts
    return rng.choice(carts)
REFUSED_CARTS = [
    ("G06_MERCHANT_CATEGORY", [{"id": "prod_watch", "quantity": 1}]),
    ("G04_IN_STOCK", [{"id": "prod_brace", "quantity": 1}]),
    ("G07_QTY_PER_LINE", [{"id": "prod_socks", "quantity": 6}]),
    ("G10_PER_TXN_CAP", [{"id": "prod_shoes", "quantity": 2}]),
    ("G03_ITEMS_KNOWN", [{"id": "prod_unicorn", "quantity": 1}]),
]


@dataclass
class Report:
    sessions: int
    seed: int
    scenario_counts: dict = field(default_factory=dict)
    outcomes: dict = field(default_factory=dict)
    denials_by_rule: dict = field(default_factory=dict)
    mandate_overruns: int = 0
    retried: int = 0
    recovered: int = 0
    released: int = 0
    offers_made: int = 0
    offers_accepted: int = 0
    attach_rate: float = 0.0
    avg_basket_with_offer_paise: int = 0
    avg_basket_without_offer_paise: int = 0
    completed_revenue_paise: int = 0
    ledger_ok: bool = False
    ledger_events: int = 0
    agents: int = 0
    replay_decisions: int = 0
    replay_identical: int = 0
    abandoned: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class _Clock:
    def __init__(self, now: int):
        self.now = now

    def __call__(self) -> int:
        return self.now


def _pick_scenario(rng: random.Random) -> str:
    x = rng.random()
    acc = 0.0
    for name, w in SCENARIO_WEIGHTS:
        acc += w
        if x < acc:
            return name
    return SCENARIO_WEIGHTS[-1][0]


def run_batch(n: int = 50, seed: int = 7, start_ts: int = 1_756_900_000) -> Report:
    rng = random.Random(seed)
    clock = _Clock(start_ts)
    conn = connect(":memory:")
    init_db(conn)
    ledger = Ledger(conn, clock)
    catalog = Catalog(conn, clock)
    seed_count = seed_catalog(catalog)
    policies = PolicyStore(conn, clock)
    agents = AgentStore(conn, clock)
    mandates = MandateStore(conn, clock)
    payments = FakePayments()
    sessions = SessionService(conn, catalog, policies, agents, mandates, ledger, payments, FakePicker(), clock)

    # Three agents with weekly mandates, renewed on expiry like a merchant would. The small-budget agent runs
    # its weekly total down on purpose; that is what exercises G12 without letting it dominate the batch.
    roster: list[list] = []
    for name, caps in (("wide-bot", dict(per_txn_cap_paise=400000, daily_cap_paise=5_000_000,
                                          total_cap_paise=30_000_000, categories=[])),
                       ("apparel-bot", dict(per_txn_cap_paise=400000, daily_cap_paise=5_000_000,
                                             total_cap_paise=30_000_000, categories=["footwear", "apparel"])),
                       ("small-bot", dict(per_txn_cap_paise=400000, daily_cap_paise=5_000_000,
                                           total_cap_paise=1_500_000, categories=[]))):
        agent, _ = agents.register(name)
        ledger.append("agent.registered", "merchant", {"agent_id": agent.id, "name": name})
        mandate = mandates.create(agent.id, expires_at=start_ts + MANDATE_DAYS * 86400, **caps)
        ledger.append("mandate.created", "merchant", mandate.to_dict())
        roster.append([agent, mandate, caps])

    report = Report(sessions=n, seed=seed, agents=len(roster))
    scenario_counts: Counter = Counter()
    outcomes: Counter = Counter()
    denials: Counter = Counter()
    per_session: list[dict] = []

    for i in range(n):
        clock.now += 3600  # one order an hour: a batch spans days, so the daily cap is a boundary, not a wall
        for entry in roster:
            if clock.now >= entry[1].expires_at:  # weekly mandate expired: the merchant re-issues it
                entry[1] = mandates.create(entry[0].id, expires_at=clock.now + MANDATE_DAYS * 86400, **entry[2])
                ledger.append("mandate.created", "merchant", {**entry[1].to_dict(), "reason": "weekly renewal"})
        scenario = _pick_scenario(rng)
        scenario_counts[scenario] += 1
        agent, mandate, _caps = rng.choices(roster, weights=[0.5, 0.3, 0.2])[0]
        intended_rule = None
        if scenario == "refused":
            intended_rule, items = rng.choice(REFUSED_CARTS)
        else:
            items = allowed_cart_for(mandate, rng)
        record = {"scenario": scenario, "agent": agent.id, "accepted_offer": False, "intended_rule": intended_rule}
        s = sessions.create(agent, items, f"batch-{seed}-{i}", f"hash-{i}")
        if s["status"] == READY and s["offers"] and scenario != "refused" and rng.random() < 0.5:
            s = sessions.update(agent, s["id"], items + [{"id": s["offers"][0]["id"], "quantity": 1}])
            record["accepted_offer"] = s["status"] == READY
        if s["status"] == READY:
            if scenario == "payfail_then_paid":
                payments.outcomes = ["failed", "paid"]
            elif scenario == "abandoned":
                payments.outcomes = ["failed", "failed"]
            else:
                payments.outcomes = ["paid"]
            try:
                s = sessions.complete(agent, s["id"], f"complete-{i}")
            except SessionError:
                s = sessions.get_any(s["id"])
            for _ in range(4):
                if s["status"] != PENDING:
                    break
                clock.now += 5
                s = sessions.reconcile(s["id"])
        record["session"] = s
        per_session.append(record)
        outcomes[_outcome(s)] += 1
        if s["status"] == NOT_READY:
            denials[(s["messages"] or [{}])[0].get("rule_id", "unknown")] += 1

    # Invariants: no mandate over-committed, per transaction, per day, or in total (every mandate ever issued).
    overruns = 0
    for mandate in mandates.all():
        rows = conn.execute("select session_id, amount_paise, created_at from reservations "
                            "where mandate_id = ? and state in ('reserved', 'committed')", (mandate.id,)).fetchall()
        if sum(r["amount_paise"] for r in rows) > mandate.total_cap_paise:
            overruns += 1
        if any(r["amount_paise"] > mandate.per_txn_cap_paise for r in rows):
            overruns += 1
        by_day: Counter = Counter()
        for r in rows:
            by_day[now_utc_day_bounds(r["created_at"])[0]] += r["amount_paise"]
        if any(v > mandate.daily_cap_paise for v in by_day.values()):
            overruns += 1
        if mandates.spent(mandate.id, clock.now)[0] > mandate.total_cap_paise:
            overruns += 1

    # Failure and cross-sell accounting from the ledger.
    retried = recovered = released = offered = accepted = 0
    with_offer: list[int] = []
    without_offer: list[int] = []
    revenue = 0
    for rec in per_session:
        s = rec["session"]
        types = {e.type for e in ledger.events(session_id=s["id"])}
        if "payment.retry" in types:
            retried += 1
            if s["status"] == COMPLETED:
                recovered += 1
        if "mandate.released" in types:
            released += 1
        if "crosssell.offered" in types:
            offered += 1
        if "crosssell.accepted" in types:
            accepted += 1
        if s["status"] == COMPLETED:
            total = s["totals"]["total_paise"]
            revenue += total
            (with_offer if "crosssell.accepted" in types else without_offer).append(total)

    verify = ledger.verify()
    from dwarpal.replay import replay as replay_ledger

    rep = replay_ledger(ledger)
    report.replay_decisions, report.replay_identical = rep.decisions, rep.identical
    report.abandoned = sum(1 for rec in per_session if rec["session"]["status"] == CANCELED)
    report.scenario_counts = dict(scenario_counts)
    report.outcomes = dict(outcomes)
    report.denials_by_rule = dict(sorted(denials.items()))
    report.mandate_overruns = overruns
    report.retried, report.recovered, report.released = retried, recovered, released
    report.offers_made, report.offers_accepted = offered, accepted
    report.attach_rate = round(accepted / offered, 3) if offered else 0.0
    report.avg_basket_with_offer_paise = int(sum(with_offer) / len(with_offer)) if with_offer else 0
    report.avg_basket_without_offer_paise = int(sum(without_offer) / len(without_offer)) if without_offer else 0
    report.completed_revenue_paise = revenue
    report.ledger_ok, report.ledger_events = verify.ok, verify.count
    conn.close()
    return report


def seed_catalog(catalog: Catalog) -> int:
    return seed(catalog)


def _outcome(s: dict) -> str:
    if s["status"] == COMPLETED:
        return "completed"
    if s["status"] == NOT_READY:
        return "refused"
    if s["status"] == CANCELED:
        return "canceled"
    return s["status"]


def render_markdown(r: Report) -> str:
    lines = [
        "# Dwarpal batch metrics", "",
        f"{r.sessions} sessions, seed {r.seed}, {r.agents} agents, scripted carts against the real gate and "
        f"state machine with fake payments. Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.", "",
        "## Outcomes", "", "| Outcome | Sessions |", "|---|---|",
    ]
    for k, v in sorted(r.outcomes.items()):
        lines.append(f"| {k} | {v} |")
    lines += ["", "Intended scenario mix: " + ", ".join(f"{k} {v}" for k, v in sorted(r.scenario_counts.items())) + ".", "",
              "## Denials by rule (every denial names its rule)", "", "| Rule | Count |", "|---|---|"]
    for k, v in r.denials_by_rule.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Bounded", "", f"- Mandate overruns (committed + reserved vs per-transaction, daily and total caps): "
              f"**{r.mandate_overruns}**", "",
              "## Failure recovery", "",
              f"- First payment attempt failed and a fresh link was issued: {r.retried}",
              f"- Of those, paid on the second attempt: {r.recovered}",
              f"- Sessions whose reserved budget was released (cancel, abandon, provider error): {r.released}", "",
              "## Cross-sell", "",
              f"- Sessions with an offer: {r.offers_made}",
              f"- Offers accepted: {r.offers_accepted}",
              f"- Attach rate: **{r.attach_rate:.0%}**",
              f"- Average completed basket with an accepted offer: {rupees(r.avg_basket_with_offer_paise)}",
              f"- Average completed basket without: {rupees(r.avg_basket_without_offer_paise)}",
              f"- Completed revenue: {rupees(r.completed_revenue_paise)}", "",
              "## Audit", "",
              f"- Ledger chain: {'verified' if r.ledger_ok else 'BROKEN'} ({r.ledger_events} events)",
              f"- Decisions replayed from their recorded inputs: {r.replay_identical} / {r.replay_decisions} identical", "",
              "## What this does and does not prove", "",
              "These are scripted inputs run through deterministic code, so zero overruns and fully explained "
              "denials are expected by construction; the evidence is that the failure paths really fire and "
              "that the accounting holds across many sessions. Nothing here measures a particular language "
              "model: the cross-sell picker and the buyer are scripted in this batch.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run a scripted batch and write a Markdown metrics report.")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None, help="output path (default runs/metrics-<timestamp>.md)")
    args = ap.parse_args(argv)
    report = run_batch(args.n, args.seed)
    md = render_markdown(report)
    out = Path(args.out) if args.out else Path("runs") / f"metrics-{int(time.time())}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"(written to {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
