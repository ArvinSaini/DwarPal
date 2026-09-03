"""Cross-sell: the revenue-growth piece, kept bounded.

The candidate set is deterministic: in stock, allowed by the merchant policy and by the agent's mandate,
not already in the cart, and priced within the *headroom* left under every cap. A picker (fake or LLM)
chooses at most two from that set and can only suggest; the agent accepts by updating its cart, and the
gate evaluates the result like any other cart. An offer can therefore never push a cart over a cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentgate.catalog import Product

MAX_OFFERS = 2


@dataclass
class Offer:
    id: str
    title: str
    price_paise: int
    reason: str

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "price_paise": self.price_paise, "reason": self.reason}


def headroom(mandate, policy: dict, total_paise: int, spent_today_paise: int, spent_total_paise: int) -> int:
    """Largest add-on price that keeps the cart under every cap. Never negative."""
    gaps = [
        mandate.per_txn_cap_paise - total_paise,
        mandate.daily_cap_paise - spent_today_paise - total_paise,
        mandate.total_cap_paise - spent_total_paise - total_paise,
    ]
    max_order = policy.get("max_order_paise")
    if type(max_order) is int:
        gaps.append(max_order - total_paise)
    return max(0, min(gaps))


def candidates(cart: list[Product], products: list[Product], policy: dict, mandate, spent_today_paise: int,
               spent_total_paise: int, limit: int = 8, cart_total_paise: int | None = None) -> list[Product]:
    total = cart_total_paise if cart_total_paise is not None else sum(p.price_paise for p in cart)
    room = headroom(mandate, policy, total, spent_today_paise, spent_total_paise)
    if room <= 0:
        return []
    in_cart = {p.id for p in cart}
    allowed = set(policy.get("allowed_categories", []))
    blocked = set(policy.get("blocked_skus", []))
    mandate_categories = set(mandate.categories)
    out = [
        p for p in products
        if p.id not in in_cart
        and p.id not in blocked
        and p.availability == "in_stock"
        and p.category is not None
        and p.category in allowed
        and (not mandate_categories or p.category in mandate_categories)
        and p.price_paise <= room
    ]
    out.sort(key=lambda p: (p.price_paise, p.id))
    return out[:limit]


class Picker(Protocol):
    def pick(self, cart: list[Product], candidates: list[Product]) -> list[Offer]: ...


class NoPicker:
    def pick(self, cart: list[Product], candidates: list[Product]) -> list[Offer]:
        return []


class FakePicker:
    """Deterministic picker: most shared tags with the cart first, then cheapest, then id."""

    def pick(self, cart: list[Product], candidates: list[Product]) -> list[Offer]:
        if not cart or not candidates:
            return []
        cart_tags = {t for p in cart for t in p.tags}
        ranked = sorted(candidates, key=lambda p: (-len(cart_tags & set(p.tags)), p.price_paise, p.id))
        offers = []
        for p in ranked[:MAX_OFFERS]:
            shared = sorted(cart_tags & set(p.tags))
            reason = (f"Pairs with {cart[0].title}: shared {', '.join(shared)}" if shared
                      else f"Popular add-on for {cart[0].title}")
            offers.append(Offer(p.id, p.title, p.price_paise, reason))
        return offers
