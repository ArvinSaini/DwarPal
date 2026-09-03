"""Payments port: the one interface through which money moves, plus an in-memory fake.

The real adapter (``razorpay_client.RazorpayPayments``) is the only module that imports the Razorpay
SDK and the only holder of the Razorpay keys. Everything else, including every test, talks to this
port. ``FakePayments`` scripts outcomes so retry and abandon paths can be exercised offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class PaymentsError(Exception):
    """The payment provider call failed (network, SDK error, refused operation)."""


@dataclass
class PaymentRequest:
    session_id: str
    agent_id: str
    mandate_id: str
    amount_paise: int
    description: str
    attempt: int
    expire_at: int
    reference_id: str


@dataclass
class LinkInfo:
    link_id: str
    url: str
    order_id: str | None
    expire_at: int


@dataclass
class Attempt:
    payment_id: str
    status: str  # 'captured' | 'failed'
    amount_paise: int
    error_code: str | None
    error_description: str | None


@dataclass
class PollResult:
    link_status: str  # 'created' | 'paid' | 'cancelled' | 'expired'
    order_id: str | None
    attempts: list[Attempt] = field(default_factory=list)


class PaymentsPort(Protocol):
    def create_link(self, req: PaymentRequest) -> LinkInfo: ...

    def poll(self, link_id: str) -> PollResult: ...

    def cancel_link(self, link_id: str) -> None: ...


OUTCOMES = ("paid", "failed", "pending", "expired", "error")


class FakePayments:
    """In-memory stand-in. One outcome is consumed per created link (default ``paid``)."""

    def __init__(self, outcomes: list[str] | None = None, fail_create: bool = False, fail_cancel: bool = False):
        self.outcomes: list[str] = list(outcomes or [])
        self.fail_create = fail_create
        self.fail_cancel = fail_cancel
        self.links: dict[str, dict] = {}
        self.created: list[LinkInfo] = []
        self.cancelled: list[str] = []

    def create_link(self, req: PaymentRequest) -> LinkInfo:
        if self.fail_create:
            raise PaymentsError("fake: create_link failed")
        n = len(self.links) + 1
        link_id = f"plink_fake{n:03d}"
        outcome = self.outcomes.pop(0) if self.outcomes else "paid"
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown fake outcome {outcome!r}; use one of {OUTCOMES}")
        info = LinkInfo(link_id, f"https://rzp.test/l/{link_id}", None, req.expire_at)
        self.links[link_id] = {"req": req, "outcome": outcome, "status": "created"}
        self.created.append(info)
        return info

    def poll(self, link_id: str) -> PollResult:
        link = self.links.get(link_id)
        if link is None:
            raise PaymentsError(f"fake: unknown link {link_id}")
        req: PaymentRequest = link["req"]
        if link["status"] == "cancelled":
            return PollResult("cancelled", None, [])
        outcome = link["outcome"]
        if link["status"] == "paid" or outcome == "paid":
            link["status"] = "paid"
            return PollResult("paid", f"order_{link_id}",
                              [Attempt(f"pay_{link_id}_ok", "captured", req.amount_paise, None, None)])
        if outcome == "error":
            raise PaymentsError("fake: poll failed")
        if outcome == "pending":
            return PollResult("created", None, [])
        if outcome == "expired":
            return PollResult("expired", None, [])
        return PollResult("created", f"order_{link_id}",
                          [Attempt(f"pay_{link_id}_fail", "failed", req.amount_paise, "BAD_REQUEST_ERROR",
                                   "Payment declined by the bank")])

    def cancel_link(self, link_id: str) -> None:
        if self.fail_cancel:
            raise PaymentsError("fake: cancel_link failed")
        if link_id not in self.links:
            raise PaymentsError(f"fake: unknown link {link_id}")
        self.links[link_id]["status"] = "cancelled"
        self.cancelled.append(link_id)

    def set_outcome(self, link_id: str, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown fake outcome {outcome!r}")
        self.links[link_id]["outcome"] = outcome
