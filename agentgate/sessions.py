"""Checkout sessions: the state machine that turns a cart into a Razorpay Payment Link, and back.

States: not_ready_for_payment -> ready_for_payment -> payment_pending -> completed | canceled.
The gate runs in *preview* mode on create/update (no money reserved), in *authoritative* mode on
complete (money reserved, link created), and in *retry* mode before a second payment attempt.
This module is the only code path that creates a payment link, and it does so only after ALLOW.
"""
from __future__ import annotations

import time
from typing import Callable

from agentgate.crosssell import Offer, candidates
from agentgate.db import dumps, loads, tx
from agentgate.gate import AUTHORITATIVE, PREVIEW, RETRY, Decision, GateInput, evaluate, gate_agent, gate_mandate
from agentgate.ids import new_id
from agentgate.payments import Attempt, LinkInfo, PaymentRequest, PaymentsError

NOT_READY = "not_ready_for_payment"
READY = "ready_for_payment"
PENDING = "payment_pending"
COMPLETED = "completed"
CANCELED = "canceled"
TERMINAL = {COMPLETED, CANCELED}
MAX_ATTEMPTS = 2


class SessionError(Exception):
    def __init__(self, status_code: int, type: str, code: str, message: str, param: str | None = None,
                 extra: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.type = type
        self.code = code
        self.message = message
        self.param = param
        self.extra = extra or {}

    def to_dict(self) -> dict:
        d = {"type": self.type, "code": self.code, "message": self.message}
        if self.param:
            d["param"] = self.param
        d.update(self.extra)
        return d


class SessionService:
    def __init__(self, conn, catalog, policies, agents, mandates, ledger, payments, picker,
                 clock: Callable[[], int] | None = None, link_ttl_s: int = 1200,
                 merchant_name: str = "Trail & Turf", trail_base: str = "/agent/v1/checkout_sessions"):
        self.conn = conn
        self.catalog = catalog
        self.policies = policies
        self.agents = agents
        self.mandates = mandates
        self.ledger = ledger
        self.payments = payments
        self.picker = picker
        self.clock = clock or (lambda: int(time.time()))
        self.link_ttl_s = link_ttl_s
        self.merchant_name = merchant_name
        self.trail_base = trail_base

    # -- row helpers -----------------------------------------------------------------------------

    def _load(self, session_id: str):
        return self.conn.execute("select * from sessions where id = ?", (session_id,)).fetchone()

    def _owned(self, agent, session_id: str):
        row = self._load(session_id)
        if row is None or row["agent_id"] != agent.id:
            raise SessionError(404, "not_found", "not_found", f"no checkout session {session_id} for this agent")
        return row

    def _set(self, session_id: str, **fields) -> None:
        fields["updated_at"] = self.clock()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with tx(self.conn):
            self.conn.execute(f"update sessions set {cols} where id = ?", (*fields.values(), session_id))

    def _attempts(self, session_id: str) -> list[dict]:
        rows = self.conn.execute("select * from payments where session_id = ? order by rowid", (session_id,)).fetchall()
        return [{"razorpay_payment_id": r["razorpay_payment_id"], "status": r["status"], "amount_paise": r["amount_paise"],
                 "error_code": r["error_code"], "error_description": r["error_description"], "attempt": r["attempt"]}
                for r in rows]

    def _payment_seen(self, session_id: str, payment_id: str) -> bool:
        return self.conn.execute("select 1 from payments where session_id = ? and razorpay_payment_id = ?",
                                 (session_id, payment_id)).fetchone() is not None

    def _record_payment(self, session_id: str, a: Attempt, attempt_no: int) -> None:
        with tx(self.conn):
            self.conn.execute(
                "insert into payments(id, session_id, razorpay_payment_id, status, amount_paise, error_code, "
                "error_description, attempt, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("lp"), session_id, a.payment_id, a.status, a.amount_paise, a.error_code, a.error_description,
                 attempt_no, self.clock()))

    def _to_dict(self, row) -> dict:
        totals = loads(row["totals"]) or {}
        attempts = self._attempts(row["id"])
        payment = None
        if row["link_id"]:
            if row["status"] == PENDING:
                pstatus = "pending"
            elif row["status"] == COMPLETED:
                pstatus = "captured"
            elif row["status"] == CANCELED:
                pstatus = "abandoned" if any(a["status"] == "failed" for a in attempts) else "cancelled"
            else:
                pstatus = "none"
            payment = {"provider": "razorpay", "method": "payment_link", "link_id": row["link_id"],
                       "url": row["link_url"], "amount_paise": totals.get("total_paise", 0),
                       "expires_at": row["link_expire_at"], "attempt": row["attempt"], "status": pstatus}
        return {
            "id": row["id"], "agent_id": row["agent_id"], "mandate_id": row["mandate_id"], "status": row["status"],
            "currency": "INR", "line_items": loads(row["line_items"]) or [], "totals": totals,
            "messages": loads(row["messages"]) or [], "offers": loads(row["offers"]) or [],
            "decision": loads(row["last_decision"]), "payment": payment, "attempts": attempts,
            "links": {"trail": f"{self.trail_base}/{row['id']}/trail"}, "attempt": row["attempt"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"],
        }

    # -- gate and offers -------------------------------------------------------------------------

    def _decide(self, agent, items, status: str | None, mode: str, session_id: str | None = None,
                exclude_self: bool = False):
        now = self.clock()
        mandate = self.mandates.active_for(agent.id, now)
        spent_total = spent_today = 0
        if mandate is not None:
            spent_total, spent_today = self.mandates.spent(
                mandate.id, now, exclude_session=session_id if exclude_self else None)
        gi = GateInput(gate_agent(agent), gate_mandate(mandate), self.policies.get(), self.catalog.snapshot(), items,
                       spent_today, spent_total, now, status, mode)
        return evaluate(gi), mandate, spent_today, spent_total, now

    def _log_decision(self, session_id: str, decision: Decision, mode: str, spent_today: int, spent_total: int,
                      now: int) -> None:
        self.ledger.append("gate.decision", "gate",
                           {"mode": mode, **decision.to_dict(), "spent_today_paise": spent_today,
                            "spent_total_paise": spent_total, "now": now}, session_id)

    @staticmethod
    def _messages(decision: Decision) -> list[dict]:
        if decision.allowed:
            return []
        return [{"type": "error", "code": "policy_denied", "rule_id": decision.rule_id, "text": decision.reason}]

    def _offers(self, decision: Decision, mandate, spent_today: int, spent_total: int) -> tuple[list[Offer], int]:
        if not decision.allowed or mandate is None:
            return [], 0
        cart = [p for p in (self.catalog.get(l.id) for l in decision.lines) if p is not None]
        cands = candidates(cart, self.catalog.all(), self.policies.get(), mandate, spent_today, spent_total,
                           cart_total_paise=decision.total_paise)
        if not cands:
            return [], 0
        try:
            offers = list(self.picker.pick(cart, cands))[:2]
        except Exception:  # offers are optional; a picker failure must never break checkout
            offers = []
        return offers, len(cands)

    @staticmethod
    def _totals(decision: Decision) -> dict:
        return {"subtotal_paise": decision.total_paise, "total_paise": decision.total_paise}

    # -- create / update -------------------------------------------------------------------------

    def create(self, agent, items, idempotency_key: str | None, body_hash: str) -> dict:
        if idempotency_key:
            row = self.conn.execute("select * from sessions where agent_id = ? and idempotency_key = ?",
                                    (agent.id, idempotency_key)).fetchone()
            if row is not None:
                if row["create_body_hash"] == body_hash:
                    return self._to_dict(row)
                raise SessionError(409, "invalid_request", "request_not_idempotent",
                                   "Idempotency-Key was already used with a different request body",
                                   param="Idempotency-Key")
        decision, mandate, spent_today, spent_total, now = self._decide(agent, items, None, PREVIEW)
        session_id = new_id("cs")
        status = READY if decision.allowed else NOT_READY
        offers, candidate_count = self._offers(decision, mandate, spent_today, spent_total)
        with tx(self.conn):
            self.conn.execute(
                "insert into sessions(id, agent_id, mandate_id, status, line_items, totals, messages, offers, "
                "last_decision, idempotency_key, create_body_hash, attempt, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (session_id, agent.id, mandate.id if mandate else None, status,
                 dumps([l.__dict__ for l in decision.lines]), dumps(self._totals(decision)),
                 dumps(self._messages(decision)), dumps([o.to_dict() for o in offers]), dumps(decision.to_dict()),
                 idempotency_key, body_hash, now, now))
        self.ledger.append("session.created", f"agent:{agent.id}",
                           {"agent_id": agent.id, "items": items, "total_paise": decision.total_paise, "status": status},
                           session_id)
        self._log_decision(session_id, decision, PREVIEW, spent_today, spent_total, now)
        if offers:
            self.ledger.append("crosssell.offered", "crosssell",
                               {"offers": [o.to_dict() for o in offers], "candidate_count": candidate_count}, session_id)
        return self._to_dict(self._load(session_id))

    def update(self, agent, session_id: str, items) -> dict:
        row = self._owned(agent, session_id)
        if row["status"] not in (NOT_READY, READY):
            raise SessionError(409, "session_state", "wrong_state",
                               f"session is {row['status']}; only not_ready_for_payment or ready_for_payment "
                               f"sessions can be updated")
        previous_offers = loads(row["offers"]) or []
        item_ids = {it.get("id") for it in items if isinstance(it, dict)} if isinstance(items, list) else set()
        accepted = [o["id"] for o in previous_offers if o["id"] in item_ids]
        decision, mandate, spent_today, spent_total, now = self._decide(agent, items, row["status"], PREVIEW, session_id)
        status = READY if decision.allowed else NOT_READY
        offers, candidate_count = self._offers(decision, mandate, spent_today, spent_total)
        self._set(session_id, status=status, line_items=dumps([l.__dict__ for l in decision.lines]),
                  totals=dumps(self._totals(decision)), messages=dumps(self._messages(decision)),
                  offers=dumps([o.to_dict() for o in offers]), last_decision=dumps(decision.to_dict()),
                  mandate_id=mandate.id if mandate else None)
        self.ledger.append("session.updated", f"agent:{agent.id}",
                           {"items": items, "total_paise": decision.total_paise, "status": status}, session_id)
        self._log_decision(session_id, decision, PREVIEW, spent_today, spent_total, now)
        if accepted:
            self.ledger.append("crosssell.accepted", "crosssell", {"offer_ids": accepted}, session_id)
        if offers:
            self.ledger.append("crosssell.offered", "crosssell",
                               {"offers": [o.to_dict() for o in offers], "candidate_count": candidate_count}, session_id)
        return self._to_dict(self._load(session_id))

    # -- complete --------------------------------------------------------------------------------

    def _create_link(self, session_id: str, agent, mandate, total: int, attempt: int) -> LinkInfo:
        now = self.clock()
        req = PaymentRequest(session_id, agent.id, mandate.id, total, f"{self.merchant_name} order {session_id}",
                             attempt, now + self.link_ttl_s, f"{session_id}-{attempt}")
        try:
            return self.payments.create_link(req)
        except PaymentsError as exc:
            self.ledger.append("provider.error", "payments",
                               {"op": "create_link", "error": str(exc), "attempt": attempt}, session_id)
            raise

    def _link_payload(self, link: LinkInfo, total: int, attempt: int, session_id: str) -> dict:
        return {"link_id": link.link_id, "url": link.url, "amount_paise": total, "attempt": attempt,
                "expire_at": link.expire_at, "reference_id": f"{session_id}-{attempt}"}

    def _release_if_open(self, session_id: str, reason: str) -> None:
        res = self.mandates.open_for(session_id)
        if res is not None:
            self.mandates.release(session_id)
            self.ledger.append("mandate.released", "sessions",
                               {"mandate_id": res.mandate_id, "amount_paise": res.amount_paise, "reason": reason},
                               session_id)

    def complete(self, agent, session_id: str, idempotency_key: str | None) -> dict:
        row = self._owned(agent, session_id)
        if (idempotency_key and row["complete_key"] == idempotency_key
                and row["status"] in (PENDING, COMPLETED, CANCELED)):
            return self._to_dict(row)  # replay of the same complete request
        if row["status"] != READY:
            raise SessionError(409, "session_state", "wrong_state",
                               f"session is {row['status']}; complete requires ready_for_payment")
        items = [{"id": l["id"], "quantity": l["quantity"]} for l in loads(row["line_items"]) or []]
        decision, mandate, spent_today, spent_total, now = self._decide(agent, items, READY, AUTHORITATIVE, session_id)
        self._log_decision(session_id, decision, AUTHORITATIVE, spent_today, spent_total, now)
        if not decision.allowed:
            self._set(session_id, status=NOT_READY, messages=dumps(self._messages(decision)),
                      last_decision=dumps(decision.to_dict()), offers="[]")
            raise SessionError(409, "policy_denied", "policy_denied", decision.reason,
                               extra={"rule_id": decision.rule_id, "session_id": session_id})
        total = decision.total_paise
        self.mandates.reserve(session_id, mandate.id, total)
        self.ledger.append("mandate.reserved", "sessions", {"mandate_id": mandate.id, "amount_paise": total}, session_id)
        try:
            link = self._create_link(session_id, agent, mandate, total, 1)
        except PaymentsError as exc:
            self._release_if_open(session_id, "provider_error")
            raise SessionError(502, "provider_error", "payment_provider_error",
                               f"could not create a payment link: {exc}") from exc
        self._set(session_id, status=PENDING, attempt=1, link_id=link.link_id, link_url=link.url,
                  link_expire_at=link.expire_at, order_id=link.order_id, complete_key=idempotency_key,
                  last_decision=dumps(decision.to_dict()))
        self.ledger.append("payment.link.created", "payments", self._link_payload(link, total, 1, session_id), session_id)
        return self._to_dict(self._load(session_id))

    # -- reconcile: capture, retry, abandon ------------------------------------------------------

    def reconcile(self, session_id: str) -> dict | None:
        row = self._load(session_id)
        if row is None:
            return None
        if row["status"] != PENDING:
            return self._to_dict(row)
        try:
            res = self.payments.poll(row["link_id"])
        except PaymentsError as exc:
            self.ledger.append("provider.error", "reconciler",
                               {"op": "poll", "error": str(exc), "link_id": row["link_id"]}, session_id)
            return self._to_dict(row)
        if res.order_id and not row["order_id"]:
            self._set(session_id, order_id=res.order_id)
        new = [a for a in res.attempts if not self._payment_seen(session_id, a.payment_id)]
        for a in new:
            self._record_payment(session_id, a, row["attempt"])
            if a.status == "failed":
                self.ledger.append("payment.failed", "reconciler",
                                   {"razorpay_payment_id": a.payment_id, "amount_paise": a.amount_paise,
                                    "error_code": a.error_code, "error_description": a.error_description,
                                    "attempt": row["attempt"], "link_id": row["link_id"]}, session_id)
        captured = next((a for a in new if a.status == "captured"), None)
        if captured is not None:
            return self._finish_paid(session_id, captured, row["attempt"])
        failed_now = any(a.status == "failed" for a in new)
        link_dead = res.link_status in ("expired", "cancelled")
        ttl_over = row["link_expire_at"] is not None and self.clock() >= row["link_expire_at"]
        if failed_now or link_dead or ttl_over:
            reason = "payment_failed" if failed_now else ("link_" + res.link_status if link_dead else "link_expired")
            return self._after_failed_attempt(self._load(session_id), reason)
        return self._to_dict(self._load(session_id))

    def reconcile_all(self) -> list[str]:
        touched = []
        rows = self.conn.execute("select id from sessions where status = ? order by rowid", (PENDING,)).fetchall()
        for r in rows:
            before = self.ledger.count()
            self.reconcile(r["id"])
            if self.ledger.count() != before:
                touched.append(r["id"])
        return touched

    def _finish_paid(self, session_id: str, captured: Attempt, attempt_no: int) -> dict:
        row = self._load(session_id)
        self.ledger.append("payment.captured", "reconciler",
                           {"razorpay_payment_id": captured.payment_id, "amount_paise": captured.amount_paise,
                            "attempt": attempt_no, "link_id": row["link_id"]}, session_id)
        res = self.mandates.open_for(session_id)
        if res is not None:
            self.mandates.commit(session_id)
            self.ledger.append("mandate.committed", "sessions",
                               {"mandate_id": res.mandate_id, "amount_paise": res.amount_paise}, session_id)
        now = self.clock()
        self._set(session_id, status=COMPLETED, completed_at=now)
        totals = loads(row["totals"]) or {}
        self.ledger.append("session.completed", "sessions",
                           {"total_paise": totals.get("total_paise", 0), "attempts": attempt_no}, session_id)
        return self._to_dict(self._load(session_id))

    def _cancel_link_quietly(self, row, actor: str) -> None:
        if not row["link_id"]:
            return
        try:
            self.payments.cancel_link(row["link_id"])
            self.ledger.append("payment.link.cancelled", actor, {"link_id": row["link_id"]}, row["id"])
        except PaymentsError as exc:
            self.ledger.append("payment.link.cancel_failed", actor, {"link_id": row["link_id"], "error": str(exc)},
                               row["id"])

    def _abandon(self, row, reason: str) -> dict:
        session_id = row["id"]
        self._cancel_link_quietly(row, "reconciler")
        self._release_if_open(session_id, reason)
        self._set(session_id, status=CANCELED)
        self.ledger.append("payment.abandoned", "reconciler", {"attempts": row["attempt"], "reason": reason}, session_id)
        self.ledger.append("session.canceled", "sessions", {"reason": reason}, session_id)
        return self._to_dict(self._load(session_id))

    def _after_failed_attempt(self, row, reason: str) -> dict:
        session_id = row["id"]
        if row["attempt"] >= MAX_ATTEMPTS:
            return self._abandon(row, reason)
        agent = self.agents.get(row["agent_id"])
        items = [{"id": l["id"], "quantity": l["quantity"]} for l in loads(row["line_items"]) or []]
        decision, mandate, spent_today, spent_total, now = self._decide(
            agent, items, PENDING, RETRY, session_id, exclude_self=True)
        self._log_decision(session_id, decision, RETRY, spent_today, spent_total, now)
        if not decision.allowed:
            return self._abandon(row, f"retry denied by {decision.rule_id}: {decision.reason}")
        self._cancel_link_quietly(row, "reconciler")
        next_attempt = row["attempt"] + 1
        try:
            link = self._create_link(session_id, agent, mandate, decision.total_paise, next_attempt)
        except PaymentsError:
            return self._abandon(self._load(session_id), "provider_error")
        self._set(session_id, attempt=next_attempt, link_id=link.link_id, link_url=link.url,
                  link_expire_at=link.expire_at, order_id=link.order_id)
        self.ledger.append("payment.retry", "reconciler",
                           {"attempt": next_attempt, "reason": reason, "previous_link_id": row["link_id"]}, session_id)
        self.ledger.append("payment.link.created", "payments",
                           self._link_payload(link, decision.total_paise, next_attempt, session_id), session_id)
        return self._to_dict(self._load(session_id))

    # -- cancel ----------------------------------------------------------------------------------

    def cancel(self, agent, session_id: str) -> dict:
        row = self._owned(agent, session_id)
        return self._cancel(row, f"agent:{agent.id}", "agent_cancelled")

    def cancel_by_merchant(self, session_id: str) -> dict:
        row = self._load(session_id)
        if row is None:
            raise SessionError(404, "not_found", "not_found", f"no checkout session {session_id}")
        return self._cancel(row, "merchant", "merchant_cancelled")

    def _cancel(self, row, actor: str, reason: str) -> dict:
        session_id = row["id"]
        if row["status"] in TERMINAL:
            raise SessionError(409, "session_state", "wrong_state", f"session is already {row['status']}")
        if row["status"] == PENDING and row["link_id"]:
            try:
                self.payments.cancel_link(row["link_id"])
                self.ledger.append("payment.link.cancelled", actor, {"link_id": row["link_id"]}, session_id)
            except PaymentsError as exc:
                # Typical cause: the customer paid at that very moment. One final poll; never record
                # "cancelled" unless the provider confirmed it.
                captured = None
                try:
                    res = self.payments.poll(row["link_id"])
                    captured = next((a for a in res.attempts
                                     if a.status == "captured" and not self._payment_seen(session_id, a.payment_id)),
                                    None)
                except PaymentsError as exc2:
                    self.ledger.append("provider.error", actor, {"op": "poll", "error": str(exc2)}, session_id)
                if captured is not None:
                    self._record_payment(session_id, captured, row["attempt"])
                    return self._finish_paid(session_id, captured, row["attempt"])
                self.ledger.append("payment.link.cancel_failed", actor,
                                   {"link_id": row["link_id"], "error": str(exc)}, session_id)
                raise SessionError(502, "provider_error", "cancel_failed",
                                   f"could not cancel the payment link: {exc}") from exc
        self._release_if_open(session_id, reason)
        self._set(session_id, status=CANCELED)
        self.ledger.append("session.canceled", actor, {"reason": reason}, session_id)
        return self._to_dict(self._load(session_id))

    # -- reads -----------------------------------------------------------------------------------

    def get(self, agent, session_id: str, reconcile: bool = True) -> dict:
        row = self._owned(agent, session_id)
        if reconcile and row["status"] == PENDING:
            return self.reconcile(session_id)
        return self._to_dict(row)

    def get_any(self, session_id: str) -> dict | None:
        row = self._load(session_id)
        return self._to_dict(row) if row else None

    def trail(self, agent, session_id: str) -> dict:
        self._owned(agent, session_id)
        return self.trail_any(session_id)

    def trail_any(self, session_id: str) -> dict:
        events = self.ledger.events(session_id=session_id)
        v = self.ledger.verify()
        return {"session_id": session_id, "events": [e.to_dict() for e in events], "head": self.ledger.head(),
                "verify": {"ok": v.ok, "count": v.count, "bad_seq": v.bad_seq, "detail": v.detail}}

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute("select * from sessions order by created_at desc, rowid desc limit ?", (limit,))
        return [self._to_dict(r) for r in rows.fetchall()]
