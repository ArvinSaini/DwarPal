import json

import pytest

from dwarpal.agents import Agent
from dwarpal.catalog import SEED_PRODUCTS
from dwarpal.gate import (ALLOW, AUTHORITATIVE, DENY, PREVIEW, RETRY, GateAgent, GateInput, GateMandate,
                            evaluate, gate_agent, gate_mandate)
from dwarpal.mandates import Mandate

CATALOG = {p.id: p.snapshot() for p in SEED_PRODUCTS}
POLICY = {"max_order_paise": 500000, "allowed_categories": ["footwear", "apparel", "accessories", "fitness"],
          "blocked_skus": [], "max_qty_per_line": 5, "in_stock_only": True}
NOW = 1_756_900_000
RULES = ["G00_WELL_FORMED", "G01_AGENT_ACTIVE", "G02_MANDATE_ACTIVE", "G03_ITEMS_KNOWN", "G04_IN_STOCK",
         "G05_SKU_NOT_BLOCKED", "G06_MERCHANT_CATEGORY", "G07_QTY_PER_LINE", "G08_ORDER_MAX",
         "G09_MANDATE_CATEGORY", "G10_PER_TXN_CAP", "G11_DAILY_CAP", "G12_TOTAL_CAP", "G13_SESSION_STATE",
         "G14_REVIEW_THRESHOLD"]


def mandate(**over) -> GateMandate:
    base = dict(id="mnd_1", currency="INR", per_txn_cap_paise=400000, daily_cap_paise=800000,
                total_cap_paise=2000000, categories=(), starts_at=NOW - 10, expires_at=NOW + 86400, status="active")
    base.update(over)
    return GateMandate(**base)


def gi(**over) -> GateInput:
    base = dict(agent=GateAgent("agt_1", "active"), mandate=mandate(), policy=dict(POLICY), catalog=CATALOG,
                items=[{"id": "prod_shoes", "quantity": 1}], spent_today_paise=0, spent_total_paise=0, now=NOW,
                session_status=None, mode=PREVIEW)
    base.update(over)
    return GateInput(**base)


def denied(d, rule):
    assert d.verdict == DENY and not d.allowed
    assert d.rule_id == rule, f"expected {rule}, got {d.rule_id}: {d.reason}"
    assert d.checks[-1].rule == rule and d.checks[-1].ok is False
    assert all(c.ok for c in d.checks[:-1])
    return d


# -- happy path -----------------------------------------------------------------------------------

def test_happy_allow_has_full_trail():
    d = evaluate(gi())
    assert d.allowed and d.verdict == ALLOW and d.rule_id == "ALLOW" and d.total_paise == 249900
    assert [c.rule for c in d.checks] == RULES
    assert all(c.ok for c in d.checks)
    assert d.lines[0].title == "Trail Running Shoes" and d.lines[0].line_total_paise == 249900
    assert "15 checks passed" in d.reason


def test_decision_serialises_to_json():
    d = evaluate(gi(items=[{"id": "prod_shoes", "quantity": 1}, {"id": "prod_bottle", "quantity": 2}]))
    doc = json.loads(json.dumps(d.to_dict()))
    assert doc["verdict"] == "ALLOW" and doc["total_paise"] == 249900 + 2 * 69900
    assert len(doc["checks"]) == 15 and doc["lines"][1]["quantity"] == 2


def test_details_mention_rupee_amounts():
    d = evaluate(gi())
    assert "INR 2,499.00" in d.checks[3].detail


def test_authoritative_and_retry_modes_accept_their_status():
    assert evaluate(gi(mode=AUTHORITATIVE, session_status="ready_for_payment")).allowed
    assert evaluate(gi(mode=RETRY, session_status="payment_pending")).allowed
    assert evaluate(gi(mode=PREVIEW, session_status="not_ready_for_payment")).allowed
    assert evaluate(gi(mode=PREVIEW, session_status="ready_for_payment")).allowed


# -- G00 ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("items", [
    [],
    "not a list",
    [{"id": "prod_shoes", "quantity": 0}],
    [{"id": "prod_shoes", "quantity": True}],
    [{"id": "prod_shoes", "quantity": "1"}],
    [{"id": "", "quantity": 1}],
    [{"quantity": 1}],
    ["prod_shoes"],
    [{"id": "prod_shoes", "quantity": 1}, {"id": "prod_shoes", "quantity": 1}],
    [{"id": f"p{i}", "quantity": 1} for i in range(21)],
])
def test_g00_well_formed(items):
    denied(evaluate(gi(items=items)), "G00_WELL_FORMED")


# -- G01 / G02 ------------------------------------------------------------------------------------

def test_g01_agent_missing_or_revoked():
    denied(evaluate(gi(agent=None)), "G01_AGENT_ACTIVE")
    d = denied(evaluate(gi(agent=GateAgent("agt_1", "revoked"))), "G01_AGENT_ACTIVE")
    assert "revoked" in d.reason


@pytest.mark.parametrize("m", [
    None,
    mandate(status="revoked"),
    mandate(expires_at=NOW),
    mandate(starts_at=NOW + 1),
    mandate(currency="USD"),
])
def test_g02_mandate_active(m):
    denied(evaluate(gi(mandate=m)), "G02_MANDATE_ACTIVE")


# -- G03 .. G08 (merchant side) -------------------------------------------------------------------

def test_g03_unknown_item():
    d = denied(evaluate(gi(items=[{"id": "prod_nope", "quantity": 1}])), "G03_ITEMS_KNOWN")
    assert "prod_nope" in d.reason


def test_g04_out_of_stock_and_policy_override():
    denied(evaluate(gi(items=[{"id": "prod_brace", "quantity": 1}])), "G04_IN_STOCK")
    policy = dict(POLICY, in_stock_only=False)
    assert evaluate(gi(items=[{"id": "prod_brace", "quantity": 1}], policy=policy)).allowed


def test_g05_blocked_sku():
    policy = dict(POLICY, blocked_skus=["prod_shoes"])
    denied(evaluate(gi(policy=policy)), "G05_SKU_NOT_BLOCKED")


def test_g06_merchant_category_and_uncategorised():
    d = denied(evaluate(gi(items=[{"id": "prod_watch", "quantity": 1}])), "G06_MERCHANT_CATEGORY")
    assert "electronics" in d.reason
    catalog = dict(CATALOG, prod_raw={"id": "prod_raw", "title": "Raw", "price_paise": 100,
                                      "availability": "in_stock", "category": None})
    d = denied(evaluate(gi(catalog=catalog, items=[{"id": "prod_raw", "quantity": 1}])), "G06_MERCHANT_CATEGORY")
    assert "not yet categorised" in d.reason


def test_g07_quantity_per_line():
    denied(evaluate(gi(items=[{"id": "prod_socks", "quantity": 6}])), "G07_QTY_PER_LINE")


def test_g08_order_max():
    policy = dict(POLICY, max_order_paise=400000)
    d = denied(evaluate(gi(items=[{"id": "prod_shoes", "quantity": 2}], policy=policy)), "G08_ORDER_MAX")
    assert "INR 4,998.00" in d.reason and "INR 4,000.00" in d.reason


# -- G09 .. G12 (mandate side) --------------------------------------------------------------------

def test_g09_mandate_category():
    denied(evaluate(gi(mandate=mandate(categories=("apparel",)))), "G09_MANDATE_CATEGORY")
    assert evaluate(gi(mandate=mandate(categories=("footwear", "apparel")))).allowed


def test_g10_per_txn_cap():
    denied(evaluate(gi(mandate=mandate(per_txn_cap_paise=249899))), "G10_PER_TXN_CAP")
    assert evaluate(gi(mandate=mandate(per_txn_cap_paise=249900))).allowed


def test_g11_daily_cap():
    denied(evaluate(gi(spent_today_paise=700000)), "G11_DAILY_CAP")
    assert evaluate(gi(spent_today_paise=800000 - 249900)).allowed


def test_g12_total_cap():
    denied(evaluate(gi(spent_total_paise=1900000)), "G12_TOTAL_CAP")
    assert evaluate(gi(spent_total_paise=2000000 - 249900)).allowed


# -- G13 / G99 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode,status", [
    (AUTHORITATIVE, None),
    (AUTHORITATIVE, "not_ready_for_payment"),
    (AUTHORITATIVE, "payment_pending"),
    (RETRY, "ready_for_payment"),
    (PREVIEW, "completed"),
    (PREVIEW, "canceled"),
    ("bogus", None),
])
def test_g13_session_state(mode, status):
    denied(evaluate(gi(mode=mode, session_status=status)), "G13_SESSION_STATE")


def test_g99_guard_never_raises():
    catalog = dict(CATALOG, prod_bad={"id": "prod_bad", "title": "Bad", "price_paise": "oops",
                                      "availability": "in_stock", "category": "footwear"})
    d = evaluate(gi(catalog=catalog, items=[{"id": "prod_bad", "quantity": 1}]))
    assert d.verdict == DENY and d.rule_id == "G99_GATE_ERROR"
    assert d.checks[-1].rule == "G99_GATE_ERROR" and "ValueError" in d.checks[-1].detail


def test_g99_on_absurd_values_still_decides():
    catalog = dict(CATALOG, prod_big={"id": "prod_big", "title": "Big", "price_paise": 10 ** 400,
                                      "availability": "in_stock", "category": "footwear"})
    d = evaluate(gi(catalog=catalog, items=[{"id": "prod_big", "quantity": 1}]))
    assert d.verdict == DENY and d.rule_id == "G08_ORDER_MAX"


# -- adapters -------------------------------------------------------------------------------------

def test_store_adapters():
    a = gate_agent(Agent("agt_9", "bot", "active", NOW))
    assert a == GateAgent("agt_9", "active")
    m = gate_mandate(Mandate("mnd_9", "agt_9", "INR", 1, 2, 3, ["footwear"], NOW, NOW + 1, "active", NOW))
    assert m.categories == ("footwear",) and m.total_cap_paise == 3
    assert gate_mandate(None) is None
    assert gate_agent(None) is None


# -- G14 review threshold -------------------------------------------------------------------------

def test_g14_review_threshold_and_merchant_approval():
    from dwarpal.gate import REVIEW
    policy = dict(POLICY, review_above_paise=200000)
    d = evaluate(gi(policy=policy))
    assert d.verdict == REVIEW and d.rule_id == "G14_REVIEW_THRESHOLD" and not d.allowed and d.needs_review
    assert d.checks[-1].rule == "G14_REVIEW_THRESHOLD" and "INR 2,499.00" in d.reason and "INR 2,000.00" in d.reason
    assert d.total_paise == 249900 and d.lines
    assert evaluate(gi(policy=policy, merchant_approved=True)).allowed
    assert evaluate(gi(policy=dict(POLICY, review_above_paise=249900))).allowed
    assert evaluate(gi(policy=dict(POLICY, review_above_paise=0))).allowed
    assert evaluate(gi(policy=policy, mode=PREVIEW, session_status="requires_review")).verdict == REVIEW


# -- refund rules ---------------------------------------------------------------------------------

def refund_input(**over):
    from dwarpal.gate import RefundInput
    base = dict(session_status="completed", captured_paise=319800, refunded_paise=0, amount_paise=69900,
                reason="bottle out of stock at dispatch", reference="shortfall-1", seen_references=(),
                captured_at=NOW - 3600, now=NOW, window_days=30)
    base.update(over)
    return RefundInput(**base)


def test_refund_allow_has_full_trail():
    from dwarpal.gate import evaluate_refund
    d = evaluate_refund(refund_input())
    assert d.allowed and d.total_paise == 69900
    assert [c.rule for c in d.checks] == ["RF00_WELL_FORMED", "RF01_SESSION_COMPLETED", "RF02_WITHIN_CAPTURE",
                                          "RF03_NO_DUPLICATE", "RF04_WITHIN_WINDOW"]


@pytest.mark.parametrize("over,rule", [
    (dict(amount_paise=0), "RF00_WELL_FORMED"),
    (dict(amount_paise=True), "RF00_WELL_FORMED"),
    (dict(reason=""), "RF00_WELL_FORMED"),
    (dict(reference=""), "RF00_WELL_FORMED"),
    (dict(session_status="payment_pending"), "RF01_SESSION_COMPLETED"),
    (dict(captured_paise=0), "RF01_SESSION_COMPLETED"),
    (dict(amount_paise=319801), "RF02_WITHIN_CAPTURE"),
    (dict(refunded_paise=300000), "RF02_WITHIN_CAPTURE"),
    (dict(seen_references=("shortfall-1",)), "RF03_NO_DUPLICATE"),
    (dict(now=NOW + 31 * 86400), "RF04_WITHIN_WINDOW"),
    (dict(captured_at=None), "RF04_WITHIN_WINDOW"),
])
def test_refund_rules(over, rule):
    from dwarpal.gate import evaluate_refund
    d = evaluate_refund(refund_input(**over))
    assert d.verdict == DENY and d.rule_id == rule, f"{d.rule_id}: {d.reason}"


def test_refund_window_zero_means_no_window_and_guard():
    from dwarpal.gate import evaluate_refund
    assert evaluate_refund(refund_input(now=NOW + 400 * 86400, window_days=0)).allowed
    assert evaluate_refund(refund_input(amount_paise=319800)).allowed
    assert evaluate_refund(refund_input(captured_paise="oops")).rule_id == "RF01_SESSION_COMPLETED"
    d = evaluate_refund(refund_input(seen_references=None))  # iteration over None raises inside
    assert d.verdict == DENY and d.rule_id == "G99_GATE_ERROR" and "TypeError" in d.checks[-1].detail
