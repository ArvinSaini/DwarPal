"""The buyer agent loop: ask the planner for an action, run it against the API, narrate, repeat."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Callable

from agentgate.buyer.client import GateAPIError, GateClient
from agentgate.buyer.planner import Action, Planner
from agentgate.money import rupees

TERMINAL = ("completed", "canceled")


@dataclass
class RunResult:
    outcome: str  # paid | payment_pending | refused | canceled | incomplete | no_plan
    session_id: str | None
    payment_url: str | None
    total_paise: int
    narrative: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    session: dict | None = None


class BuyerAgent:
    def __init__(self, client: GateClient, planner: Planner, clock: Callable[[], int] | None = None,
                 sleep: Callable[[float], None] | None = None, wait_for_payment_s: int = 0, poll_every_s: int = 3,
                 printer: Callable[[str], None] | None = None):
        self.client = client
        self.planner = planner
        self.clock = clock or (lambda: int(time.time()))
        self.sleep = sleep or time.sleep
        self.wait_for_payment_s = wait_for_payment_s
        self.poll_every_s = poll_every_s
        self.printer = printer
        self.session: dict | None = None

    # -- run loop --------------------------------------------------------------------------------

    def run(self, max_steps: int = 12) -> RunResult:
        history: list[dict] = []
        narrative: list[str] = []
        self.session = None

        def say(line: str) -> None:
            narrative.append(line)
            if self.printer:
                self.printer(line)

        for _ in range(max_steps):
            action = self.planner.decide(history)
            if action.say:
                say(action.say)
            if action.tool == "done":
                break
            result = self._execute(action, say)
            history.append({"action": action, "result": result})
        else:
            say(f"Stopped after {max_steps} steps without finishing.")

        outcome = self._wait_and_classify(say)
        s = self.session or {}
        payment = s.get("payment") or {}
        return RunResult(outcome, s.get("id"), payment.get("url"), (s.get("totals") or {}).get("total_paise", 0),
                         narrative, history, self.session)

    # -- actions ---------------------------------------------------------------------------------

    def _resolve(self, args: dict, say) -> dict:
        """Replace ``$session`` with the current session id and ``$offerN`` with the N-th offered item id."""
        args = copy.deepcopy(args)
        if args.get("session_id") == "$session":
            if self.session is None:
                raise ValueError("no checkout session yet")
            args["session_id"] = self.session["id"]
        items = args.get("items")
        if isinstance(items, list):
            resolved = []
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("id"), str) and it["id"].startswith("$offer"):
                    offers = (self.session or {}).get("offers") or []
                    try:
                        idx = int(it["id"][6:] or 0)
                    except ValueError:
                        idx = 0
                    if idx < len(offers):
                        resolved.append({**it, "id": offers[idx]["id"]})
                        say(f"Accepting offered add-on: {offers[idx]['title']} ({rupees(offers[idx]['price_paise'])})")
                    else:
                        say("No offered add-on to accept; keeping the cart as is.")
                    continue
                resolved.append(it)
            args["items"] = resolved
        return args

    def _execute(self, action: Action, say) -> dict:
        try:
            args = self._resolve(action.args, say)
            tool = action.tool
            if tool == "list_products":
                items = self.client.products(args.get("q"), args.get("category"))
                filt = f" matching '{args.get('q') or args.get('category')}'" if (args.get("q") or args.get("category")) else ""
                say(f"Browsed {len(items)} products{filt}.")
                return {"items": items, "count": len(items)}
            if tool == "create_checkout_session":
                s = self.client.create(args.get("items") or [])
                self.session = s
                self._describe("Created", s, say)
                return s
            if tool == "update_checkout_session":
                s = self.client.update(args["session_id"], args.get("items") or [])
                self.session = s
                self._describe("Updated", s, say)
                return s
            if tool == "complete_checkout_session":
                s = self.client.complete(args["session_id"])
                self.session = s
                pay = s.get("payment") or {}
                say(f"Checkout complete: pay {rupees(pay.get('amount_paise', 0))} at {pay.get('url')} "
                    f"(attempt {pay.get('attempt', 1)}).")
                return s
            if tool == "get_checkout_session":
                s = self.client.get(args["session_id"])
                if self.session is None or s.get("id") == self.session.get("id"):
                    self.session = s
                self._describe("Checked", s, say)
                return s
            say(f"Unknown tool {tool}; ignoring.")
            return {"error": {"message": f"unknown tool {tool}"}}
        except GateAPIError as exc:
            rule = f", rule {exc.body['rule_id']}" if "rule_id" in exc.body else ""
            say(f"API error {exc.status_code}: {exc.body.get('message')} ({exc.body.get('code')}{rule})")
            return {"error": exc.body, "status_code": exc.status_code}
        except (ValueError, KeyError, TypeError) as exc:
            say(f"Could not run {action.tool}: {exc}")
            return {"error": {"message": f"could not run {action.tool}: {exc}"}}

    def _describe(self, verb: str, s: dict, say) -> None:
        total = rupees((s.get("totals") or {}).get("total_paise", 0))
        status = s.get("status")
        if status == "not_ready_for_payment":
            msg = (s.get("messages") or [{}])[0]
            say(f"{verb} session {s['id']}: refused by the gate, rule {msg.get('rule_id')}: {msg.get('text')}")
        elif status == "requires_review":
            say(f"{verb} session {s['id']}: {total} is above the store's review threshold; waiting for the merchant "
                f"to approve")
        elif status == "ready_for_payment":
            offers = s.get("offers") or []
            extra = (", offers: " + ", ".join(f"{o['title']} ({rupees(o['price_paise'])})" for o in offers)) if offers else ""
            say(f"{verb} session {s['id']}: ready, total {total}{extra}")
        elif status == "payment_pending":
            pay = s.get("payment") or {}
            say(f"{verb} session {s['id']}: awaiting payment (attempt {s.get('attempt')}) at {pay.get('url')}")
        elif status == "completed":
            say(f"{verb} session {s['id']}: payment captured, {total}")
        else:
            say(f"{verb} session {s['id']}: {status}")

    # -- after the loop --------------------------------------------------------------------------

    def _wait_and_classify(self, say) -> str:
        s = self.session
        if s is None:
            return "no_plan"
        if s.get("status") == "payment_pending" and self.wait_for_payment_s > 0:
            deadline = self.clock() + self.wait_for_payment_s
            last_attempt = s.get("attempt")
            say(f"Waiting up to {self.wait_for_payment_s}s for the payment to complete...")
            while self.clock() < deadline:
                self.sleep(self.poll_every_s)
                try:
                    s = self.client.get(s["id"])
                except GateAPIError as exc:
                    say(f"Could not check the payment: {exc}")
                    break
                self.session = s
                if s.get("attempt") != last_attempt:
                    pay = s.get("payment") or {}
                    say(f"Payment attempt {last_attempt} failed; the store issued a fresh link "
                        f"(attempt {s.get('attempt')}): {pay.get('url')}")
                    last_attempt = s.get("attempt")
                if s.get("status") in TERMINAL:
                    break
            s = self.session
        status = s.get("status")
        if status == "completed":
            say(f"Payment captured: {rupees((s.get('totals') or {}).get('total_paise', 0))}. Done.")
            return "paid"
        if status == "payment_pending":
            return "payment_pending"
        if status == "not_ready_for_payment":
            return "refused"
        if status == "requires_review":
            return "requires_review"
        if status == "canceled":
            return "canceled"
        return "incomplete"
