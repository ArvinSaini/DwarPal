import pytest

from dwarpal.catalog import Catalog, Product, seed
from dwarpal.ledger import Ledger
from dwarpal.payments import PaymentRequest, PaymentsError
from dwarpal.razorpay_client import (MIN_LINK_TTL_S, RazorpayPayments, push_items, sync_items,
                                       verify_webhook_signature)

NOW = 1_756_900_000


class Recorder:
    """Records every SDK call as (name, args, kwargs) and returns canned responses."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.link_response = {"id": "plink_1", "short_url": "https://rzp.io/l/abc", "order_id": None,
                              "status": "created", "notes": {"session_id": "cs_1"}, "payments": []}
        self.order_payments = {"items": []}
        self.all_payments = {"items": []}
        self.items_response = {"items": []}
        self.raise_on: set[str] = set()
        self.created_items: list[dict] = []
        me = self

        class PaymentLink:
            def create(self, data, **kw):
                me._rec("payment_link.create", data, kw)
                return {**me.link_response, "expire_by": data["expire_by"], "notes": data["notes"]}

            def fetch(self, link_id, **kw):
                me._rec("payment_link.fetch", link_id, kw)
                return me.link_response

            def cancel(self, link_id, **kw):
                me._rec("payment_link.cancel", link_id, kw)
                return {"id": link_id, "status": "cancelled"}

        class Order:
            def payments(self, order_id, **kw):
                me._rec("order.payments", order_id, kw)
                return me.order_payments

        class Payment:
            def all(self, params=None, **kw):
                me._rec("payment.all", params, kw)
                return me.all_payments

            def refund(self, payment_id, data=None, **kw):
                me._rec("payment.refund", (payment_id, data), kw)
                return {"id": "rfnd_1", "status": "pending", "amount": data["amount"]}

        class Item:
            def all(self, params=None, **kw):
                me._rec("item.all", params, kw)
                return me.items_response

            def create(self, data, **kw):
                me._rec("item.create", data, kw)
                me.created_items.append(data)
                return {"id": f"item_{len(me.created_items):03d}", **data}

        self.payment_link, self.order, self.payment, self.item = PaymentLink(), Order(), Payment(), Item()

    def _rec(self, name, arg, kw):
        if name in self.raise_on:
            raise RuntimeError(f"boom from {name}")
        self.calls.append((name, arg, kw))

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def stub():
    return Recorder()


@pytest.fixture
def adapter(stub):
    return RazorpayPayments("rzp_test_abc", "secret", client=stub, clock=lambda: NOW)


def req(amount=100, attempt=1):
    return PaymentRequest("cs_1", "agt_1", "mnd_1", amount, "Trail & Turf order cs_1", attempt, NOW + 1200,
                          f"cs_1-{attempt}")


def test_live_or_missing_keys_are_refused(stub):
    with pytest.raises(ValueError):
        RazorpayPayments("rzp_live_abc", "secret", client=stub)
    with pytest.raises(ValueError):
        RazorpayPayments("", "secret", client=stub)


def test_create_link_sends_bounded_request(adapter, stub):
    info = adapter.create_link(req(24990))
    assert info.link_id == "plink_1" and info.url == "https://rzp.io/l/abc" and info.order_id is None
    name, data, kw = stub.named("payment_link.create")[0]
    assert data["amount"] == 24990 and data["currency"] == "INR" and data["reference_id"] == "cs_1-1"
    assert data["notes"] == {"session_id": "cs_1", "agent_id": "agt_1", "mandate_id": "mnd_1", "attempt": "1"}
    assert data["expire_by"] >= NOW + MIN_LINK_TTL_S and data["expire_by"] == info.expire_at
    assert data["notify"] == {"sms": False, "email": False} and data["reminder_enable"] is False
    assert kw["timeout"] == 10


def test_poll_paid_link_dedupes_link_and_order_payments(adapter, stub):
    stub.link_response = {"id": "plink_1", "status": "paid", "order_id": "order_1", "notes": {"session_id": "cs_1"},
                          "payments": [{"payment_id": "pay_1", "status": "captured", "amount": 100}]}
    stub.order_payments = {"items": [{"id": "pay_1", "status": "captured", "amount": 100}]}
    res = adapter.poll("plink_1")
    assert res.link_status == "paid" and res.order_id == "order_1"
    assert [(a.payment_id, a.status) for a in res.attempts] == [("pay_1", "captured")]


def test_poll_reads_failed_attempts_from_the_order(adapter, stub):
    stub.link_response = {"id": "plink_1", "status": "created", "order_id": "order_1", "payments": [],
                          "notes": {"session_id": "cs_1"}}
    stub.order_payments = {"items": [{"id": "pay_f", "status": "failed", "amount": 100,
                                      "error_code": "BAD_REQUEST_ERROR",
                                      "error_description": "Payment was declined by the bank"}]}
    res = adapter.poll("plink_1")
    assert res.link_status == "created" and len(res.attempts) == 1
    a = res.attempts[0]
    assert a.status == "failed" and a.error_code == "BAD_REQUEST_ERROR" and "declined" in a.error_description
    assert stub.named("payment.all") == []


def test_poll_before_order_id_falls_back_to_notes_match(adapter, stub):
    stub.link_response = {"id": "plink_1", "status": "created", "order_id": None, "payments": [],
                          "notes": {"session_id": "cs_1"}}
    stub.all_payments = {"items": [
        {"id": "pay_other", "status": "failed", "amount": 5, "notes": {"session_id": "cs_other"}},
        {"id": "pay_mine", "status": "failed", "amount": 100, "notes": {"session_id": "cs_1"},
         "error_code": "GATEWAY_ERROR", "error_description": "x"},
        {"id": "pay_auth", "status": "authorized", "amount": 100, "notes": {"session_id": "cs_1"}},
    ]}
    res = adapter.poll("plink_1")
    assert [a.payment_id for a in res.attempts] == ["pay_mine"]
    assert stub.named("order.payments") == []


def test_sdk_errors_become_payments_errors(adapter, stub):
    stub.raise_on = {"payment_link.create", "payment_link.fetch", "payment_link.cancel"}
    with pytest.raises(PaymentsError):
        adapter.create_link(req())
    with pytest.raises(PaymentsError):
        adapter.poll("plink_1")
    with pytest.raises(PaymentsError):
        adapter.cancel_link("plink_1")


def test_cancel_link_calls_sdk(adapter, stub):
    adapter.cancel_link("plink_9")
    assert stub.named("payment_link.cancel")[0][1] == "plink_9"


def test_sync_items_maps_fields_and_keeps_local_overlay(conn, clock, stub):
    catalog = Catalog(conn, clock)
    catalog.upsert(Product(id="prod_shoes", title="Old title", description="", price_paise=1, category="footwear",
                           tags=["running"], razorpay_item_id="item_shoes"))
    stub.items_response = {"items": [
        {"id": "item_shoes", "name": "Trail Running Shoes", "description": "New desc", "amount": 249900,
         "currency": "INR", "active": True},
        {"id": "item_new", "name": "Headband", "description": None, "amount": 19900, "currency": "INR", "active": True},
        {"id": "item_gone", "name": "Retired", "description": "", "amount": 100, "currency": "INR", "active": False},
    ]}
    ledger = Ledger(conn, clock)
    assert sync_items(catalog, stub, ledger) == 2
    shoes = catalog.get("prod_shoes")
    assert shoes.title == "Trail Running Shoes" and shoes.price_paise == 249900 and shoes.description == "New desc"
    assert shoes.category == "footwear" and shoes.tags == ["running"] and shoes.source == "razorpay"
    new = catalog.find_by_razorpay_item_id("item_new")
    assert new and new.title == "Headband" and new.category is None and new.price_paise == 19900
    assert catalog.find_by_razorpay_item_id("item_gone") is None
    assert ledger.events()[-1].type == "catalog.synced" and ledger.events()[-1].payload["count"] == 2


def test_push_items_creates_missing_items_and_stores_ids(conn, clock, stub):
    catalog = Catalog(conn, clock)
    seed(catalog)
    catalog.upsert(Product(id="prod_x", title="Already", description="", price_paise=5, razorpay_item_id="item_x"))
    assert push_items(catalog, stub) == 10
    assert len(stub.created_items) == 10 and stub.created_items[0]["amount"] == 249900
    assert all(p.razorpay_item_id for p in catalog.all())
    assert push_items(catalog, stub) == 0


def test_verify_webhook_signature():
    import hashlib
    import hmac
    body = b'{"event":"payment.captured"}'
    good = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, good, "whsec")
    assert verify_webhook_signature(body, " " + good + "\n", "whsec")
    assert not verify_webhook_signature(body, "nope", "whsec")
    assert not verify_webhook_signature(body, "", "whsec")


def test_refund_calls_sdk_with_amount_and_notes(adapter, stub):
    info = adapter.refund("pay_9", 500, {"session_id": "cs_1", "reference": "r1"})
    assert info.refund_id == "rfnd_1" and info.status == "pending" and info.amount_paise == 500
    name, (pid, data), kw = stub.named("payment.refund")[0]
    assert pid == "pay_9" and data["amount"] == 500 and data["notes"]["reference"] == "r1" and kw["timeout"] == 10
    stub.raise_on = {"payment.refund"}
    with pytest.raises(PaymentsError):
        adapter.refund("pay_9", 1, {})
