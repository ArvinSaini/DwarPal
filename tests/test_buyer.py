import pytest

from dwarpal.buyer.agent import BuyerAgent
from dwarpal.buyer.client import GateAPIError, GateClient
from dwarpal.buyer.planner import TOOLS, Action, LLMPlanner, ScriptedPlanner
from dwarpal.llm import FakeLLM, LLMError
from tests.conftest import make_client

SHOES = [{"id": "prod_shoes", "quantity": 1}]
BOTTLE = [{"id": "prod_bottle", "quantity": 1}]
WATCH = [{"id": "prod_watch", "quantity": 1}]


@pytest.fixture
def gate(world):
    http = make_client(world)
    http.headers.pop("Authorization")  # the GateClient must add its own auth
    return GateClient("", world.api_key, http=http)


def agent_for(gate, plan, **kw):
    return BuyerAgent(gate, ScriptedPlanner(plan), **kw)


def test_client_calls_and_errors(gate):
    assert gate.discovery()["merchant_id"] == "trail-and-turf"
    assert len(gate.products()) == 10 and gate.products(category="footwear")[0]["id"] == "prod_shoes"
    with pytest.raises(GateAPIError) as ei:
        gate.get("cs_nope")
    assert ei.value.status_code == 404 and ei.value.body["code"] == "not_found"
    s = gate.create(SHOES)
    assert s["status"] == "ready_for_payment"
    assert gate.create(SHOES, idempotency_key="fixed")["id"] == gate.create(SHOES, idempotency_key="fixed")["id"]
    assert gate.update(s["id"], WATCH)["status"] == "not_ready_for_payment"
    assert gate.cancel(s["id"])["status"] == "canceled"
    assert gate.trail(s["id"])["verify"]["ok"]


def test_happy_scripted_run(gate):
    plan = [Action("list_products"),
            Action("create_checkout_session", {"items": SHOES + BOTTLE}),
            Action("complete_checkout_session", {"session_id": "$session"}),
            Action("done", say="Ordered shoes and a bottle.")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "payment_pending" and r.payment_url.startswith("https://")
    assert r.total_paise == 249900 + 69900 and r.session_id.startswith("cs_")
    text = "\n".join(r.narrative)
    assert "INR 3,198.00" in text and "Ordered shoes and a bottle." in text and "10 products" in text
    assert [s["action"].tool for s in r.steps] == ["list_products", "create_checkout_session",
                                                   "complete_checkout_session"]


def test_refused_scripted_run(gate):
    plan = [Action("create_checkout_session", {"items": WATCH}), Action("done")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "refused" and r.payment_url is None
    assert "G06_MERCHANT_CATEGORY" in "\n".join(r.narrative)


def test_replan_scripted_run(gate, world):
    plan = [Action("create_checkout_session", {"items": WATCH}),
            Action("update_checkout_session", {"session_id": "$session", "items": SHOES}),
            Action("complete_checkout_session", {"session_id": "$session"}), Action("done")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "payment_pending"
    s = world.sessions.get_any(r.session_id)
    assert [l["id"] for l in s["line_items"]] == ["prod_shoes"]


def test_crosssell_scripted_run_accepts_first_offer(gate, world):
    plan = [Action("create_checkout_session", {"items": SHOES}),
            Action("update_checkout_session", {"session_id": "$session",
                                               "items": SHOES + [{"id": "$offer0", "quantity": 1}]}),
            Action("complete_checkout_session", {"session_id": "$session"}), Action("done")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "payment_pending" and r.total_paise == 249900 + 49900
    s = world.sessions.get_any(r.session_id)
    assert {l["id"] for l in s["line_items"]} == {"prod_shoes", "prod_socks"}
    assert any(e.type == "crosssell.accepted" for e in world.ledger.events(session_id=r.session_id))


def test_payfail_scripted_run_waits_and_recovers(gate, world):
    world.payments.outcomes = ["failed", "paid"]
    plan = [Action("create_checkout_session", {"items": SHOES}),
            Action("complete_checkout_session", {"session_id": "$session"}), Action("done")]
    agent = agent_for(gate, plan, clock=world.clock, sleep=lambda s: world.clock.tick(s),
                      wait_for_payment_s=30, poll_every_s=3)
    r = agent.run()
    assert r.outcome == "paid"
    s = world.sessions.get_any(r.session_id)
    assert s["status"] == "completed" and s["attempt"] == 2
    assert "attempt 2" in "\n".join(r.narrative).lower()


def test_wait_times_out_when_unpaid(gate, world):
    world.payments.outcomes = ["pending"]
    plan = [Action("create_checkout_session", {"items": SHOES}),
            Action("complete_checkout_session", {"session_id": "$session"}), Action("done")]
    agent = agent_for(gate, plan, clock=world.clock, sleep=lambda s: world.clock.tick(s),
                      wait_for_payment_s=10, poll_every_s=3)
    r = agent.run()
    assert r.outcome == "payment_pending"


def test_api_errors_are_reported_not_raised(gate, world):
    world.mandates.revoke(world.mandate.id)
    plan = [Action("create_checkout_session", {"items": SHOES}), Action("done")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "refused" and "G02_MANDATE_ACTIVE" in "\n".join(r.narrative)
    plan = [Action("get_checkout_session", {"session_id": "cs_nope"}), Action("done")]
    r = agent_for(gate, plan).run()
    assert r.outcome == "no_plan" and "404" in "\n".join(r.narrative)


def test_agent_stops_at_max_steps(gate):
    r = agent_for(gate, [Action("list_products")] * 20).run(max_steps=3)
    assert r.outcome == "no_plan" and len(r.steps) == 3


# -- LLM planner ----------------------------------------------------------------------------------

def test_llm_planner_drives_happy_path(gate):
    llm = FakeLLM([
        [{"name": "list_products", "arguments": {}}],
        [{"name": "create_checkout_session", "arguments": {"items": SHOES}}],
        [{"name": "complete_checkout_session", "arguments": {"session_id": "$session"}}],
        [{"name": "done", "arguments": {"summary": "Bought the shoes; pay at the link."}}],
    ])
    r = BuyerAgent(gate, LLMPlanner(llm, "buy me trail shoes", 400000)).run()
    assert r.outcome == "payment_pending"
    assert llm.calls[0]["tools"] == TOOLS
    messages = llm.calls[-1]["messages"]
    assert messages[0]["role"] == "system" and "buy me trail shoes" in messages[1]["content"]
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and "<untrusted_catalog>" in tool_msgs[0]["content"]
    assert "Bought the shoes" in "\n".join(r.narrative)


def test_llm_planner_replans_after_refusal(gate):
    llm = FakeLLM([
        [{"name": "create_checkout_session", "arguments": {"items": WATCH}}],
        [{"name": "update_checkout_session", "arguments": {"session_id": "$session", "items": SHOES}}],
        [{"name": "complete_checkout_session", "arguments": {"session_id": "$session"}}],
        "All done.",
    ])
    r = BuyerAgent(gate, LLMPlanner(llm, "buy a watch or shoes", 400000)).run()
    assert r.outcome == "payment_pending"
    refusal_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "G06_MERCHANT_CATEGORY" in refusal_msg


def test_llm_planner_handles_parallel_tool_calls(gate):
    llm = FakeLLM([
        [{"name": "list_products", "arguments": {"q": "shoes"}}, {"name": "list_products", "arguments": {"q": "bottle"}}],
        [{"name": "create_checkout_session", "arguments": {"items": SHOES}}],
        [{"name": "done", "arguments": {"summary": "ok"}}],
    ])
    r = BuyerAgent(gate, LLMPlanner(llm, "x", 400000)).run()
    assert r.outcome == "incomplete" and [s["action"].args.get("q") for s in r.steps[:2]] == ["shoes", "bottle"]
    roles = [m["role"] for m in llm.calls[-1]["messages"]]
    assert roles.count("tool") == 3 and roles[-1] == "tool"


def test_llm_planner_without_tool_calls_is_nudged_then_stops(gate):
    llm = FakeLLM(["I would rather not.", "Still no."])
    r = BuyerAgent(gate, LLMPlanner(llm, "buy shoes", 400000)).run()
    assert r.outcome == "no_plan" and len(llm.calls) == 2
    assert "Call a tool" in llm.calls[1]["messages"][-1]["content"]


def test_llm_planner_provider_error_fails_closed(gate):
    r = BuyerAgent(gate, LLMPlanner(FakeLLM([LLMError("provider down")]), "buy shoes", 400000)).run()
    assert r.outcome == "no_plan" and "provider down" in "\n".join(r.narrative)


def test_llm_planner_unknown_tool_is_reported(gate):
    llm = FakeLLM([[{"name": "teleport", "arguments": {}}], [{"name": "done", "arguments": {}}]])
    r = BuyerAgent(gate, LLMPlanner(llm, "x", 400000)).run()
    assert r.outcome == "no_plan"
    assert "unknown tool" in [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]["content"]


def test_wait_polls_are_bounded_so_a_no_op_sleep_cannot_spin(gate, world):
    """With a fake payments adapter the sleep does nothing, so the wait must be bounded by a poll budget
    rather than only by the wall clock."""
    world.payments.outcomes = ["pending"]
    plan = [Action("create_checkout_session", {"items": SHOES}),
            Action("complete_checkout_session", {"session_id": "$session"}), Action("done")]
    calls = []
    real_get = gate.get

    def counting_get(session_id):
        calls.append(session_id)
        return real_get(session_id)

    gate.get = counting_get
    agent = agent_for(gate, plan, clock=lambda: 1_756_900_000, sleep=lambda s: None,
                      wait_for_payment_s=30, poll_every_s=3)
    r = agent.run()
    assert r.outcome == "payment_pending"
    assert len(calls) == 10, f"expected 30s / 3s = 10 polls, got {len(calls)}"
