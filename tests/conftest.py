from dataclasses import dataclass

import pytest

from agentgate.agents import Agent, AgentStore
from agentgate.catalog import Catalog, seed
from agentgate.crosssell import FakePicker
from agentgate.db import connect, init_db
from agentgate.ledger import Ledger
from agentgate.mandates import Mandate, MandateStore
from agentgate.payments import FakePayments
from agentgate.policy import PolicyStore
from agentgate.sessions import SessionService


class Clock:
    """Deterministic clock for tests. Default: 2026-09-03T12:26:40Z."""

    def __init__(self, now: int = 1_756_900_000):
        self.now = now

    def __call__(self) -> int:
        return self.now

    def tick(self, seconds: int = 1) -> int:
        self.now += seconds
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


@dataclass
class World:
    conn: object
    clock: Clock
    ledger: Ledger
    catalog: Catalog
    policies: PolicyStore
    agents: AgentStore
    mandates: MandateStore
    payments: FakePayments
    sessions: SessionService
    agent: Agent
    api_key: str
    mandate: Mandate


@pytest.fixture
def world(conn, clock) -> World:
    ledger = Ledger(conn, clock)
    catalog = Catalog(conn, clock)
    seed(catalog)
    policies = PolicyStore(conn, clock)
    agents = AgentStore(conn, clock)
    mandates = MandateStore(conn, clock)
    payments = FakePayments()
    sessions = SessionService(conn, catalog, policies, agents, mandates, ledger, payments, FakePicker(), clock)
    agent, key = agents.register("shopbot")
    mandate = mandates.create(agent.id, 400000, 800000, 2000000, [], clock.now + 7 * 86400)
    return World(conn, clock, ledger, catalog, policies, agents, mandates, payments, sessions, agent, key, mandate)


def make_ctx(world: World, **settings_over):
    """An AppContext over the test world's stores."""
    from agentgate.config import Settings
    from agentgate.context import AppContext
    from agentgate.enrichment import FakeEnricher

    overrides = {"razorpay_webhook_secret": "whsec", "merchant_token": "merchant-secret"}
    overrides.update(settings_over)
    settings = Settings(db_path=":memory:", **overrides)
    return AppContext(settings=settings, conn=world.conn, clock=world.clock, ledger=world.ledger,
                      catalog=world.catalog, policies=world.policies, agents=world.agents, mandates=world.mandates,
                      payments=world.payments, picker=FakePicker(), enricher=FakeEnricher(), sessions=world.sessions)


def make_client(world: World, **settings_over):
    """A FastAPI TestClient wired to the test world. Authenticated as the world's agent by default."""
    from fastapi.testclient import TestClient

    from agentgate.api import create_app

    client = TestClient(create_app(make_ctx(world, **settings_over)))
    client.headers.update({"Authorization": f"Bearer {world.api_key}"})
    return client


@pytest.fixture
def app_client(world):
    return make_client(world)
