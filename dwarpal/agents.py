"""Registered buyer agents and their API keys. Keys are shown once and stored as SHA-256."""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from dwarpal.db import tx
from dwarpal.ids import new_id


@dataclass
class Agent:
    id: str
    name: str
    status: str
    created_at: int

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status, "created_at": self.created_at}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AgentStore:
    def __init__(self, conn, clock: Callable[[], int] | None = None):
        self.conn = conn
        self.clock = clock or (lambda: int(time.time()))

    @staticmethod
    def _row(row) -> Agent:
        return Agent(row["id"], row["name"], row["status"], row["created_at"])

    def register(self, name: str) -> tuple[Agent, str]:
        name = name.strip()
        if not name:
            raise ValueError("agent name must not be empty")
        key = "agk_" + secrets.token_urlsafe(24)
        agent = Agent(new_id("agt"), name, "active", self.clock())
        with tx(self.conn):
            self.conn.execute("insert into agents(id, name, api_key_hash, status, created_at) values (?, ?, ?, ?, ?)",
                              (agent.id, agent.name, hash_key(key), agent.status, agent.created_at))
        return agent, key

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
