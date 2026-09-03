"""Merchant policy: what the store lets ANY agent buy. Validated JSON, one row."""
from __future__ import annotations

import time
from typing import Callable

from agentgate.db import dumps, loads, tx

DEFAULT_POLICY: dict = {
    "max_order_paise": 500000,
    "allowed_categories": ["footwear", "apparel", "accessories", "fitness"],
    "blocked_skus": [],
    "max_qty_per_line": 5,
    "in_stock_only": True,
    "review_above_paise": 0,  # 0 = never ask the merchant; otherwise orders above this wait for approval
    "refund_window_days": 30,  # 0 = no window; otherwise refunds only within N days of capture
}
REQUIRED_KEYS = ("max_order_paise", "allowed_categories", "blocked_skus", "max_qty_per_line", "in_stock_only")
OPTIONAL_KEYS = ("review_above_paise", "refund_window_days")
POLICY_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS


class PolicyError(ValueError):
    """The policy document is malformed."""


def _str_list(value, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise PolicyError(f"{name} must be a list of non-empty strings")
    out: list[str] = []
    for v in value:
        v = v.strip()
        if v not in out:
            out.append(v)
    return out


def validate_policy(doc) -> dict:
    """Return a cleaned copy of ``doc`` or raise ``PolicyError`` naming the first problem."""
    if not isinstance(doc, dict):
        raise PolicyError("policy must be a JSON object")
    unknown = set(doc) - set(POLICY_KEYS)
    if unknown:
        raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
    missing = set(REQUIRED_KEYS) - set(doc)
    if missing:
        raise PolicyError(f"missing policy keys: {sorted(missing)}")
    mo = doc["max_order_paise"]
    if type(mo) is not int or mo < 0:
        raise PolicyError("max_order_paise must be a non-negative integer (paise)")
    mq = doc["max_qty_per_line"]
    if type(mq) is not int or mq < 1:
        raise PolicyError("max_qty_per_line must be an integer >= 1")
    if type(doc["in_stock_only"]) is not bool:
        raise PolicyError("in_stock_only must be true or false")
    review = doc.get("review_above_paise", 0)
    if type(review) is not int or review < 0:
        raise PolicyError("review_above_paise must be a non-negative integer (paise); 0 disables review")
    window = doc.get("refund_window_days", 30)
    if type(window) is not int or window < 0:
        raise PolicyError("refund_window_days must be a non-negative integer; 0 disables the window")
    return {
        "max_order_paise": mo,
        "allowed_categories": _str_list(doc["allowed_categories"], "allowed_categories"),
        "blocked_skus": _str_list(doc["blocked_skus"], "blocked_skus"),
        "max_qty_per_line": mq,
        "in_stock_only": doc["in_stock_only"],
        "review_above_paise": review,
        "refund_window_days": window,
    }


class PolicyStore:
    def __init__(self, conn, clock: Callable[[], int] | None = None):
        self.conn = conn
        self.clock = clock or (lambda: int(time.time()))

    def get(self) -> dict:
        row = self.conn.execute("select json from policy where id = 1").fetchone()
        return loads(row["json"]) if row else validate_policy(DEFAULT_POLICY)

    def set(self, doc) -> dict:
        clean = validate_policy(doc)
        with tx(self.conn):
            self.conn.execute(
                "insert into policy(id, json, updated_at) values (1, ?, ?) "
                "on conflict(id) do update set json = excluded.json, updated_at = excluded.updated_at",
                (dumps(clean), self.clock()))
        return clean
