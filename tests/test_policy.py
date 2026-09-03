import pytest

from dwarpal.policy import DEFAULT_POLICY, PolicyError, PolicyStore, validate_policy


def test_fresh_db_returns_default(conn, clock):
    assert PolicyStore(conn, clock).get() == DEFAULT_POLICY


def test_set_persists_and_dedupes(conn, clock):
    store = PolicyStore(conn, clock)
    new = dict(DEFAULT_POLICY, max_order_paise=100000, blocked_skus=["prod_gel", "prod_gel"])
    out = store.set(new)
    assert out["blocked_skus"] == ["prod_gel"]
    assert PolicyStore(conn, clock).get()["max_order_paise"] == 100000


@pytest.mark.parametrize("bad", [
    dict(DEFAULT_POLICY, max_order_paise=-1),
    dict(DEFAULT_POLICY, max_order_paise=True),
    dict(DEFAULT_POLICY, allowed_categories="footwear"),
    dict(DEFAULT_POLICY, allowed_categories=["footwear", ""]),
    dict(DEFAULT_POLICY, max_qty_per_line=True),
    dict(DEFAULT_POLICY, max_qty_per_line=0),
    dict(DEFAULT_POLICY, in_stock_only="yes"),
    dict(DEFAULT_POLICY, extra=1),
    dict(DEFAULT_POLICY, review_above_paise=-1),
    dict(DEFAULT_POLICY, review_above_paise="2000"),
    {k: v for k, v in DEFAULT_POLICY.items() if k != "blocked_skus"},
    "not a dict",
])
def test_validate_rejects(bad):
    with pytest.raises(PolicyError):
        validate_policy(bad)


def test_validate_returns_copy():
    p = validate_policy(DEFAULT_POLICY)
    assert p == DEFAULT_POLICY and p is not DEFAULT_POLICY


def test_review_threshold_is_optional_and_defaults_to_zero():
    doc = {k: v for k, v in DEFAULT_POLICY.items() if k != "review_above_paise"}
    assert validate_policy(doc)["review_above_paise"] == 0
    assert validate_policy(dict(DEFAULT_POLICY, review_above_paise=300000))["review_above_paise"] == 300000


def test_refund_window_is_optional_and_validated():
    doc = {k: v for k, v in DEFAULT_POLICY.items() if k != "refund_window_days"}
    assert validate_policy(doc)["refund_window_days"] == 30
    with pytest.raises(PolicyError):
        validate_policy(dict(DEFAULT_POLICY, refund_window_days=-1))
