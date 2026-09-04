"""Registered buyer agents and their API keys. Keys are shown once and stored as SHA-256."""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from dwarpal.db import tx
from dwarpal.ids import new_id
from dwarpal.signing import load_public_key


@dataclass
class Agent:
    id: str
    name: str
    status: str
    created_at: int
    public_key: str | None = None  # base64 Ed25519; set => every request must be signed

    @property
    def signs_requests(self) -> bool:
        return bool(self.public_key)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status, "created_at": self.created_at,
                "signs_requests": self.signs_requests}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AgentStore:
    def __init__(self, conn, clock: Callable[[], int] | None = None):
        self.conn = conn
        self.clock = clock or (lambda: int(time.time()))

    @staticmethod
    def _row(row) -> Agent:
        return Agent(row["id"], row["name"], row["status"], row["created_at"], row["public_key"])

    def register(self, name: str, public_key: str | None = None) -> tuple[Agent, str]:
        name = name.strip()
        if not name:
            raise ValueError("agent name must not be empty")
        public_key = (public_key or "").strip() or None
        if public_key:
            load_public_key(public_key)  # ValueError if it is not a 32-byte Ed25519 key
        key = "agk_" + secrets.token_urlsafe(24)
        agent = Agent(new_id("agt"), name, "active", self.clock(), public_key)
        with tx(self.conn):
            self.conn.execute("insert into agents(id, name, api_key_hash, status, created_at, public_key) "
                              "values (?, ?, ?, ?, ?, ?)",
                              (agent.id, agent.name, hash_key(key), agent.status, agent.created_at, public_key))
        return agent, key

    # -- replay protection for signing agents ----------------------------------------------------

    def remember_nonce(self, agent_id: str, nonce: str, now: int) -> bool:
        """True the first time this agent presents this nonce, False on every replay."""
        with tx(self.conn):
            cur = self.conn.execute("insert or ignore into agent_nonces(agent_id, nonce, ts) values (?, ?, ?)",
                                    (agent_id, nonce, now))
            return cur.rowcount == 1

    def prune_nonces(self, now: int, max_age_s: int) -> int:
        with tx(self.conn):
            return self.conn.execute("delete from agent_nonces where ts < ?", (now - max_age_s,)).rowcount

    def authenticate(self, key: str | None) -> Agent | None:
        """Return the agent for this key whatever its status; the gate reports a revoked agent explicitly."""
        if not key:
            return None
        row = self.conn.execute("select * from agents where api_key_hash = ?", (hash_key(key),)).fetchone()
        return self._row(row) if row else None

    def get(self, agent_id: str) -> Agent | None:
        row = self.conn.execute("select * from agents where id = ?", (agent_id,)).fetchone()
        return self._row(row) if row else None

    def revoke(self, agent_id: str) -> Agent:
        with tx(self.conn):
            cur = self.conn.execute("update agents set status = 'revoked' where id = ?", (agent_id,))
            if cur.rowcount == 0:
                raise KeyError(agent_id)
        return self.get(agent_id)

    def all(self) -> list[Agent]:
        return [self._row(r) for r in self.conn.execute("select * from agents order by created_at, rowid").fetchall()]
