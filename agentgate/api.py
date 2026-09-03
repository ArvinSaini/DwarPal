"""Agent-facing HTTP API, shaped after the ACP checkout-session endpoints, plus the Razorpay webhook.

Deviations from ACP are documented in the README: bearer agent keys instead of a shared-payment token,
and an extra ``payment_pending`` status because the payer completes a Razorpay Payment Link.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agentgate import __version__
from agentgate.context import AppContext
from agentgate.db import tx
from agentgate.ledger import canonical
from agentgate.sessions import CANCELED, COMPLETED, NOT_READY, PENDING, READY, SessionError

API_VERSION = "2026-09-03"
ApiError = SessionError  # same wire shape: {type, code, message, param?}


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature.strip())


async def read_items(request: Request) -> tuple[list, str]:
    """Structural validation only; the gate does the semantic validation (rule G00 onwards)."""
    try:
        body = json.loads(await request.body() or b"")
    except ValueError:
        raise ApiError(400, "invalid_request", "invalid", "request body must be valid JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "invalid", "request body must be a JSON object")
    if "items" not in body:
        raise ApiError(400, "invalid_request", "missing", "items is required", param="items")
    if not isinstance(body["items"], list) or not body["items"]:
        raise ApiError(400, "invalid_request", "invalid", "items must be a non-empty list", param="items")
    return body["items"], hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def require_key(idempotency_key: str | None) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise ApiError(400, "invalid_request", "missing", "Idempotency-Key header is required", param="Idempotency-Key")
    return idempotency_key.strip()


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="AgentGate", version=__version__,
                  description="Makes a Razorpay merchant sellable to AI buyer agents, safely.")
    app.state.ctx = ctx

    # -- cross-cutting ----------------------------------------------------------------------------

    @app.middleware("http")
    async def echo_request_id(request: Request, call_next):
        response = await call_next(request)
        request_id = request.headers.get("Request-Id")
        if request_id:
            response.headers["Request-Id"] = request_id
        return response

    @app.exception_handler(SessionError)
    async def on_api_error(request: Request, exc: SessionError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={"type": "invalid_request", "code": "invalid",
                                                      "message": str(exc.errors()[:1])})

    def current_agent(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise ApiError(401, "unauthorized", "missing_api_key",
                           "Authorization: Bearer <agent api key> is required")
        agent = ctx.agents.authenticate(authorization[7:].strip())
        if agent is None:
            raise ApiError(401, "unauthorized", "invalid_api_key", "unknown API key")
        return agent

    # -- discovery and health ---------------------------------------------------------------------

    @app.get("/.well-known/agent-commerce.json")
    def well_known():
        policy = ctx.policies.get()
        base = ctx.settings.base_url
        return {
            "merchant_id": ctx.settings.merchant_id,
            "merchant_name": ctx.settings.merchant_name,
            "currency": "INR",
            "api_version": API_VERSION,
            "auth": "bearer",
            "feed_url": f"{base}/agent/v1/products",
            "checkout_url": f"{base}/agent/v1/checkout_sessions",
            "payment_rails": ["razorpay:payment_link"],
            "session_statuses": [NOT_READY, READY, PENDING, COMPLETED, CANCELED],
            "policy": {"allowed_categories": policy["allowed_categories"], "max_order_paise": policy["max_order_paise"],
                       "max_qty_per_line": policy["max_qty_per_line"]},
            "deviations_from_acp": [
                "payment_pending: the payer completes a Razorpay Payment Link; complete does not charge synchronously",
                "agents authenticate with a merchant-issued bearer key; no shared payment token",
            ],
            "docs": f"{base}/docs",
        }

    @app.get("/health")
    def health():
        v = ctx.ledger.verify()
        return {"status": "ok", "ledger_ok": v.ok, "ledger_events": v.count, "payments": ctx.payments_mode}

    # -- agent API --------------------------------------------------------------------------------

    router = APIRouter(prefix="/agent/v1")

    @router.get("/products")
    def products(q: str | None = None, category: str | None = None, agent=Depends(current_agent)):
        items = ctx.catalog.feed(q, category)
        return {"items": items, "count": len(items), "currency": "INR"}

    @router.post("/checkout_sessions", status_code=201)
    async def create_session(request: Request, agent=Depends(current_agent),
                             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        items, body_hash = await read_items(request)
        return ctx.sessions.create(agent, items, require_key(idempotency_key), body_hash)

    @router.get("/checkout_sessions/{session_id}")
    def get_session(session_id: str, agent=Depends(current_agent)):
        return ctx.sessions.get(agent, session_id)

    @router.post("/checkout_sessions/{session_id}")
    async def update_session(session_id: str, request: Request, agent=Depends(current_agent)):
        items, _ = await read_items(request)
        return ctx.sessions.update(agent, session_id, items)

    @router.post("/checkout_sessions/{session_id}/complete")
    def complete_session(session_id: str, agent=Depends(current_agent),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
        return ctx.sessions.complete(agent, session_id, require_key(idempotency_key))

    @router.post("/checkout_sessions/{session_id}/cancel")
    def cancel_session(session_id: str, agent=Depends(current_agent)):
        return ctx.sessions.cancel(agent, session_id)

    @router.get("/checkout_sessions/{session_id}/trail")
    def session_trail(session_id: str, agent=Depends(current_agent)):
        return ctx.sessions.trail(agent, session_id)

    app.include_router(router)

    # -- Razorpay webhook (optional; polling covers everything it does) ---------------------------

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request):
        secret = ctx.settings.razorpay_webhook_secret
        if not secret:
            raise ApiError(503, "provider_error", "webhooks_disabled",
                           "RAZORPAY_WEBHOOK_SECRET is not configured; polling reconciliation is active instead")
        body = await request.body()
        if not verify_webhook_signature(body, request.headers.get("X-Razorpay-Signature", ""), secret):
            raise ApiError(401, "unauthorized", "bad_signature", "webhook signature does not verify")
        event_id = request.headers.get("X-Razorpay-Event-Id") or hashlib.sha256(body).hexdigest()
        try:
            with tx(ctx.conn):
                ctx.conn.execute("insert into webhook_events(event_id, received_at) values (?, ?)",
                                 (event_id, ctx.clock()))
        except sqlite3.IntegrityError:
            return {"status": "duplicate", "event_id": event_id}
        try:
            doc = json.loads(body or b"{}")
        except ValueError:
            doc = {}
        event = doc.get("event") if isinstance(doc, dict) else None
        payload = (doc.get("payload") if isinstance(doc, dict) else None) or {}
        session_id = None
        for key in ("payment", "payment_link", "order"):
            entity = (payload.get(key) or {}).get("entity") or {}
            found = (entity.get("notes") or {}).get("session_id")
            if found:
                session_id = found
                break
        ctx.ledger.append("webhook.received", "webhook", {"event": event, "event_id": event_id, "session_id": session_id},
                          session_id)
        if session_id and ctx.sessions.get_any(session_id) is not None:
            ctx.sessions.reconcile(session_id)
        return {"status": "ok", "event": event, "session_id": session_id}

    return app
