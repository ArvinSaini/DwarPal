"""Append-only, hash-chained audit ledger.

``hash = sha256(prev_hash + canonical_json({seq, id, ts, type, actor, session_id, payload}))``
with a genesis ``prev_hash`` of 64 zeros. Tamper-evident, not tamper-proof: the chain detects
modification, insertion, deletion and reordering, but not truncation of the tail, so the receipt's
head hash is the anchor to keep somewhere else.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from dwarpal.db import tx
from dwarpal.ids import new_id
from dwarpal.money import rupees

GENESIS = "0" * 64
TAMPER_FIELDS = ("amount_paise", "total_paise")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(prev_hash: str, body: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(body)).encode("utf-8")).hexdigest()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


@dataclass
class Event:
    seq: int
    id: str
    ts: int
    type: str
    actor: str
    session_id: str | None
    payload: dict
    prev_hash: str
    hash: str

    def body(self) -> dict:
        return {"seq": self.seq, "id": self.id, "ts": self.ts, "type": self.type, "actor": self.actor,
                "session_id": self.session_id, "payload": self.payload}

    def to_dict(self) -> dict:
        return {**self.body(), "prev_hash": self.prev_hash, "hash": self.hash}


@dataclass
class VerifyResult:
    ok: bool
    count: int
    bad_seq: int | None
    detail: str


class Ledger:
    def __init__(self, conn, clock: Callable[[], int] | None = None):
        self.conn = conn
        self.clock = clock or (lambda: int(time.time()))

    # -- writing ---------------------------------------------------------------------------------

    def append(self, type: str, actor: str, payload: dict, session_id: str | None = None) -> Event:
        # Normalise through JSON so what is hashed is exactly what a reload produces.
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        with tx(self.conn):
            row = self.conn.execute("select seq, hash from ledger order by seq desc limit 1").fetchone()
            seq = (row["seq"] + 1) if row else 1
            prev = row["hash"] if row else GENESIS
            ev = Event(seq, new_id("evt"), self.clock(), type, actor, session_id, payload, prev, "")
            ev.hash = event_hash(prev, ev.body())
            self.conn.execute(
                "insert into ledger(seq, id, ts, type, actor, session_id, payload, prev_hash, hash) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ev.seq, ev.id, ev.ts, ev.type, ev.actor, ev.session_id, canonical(ev.payload), ev.prev_hash, ev.hash),
            )
        return ev

    # -- reading ---------------------------------------------------------------------------------

    @staticmethod
    def _row(row) -> Event:
        return Event(row["seq"], row["id"], row["ts"], row["type"], row["actor"], row["session_id"],
                     json.loads(row["payload"]), row["prev_hash"], row["hash"])

    def events(self, session_id: str | None = None, limit: int | None = None) -> list[Event]:
        sql, params = "select * from ledger", []
        if session_id is not None:
            sql += " where session_id = ?"
            params.append(session_id)
        sql += " order by seq asc"
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, params).fetchall()]

    def head(self) -> str:
        row = self.conn.execute("select hash from ledger order by seq desc limit 1").fetchone()
        return row["hash"] if row else GENESIS

    def count(self) -> int:
        return self.conn.execute("select count(*) from ledger").fetchone()[0]

    def verify(self) -> VerifyResult:
        prev, expected_seq, n = GENESIS, 1, 0
        for row in self.conn.execute("select * from ledger order by seq asc"):
            n += 1
            if row["seq"] != expected_seq:
                return VerifyResult(False, n, row["seq"], f"seq {row['seq']} out of order; expected {expected_seq}")
            try:
                payload = json.loads(row["payload"])
            except ValueError:
                return VerifyResult(False, n, row["seq"], f"seq {row['seq']}: payload does not parse")
            if row["prev_hash"] != prev:
                return VerifyResult(False, n, row["seq"], f"seq {row['seq']}: prev_hash does not match the previous event")
            body = {"seq": row["seq"], "id": row["id"], "ts": row["ts"], "type": row["type"], "actor": row["actor"],
                    "session_id": row["session_id"], "payload": payload}
            if event_hash(prev, body) != row["hash"]:
                return VerifyResult(False, n, row["seq"], f"seq {row['seq']}: hash mismatch, the event was modified")
            prev, expected_seq = row["hash"], expected_seq + 1
        return VerifyResult(True, n, None, "ledger chain verified")

    # -- demo helpers ----------------------------------------------------------------------------

    def tamper(self, seq: int) -> None:
        """Multiply the first amount field of one event by 10 WITHOUT re-hashing. For the demo only."""
        row = self.conn.execute("select payload from ledger where seq = ?", (seq,)).fetchone()
        if row is None:
            raise ValueError(f"no event with seq {seq}")
        payload = json.loads(row["payload"])
        for field in TAMPER_FIELDS:
            if type(payload.get(field)) is int:
                payload[field] *= 10
                break
        else:
            raise ValueError(f"event {seq} has no amount field to tamper")
        with tx(self.conn):
            self.conn.execute("update ledger set payload = ? where seq = ?", (canonical(payload), seq))

    def receipt(self, session_id: str) -> str:
        """Markdown receipt for one session: cart, decision trail, payment attempts, chain status."""
        evs = self.events(session_id=session_id)
        if not evs:
            raise ValueError(f"no ledger events for session {session_id}")
        out: list[str] = [f"# Receipt for session {session_id}", "",
                          f"Generated {_iso(self.clock())} from {len(evs)} ledger events.", ""]

        created = next((e for e in evs if e.type == "session.created"), None)
        if created:
            out += ["## Cart", ""]
            for item in created.payload.get("items", []):
                out.append(f"- {item.get('id')} x {item.get('quantity')}")
            if "total_paise" in created.payload:
                out.append(f"- Total: {rupees(created.payload['total_paise'])}")
            out.append("")

        decisions = [e for e in evs if e.type == "gate.decision"]
        if decisions:
            d = decisions[-1].payload
            out += [f"## Last decision: {d.get('verdict')} ({d.get('rule_id')})", "", f"{d.get('reason', '')}", "",
                    "| Rule | Result | Detail |", "|---|---|---|"]
            for c in d.get("checks", []):
                out.append(f"| {c.get('rule')} | {'pass' if c.get('ok') else 'FAIL'} | {c.get('detail', '')} |")
            out.append("")

        pays = [e for e in evs if e.type.startswith("payment.") or e.type.startswith("mandate.")]
        if pays:
            out += ["## Payment and mandate events", ""]
            for e in pays:
                out.append(f"- seq {e.seq} `{e.type}` {canonical(e.payload)}")
            out.append("")

        out += ["## All events", "", "| Seq | Time | Type | Actor | Payload |", "|---|---|---|---|---|"]
        for e in evs:
            summary = canonical(e.payload)
            if len(summary) > 140:
                summary = summary[:137] + "..."
            out.append(f"| {e.seq} | {_iso(e.ts)} | {e.type} | {e.actor} | `{summary}` |")
        out.append("")

        v = self.verify()
        out += [f"- Head: `{self.head()}`",
                "- Chain: verified" if v.ok else f"- Chain: BROKEN at seq {v.bad_seq} ({v.detail})", ""]
        return "\n".join(out)
