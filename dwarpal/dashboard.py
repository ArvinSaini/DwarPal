"""Merchant dashboard: server-rendered pages over the same stores the API uses. Cookie auth with one token."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dwarpal.enrichment import EnrichmentStore
from dwarpal.money import rupees
from dwarpal.payments import PaymentsError
from dwarpal.policy import PolicyError
from dwarpal.sessions import SessionError

COOKIE = "merchant_session"
HERE = Path(__file__).resolve().parent


class NeedsLogin(Exception):
    pass


def _ts(t) -> str:
    if not t:
        return ""
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _paise_from_rupees(text: str, name: str) -> int:
    try:
        value = round(float(str(text).replace(",", "").strip()) * 100)
    except ValueError:
        raise ValueError(f"{name} must be a number of rupees")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return int(value)


def install_dashboard(app: FastAPI, ctx) -> None:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["rupees"] = rupees
    templates.env.filters["ts"] = _ts
    templates.env.filters["json"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    router = APIRouter(prefix="/dashboard")

    def require_merchant(request: Request) -> bool:
        if request.cookies.get(COOKIE) != ctx.settings.merchant_token:
            raise NeedsLogin()
        return True

    @app.exception_handler(NeedsLogin)
    async def _needs_login(request: Request, exc: NeedsLogin):
        return RedirectResponse("/dashboard/login", status_code=303)

    def render(request: Request, name: str, status_code: int = 200, **vars):
        base = {"merchant_name": ctx.settings.merchant_name, "msg": request.query_params.get("msg"),
                "payments_mode": ctx.payments_mode, "llm_configured": ctx.settings.llm_configured,
                "razorpay_configured": ctx.settings.razorpay_configured, "path": request.url.path}
        base.update(vars)
        return templates.TemplateResponse(request, name, base, status_code=status_code)

    def redirect(path: str, msg: str | None = None) -> RedirectResponse:
        return RedirectResponse(path + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def enrichments() -> EnrichmentStore:
        return EnrichmentStore(ctx.conn, ctx.catalog, ctx.ledger, ctx.clock)

    # -- auth -------------------------------------------------------------------------------------

    def _login(request: Request, token: str | None):
        if token is None:
            return render(request, "login.html", error=None)
        if token == ctx.settings.merchant_token:
            resp = RedirectResponse("/dashboard/", status_code=303)
            resp.set_cookie(COOKIE, token, httponly=True, samesite="lax")
            return resp
        return render(request, "login.html", error="That token was not recognised.")

    @router.get("/login", response_class=HTMLResponse)
    def login_get(request: Request, token: str | None = None):
        return _login(request, token)

    @router.post("/login", response_class=HTMLResponse)
    def login_post(request: Request, token: str = Form("")):
        return _login(request, token)

    @router.get("/logout")
    def logout():
        resp = RedirectResponse("/dashboard/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # -- overview ---------------------------------------------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request, _=Depends(require_merchant)):
        all_sessions = ctx.sessions.list(limit=1000)
        counts = Counter(s["status"] for s in all_sessions)
        v = ctx.ledger.verify()
        return render(request, "index.html", product_count=ctx.catalog.count(), agents=ctx.agents.all(),
                      pending_count=len(enrichments().pending()), sessions=all_sessions[:8], counts=dict(counts),
                      session_count=len(all_sessions), verify=v, head=ctx.ledger.head(), policy=ctx.policies.get())

    # -- products and enrichment ------------------------------------------------------------------

    @router.get("/products", response_class=HTMLResponse)
    def products(request: Request, _=Depends(require_merchant)):
        by_id = {p.id: p for p in ctx.catalog.all()}
        pending = [(e, by_id.get(e.product_id)) for e in enrichments().pending()]
        return render(request, "products.html", products=list(by_id.values()), pending=pending,
                      allowed=ctx.policies.get()["allowed_categories"])

    @router.post("/products/sync")
    def products_sync(_=Depends(require_merchant)):
        if not ctx.settings.razorpay_configured:
            return redirect("/dashboard/products", "Razorpay test keys are not configured; set RAZORPAY_KEY_ID and "
                                                   "RAZORPAY_KEY_SECRET to sync Items")
        from dwarpal.razorpay_client import RazorpayPayments, sync_items

        client = getattr(ctx.payments, "client", None)
        if client is None:
            client = RazorpayPayments(ctx.settings.razorpay_key_id, ctx.settings.razorpay_key_secret).client
        try:
            n = sync_items(ctx.catalog, client, ctx.ledger)
        except PaymentsError as exc:
            return redirect("/dashboard/products", f"Sync failed: {exc}")
        return redirect("/dashboard/products", f"Synced {n} item(s) from Razorpay")

    @router.post("/products/enrich")
    def products_enrich(_=Depends(require_merchant)):
        created = enrichments().propose_all(ctx.enricher, ctx.policies.get()["allowed_categories"])
        return redirect("/dashboard/products", f"{len(created)} proposal(s) created; review them below")

    @router.post("/enrichments/{enrichment_id}/approve")
    def enrichment_approve(enrichment_id: str, _=Depends(require_merchant)):
        try:
            e = enrichments().approve(enrichment_id)
        except KeyError:
            return redirect("/dashboard/products", "Proposal not found")
        except ValueError as exc:
            return redirect("/dashboard/products", str(exc))
        return redirect("/dashboard/products", f"Approved {e.product_id} as {e.proposal.get('category')}")

    @router.post("/enrichments/{enrichment_id}/reject")
    def enrichment_reject(enrichment_id: str, _=Depends(require_merchant)):
        try:
            e = enrichments().reject(enrichment_id)
        except KeyError:
            return redirect("/dashboard/products", "Proposal not found")
        except ValueError as exc:
            return redirect("/dashboard/products", str(exc))
        return redirect("/dashboard/products", f"Rejected proposal for {e.product_id}")

    # -- agents and mandates ----------------------------------------------------------------------

    def agent_rows():
        now = ctx.clock()
        rows = []
        for a in ctx.agents.all():
            m = ctx.mandates.active_for(a.id, now)
            spent = ctx.mandates.spent(m.id, now) if m else (0, 0)
            rows.append({"agent": a, "mandate": m, "spent_total": spent[0], "spent_today": spent[1]})
        return rows

    @router.get("/agents", response_class=HTMLResponse)
    def agents_page(request: Request, _=Depends(require_merchant)):
        return render(request, "agents.html", rows=agent_rows(), new_key=None, new_agent=None, error=None)

    @router.post("/agents", response_class=HTMLResponse)
    def agents_register(request: Request, _=Depends(require_merchant), name: str = Form(""), per_txn: str = Form("4000"),
                        daily: str = Form("8000"), total: str = Form("20000"), categories: str = Form(""),
                        days: str = Form("7")):
        try:
            if not name.strip():
                raise ValueError("name is required")
            caps = (_paise_from_rupees(per_txn, "per-order cap"), _paise_from_rupees(daily, "daily cap"),
                    _paise_from_rupees(total, "total cap"))
            ndays = int(days)
            if ndays < 1:
                raise ValueError("days must be at least 1")
            cats = [c.strip() for c in categories.split(",") if c.strip()]
            agent, key = ctx.agents.register(name)
            ctx.ledger.append("agent.registered", "merchant", {"agent_id": agent.id, "name": agent.name})
            mandate = ctx.mandates.create(agent.id, caps[0], caps[1], caps[2], cats, ctx.clock() + ndays * 86400)
            ctx.ledger.append("mandate.created", "merchant", mandate.to_dict())
        except ValueError as exc:
            return render(request, "agents.html", rows=agent_rows(), new_key=None, new_agent=None,
                          error=f"Error: {exc}")
        return render(request, "agents.html", rows=agent_rows(), new_key=key, new_agent=agent, error=None)

    @router.post("/agents/{agent_id}/revoke")
    def agents_revoke(agent_id: str, _=Depends(require_merchant)):
        try:
            ctx.agents.revoke(agent_id)
        except KeyError:
            return redirect("/dashboard/agents", "Agent not found")
        ctx.ledger.append("agent.revoked", "merchant", {"agent_id": agent_id})
        return redirect("/dashboard/agents", f"Revoked {agent_id}; its checkouts now fail rule G01")

    # -- policy -----------------------------------------------------------------------------------

    @router.get("/policy", response_class=HTMLResponse)
    def policy_page(request: Request, _=Depends(require_merchant)):
        return render(request, "policy.html", policy_json=json.dumps(ctx.policies.get(), indent=2), error=None)

    @router.post("/policy", response_class=HTMLResponse)
    def policy_save(request: Request, _=Depends(require_merchant), json_text: str = Form("", alias="json")):
        try:
            doc = json.loads(json_text)
            clean = ctx.policies.set(doc)
        except (ValueError, PolicyError) as exc:
            return render(request, "policy.html", policy_json=json_text, error=f"Error: {exc}")
        ctx.ledger.append("policy.updated", "merchant", {"policy": clean})
        return redirect("/dashboard/policy", "Policy saved")

    # -- sessions ---------------------------------------------------------------------------------

    @router.get("/sessions", response_class=HTMLResponse)
    def sessions_page(request: Request, _=Depends(require_merchant)):
        return render(request, "sessions.html", sessions=ctx.sessions.list(limit=200))

    @router.get("/sessions/{session_id}", response_class=HTMLResponse)
    def session_page(request: Request, session_id: str, _=Depends(require_merchant)):
        s = ctx.sessions.get_any(session_id)
        if s is None:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        return render(request, "session.html", s=s, trail=ctx.sessions.trail_any(session_id))

    @router.post("/sessions/{session_id}/cancel")
    def session_cancel(session_id: str, _=Depends(require_merchant)):
        try:
            ctx.sessions.cancel_by_merchant(session_id)
        except SessionError as exc:
            return redirect(f"/dashboard/sessions/{session_id}", exc.message)
        return redirect(f"/dashboard/sessions/{session_id}", "Session cancelled and any reservation released")

    @router.post("/sessions/{session_id}/approve")
    def session_approve(session_id: str, _=Depends(require_merchant), note: str = Form("")):
        try:
            s = ctx.sessions.approve_review(session_id, note, actor="merchant")
        except SessionError as exc:
            return redirect(f"/dashboard/sessions/{session_id}", exc.message)
        return redirect(f"/dashboard/sessions/{session_id}", f"Approved; the session is now {s['status']}")

    @router.post("/sessions/{session_id}/refund")
    def session_refund(session_id: str, _=Depends(require_merchant), amount: str = Form(""), reason: str = Form(""),
                       reference: str = Form("")):
        try:
            paise = _paise_from_rupees(amount, "refund amount")
            s = ctx.sessions.refund(session_id, paise, reason.strip(), reference.strip() or f"dash-{ctx.clock()}",
                                    actor="merchant")
        except ValueError as exc:
            return redirect(f"/dashboard/sessions/{session_id}", f"Error: {exc}")
        except SessionError as exc:
            rule = exc.extra.get("rule_id")
            return redirect(f"/dashboard/sessions/{session_id}", f"Refund refused{f' by {rule}' if rule else ''}: {exc.message}")
        return redirect(f"/dashboard/sessions/{session_id}",
                        f"Refund of {rupees(paise)} created ({s['refunds'][-1]['razorpay_refund_id']})")

    @router.post("/sessions/{session_id}/decline")
    def session_decline(session_id: str, _=Depends(require_merchant), note: str = Form("")):
        try:
            ctx.sessions.decline_review(session_id, note, actor="merchant")
        except SessionError as exc:
            return redirect(f"/dashboard/sessions/{session_id}", exc.message)
        return redirect(f"/dashboard/sessions/{session_id}", "Declined; the agent can change the cart")

    # -- ledger -----------------------------------------------------------------------------------

    def ledger_page(request: Request, replay_text: str | None = None, replay_ok: bool | None = None):
        events = ctx.ledger.events()
        return render(request, "ledger.html", events=events[-300:], total=len(events), verify=ctx.ledger.verify(),
                      head=ctx.ledger.head(), replay_text=replay_text, replay_ok=replay_ok)

    @router.get("/ledger", response_class=HTMLResponse)
    def ledger_get(request: Request, _=Depends(require_merchant)):
        return ledger_page(request)

    @router.post("/ledger/verify", response_class=HTMLResponse)
    def ledger_verify(request: Request, _=Depends(require_merchant)):
        return ledger_page(request)

    @router.post("/ledger/replay", response_class=HTMLResponse)
    def ledger_replay(request: Request, _=Depends(require_merchant)):
        from dwarpal.replay import render_report, replay

        report = replay(ctx.ledger)
        return ledger_page(request, render_report(report), report.ok)

    @router.get("/ledger/receipt/{session_id}")
    def ledger_receipt(session_id: str, _=Depends(require_merchant)):
        try:
            md = ctx.ledger.receipt(session_id)
        except ValueError:
            return PlainTextResponse("no such session", status_code=404)
        return PlainTextResponse(md, media_type="text/markdown")

    app.include_router(router)
