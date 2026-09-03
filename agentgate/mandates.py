"""Per-agent spend mandates and the reservation ledger that makes their caps real.

A mandate says how much one agent may spend: per transaction, per UTC day, and in total, optionally
restricted to categories. Money is *reserved* when a checkout is completed, *committed* when Razorpay
captures the payment, and *released* on cancel, abandon or provider error. Spend = reserved + committed,
so an agent can never over-commit while a payment is still pending.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from agentgate.db import dumps, loads, now_utc_day_bounds, tx
from agentgate.ids import new_id
from agentgate.money import require_paise


class MandateError(ValueError):
    """Invalid mandate parameters or an impossible reservation transition."""


@dataclass
class Mandate:
    id: str
    agent_id: str
    currency: str
    per_txn_cap_paise: int
    daily_cap_paise: int
    total_cap_paise: int
    categories: list[str]
    starts_at: int
    expires_at: int
    status: str
    created_at: int

    def to_dict(self) -> dict:
        return {
            "id": self.id, "agent_id": self.agent_id, "currency": self.currency,
            "per_txn_cap_paise": self.per_txn_cap_paise, "daily_cap_paise": self.daily_cap_paise,
            "total_cap_paise": self.total_cap_paise, "categories": list(self.categories),
            "starts_at": self.starts_at, "expires_at": self.expires_at, "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class Reservation:
    id: str
    session_id: str
    mandate_id: str
    amount_paise: int
    state: str
    created_at: int
    updated_at: int


class MandateStore:
    def __init__(self, conn, clock: Callable[[], int] | None = None):
        self.conn = conn
        self.clock = clock or (lambda: int(time.time()))

    # -- mandates ---------------------------------------------------------------------------------

    @staticmethod
    def _row(row) -> Mandate:
        return Mandate(row["id"], row["agent_id"], row["currency"], row["per_txn_cap_paise"], row["daily_cap_paise"],
                       row["total_cap_paise"], loads(row["categories"]) or [], row["starts_at"], row["expires_at"],
                       row["status"], row["created_at"])

    def create(self, agent_id: str, per_txn_cap_paise: int, daily_cap_paise: int, total_cap_paise: int,
               categories: list[str], expires_at: int, starts_at: int | None = None) -> Mandate:
        now = self.clock()
        starts_at = now if starts_at is None else starts_at
        try:
            per_txn = require_paise(per_txn_cap_paise, "per_txn_cap_paise")
            daily = require_paise(daily_cap_paise, "daily_cap_paise")
            total = require_paise(total_cap_paise, "total_cap_paise")
        except ValueError as exc:
            raise MandateError(str(exc)) from exc
        if not isinstance(categories, list) or any(not isinstance(c, str) or not c.strip() for c in categories):
            raise MandateError("categories must be a list of non-empty strings (empty list = no restriction)")
        if type(expires_at) is not int or type(starts_at) is not int or expires_at <= starts_at:
            raise MandateError("expires_at must be an integer timestamp after starts_at")
        mandate = Mandate(new_id("mnd"), agent_id, "INR", per_txn, daily, total,
                          [c.strip() for c in categories], starts_at, expires_at, "active", now)
        with tx(self.conn):
            self.conn.execute("update mandates set status = 'revoked' where agent_id = ? and status = 'active'", (agent_id,))
            self.conn.execute(
                """insert into mandates(id, agent_id, currency, per_txn_cap_paise, daily_cap_paise, total_cap_paise,
                                        categories, starts_at, expires_at, status, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mandate.id, mandate.agent_id, mandate.currency, mandate.per_txn_cap_paise, mandate.daily_cap_paise,
                 mandate.total_cap_paise, dumps(mandate.categories), mandate.starts_at, mandate.expires_at,
                 mandate.status, mandate.created_at))
        return mandate

    def get(self, mandate_id: str) -> Mandate | None:
        row = self.conn.execute("select * from mandates where id = ?", (mandate_id,)).fetchone()
        return self._row(row) if row else None

    def active_for(self, agent_id: str, now: int) -> Mandate | None:
        row = self.conn.execute(
            "select * from mandates where agent_id = ? and status = 'active' and starts_at <= ? and expires_at > ? "
            "order by created_at desc, rowid desc limit 1", (agent_id, now, now)).fetchone()
        return self._row(row) if row else None

    def for_agent(self, agent_id: str) -> list[Mandate]:
        rows = self.conn.execute("select * from mandates where agent_id = ? order by created_at, rowid", (agent_id,))
        return [self._row(r) for r in rows.fetchall()]

    def all(self) -> list[Mandate]:
        return [self._row(r) for r in self.conn.execute("select * from mandates order by created_at, rowid").fetchall()]

    def revoke(self, mandate_id: str) -> Mandate:
        with tx(self.conn):
            cur = self.conn.execute("update mandates set status = 'revoked' where id = ?", (mandate_id,))
            if cur.rowcount == 0:
                raise KeyError(mandate_id)
        return self.get(mandate_id)

    # -- reservations -----------------------------------------------------------------------------

    @staticmethod
    def _res(row) -> Reservation:
        return Reservation(row["id"], row["session_id"], row["mandate_id"], row["amount_paise"], row["state"],
                           row["created_at"], row["updated_at"])

    def open_for(self, session_id: str) -> Reservation | None:
        row = self.conn.execute("select * from reservations where session_id = ? and state = 'reserved'",
                                (session_id,)).fetchone()
        return self._res(row) if row else None

    def reservations_for(self, session_id: str) -> list[Reservation]:
        rows = self.conn.execute("select * from reservations where session_id = ? order by rowid", (session_id,))
        return [self._res(r) for r in rows.fetchall()]

    def reserve(self, session_id: str, mandate_id: str, amount_paise: int) -> Reservation:
        try:
            amount = require_paise(amount_paise)
        except ValueError as exc:
            raise MandateError(str(exc)) from exc
        now = self.clock()
        with tx(self.conn):
            if self.open_for(session_id) is not None:
                raise MandateError(f"session {session_id} already has an open reservation")
            res = Reservation(new_id("rsv"), session_id, mandate_id, amount, "reserved", now, now)
            self.conn.execute(
                "insert into reservations(id, session_id, mandate_id, amount_paise, state, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (res.id, res.session_id, res.mandate_id, res.amount_paise, res.state, res.created_at, res.updated_at))
        return res

    def _transition(self, session_id: str, new_state: str) -> Reservation:
        with tx(self.conn):
            cur = self.conn.execute(
                "update reservations set state = ?, updated_at = ? where session_id = ? and state = 'reserved'",
                (new_state, self.clock(), session_id))
            if cur.rowcount == 0:
                raise MandateError(f"no open reservation for session {session_id}")
            row = self.conn.execute("select * from reservations where session_id = ? and state = ?",
                                    (session_id, new_state)).fetchone()
        return self._res(row)

    def commit(self, session_id: str) -> Reservation:
        return self._transition(session_id, "committed")

    def release(self, session_id: str) -> Reservation:
        return self._transition(session_id, "released")

    def spent(self, mandate_id: str, now: int, exclude_session: str | None = None) -> tuple[int, int]:
        """(spent_total, spent_today) over reserved + committed reservations; today = UTC day of ``now``."""
        start, end = now_utc_day_bounds(now)
        rows = self.conn.execute(
            "select session_id, amount_paise, created_at from reservations "
            "where mandate_id = ? and state in ('reserved', 'committed')", (mandate_id,)).fetchall()
        total = today = 0
        for r in rows:
            if exclude_session is not None and r["session_id"] == exclude_session:
                continue
            total += r["amount_paise"]
            if start <= r["created_at"] < end:
                today += r["amount_paise"]
        # Refunds give budget back against the total cap. The daily cap is about outflow velocity, so it is
        # left alone on purpose.
        refunded = self.conn.execute("select coalesce(sum(amount_paise), 0) from refunds where mandate_id = ?",
                                     (mandate_id,)).fetchone()[0]
        return max(0, total - refunded), today
