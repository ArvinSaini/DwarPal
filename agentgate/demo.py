"""Demo scenarios: register a fresh agent with a mandate, run the buyer agent in-process, print the trail."""
from __future__ import annotations

import time
from typing import Callable

from agentgate.buyer.agent import BuyerAgent, RunResult
from agentgate.buyer.client import GateClient
from agentgate.buyer.planner import Action, LLMPlanner, ScriptedPlanner
from agentgate.ledger import canonical
from agentgate.money import rupees
from agentgate.payments import FakePayments

SCENARIOS = ("happy", "refused", "replan", "payfail", "crosssell")

INTENTS = {
    "happy": "Buy me trail running shoes and a steel water bottle for my runs. Budget 4,000 rupees.",
    "refused": "Buy me a GPS running smartwatch. Budget 8,000 rupees.",
    "replan": "Buy me a GPS running smartwatch; if the store will not sell it to you, get trail running shoes instead. "
              "Budget 4,000 rupees.",
    "payfail": "Buy me trail running shoes. Budget 3,000 rupees.",
    "crosssell": "Buy me trail running shoes and whatever the store suggests goes with them. Budget 3,500 rupees.",
}
BUDGETS = {"happy": 400000, "refused": 800000, "replan": 400000, "payfail": 300000, "crosssell": 350000}

SHOES = [{"id": "prod_shoes", "quantity": 1}]
BOTTLE = [{"id": "prod_bottle", "quantity": 1}]
WATCH = [{"id": "prod_watch", "quantity": 1}]

# Demo mandate for every scenario: INR 4,000 per order, 8,000 per day, 20,000 total, any category.
MANDATE = dict(per_txn_cap_paise=400000, daily_cap_paise=800000, total_cap_paise=2000000, categories=[])


def scripted_plan(scenario: str) -> list[Action]:
    if scenario == "happy":
        return [Action("list_products", say="Looking for running shoes and a bottle."),
                Action("create_checkout_session", {"items": SHOES + BOTTLE}),
                Action("complete_checkout_session", {"session_id": "$session"}),
                Action("done", say="Ordered trail shoes and a steel bottle; the user pays at the link.")]
    if scenario == "refused":
        return [Action("list_products", say="Looking for a GPS smartwatch."),
                Action("create_checkout_session", {"items": WATCH}),
                Action("done", say="The store will not sell electronics to agents; nothing was bought.")]
    if scenario == "replan":
        return [Action("list_products", say="Trying the smartwatch first."),
                Action("create_checkout_session", {"items": WATCH}),
                Action("update_checkout_session", {"session_id": "$session", "items": SHOES},
                       say="Refused, so switching to the trail shoes within the mandate."),
                Action("complete_checkout_session", {"session_id": "$session"}),
                Action("done", say="Bought the trail shoes instead; the user pays at the link.")]
    if scenario == "payfail":
        return [Action("list_products", say="Looking for trail running shoes."),
                Action("create_checkout_session", {"items": SHOES}),
                Action("complete_checkout_session", {"session_id": "$session"}),
                Action("done", say="Ordered the trail shoes; the user pays at the link.")]
    if scenario == "crosssell":
        return [Action("list_products", say="Looking for trail running shoes."),
                Action("create_checkout_session", {"items": SHOES}),
                Action("update_checkout_session", {"session_id": "$session",
                                                   "items": SHOES + [{"id": "$offer0", "quantity": 1}]},
                       say="Taking the store's first suggested add-on."),
                Action("complete_checkout_session", {"session_id": "$session"}),
                Action("done", say="Ordered the shoes plus the suggested add-on; the user pays at the link.")]
    raise ValueError(f"unknown scenario {scenario!r}; choose one of {SCENARIOS}")


def summarise_event(e: dict) -> str:
    p = e.get("payload") or {}
    t = e.get("type", "")
    if t == "gate.decision":
        return f"{p.get('verdict')} {p.get('rule_id')} ({p.get('mode')}): {str(p.get('reason'))[:70]}"
    if t == "payment.link.created":
        return f"attempt {p.get('attempt')} {rupees(p.get('amount_paise', 0))} {p.get('url')}"
    if t in ("payment.failed", "payment.captured"):
        return f"{p.get('razorpay_payment_id')} {rupees(p.get('amount_paise', 0))} {p.get('error_code') or ''}".strip()
    if t.startswith("mandate."):
        return f"{rupees(p.get('amount_paise', 0))} {p.get('reason') or ''}".strip() if "amount_paise" in p else canonical(p)[:70]
    if t == "crosssell.offered":
        return ", ".join(o.get("title", "?") for o in p.get("offers", []))
    if t in ("payment.retry", "payment.abandoned", "session.canceled"):
        return f"{p.get('reason', '')}"[:70]
    return canonical(p)[:70]


def print_trail(ctx, session_id: str, printer: Callable[[str], None]) -> None:
    trail = ctx.sessions.trail_any(session_id)
    ok = trail["verify"]["ok"]
    printer(f"Ledger trail for {session_id}: {len(trail['events'])} events, chain {'verified' if ok else 'BROKEN'}")
    for e in trail["events"]:
        printer(f"  {e['seq']:>4}  {e['type']:<28} {e['actor']:<16} {summarise_event(e)}")
    printer(f"  head {trail['head']}")


def run_demo(ctx, scenario: str, planner: str = "scripted", llm=None, wait_s: int = 0,
             printer: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
             poll_every_s: int = 3, max_steps: int = 12) -> RunResult:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose one of {SCENARIOS}")
    from fastapi.testclient import TestClient

    from agentgate.api import create_app

    agent, key = ctx.agents.register(f"demo-{scenario}")
    ctx.ledger.append("agent.registered", "merchant", {"agent_id": agent.id, "name": agent.name})
    mandate = ctx.mandates.create(agent.id, expires_at=ctx.clock() + 7 * 86400, **MANDATE)
    ctx.ledger.append("mandate.created", "merchant", mandate.to_dict())
    if scenario == "payfail" and isinstance(ctx.payments, FakePayments):
        ctx.payments.outcomes = ["failed", "paid"]

    printer(f"Scenario: {scenario}")
    printer(f"Intent:   {INTENTS[scenario]}")
    printer(f"Agent:    {agent.id} ({agent.name}), mandate {rupees(mandate.per_txn_cap_paise)} per order, "
            f"{rupees(mandate.daily_cap_paise)} per day, {rupees(mandate.total_cap_paise)} total")
    printer(f"Payments: {ctx.payments_mode}; planner: {planner}")
    printer("")

    client = GateClient("", key, http=TestClient(create_app(ctx)))
    if planner == "llm":
        if llm is None:
            raise ValueError("planner='llm' needs an LLM client")
        plan = LLMPlanner(llm, INTENTS[scenario], BUDGETS[scenario])
    else:
        plan = ScriptedPlanner(scripted_plan(scenario))
    runner = BuyerAgent(client, plan, clock=ctx.clock, sleep=sleep, wait_for_payment_s=wait_s,
                        poll_every_s=poll_every_s, printer=lambda line: printer("  " + line))
    result = runner.run(max_steps=max_steps)
    printer("")
    printer(f"Outcome: {result.outcome}")
    if result.session_id:
        printer("")
        print_trail(ctx, result.session_id, printer)
    return result
