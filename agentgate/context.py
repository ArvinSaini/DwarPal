"""Wiring: build every store and service from Settings, once, and hand them around as one object."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from agentgate.agents import AgentStore
from agentgate.catalog import Catalog
from agentgate.config import Settings
from agentgate.crosssell import FakePicker
from agentgate.db import connect, init_db
from agentgate.ledger import Ledger
from agentgate.mandates import MandateStore
from agentgate.payments import FakePayments
from agentgate.policy import PolicyStore
from agentgate.sessions import SessionService


@dataclass
class AppContext:
    settings: Settings
    conn: object
    clock: Callable[[], int]
    ledger: Ledger
    catalog: Catalog
    policies: PolicyStore
    agents: AgentStore
    mandates: MandateStore
    payments: object
    picker: object
    enricher: object | None
    sessions: SessionService

    @property
    def payments_mode(self) -> str:
        return "fake" if isinstance(self.payments, FakePayments) else "razorpay"


def fake_outcomes(settings: Settings) -> list[str]:
    return [o.strip() for o in (settings.fake_outcomes or "").split(",") if o.strip()]


def build_context(settings: Settings, *, conn=None, clock: Callable[[], int] | None = None, payments=None,
                  picker=None, enricher=None, use_fake_payments: bool = False) -> AppContext:
    clock = clock or (lambda: int(time.time()))
    if conn is None:
        conn = connect(settings.db_path)
    init_db(conn)
    ledger = Ledger(conn, clock)
    catalog = Catalog(conn, clock)
    policies = PolicyStore(conn, clock)
    agents = AgentStore(conn, clock)
    mandates = MandateStore(conn, clock)

    if payments is None:
        if settings.razorpay_configured and not use_fake_payments:
            from agentgate.razorpay_client import RazorpayPayments  # the only place the SDK is imported

            payments = RazorpayPayments(settings.razorpay_key_id, settings.razorpay_key_secret)
        else:
            payments = FakePayments(fake_outcomes(settings))

    if picker is None or enricher is None:
        llm = None
        if settings.llm_configured:
            from agentgate.llm import LLMClient

            llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
        if picker is None:
            if llm is not None:
                from agentgate.crosssell import LLMPicker

                picker = LLMPicker(llm, settings.llm_model)
            else:
                picker = FakePicker()
        if enricher is None:
            from agentgate.enrichment import FakeEnricher, LLMEnricher

            enricher = LLMEnricher(llm, settings.llm_model) if llm is not None else FakeEnricher()

    sessions = SessionService(conn, catalog, policies, agents, mandates, ledger, payments, picker, clock,
                              merchant_name=settings.merchant_name)
    return AppContext(settings, conn, clock, ledger, catalog, policies, agents, mandates, payments, picker,
                      enricher, sessions)
