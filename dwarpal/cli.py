"""Command line: ``python -m dwarpal <command>``. Exit codes: 0 ok, 1 configuration or provider error, 2 broken ledger."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone

from dwarpal import __version__
from dwarpal.config import Settings
from dwarpal.context import build_context
from dwarpal.demo import SCENARIOS
from dwarpal.enrichment import EnrichmentStore, FakeEnricher
from dwarpal.ledger import parse_anchor
from dwarpal.money import rupees
from dwarpal.payments import PaymentsError
from dwarpal.policy import PolicyError
from dwarpal.signing import generate_keypair


class ConfigError(Exception):
    pass


def _paise(text: str, name: str) -> int:
    try:
        value = round(float(str(text).replace(",", "")) * 100)
    except ValueError:
        raise ConfigError(f"{name} must be a number of rupees, got {text!r}")
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return int(value)


def _ts(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _require_razorpay(settings: Settings) -> None:
    if not settings.razorpay_configured:
        raise ConfigError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (Razorpay TEST keys) are not configured")


def _razorpay_client(settings: Settings):
    from dwarpal.razorpay_client import RazorpayPayments

    return RazorpayPayments(settings.razorpay_key_id, settings.razorpay_key_secret).client


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dwarpal",
                                description="DwarPal: makes a Razorpay merchant sellable to AI buyer agents, safely.")
    p.add_argument("--db", help="SQLite file (default: DWARPAL_DB or dwarpal.db)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database and store the default merchant policy")
    s = sub.add_parser("seed", help="load the Trail & Turf demo catalog")
    s.add_argument("--raw", action="store_true", help="strip categories and tags so enrichment has work to do")
    s.add_argument("--push", action="store_true", help="also create the products as Razorpay Items (test keys)")
    sub.add_parser("sync-items", help="pull the merchant's Razorpay Items into the catalog (test keys)")
    s = sub.add_parser("enrich", help="ask the model (or the fake rules) to propose catalog metadata")
    s.add_argument("--fake-llm", action="store_true", help="use the deterministic keyword rules")
    s.add_argument("--all", action="store_true", help="propose for every product, not only uncategorised ones")
    s = sub.add_parser("approve", help="approve pending enrichment proposals")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--id")
    s = sub.add_parser("reject", help="reject one pending enrichment proposal")
    s.add_argument("--id", required=True)

    s = sub.add_parser("agent", help="register, list or revoke buyer agents")
    ss = s.add_subparsers(dest="agent_cmd", required=True)
    a = ss.add_parser("add", help="register an agent with a spend mandate; prints the API key once")
    a.add_argument("name")
    a.add_argument("--per-txn", default="4000", help="per-order cap in rupees (default 4000)")
    a.add_argument("--daily", default="8000", help="daily cap in rupees (default 8000)")
    a.add_argument("--total", default="20000", help="total cap in rupees (default 20000)")
    a.add_argument("--categories", default="", help="comma-separated categories (blank = any the store sells)")
    a.add_argument("--days", type=int, default=7, help="mandate validity in days (default 7)")
    a.add_argument("--pubkey", help="base64 Ed25519 public key; from then on the agent must sign every request")
    ss.add_parser("keygen", help="generate an Ed25519 keypair for an agent operator (the merchant never sees "
                                 "the private half)")
    r = ss.add_parser("revoke")
    r.add_argument("agent_id")
    ss.add_parser("list")

    s = sub.add_parser("policy", help="show or replace the merchant policy")
    ss = s.add_subparsers(dest="policy_cmd", required=True)
    ss.add_parser("show")
    st = ss.add_parser("set")
    st.add_argument("file", help="JSON file with the policy document")

    s = sub.add_parser("serve", help="run the API and dashboard (with a background reconciler)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-reconciler", action="store_true")
    s.add_argument("--fake-payments", action="store_true", help="use the in-memory payments fake even if keys exist")

    s = sub.add_parser("reconcile", help="poll pending sessions for payment results")
    s.add_argument("--once", action="store_true")
    s.add_argument("--every", type=int, default=3, help="seconds between polls when looping")

    s = sub.add_parser("ledger", help="verify, show, tamper (demo) or export a receipt")
    ss = s.add_subparsers(dest="ledger_cmd", required=True)
    vf = ss.add_parser("verify")
    vf.add_argument("--anchor", help="a <seq>:<hash> printed earlier by `ledger anchor`; also proves the tail "
                                     "was not cut or rewritten since")
    ss.add_parser("anchor", help="print the head as <seq>:<hash>; keep it outside the database")
    ss.add_parser("replay", help="re-run every recorded gate decision from its recorded input and compare")
    sh = ss.add_parser("show")
    sh.add_argument("--limit", type=int, default=50, help="show the last N events")
    sh.add_argument("--session", help="only events for this session")
    rc = ss.add_parser("receipt")
    rc.add_argument("session_id")
    tp = ss.add_parser("tamper", help="DEMO: multiply an amount in one event without re-hashing")
    tp.add_argument("seq", type=int, nargs="?", default=None,
                    help="event to alter (default: the earliest one carrying an amount)")

    s = sub.add_parser("review", help="list, approve or decline orders waiting for merchant review")
    ss = s.add_subparsers(dest="review_cmd", required=True)
    ss.add_parser("list")
    ap = ss.add_parser("approve")
    ap.add_argument("session_id")
    ap.add_argument("--note", default="")
    dc = ss.add_parser("decline")
    dc.add_argument("session_id")
    dc.add_argument("--note", default="")

    s = sub.add_parser("refund", help="refund part or all of a completed session (gated: rules RF00-RF04)")
    s.add_argument("session_id")
    s.add_argument("--amount", required=True, help="amount in rupees")
    s.add_argument("--reason", required=True)
    s.add_argument("--reference", default=None, help="idempotent reference (default: a timestamp)")

    s = sub.add_parser("eval", help="run the adversarial gate eval (offline, no model) and print the table")
    s.add_argument("--out", help="also write the Markdown table to this file")

    s = sub.add_parser("metrics", help="run a scripted batch and write a Markdown report")
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out")

    s = sub.add_parser("demo", help="run a buyer-agent scenario end to end")
    s.add_argument("--scenario", required=True, choices=SCENARIOS)
    s.add_argument("--planner", choices=("scripted", "llm"), default="scripted")
    s.add_argument("--payments", choices=("fake", "real"), default="fake")
    s.add_argument("--wait", type=int, default=None, help="seconds to wait for the payment (fake: 30, real: 180)")
    s.add_argument("--sign", action="store_true",
                   help="the demo agent registers a public key and signs every request (ed25519)")
    return p


def main(argv=None, env=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message
        return int(exc.code or 0)
    settings = Settings.from_env(env) if env is not None else Settings.from_env()
    if args.db:
        settings.db_path = args.db
    try:
        return _dispatch(args, settings)
    except (ConfigError, PaymentsError, PolicyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args, settings: Settings) -> int:
    cmd = args.cmd

    if cmd == "init":
        ctx = build_context(settings, use_fake_payments=True)
        ctx.policies.set(ctx.policies.get())
        print(f"Initialised {settings.db_path} with the default merchant policy.")
        return 0

    if cmd == "seed":
        if args.push:
            _require_razorpay(settings)
        from dwarpal.catalog import seed

        ctx = build_context(settings, use_fake_payments=True)
        n = seed(ctx.catalog, raw=args.raw)
        print(f"Seeded {n} products for {settings.merchant_name}{' (raw: no categories yet)' if args.raw else ''}.")
        if args.push:
            from dwarpal.razorpay_client import push_items

            pushed = push_items(ctx.catalog, _razorpay_client(settings), ctx.ledger)
            print(f"Created {pushed} Razorpay Item(s) in the test account.")
        return 0

    if cmd == "sync-items":
        _require_razorpay(settings)
        from dwarpal.razorpay_client import sync_items

        ctx = build_context(settings, use_fake_payments=True)
        n = sync_items(ctx.catalog, _razorpay_client(settings), ctx.ledger)
        print(f"Synced {n} item(s) from Razorpay. Uncategorised ones need enrichment before agents can buy them.")
        return 0

    if cmd == "enrich":
        ctx = build_context(settings, use_fake_payments=True)
        enricher = FakeEnricher() if (args.fake_llm or not settings.llm_configured) else ctx.enricher
        store = EnrichmentStore(ctx.conn, ctx.catalog, ctx.ledger, ctx.clock)
        created = store.propose_all(enricher, ctx.policies.get()["allowed_categories"], only_uncategorised=not args.all)
        note = "" if settings.llm_configured and not args.fake_llm else " (fake keyword rules; set LLM_* to use a model)"
        print(f"{len(created)} proposal(s) created by {enricher.name}{note}. Review with the dashboard or `approve`.")
        for e in created:
            print(f"  {e.id}  {e.product_id:<14} -> {e.proposal['category']:<12} tags: {', '.join(e.proposal['tags'])}")
        return 0

    if cmd in ("approve", "reject"):
        ctx = build_context(settings, use_fake_payments=True)
        store = EnrichmentStore(ctx.conn, ctx.catalog, ctx.ledger, ctx.clock)
        ids = [e.id for e in store.pending()] if getattr(args, "all", False) else [args.id]
        done = 0
        for eid in ids:
            try:
                e = store.approve(eid) if cmd == "approve" else store.reject(eid)
            except KeyError:
                raise ConfigError(f"no enrichment proposal {eid}")
            except ValueError as exc:
                raise ConfigError(str(exc))
            done += 1
            print(f"  {cmd}d {e.product_id} as {e.proposal.get('category')}")
        print(f"{done} {cmd}d.")
        return 0

    if cmd == "agent":
        ctx = build_context(settings, use_fake_payments=True)
        if args.agent_cmd == "keygen":
            private, public = generate_keypair()
            print("Ed25519 keypair for an agent operator. The merchant only ever sees the public half.")
            print(f"Private key (stays with the agent): {private}")
            print(f"Public key (give to the merchant, `agent add --pubkey`): {public}")
            return 0
        if args.agent_cmd == "add":
            caps = (_paise(args.per_txn, "--per-txn"), _paise(args.daily, "--daily"), _paise(args.total, "--total"))
            cats = [c.strip() for c in args.categories.split(",") if c.strip()]
            try:
                agent, key = ctx.agents.register(args.name, public_key=args.pubkey)
            except ValueError as exc:
                raise ConfigError(f"--pubkey: {exc}")
            ctx.ledger.append("agent.registered", "merchant",
                              {"agent_id": agent.id, "name": agent.name, "signs_requests": agent.signs_requests})
            mandate = ctx.mandates.create(agent.id, caps[0], caps[1], caps[2], cats, ctx.clock() + args.days * 86400)
            ctx.ledger.append("mandate.created", "merchant", mandate.to_dict())
            print(f"Registered agent {agent.id} ({agent.name}).")
            if agent.signs_requests:
                print("This agent must sign every request (ed25519); a leaked API key alone is useless.")
            print(f"Mandate {mandate.id}: {rupees(caps[0])} per order, {rupees(caps[1])} per day, "
                  f"{rupees(caps[2])} total, categories {cats or 'any'}, until {_ts(mandate.expires_at)}.")
            print(f"API key (shown once, store it now): {key}")
            return 0
        if args.agent_cmd == "revoke":
            try:
                ctx.agents.revoke(args.agent_id)
            except KeyError:
                raise ConfigError(f"no agent {args.agent_id}")
            ctx.ledger.append("agent.revoked", "merchant", {"agent_id": args.agent_id})
            print(f"Agent {args.agent_id} revoked; its checkouts now fail rule G01.")
            return 0
        now = ctx.clock()
        for a in ctx.agents.all():
            m = ctx.mandates.active_for(a.id, now)
            caps = (f"{rupees(m.per_txn_cap_paise)} / order, {rupees(m.daily_cap_paise)} / day, "
                    f"{rupees(m.total_cap_paise)} total, {', '.join(m.categories) or 'any category'}") if m else "no active mandate"
            spent = ctx.mandates.spent(m.id, now)[0] if m else 0
            auth = "signed" if a.signs_requests else "bearer"
            print(f"{a.id}  {a.name:<16} {a.status:<8} {auth:<7} {caps}; spent {rupees(spent)}")
        return 0

    if cmd == "policy":
        ctx = build_context(settings, use_fake_payments=True)
        if args.policy_cmd == "show":
            print(json.dumps(ctx.policies.get(), indent=2))
            return 0
        try:
            with open(args.file, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"could not read policy file: {exc}")
        clean = ctx.policies.set(doc)
        ctx.ledger.append("policy.updated", "merchant", {"policy": clean})
        print("Policy saved.")
        return 0

    if cmd == "serve":
        import uvicorn

        from dwarpal.api import create_app

        ctx = build_context(settings, use_fake_payments=args.fake_payments)
        app = create_app(ctx)
        base = f"http://{args.host}:{args.port}"
        print(f"DwarPal for {settings.merchant_name}: payments={ctx.payments_mode}, "
              f"model={'configured' if settings.llm_configured else 'fake'}")
        print(f"  discovery  {base}/.well-known/agent-commerce.json")
        print(f"  api docs   {base}/docs")
        print(f"  dashboard  {base}/dashboard/login?token={settings.merchant_token}")
        if not args.no_reconciler:
            def loop():
                while True:
                    try:
                        ctx.sessions.reconcile_all()
                    except Exception as exc:  # keep polling no matter what
                        print(f"reconciler: {exc}", file=sys.stderr)
                    time.sleep(3)

            threading.Thread(target=loop, name="reconciler", daemon=True).start()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if cmd == "reconcile":
        ctx = build_context(settings)
        while True:
            touched = ctx.sessions.reconcile_all()
            print(f"{len(touched)} session(s) updated" + (f": {', '.join(touched)}" if touched else ""))
            if args.once:
                return 0
            time.sleep(args.every)

    if cmd == "ledger":
        ctx = build_context(settings, use_fake_payments=True)
        if args.ledger_cmd == "anchor":
            print(ctx.ledger.anchor())
            return 0
        if args.ledger_cmd == "verify":
            try:
                anchor = parse_anchor(args.anchor) if args.anchor else None
            except ValueError as exc:
                raise ConfigError(str(exc))
            v = ctx.ledger.verify(anchor=anchor)
            if v.ok:
                print(f"ledger chain verified: {v.count} events, head {ctx.ledger.head()}"
                      + (f"; anchor {anchor.seq}:{anchor.hash[:12]}… present" if anchor else ""))
                return 0
            print(f"ledger chain BROKEN at seq {v.bad_seq}: {v.detail}")
            return 2
        if args.ledger_cmd == "replay":
            from dwarpal.replay import render_report, replay

            report = replay(ctx.ledger)
            print(render_report(report))
            return 0 if report.ok else 2
        if args.ledger_cmd == "show":
            events = ctx.ledger.events(session_id=args.session)[-args.limit:]
            for e in events:
                payload = json.dumps(e.payload, ensure_ascii=False)
                print(f"{e.seq:>5}  {_ts(e.ts)}  {e.type:<30} {e.actor:<18} {e.session_id or '-':<18} "
                      f"{payload[:80]}  {e.hash[:12]}")
            return 0
        if args.ledger_cmd == "receipt":
            try:
                print(ctx.ledger.receipt(args.session_id))
            except ValueError as exc:
                raise ConfigError(str(exc))
            return 0
        try:
            seq = ctx.ledger.tamper(args.seq)
        except ValueError as exc:
            raise ConfigError(str(exc))
        print(f"Tampered with event {seq} (amount x10, hash untouched). Run `ledger verify` to see the break.")
        return 0

    if cmd == "review":
        from dwarpal.sessions import SessionError

        ctx = build_context(settings, use_fake_payments=True)
        if args.review_cmd == "list":
            pending = ctx.sessions.pending_reviews()
            for s in pending:
                print(f"{s['id']}  agent {s['agent_id']}  {rupees(s['totals'].get('total_paise', 0))}  "
                      f"created {_ts(s['created_at'])}")
            print(f"{len(pending)} order(s) awaiting review")
            return 0
        try:
            fn = ctx.sessions.approve_review if args.review_cmd == "approve" else ctx.sessions.decline_review
            s = fn(args.session_id, args.note, actor="merchant")
        except SessionError as exc:
            raise ConfigError(exc.message)
        print(f"{args.session_id}: {args.review_cmd}d, session is now {s['status']}")
        return 0

    if cmd == "refund":
        from dwarpal.sessions import SessionError

        ctx = build_context(settings)
        paise = _paise(args.amount, "--amount")
        reference = args.reference or f"cli-{ctx.clock()}"
        try:
            s = ctx.sessions.refund(args.session_id, paise, args.reason, reference, actor="merchant")
        except SessionError as exc:
            rule = exc.extra.get("rule_id")
            raise ConfigError(f"refund refused{f' by {rule}' if rule else ''}: {exc.message}")
        r = s["refunds"][-1]
        print(f"Refund {r['razorpay_refund_id']} of {rupees(r['amount_paise'])} created for {args.session_id} "
              f"(status {r['status']}, reference {r['reference']}).")
        return 0

    if cmd == "eval":
        from dwarpal.evalset import render_markdown, run_eval

        results = run_eval()
        md = render_markdown(results)
        print(md)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(md)
        mismatches = [r for r in results if r.verdict != r.expected_verdict
                      or (r.expected_rule and r.rule_id != r.expected_rule)]
        if mismatches:
            print(f"{len(mismatches)} case(s) did not match their expected outcome", file=sys.stderr)
            return 1
        return 0

    if cmd == "metrics":
        from dwarpal.metrics import main as metrics_main

        margs = ["--n", str(args.n), "--seed", str(args.seed)]
        if args.out:
            margs += ["--out", args.out]
        return metrics_main(margs)

    if cmd == "demo":
        from dwarpal.demo import run_demo

        if args.payments == "real":
            _require_razorpay(settings)
        llm = None
        if args.planner == "llm":
            if not settings.llm_configured:
                raise ConfigError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are not configured (see .env.example)")
            from dwarpal.llm import LLMClient

            llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
        ctx = build_context(settings, use_fake_payments=(args.payments == "fake"))
        if ctx.catalog.count() == 0:
            from dwarpal.catalog import seed

            seed(ctx.catalog)
            print("(catalog was empty; seeded the demo products)")
        wait = args.wait if args.wait is not None else (180 if args.payments == "real" else 30)
        sleep = (lambda s: None) if ctx.payments_mode == "fake" else time.sleep
        run_demo(ctx, args.scenario, planner=args.planner, llm=llm, wait_s=wait, printer=print, sleep=sleep,
                 sign=args.sign)
        return 0

    raise ConfigError(f"unknown command {cmd}")
