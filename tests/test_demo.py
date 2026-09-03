import pytest

from agentgate.demo import INTENTS, SCENARIOS, run_demo, scripted_plan
from tests.conftest import make_ctx


def test_every_scenario_has_a_plan_and_intent():
    for s in SCENARIOS:
        assert scripted_plan(s) and scripted_plan(s)[-1].tool == "done" and INTENTS[s]
    with pytest.raises(ValueError):
        scripted_plan("nope")


@pytest.mark.parametrize("scenario,outcome", [
    ("happy", "paid"), ("refused", "refused"), ("replan", "paid"), ("payfail", "paid"), ("crosssell", "paid"),
])
def test_run_demo_scenarios_on_fakes(world, scenario, outcome):
    ctx = make_ctx(world)
    out: list[str] = []
    r = run_demo(ctx, scenario, planner="scripted", wait_s=30, printer=out.append,
                 sleep=lambda s: world.clock.tick(s))
    assert r.outcome == outcome, "\n".join(out)
    text = "\n".join(out)
    assert "gate.decision" in text and "Ledger" in text
    if scenario == "payfail":
        assert world.sessions.get_any(r.session_id)["attempt"] == 2 and "payment.retry" in text
    if scenario == "crosssell":
        assert "crosssell.accepted" in text
    if scenario == "refused":
        assert "G06_MERCHANT_CATEGORY" in text
    agents = world.agents.all()
    assert agents[-1].name.startswith("demo-") and world.mandates.active_for(agents[-1].id, world.clock.now)
    assert any(e.type == "agent.registered" for e in world.ledger.events())


def test_run_demo_with_llm_planner_uses_fake_llm(world):
    from agentgate.llm import FakeLLM

    ctx = make_ctx(world)
    llm = FakeLLM([
        [{"name": "list_products", "arguments": {}}],
        [{"name": "create_checkout_session", "arguments": {"items": [{"id": "prod_shoes", "quantity": 1}]}}],
        [{"name": "complete_checkout_session", "arguments": {"session_id": "$session"}}],
        "Done, pay at the link.",
    ])
    r = run_demo(ctx, "happy", planner="llm", llm=llm, wait_s=0, printer=lambda s: None)
    assert r.outcome == "payment_pending"
    assert INTENTS["happy"] in llm.calls[0]["messages"][1]["content"]


def test_run_demo_rejects_unknown_scenario(world):
    with pytest.raises(ValueError):
        run_demo(make_ctx(world), "nope", printer=lambda s: None)
