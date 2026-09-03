import pytest

from dwarpal.payments import FakePayments, PaymentRequest, PaymentsError


def req(amount: int = 100, attempt: int = 1) -> PaymentRequest:
    return PaymentRequest(session_id="cs_1", agent_id="agt_1", mandate_id="mnd_1", amount_paise=amount,
                          description="test", attempt=attempt, expire_at=2_000_000_000, reference_id=f"cs_1-{attempt}")


def test_create_link_and_paid_poll():
    fp = FakePayments()
    info = fp.create_link(req(100))
    assert info.link_id == "plink_fake001" and info.url.endswith("plink_fake001") and info.order_id is None
    res = fp.poll(info.link_id)
    assert res.link_status == "paid" and res.order_id == "order_plink_fake001"
    assert len(res.attempts) == 1 and res.attempts[0].status == "captured" and res.attempts[0].amount_paise == 100
    assert fp.created == [info]


def test_failed_outcome():
    fp = FakePayments(["failed"])
    info = fp.create_link(req(250))
    res = fp.poll(info.link_id)
    assert res.link_status == "created" and res.order_id
    assert res.attempts[0].status == "failed" and res.attempts[0].error_code == "BAD_REQUEST_ERROR"
    assert fp.poll(info.link_id).attempts[0].payment_id == res.attempts[0].payment_id  # stable across polls


def test_fail_create_raises():
    with pytest.raises(PaymentsError):
        FakePayments(fail_create=True).create_link(req())


def test_cancel_then_poll():
    fp = FakePayments(["pending"])
    info = fp.create_link(req())
    fp.cancel_link(info.link_id)
    assert fp.cancelled == [info.link_id]
    res = fp.poll(info.link_id)
    assert res.link_status == "cancelled" and res.attempts == []
    with pytest.raises(PaymentsError):
        FakePayments(fail_cancel=True).cancel_link("plink_missing")


def test_pending_expired_error_outcomes():
    fp = FakePayments(["pending", "expired", "error"])
    a, b, c = (fp.create_link(req()) for _ in range(3))
    assert fp.poll(a.link_id).link_status == "created" and fp.poll(a.link_id).attempts == []
    assert fp.poll(b.link_id).link_status == "expired"
    with pytest.raises(PaymentsError):
        fp.poll(c.link_id)


def test_set_outcome_changes_later_polls():
    fp = FakePayments(["pending"])
    info = fp.create_link(req())
    assert fp.poll(info.link_id).link_status == "created"
    fp.set_outcome(info.link_id, "paid")
    assert fp.poll(info.link_id).link_status == "paid"


def test_refund_records_and_can_fail():
    fp = FakePayments()
    info = fp.refund("pay_1", 500, {"session_id": "cs_1"})
    assert info.refund_id == "rfnd_fake001" and info.status == "processed" and info.amount_paise == 500
    assert fp.refunds == [("pay_1", 500, {"session_id": "cs_1"})]
    with pytest.raises(PaymentsError):
        FakePayments(fail_refund=True).refund("pay_1", 1, {})
