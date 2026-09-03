"""Deterministic policy gate. The thesis of the project.

``evaluate(GateInput) -> Decision`` is a pure function: every input is passed in explicitly (agent,
mandate, merchant policy, a catalog snapshot, the cart, prior spend, the clock, the session status).
No I/O, no clock reads, no LLM. Rules run in a fixed order and the first failing rule decides; every
rule that ran is recorded with a plain-English detail so the ledger can show *why*. The gate never
raises: any internal error becomes a DENY on G99 with the exception type in the trail.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from dwarpal.money import rupees

ALLOW = "ALLOW"
DENY = "DENY"
REVIEW = "REVIEW"  # not a denial: the order waits for a human at the merchant
PREVIEW = "preview"
AUTHORITATIVE = "authoritative"
RETRY = "retry"
MAX_LINES = 20

RULE_IDS = (
    "G00_WELL_FORMED", "G01_AGENT_ACTIVE", "G02_MANDATE_ACTIVE", "G03_ITEMS_KNOWN", "G04_IN_STOCK",
    "G05_SKU_NOT_BLOCKED", "G06_MERCHANT_CATEGORY", "G07_QTY_PER_LINE", "G08_ORDER_MAX",
    "G09_MANDATE_CATEGORY", "G10_PER_TXN_CAP", "G11_DAILY_CAP", "G12_TOTAL_CAP", "G13_SESSION_STATE",
    "G14_REVIEW_THRESHOLD",
)
GUARD_RULE = "G99_GATE_ERROR"

_EXPECTED_STATUS = {
    PREVIEW: {None, "not_ready_for_payment", "ready_for_payment", "requires_review"},
    AUTHORITATIVE: {"ready_for_payment"},
    RETRY: {"payment_pending"},
}


@dataclass(frozen=True)
class GateAgent:
    id: str
    status: str


@dataclass(frozen=True)
class GateMandate:
    id: str
    currency: str
    per_txn_cap_paise: int
    daily_cap_paise: int
    total_cap_paise: int
    categories: tuple[str, ...]
    starts_at: int
    expires_at: int
    status: str


@dataclass
class GateInput:
    agent: GateAgent | None
    mandate: GateMandate | None
    policy: dict
    catalog: dict[str, dict]
    items: list
    spent_today_paise: int
    spent_total_paise: int
    now: int
    session_status: str | None
    mode: str = PREVIEW
    merchant_approved: bool = False  # a merchant approved this exact cart total for this session


@dataclass
class Check:
    rule: str
    ok: bool
    detail: str


@dataclass
class Line:
    id: str
    title: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int
    category: str | None


@dataclass
class Decision:
    verdict: str
    rule_id: str
    reason: str
    checks: list[Check]
    lines: list[Line]
    total_paise: int

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW

    @property
    def needs_review(self) -> bool:
        return self.verdict == REVIEW

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "rule_id": self.rule_id, "reason": self.reason,
            "checks": [asdict(c) for c in self.checks], "lines": [asdict(l) for l in self.lines],
            "total_paise": self.total_paise,
        }


@dataclass
class RefundInput:
    session_status: str | None
    captured_paise: int
    refunded_paise: int
    amount_paise: object
    reason: object
    reference: object
    seen_references: tuple[str, ...]
    captured_at: int | None
    now: int
    window_days: int


REFUND_RULE_IDS = ("RF00_WELL_FORMED", "RF01_SESSION_COMPLETED", "RF02_WITHIN_CAPTURE", "RF03_NO_DUPLICATE",
                   "RF04_WITHIN_WINDOW")


def evaluate_refund(ri: RefundInput) -> Decision:
    """Refunds are money actions too. Same shape as ``evaluate``: ordered rules, first failure decides, never raises."""
    checks: list[Check] = []
    try:
        return _evaluate_refund(ri, checks)
    except Exception as exc:
        detail = f"internal error {type(exc).__name__}: {exc}"
        checks.append(Check(GUARD_RULE, False, detail))
        return Decision(DENY, GUARD_RULE, f"gate error: {type(exc).__name__}", checks, [], 0)


def _evaluate_refund(ri: RefundInput, checks: list[Check]) -> Decision:
    def ok(rule: str, detail: str) -> None:
        checks.append(Check(rule, True, detail))

    def fail(rule: str, detail: str, amount: int = 0) -> Decision:
        checks.append(Check(rule, False, detail))
        return Decision(DENY, rule, detail, checks, [], amount)

    amount = ri.amount_paise
    if type(amount) is not int or amount < 1:
        return fail("RF00_WELL_FORMED", "refund amount must be an integer number of paise >= 1")
    if not isinstance(ri.reason, str) or not ri.reason.strip() or len(ri.reason) > 200:
        return fail("RF00_WELL_FORMED", "a reason of 1 to 200 characters is required", amount)
    if not isinstance(ri.reference, str) or not ri.reference.strip() or len(ri.reference) > 64:
        return fail("RF00_WELL_FORMED", "a reference of 1 to 64 characters is required (used to prevent duplicates)", amount)
    ok("RF00_WELL_FORMED", f"refund {rupees(amount)} with reason and reference {ri.reference!r}")

    if ri.session_status != "completed" or type(ri.captured_paise) is not int or ri.captured_paise <= 0:
        return fail("RF01_SESSION_COMPLETED",
                    f"session is {ri.session_status!r} with {rupees(ri.captured_paise) if type(ri.captured_paise) is int else ri.captured_paise!r} captured; only a completed, captured session can be refunded",
                    amount)
    ok("RF01_SESSION_COMPLETED", f"session completed with {rupees(ri.captured_paise)} captured")

    remaining = ri.captured_paise - ri.refunded_paise
    if amount > remaining:
        return fail("RF02_WITHIN_CAPTURE",
                    f"refund {rupees(amount)} exceeds the refundable balance {rupees(remaining)} "
                    f"({rupees(ri.captured_paise)} captured, {rupees(ri.refunded_paise)} already refunded)", amount)
    ok("RF02_WITHIN_CAPTURE", f"refund {rupees(amount)} within the refundable balance {rupees(remaining)}")

    if ri.reference in ri.seen_references:
        return fail("RF03_NO_DUPLICATE", f"reference {ri.reference!r} was already refunded on this session", amount)
    ok("RF03_NO_DUPLICATE", "reference not seen before on this session")

    if type(ri.window_days) is int and ri.window_days > 0:
        if ri.captured_at is None:
            return fail("RF04_WITHIN_WINDOW", "capture time unknown; cannot verify the refund window", amount)
        deadline = ri.captured_at + ri.window_days * 86400
        if ri.now > deadline:
            return fail("RF04_WITHIN_WINDOW",
                        f"refund window of {ri.window_days} days after capture has passed", amount)
        ok("RF04_WITHIN_WINDOW", f"within the {ri.window_days}-day refund window")
    else:
        ok("RF04_WITHIN_WINDOW", "merchant has not set a refund window")

    return Decision(ALLOW, ALLOW, f"all {len(checks)} refund checks passed", checks, [], amount)


def gate_agent(agent) -> GateAgent | None:
    return None if agent is None else GateAgent(agent.id, agent.status)


def gate_mandate(mandate) -> GateMandate | None:
    if mandate is None:
        return None
    return GateMandate(mandate.id, mandate.currency, mandate.per_txn_cap_paise, mandate.daily_cap_paise,
                       mandate.total_cap_paise, tuple(mandate.categories), mandate.starts_at, mandate.expires_at,
                       mandate.status)


def evaluate(gi: GateInput) -> Decision:
    checks: list[Check] = []
    try:
        return _evaluate(gi, checks)
    except Exception as exc:  # the gate never raises
        detail = f"internal error {type(exc).__name__}: {exc}"
        checks.append(Check(GUARD_RULE, False, detail))
        return Decision(DENY, GUARD_RULE, f"gate error: {type(exc).__name__}", checks, [], 0)


def _evaluate(gi: GateInput, checks: list[Check]) -> Decision:
    def ok(rule: str, detail: str) -> None:
        checks.append(Check(rule, True, detail))

    def fail(rule: str, detail: str, lines: list[Line] | None = None, total: int = 0) -> Decision:
        checks.append(Check(rule, False, detail))
        return Decision(DENY, rule, detail, checks, lines or [], total)

    # G00: structure. Everything after this may rely on the shape of ``items``.
    items = gi.items
    if not isinstance(items, list) or not items:
        return fail("G00_WELL_FORMED", "cart has no lines")
    if len(items) > MAX_LINES:
        return fail("G00_WELL_FORMED", f"cart has {len(items)} lines; at most {MAX_LINES} are allowed")
    seen: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not it["id"]:
            return fail("G00_WELL_FORMED", f"line {i}: id must be a non-empty string")
        q = it.get("quantity")
        if type(q) is not int or q < 1:
            return fail("G00_WELL_FORMED", f"line {i} ({it['id']}): quantity must be an integer >= 1")
        if it["id"] in seen:
            return fail("G00_WELL_FORMED", f"line {i}: duplicate item {it['id']}")
        seen.add(it["id"])
    ok("G00_WELL_FORMED", f"{len(items)} line(s), quantities valid, no duplicates")

    # G01: the agent is registered and active.
    if gi.agent is None:
        return fail("G01_AGENT_ACTIVE", "agent is not registered with this merchant")
    if gi.agent.status != "active":
        return fail("G01_AGENT_ACTIVE", f"agent {gi.agent.id} is {gi.agent.status}")
    ok("G01_AGENT_ACTIVE", f"agent {gi.agent.id} is active")

    # G02: the mandate exists and is in force.
    m = gi.mandate
    if m is None:
        return fail("G02_MANDATE_ACTIVE", f"agent {gi.agent.id} has no active spend mandate")
    if m.status != "active":
        return fail("G02_MANDATE_ACTIVE", f"mandate {m.id} is {m.status}")
    if gi.now < m.starts_at:
        return fail("G02_MANDATE_ACTIVE", f"mandate {m.id} is not valid until {m.starts_at}")
    if gi.now >= m.expires_at:
        return fail("G02_MANDATE_ACTIVE", f"mandate {m.id} expired at {m.expires_at}")
    if m.currency != "INR":
        return fail("G02_MANDATE_ACTIVE", f"mandate currency {m.currency} is not INR")
    ok("G02_MANDATE_ACTIVE", f"mandate {m.id} is active until {m.expires_at}")

    # G03: every item exists; build priced lines from the merchant's own catalog snapshot.
    lines: list[Line] = []
    for it in items:
        p = gi.catalog.get(it["id"])
        if p is None:
            return fail("G03_ITEMS_KNOWN", f"unknown item {it['id']}")
        price = p["price_paise"]
        if type(price) is not int or price < 0:
            raise ValueError(f"catalog price for {it['id']} is not a non-negative integer: {price!r}")
        lines.append(Line(it["id"], p["title"], it["quantity"], price, price * it["quantity"], p.get("category")))
    total = sum(l.line_total_paise for l in lines)
    ok("G03_ITEMS_KNOWN", f"all {len(lines)} item(s) found in the catalog; cart total {rupees(total)}")

    policy = gi.policy

    # G04: stock.
    if policy.get("in_stock_only", True):
        for l in lines:
            if gi.catalog[l.id]["availability"] != "in_stock":
                return fail("G04_IN_STOCK", f"{l.id} ({l.title}) is out of stock", lines, total)
        ok("G04_IN_STOCK", "all items are in stock")
    else:
        ok("G04_IN_STOCK", "merchant policy does not require stock")

    # G05: blocked SKUs.
    blocked = set(policy.get("blocked_skus", []))
    for l in lines:
        if l.id in blocked:
            return fail("G05_SKU_NOT_BLOCKED", f"{l.id} ({l.title}) is blocked by merchant policy", lines, total)
    ok("G05_SKU_NOT_BLOCKED", "no blocked items")

    # G06: merchant category policy. An uncategorised item has not been approved for agents at all.
    allowed = list(policy.get("allowed_categories", []))
    for l in lines:
        if l.category is None:
            return fail("G06_MERCHANT_CATEGORY",
                        f"{l.id} ({l.title}) is not yet categorised; the merchant has not approved it for agents",
                        lines, total)
        if l.category not in allowed:
            return fail("G06_MERCHANT_CATEGORY",
                        f"{l.id} ({l.title}) is in category '{l.category}', which the merchant does not sell to "
                        f"agents (allowed: {', '.join(allowed) or 'none'})", lines, total)
    ok("G06_MERCHANT_CATEGORY", f"categories within merchant policy: {sorted({l.category for l in lines})}")

    # G07: quantity per line.
    max_qty = policy.get("max_qty_per_line", MAX_LINES)
    for l in lines:
        if l.quantity > max_qty:
            return fail("G07_QTY_PER_LINE",
                        f"{l.id} ({l.title}): quantity {l.quantity} exceeds the merchant limit of {max_qty} per line",
                        lines, total)
    ok("G07_QTY_PER_LINE", f"every line within the merchant limit of {max_qty} per line")

    # G08: order maximum.
    max_order = policy.get("max_order_paise")
    if type(max_order) is int and total > max_order:
        return fail("G08_ORDER_MAX", f"order total {rupees(total)} exceeds the merchant maximum {rupees(max_order)}",
                    lines, total)
    ok("G08_ORDER_MAX", f"order total {rupees(total)} within the merchant maximum {rupees(max_order)}")

    # G09: mandate categories (empty tuple = no restriction).
    if m.categories:
        for l in lines:
            if l.category not in m.categories:
                return fail("G09_MANDATE_CATEGORY",
                            f"{l.id} ({l.title}) is in '{l.category}', outside the mandate's categories "
                            f"{list(m.categories)}", lines, total)
        ok("G09_MANDATE_CATEGORY", f"all items within the mandate's categories {list(m.categories)}")
    else:
        ok("G09_MANDATE_CATEGORY", "mandate has no category restriction")

    # G10..G12: caps.
    if total > m.per_txn_cap_paise:
        return fail("G10_PER_TXN_CAP",
                    f"cart {rupees(total)} exceeds the per-transaction cap {rupees(m.per_txn_cap_paise)}", lines, total)
    ok("G10_PER_TXN_CAP", f"cart {rupees(total)} within the per-transaction cap {rupees(m.per_txn_cap_paise)}")

    if gi.spent_today_paise + total > m.daily_cap_paise:
        return fail("G11_DAILY_CAP",
                    f"today's spend {rupees(gi.spent_today_paise)} plus cart {rupees(total)} exceeds the daily cap "
                    f"{rupees(m.daily_cap_paise)}", lines, total)
    ok("G11_DAILY_CAP", f"today's spend {rupees(gi.spent_today_paise)} plus cart {rupees(total)} within the daily cap "
                        f"{rupees(m.daily_cap_paise)}")

    if gi.spent_total_paise + total > m.total_cap_paise:
        return fail("G12_TOTAL_CAP",
                    f"mandate spend {rupees(gi.spent_total_paise)} plus cart {rupees(total)} exceeds the total cap "
                    f"{rupees(m.total_cap_paise)}", lines, total)
    ok("G12_TOTAL_CAP", f"mandate spend {rupees(gi.spent_total_paise)} plus cart {rupees(total)} within the total cap "
                        f"{rupees(m.total_cap_paise)}")

    # G13: the session is in a state where this kind of evaluation makes sense (replay guard).
    expected = _EXPECTED_STATUS.get(gi.mode)
    if expected is None:
        return fail("G13_SESSION_STATE", f"unknown gate mode {gi.mode!r}", lines, total)
    if gi.session_status not in expected:
        return fail("G13_SESSION_STATE",
                    f"session is {gi.session_status!r}; a {gi.mode} evaluation requires "
                    f"{sorted(str(s) for s in expected)}", lines, total)
    ok("G13_SESSION_STATE", f"session status {gi.session_status!r} is valid for a {gi.mode} evaluation")

    # G14: orders above the merchant's review threshold wait for a human unless one already approved this total.
    threshold = policy.get("review_above_paise", 0)
    if type(threshold) is int and threshold > 0 and total > threshold:
        if gi.merchant_approved:
            ok("G14_REVIEW_THRESHOLD", f"cart {rupees(total)} is above the review threshold {rupees(threshold)} "
                                       f"and the merchant approved this exact total")
        else:
            detail = (f"cart {rupees(total)} is above the merchant's review threshold {rupees(threshold)}; "
                      f"waiting for the merchant to approve or decline")
            checks.append(Check("G14_REVIEW_THRESHOLD", False, detail))
            return Decision(REVIEW, "G14_REVIEW_THRESHOLD", detail, checks, lines, total)
    elif type(threshold) is int and threshold > 0:
        ok("G14_REVIEW_THRESHOLD", f"cart {rupees(total)} is within the review threshold {rupees(threshold)}")
    else:
        ok("G14_REVIEW_THRESHOLD", "merchant has not set a review threshold")

    return Decision(ALLOW, ALLOW, f"all {len(checks)} checks passed", checks, lines, total)
