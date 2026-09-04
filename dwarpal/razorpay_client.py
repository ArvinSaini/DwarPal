"""Razorpay test-mode adapter. The only module that talks to the Razorpay SDK.

Facts this code depends on (verified against Razorpay's docs and a live test account by earlier work):
- A Payment Link's ``payments`` array lists only *captured* payments. Failed attempts must be read from
  ``GET /orders/{order_id}/payments`` once the link carries an ``order_id`` (it does after the first attempt),
  and before that from the Payments list matched on the link's ``notes``.
- Payment Links must expire at least 15 minutes after creation.
- A test account allows roughly 30 Payment Links, so tests and metrics use ``FakePayments``.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Callable

from dwarpal import __version__
from dwarpal.catalog import Catalog, Product
from dwarpal.payments import Attempt, LinkInfo, PaymentRequest, PaymentsError, PollResult, RefundInfo

REQUEST_TIMEOUT_S = 10
MIN_LINK_TTL_S = 16 * 60


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature.strip())


class RazorpayPayments:
    def __init__(self, key_id: str | None, key_secret: str | None, client=None, timeout_s: int = REQUEST_TIMEOUT_S,
                 clock: Callable[[], int] | None = None):
        if not key_id or not key_id.startswith("rzp_test_"):
            raise ValueError("DwarPal only runs on Razorpay TEST keys (rzp_test_...); refusing this key id")
        self.key_id = key_id
        self.timeout_s = timeout_s
        self.clock = clock or (lambda: int(time.time()))
        if client is None:
            import razorpay  # imported lazily so nothing else needs the SDK

            client = razorpay.Client(auth=(key_id, key_secret))
            try:
                client.set_app_details({"title": "DwarPal", "version": __version__})
            except Exception:
                pass
        self.client = client

    def create_link(self, req: PaymentRequest) -> LinkInfo:
        now = self.clock()
        expire_by = max(req.expire_at, now + MIN_LINK_TTL_S)
        data = {
            "amount": req.amount_paise,
            "currency": "INR",
            "description": req.description[:255],
            "reference_id": req.reference_id[:40],
            "notes": {"session_id": req.session_id, "agent_id": req.agent_id, "mandate_id": req.mandate_id,
                      "attempt": str(req.attempt)},
            "expire_by": expire_by,
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
        }
        try:
            link = self.client.payment_link.create(data, timeout=self.timeout_s)
        except Exception as exc:
            raise PaymentsError(f"razorpay create_link failed: {type(exc).__name__}: {exc}") from exc
        return LinkInfo(link["id"], link.get("short_url") or "", link.get("order_id") or None,
                        int(link.get("expire_by") or expire_by))

    def poll(self, link_id: str) -> PollResult:
        try:
            link = self.client.payment_link.fetch(link_id, timeout=self.timeout_s)
            status = link.get("status") or "created"
            order_id = link.get("order_id") or None
            attempts: dict[str, Attempt] = {}
            for p in link.get("payments") or []:
                pid = p.get("payment_id") or p.get("id")
                if pid and p.get("status") == "captured":
                    attempts[pid] = Attempt(pid, "captured", int(p["amount"]), None, None)
            if order_id:
                raw = self.client.order.payments(order_id, timeout=self.timeout_s).get("items") or []
            else:
                session_id = (link.get("notes") or {}).get("session_id")
                raw = [p for p in (self.client.payment.all({"count": 100}, timeout=self.timeout_s).get("items") or [])
                       if session_id and (p.get("notes") or {}).get("session_id") == session_id]
            for p in raw:
                pid = p.get("id")
                if not pid or pid in attempts:
                    continue
                if p.get("status") == "captured":
                    attempts[pid] = Attempt(pid, "captured", int(p["amount"]), None, None)
                elif p.get("status") == "failed":
                    attempts[pid] = Attempt(pid, "failed", int(p["amount"]), p.get("error_code"),
                                            p.get("error_description"))
            return PollResult(status, order_id, list(attempts.values()))
        except PaymentsError:
            raise
        except Exception as exc:
            raise PaymentsError(f"razorpay poll failed: {type(exc).__name__}: {exc}") from exc

    def cancel_link(self, link_id: str) -> None:
        try:
            self.client.payment_link.cancel(link_id, timeout=self.timeout_s)
        except Exception as exc:
            raise PaymentsError(f"razorpay cancel_link failed: {type(exc).__name__}: {exc}") from exc

    def refund(self, payment_id: str, amount_paise: int, notes: dict) -> RefundInfo:
        try:
            data = self.client.payment.refund(payment_id, {"amount": amount_paise, "notes": notes},
                                              timeout=self.timeout_s)
        except Exception as exc:
            raise PaymentsError(f"razorpay refund failed: {type(exc).__name__}: {exc}") from exc
        return RefundInfo(data["id"], data.get("status") or "pending", int(data.get("amount") or amount_paise))


# -- catalog sync ---------------------------------------------------------------------------------

def sync_items(catalog: Catalog, client, ledger=None, timeout_s: int = REQUEST_TIMEOUT_S) -> int:
    """Pull the merchant's Razorpay Items into the catalog. Local enrichment and stock are preserved."""
    try:
        items = client.item.all({"count": 100}, timeout=timeout_s).get("items") or []
    except Exception as exc:
        raise PaymentsError(f"razorpay item.all failed: {type(exc).__name__}: {exc}") from exc
    count = 0
    for it in items:
        if it.get("active") is False:
            continue
        item_id = it["id"]
        existing = catalog.find_by_razorpay_item_id(item_id)
        if existing is None:
            product = Product(id="prod_" + item_id.replace("item_", "").lower(), title=it.get("name") or item_id,
                              source="razorpay", razorpay_item_id=item_id)
        else:
            product = existing
        product.title = it.get("name") or product.title
        product.description = it.get("description") or ""
        product.price_paise = int(it.get("amount") or 0)
        product.currency = it.get("currency") or "INR"
        product.source = "razorpay"
        catalog.upsert(product)
        count += 1
    if ledger is not None:
        ledger.append("catalog.synced", "merchant", {"count": count, "source": "razorpay_items"})
    return count


def push_items(catalog: Catalog, client, ledger=None, timeout_s: int = REQUEST_TIMEOUT_S) -> int:
    """Create a Razorpay Item for every local product that has none, so the sync demo is real."""
    count = 0
    for p in catalog.all():
        if p.razorpay_item_id:
            continue
        try:
            item = client.item.create({"name": p.title[:120], "description": (p.description or "")[:255],
                                       "amount": p.price_paise, "currency": p.currency}, timeout=timeout_s)
        except Exception as exc:
            raise PaymentsError(f"razorpay item.create failed for {p.id}: {type(exc).__name__}: {exc}") from exc
        p.razorpay_item_id = item["id"]
        catalog.upsert(p)
        count += 1
    if ledger is not None and count:
        ledger.append("catalog.pushed", "merchant", {"count": count, "target": "razorpay_items"})
    return count
