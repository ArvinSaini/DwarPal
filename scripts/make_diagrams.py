"""Regenerate the README diagrams in docs/img/ as standalone SVG.

    python scripts/make_diagrams.py

Hand-laid-out UML-flavoured diagrams: system context, checkout sequence, the gate,
the session state machine, the domain model. No dependencies, no network, no fonts to
install: everything is drawn with plain shapes and a system sans stack, on a light card
that stays legible in GitHub's light and dark themes.
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"

FONT = "Segoe UI, Noto Sans, Helvetica Neue, Arial, sans-serif"
MONO = "Cascadia Mono, Consolas, DejaVu Sans Mono, monospace"
INK, MUTED, PAPER, EDGE, RULE = "#1b1f24", "#5f6b7a", "#fbfaf7", "#7d8894", "#e0dbd0"

# stroke, fill
PAL = {
    "core":   ("#2b5f8e", "#e8f1f8"),   # deterministic dwarpal code
    "model":  ("#a06a12", "#fdf2df"),   # the model proposes
    "ext":    ("#6b7280", "#f0f0ee"),   # outside the trust boundary
    "ledger": ("#6244a8", "#efeafb"),   # audit trail
    "store":  ("#177b7b", "#e4f3f2"),   # state
    "allow":  ("#1f7a4d", "#e6f4ec"),
    "deny":   ("#a63122", "#fbeae7"),
    "review": ("#a06a12", "#fdf2df"),
    "plain":  ("#7d7566", "#f7f5f0"),
}

WARN: list[str] = []


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tw(s: str, size: float) -> float:
    """Rough advance width for the sans stack: good enough to catch overflow."""
    return len(s) * size * 0.53


def fits(s: str, size: float, room: float, where: str) -> str:
    if tw(s, size) > room:
        WARN.append(f"{where}: {tw(s, size):.0f}px of text in {room:.0f}px  ->  {s!r}")
    return s


def text(x, y, s, size=11.5, fill=MUTED, anchor="start", weight="normal", mono=False, opacity=None):
    op = f' opacity="{opacity}"' if opacity else ""
    fam = MONO if mono else FONT
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{op}>{esc(s)}</text>')


def box(x, y, w, h, title, lines=(), kind="core", rx=9, dashed=False, double=False,
        title_size=13.0, line_size=11.0, pad=13, mono_title=False):
    stroke, fill = PAL[kind]
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
           f'stroke="{stroke}" stroke-width="1.4"{dash}/>']
    if double:  # UML final state
        out.append(f'<rect x="{x + 4}" y="{y + 4}" width="{w - 8}" height="{h - 8}" rx="{rx - 3}" '
                   f'fill="none" stroke="{stroke}" stroke-width="1"/>')
    ty = y + pad + title_size - 2
    fits(title, title_size, w - 2 * pad, f"box {title!r}")
    out.append(text(x + pad, ty, title, title_size, INK, weight="600", mono=mono_title))
    ly = ty + 15
    for ln in lines:
        fits(ln, line_size, w - 2 * pad, f"box {title!r} line")
        out.append(text(x + pad, ly, ln, line_size, MUTED))
        ly += line_size + 4
    return out


def arrow(pts, color=EDGE, dashed=False, width=1.3, head=True, back=False, size=6.5):
    """pts: [(x,y), ...] polyline. Arrowhead on the last segment."""
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    d = " ".join(f"{'M' if i == 0 else 'L'} {p[0]:.1f} {p[1]:.1f}" for i, p in enumerate(pts))
    out = [f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash} '
           f'stroke-linejoin="round" stroke-linecap="round"/>']
    if head:
        out.append(head_at(pts[-2], pts[-1], color, size))
    if back:
        out.append(head_at(pts[1], pts[0], color, size))
    return out


def head_at(a, b, color=EDGE, size=6.5):
    import math
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    p1 = (b[0] - size * math.cos(ang - 0.42), b[1] - size * math.sin(ang - 0.42))
    p2 = (b[0] - size * math.cos(ang + 0.42), b[1] - size * math.sin(ang + 0.42))
    return (f'<path d="M {b[0]:.1f} {b[1]:.1f} L {p1[0]:.1f} {p1[1]:.1f} L {p2[0]:.1f} {p2[1]:.1f} Z" '
            f'fill="{color}"/>')


def chip(x, y, label, kind, size=10.5):
    stroke, fill = PAL[kind]
    w = tw(label, size) + 16
    return [f'<rect x="{x}" y="{y}" width="{w:.1f}" height="18" rx="9" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1"/>', text(x + 8, y + 12.7, label, size, stroke, weight="600")], w


def legend(x, y, items):
    out, cx = [], x
    for lbl, kind in items:
        stroke, fill = PAL[kind]
        out.append(f'<rect x="{cx}" y="{y}" width="12" height="12" rx="3" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1.2"/>')
        out.append(text(cx + 18, y + 10.5, lbl, 10.8, MUTED))
        cx += 18 + tw(lbl, 10.8) + 22
    return out, cx


def page(w, h, title, subtitle, body, legend_items=None):
    out = [f'<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           f'role="img" aria-label="{esc(title)}">',
           f'<rect width="{w}" height="{h}" rx="10" fill="{PAPER}" stroke="{RULE}"/>',
           text(24, 36, title, 19, INK, weight="600"),
           text(24, 57, subtitle, 12, MUTED)]
    if legend_items:
        li, end = legend(0, 0, legend_items)
        width = end
        li, _ = legend(w - 24 - width, 22, legend_items)
        out += li
    out += body
    out.append("</svg>")
    return "\n".join(out)


def write(name: str, svg: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(svg + "\n", encoding="utf-8", newline="\n")
    print(f"wrote docs/img/{name}  ({len(svg):,} bytes)")


# ---------------------------------------------------------------------------------------------
# 1. System context and trust boundary
# ---------------------------------------------------------------------------------------------

def system_context() -> str:
    b = []
    # trust boundary
    b.append('<rect x="24" y="142" width="776" height="410" rx="14" fill="none" stroke="#9aa3ae" '
             'stroke-width="1.4" stroke-dasharray="7 5"/>')
    b.append(f'<rect x="24" y="130" width="244" height="24" rx="6" fill="{PAPER}" stroke="#9aa3ae" '
             'stroke-width="1.2" stroke-dasharray="7 5"/>')
    b.append(text(36, 146.5, "merchant trust boundary", 11.5, MUTED, weight="600"))

    b += box(42, 76, 366, 50, "Buyer agent (LLM)",
             ["outside the boundary · everything it sends is untrusted"], kind="ext")
    b += box(420, 76, 362, 50, "Merchant (human)",
             ["approves catalog metadata, reviews big orders, refunds"], kind="ext")

    b += box(42, 176, 366, 62, "api.py — agent API",
             ["ACP-shaped checkout sessions · discovery · webhook",
              "Bearer agk_… · Ed25519 request signatures · Idempotency-Key"])
    b += box(420, 176, 362, 62, "dashboard.py — merchant console",
             ["products · agents · policy · sessions · ledger",
              "approve enrichment, review orders, refund, verify"])

    b += box(42, 254, 740, 50, "sessions.py — the session state machine",
             ["the only code path that creates a Payment Link, and only after ALLOW plus a mandate reservation"])

    b += box(42, 322, 176, 78, "gate.py",
             ["15 purchase rules", "5 refund rules", "pure, never raises"])
    b += box(230, 322, 176, 78, "mandates.py",
             ["per-agent caps", "reserve, commit, release", "refunds give budget back"])
    b += box(418, 322, 176, 78, "ledger.py", ["append-only hash chain", "verify · anchor · replay"],
             kind="ledger")
    b += box(606, 322, 176, 78, "razorpay_client.py",
             ["the only Razorpay key", "holder · test keys only", "FakePayments in tests"])

    b += box(42, 418, 366, 62, "catalog.py · policy.py · agents.py",
             ["products and the agent feed · merchant policy document",
              "agent keys, hashed at rest · one merchant, one process"], kind="store")
    b += box(420, 418, 362, 62, "enrichment.py · crosssell.py — the model",
             ["category metadata a human must approve before the gate",
              "≤ 2 add-ons from a pre-filtered set; the gate re-judges"], kind="model")

    b += box(42, 498, 740, 36, "SQLite (WAL) — products · agents · mandates · reservations · sessions · "
                               "payments · refunds · ledger", kind="store", title_size=12)

    b += box(824, 322, 152, 78, "Razorpay", ["test mode only", "Payment Links, Orders", "Refunds, Items"],
             kind="ext")
    b += box(824, 418, 152, 62, "LLM provider", ["OpenAI-compatible ·", "FakeLLM when offline"], kind="ext")

    b += arrow([(300, 126), (300, 176)])
    b.append(text(308, 152, "Bearer agk_… + Idempotency-Key", 10.5, MUTED))
    b += arrow([(600, 126), (600, 176)])
    b.append(text(608, 152, "merchant token cookie", 10.5, MUTED))
    b += arrow([(225, 238), (225, 254)])
    b += arrow([(601, 238), (601, 254)])
    for x in (130, 318, 506, 694):
        b += arrow([(x, 304), (x, 322)])
    b += arrow([(782, 361), (824, 361)])
    b += arrow([(782, 449), (824, 449)])

    return page(1000, 582,
                "DwarPal — system context and trust boundary",
                "One Python process, one SQLite file. The model proposes; deterministic code disposes; "
                "only the payments adapter can reach Razorpay.",
                b, [("deterministic code", "core"), ("the model proposes", "model"), ("external", "ext")])


# ---------------------------------------------------------------------------------------------
# 2. Checkout sequence (happy path, with the money moment in the middle)
# ---------------------------------------------------------------------------------------------

LANES = [
    ("Buyer agent", "the demo LLM shopper", "ext"),
    ("DwarPal API", "api.py + sessions.py", "core"),
    ("Gate", "gate.py — pure", "core"),
    ("Mandates", "mandates.py", "core"),
    ("Payments", "razorpay_client.py", "core"),
    ("Razorpay", "test mode", "ext"),
    ("Ledger", "ledger.py", "ledger"),
]


def sequence() -> str:
    W, top, step = 1000, 74, 30
    lane_w = (W - 40) / len(LANES)
    cx = [20 + lane_w * (i + 0.5) for i in range(len(LANES))]
    b, bottom = [], 0

    rows = [
        (0, 1, "GET /agent/v1/products", False),
        (1, 0, "merchant-approved fields, our own prices", True),
        (0, 1, "POST /agent/v1/checkout_sessions   {items}", False),
        (1, 2, "evaluate(GateInput, mode=preview)", False),
        (2, 1, "ALLOW · every rule that ran, with a plain-English reason", True),
        (1, 6, "session.created · gate.decision  (hash-chained)", False),
        (1, 0, "ready_for_payment + at most two cross-sell offers", True),
        (0, 1, "POST /agent/v1/checkout_sessions/{id}/complete", False),
        (1, 2, "evaluate(GateInput, mode=authoritative)", False),
        (2, 1, "ALLOW  — a DENY here is 409 policy_denied with the rule_id", True),
        (1, 3, "reserve(session, mandate, total)", False),
        (1, 4, "create_link(PaymentRequest)", False),
        (4, 5, "POST /v1/payment_links", False),
        (5, 4, "link id + short url", True),
        (1, 6, "mandate.reserved · payment.link.created", False),
        (1, 0, "payment_pending + the link the buyer must open", True),
        None,  # note
        (1, 4, "poll(link) — reconciler every 3 s, on GET, or webhook", False),
        (4, 5, "fetch the link, then the order's payments", False),
        (5, 4, "captured (or failed → gate re-runs in retry mode)", True),
        (1, 3, "commit(session) — reserved money becomes spent", False),
        (1, 6, "payment.captured · mandate.committed · session.completed", False),
    ]

    y = top + 44 + 26
    lines = []
    for r in rows:
        if r is None:
            lines.append(("note", y + 6))
            y += 66
            continue
        lines.append((r, y))
        y += step
    bottom = y - step + 22

    # lifelines and headers
    for i, (name, sub, kind) in enumerate(LANES):
        stroke, _ = PAL[kind]
        b.append(f'<path d="M {cx[i]:.1f} {top + 44} L {cx[i]:.1f} {bottom}" stroke="#c3c9d1" '
                 f'stroke-width="1.2" stroke-dasharray="4 5"/>')
        b += box(cx[i] - 64, top, 128, 44, name, [sub], kind=kind, title_size=12, line_size=9.8, pad=9)

    for item, yy in lines:
        if item == "note":
            nx, nw = cx[4] - 96, 320
            b.append(f'<path d="M {nx} {yy} h {nw - 12} l 12 12 v 26 h {-nw} v -38 Z" fill="#fff8e6" '
                     f'stroke="#c9a227" stroke-width="1.2"/>')
            b.append(text(nx + 12, yy + 24, "a human pays the link on the Razorpay test checkout", 10.8, "#7a5c00"))
            continue
        i, j, label, dashed = item
        x1, x2 = cx[i], cx[j]
        d = 1 if x2 > x1 else -1
        b += arrow([(x1 + 3 * d, yy), (x2 - 3 * d, yy)], dashed=dashed,
                   color="#8d97a3" if dashed else "#3d4a58")
        mid = (x1 + x2) / 2
        b.append(f'<rect x="{mid - tw(label, 10.4) / 2 - 5:.1f}" y="{yy - 16}" '
                 f'width="{tw(label, 10.4) + 10:.1f}" height="14" fill="{PAPER}"/>')
        b.append(text(mid, yy - 5.5, label, 10.4, "#39434f" if not dashed else MUTED, anchor="middle"))
        if mid - tw(label, 10.4) / 2 < 22 or mid + tw(label, 10.4) / 2 > W - 22:
            WARN.append(f"sequence label runs off the card: {label!r}")

    return page(W, bottom + 34,
                "Checkout — one session, end to end",
                "Solid arrows are calls, dashed arrows are replies. Nothing reaches Razorpay until the gate has "
                "said ALLOW and the mandate has reserved the money.",
                b, [("dwarpal", "core"), ("audit trail", "ledger"), ("external", "ext")])


# ---------------------------------------------------------------------------------------------
# 3. The gate
# ---------------------------------------------------------------------------------------------

PAL["policy"] = ("#3f5aa6", "#eaeefb")

RULES = [
    ("G00_WELL_FORMED", "non-empty, well-typed, ≤ 20 unique lines", "plain"),
    ("G01_AGENT_ACTIVE", "the agent is registered and not revoked", "core"),
    ("G02_MANDATE_ACTIVE", "an active INR mandate, inside its window", "core"),
    ("G03_ITEMS_KNOWN", "every id exists; priced from our catalog", "store"),
    ("G04_IN_STOCK", "in stock, when the policy requires it", "policy"),
    ("G05_SKU_NOT_BLOCKED", "not on the merchant's blocked list", "policy"),
    ("G06_MERCHANT_CATEGORY", "category approved for agents", "policy"),
    ("G07_QTY_PER_LINE", "quantity within the per-line limit", "policy"),
    ("G08_ORDER_MAX", "total within the store's maximum order", "policy"),
    ("G09_MANDATE_CATEGORY", "inside the mandate's categories, if any", "core"),
    ("G10_PER_TXN_CAP", "total ≤ the per-transaction cap", "core"),
    ("G11_DAILY_CAP", "today's reserved + committed + cart ≤ cap", "core"),
    ("G12_TOTAL_CAP", "all reserved + committed + cart ≤ cap", "core"),
    ("G13_SESSION_STATE", "replay guard: preview / authoritative / retry", "plain"),
    ("G14_REVIEW_THRESHOLD", "over the threshold → REVIEW, unless approved", "review"),
    ("G99_GATE_ERROR", "an internal error is a DENY, never a raise", "deny"),
]


def gate() -> str:
    b = []
    b += box(24, 78, 892, 36, "GateInput — agent · mandate · merchant policy · catalog snapshot · cart · "
                              "reserved + committed spend · clock · session status · mode",
             kind="plain", title_size=11.5)
    b += arrow([(243, 114), (243, 132)])

    for i, (rule, detail, kind) in enumerate(RULES):
        col, row = divmod(i, 8)
        x, y = 24 + col * 454, 134 + row * 38
        stroke, fill = PAL[kind]
        b.append(f'<rect x="{x}" y="{y}" width="438" height="32" rx="7" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="1.2"/>')
        fits(rule, 10.5, 148, "rule id")
        b.append(text(x + 12, y + 20.5, rule, 10.5, stroke, weight="600", mono=True))
        fits(detail, 10.5, 266, f"rule {rule} detail")
        b.append(text(x + 160, y + 20.5, detail, 10.5, "#39434f"))

    b += arrow([(462, 416), (470, 416), (470, 150), (478, 150)])
    b.append(text(470, 128, "in order", 10, MUTED, anchor="middle"))

    b += box(24, 452, 290, 62, "ALLOW", ["reserve on the mandate, then exactly", "one Razorpay Payment Link"],
             kind="allow")
    b += box(330, 452, 290, 62, "DENY", ["the first failing rule, its id, its reason",
                                         "409 policy_denied · the agent can replan"], kind="deny")
    b += box(636, 452, 280, 62, "REVIEW", ["status requires_review — a human",
                                           "decides before anything moves"], kind="review")

    b += box(24, 530, 892, 58, "Refunds are money actions too — evaluate_refund(RefundInput)",
             ["RF00 well-formed · RF01 the session completed and a payment was captured · RF02 within the "
              "refundable balance ·",
              "RF03 no duplicate reference · RF04 inside the merchant's refund window — behind the same G99 "
              "guard, and budget goes back"], kind="plain", title_size=12)

    lg, _ = legend(24, 602, [("structure", "plain"), ("agent + mandate", "core"), ("catalog", "store"),
                             ("merchant policy", "policy"), ("review", "review"), ("guard", "deny")])
    b += lg
    return page(940, 634, "The gate — fifteen rules, first failure decides",
                "evaluate(GateInput) → Decision. A pure function: no I/O, no clock, no model. "
                "Every rule that ran is recorded with a plain-English reason.", b)


# ---------------------------------------------------------------------------------------------
# 4. Session state machine
# ---------------------------------------------------------------------------------------------

def badge(x, y, n):
    return [f'<circle cx="{x}" cy="{y}" r="9.5" fill="#ffffff" stroke="#7d8894" stroke-width="1.2"/>',
            text(x, y + 3.6, str(n), 10, "#39434f", anchor="middle", weight="700")]


STEPS = [
    "create / update, ALLOW (preview): nothing reserved yet, offers attached",
    "DENY — messages[] carry the rule id and reason; the agent can fix the cart",
    "REVIEW: the total is over the merchant's review threshold",
    "the merchant approves — stored against that exact total, so a new cart re-gates",
    "the merchant declines, with a note the agent can read",
    "update: the agent changes the cart and the gate runs again from scratch",
    "complete: authoritative gate run → reserve → exactly one Payment Link (attempt 1)",
    "complete DENY → 409 policy_denied · a provider error releases and returns 502",
    "attempt 1 failed or the link expired: retry-mode gate, cancel, fresh link",
    "a captured payment commits the reservation: reserved money becomes spent money",
    "second failure abandons · cancel releases; a refused cancel gets a final poll",
    "refund RF00–RF04: recorded, and the budget goes back against the mandate's total cap",
]


def states() -> str:
    b = ['<g transform="translate(0,16)">']
    b.append(f'<circle cx="40" cy="152" r="7" fill="#39434f"/>')
    b.append('<path d="M 112 132 L 132 152 L 112 172 L 92 152 Z" fill="#e8f1f8" stroke="#2b5f8e" '
             'stroke-width="1.4"/>')
    b += arrow([(49, 152), (88, 152)])
    b.append(text(66, 145, "create", 10, MUTED, anchor="middle"))
    b.append(text(112, 190, "the gate", 10, MUTED, anchor="middle"))

    b += box(210, 60, 200, 52, "not_ready_for_payment", ["the agent may fix the cart"],
             kind="plain", title_size=12.5, line_size=10, mono_title=True)
    b += box(210, 140, 200, 52, "requires_review", ["waiting for the merchant"],
             kind="review", title_size=12.5, line_size=10, mono_title=True)
    b += box(210, 250, 200, 52, "ready_for_payment", ["the gate said ALLOW"],
             kind="core", title_size=12.5, line_size=10, mono_title=True)
    b += box(520, 250, 200, 52, "payment_pending", ["reserved · link issued"],
             kind="policy", title_size=12.5, line_size=10, mono_title=True)
    b += box(800, 180, 200, 52, "completed", ["committed · refundable"],
             kind="allow", title_size=12.5, line_size=10, mono_title=True, double=True)
    b += box(800, 330, 200, 52, "canceled", ["reservation released"],
             kind="deny", title_size=12.5, line_size=10, mono_title=True, double=True)

    edges = [
        ([(120, 140), (210, 90)], (165, 115), 2),
        ([(134, 152), (210, 166)], (172, 159), 3),
        ([(120, 164), (210, 268)], (165, 216), 1),
        ([(260, 192), (260, 250)], (260, 221), 4),
        ([(360, 140), (360, 112)], (360, 126), 5),
        ([(410, 86), (440, 86), (440, 230), (360, 230), (360, 250)], (440, 158), 6),
        ([(210, 266), (178, 266), (178, 292), (210, 292)], (178, 279), 8),
        ([(410, 276), (520, 276)], (465, 276), 7),
        ([(570, 250), (570, 216), (670, 216), (670, 250)], (620, 216), 9),
        ([(720, 264), (800, 214)], (760, 239), 10),
        ([(720, 290), (800, 344)], (760, 317), 11),
        ([(840, 180), (840, 148), (940, 148), (940, 180)], (890, 148), 12),
    ]
    for pts, (bx, by), n in edges:
        b += arrow(pts)
        b += badge(bx, by, n)

    b.append('</g>')
    b.append(f'<path d="M 24 416 H 996" stroke="{RULE}" stroke-width="1"/>')
    for i, step in enumerate(STEPS):
        col, row = divmod(i, 6)
        x, y = 24 + col * 500, 444 + row * 21
        b += badge(x + 10, y - 3.5, i + 1)
        fits(step, 10.5, 468, f"step {i + 1}")
        b.append(text(x + 26, y, step, 10.5, "#39434f"))

    return page(1020, 596, "Session state machine — including every failure path",
                "Six states, and the merchant or the agent can always see why the session is in the one it is in. "
                "Double borders are final states.", b)


# ---------------------------------------------------------------------------------------------
# 5. Domain model
# ---------------------------------------------------------------------------------------------

def uml_class(x, y, w, name, note, attrs, kind="store"):
    stroke, fill = PAL[kind]
    h = 30 + 8 + len(attrs) * 15.5 + 6
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h:.1f}" rx="8" fill="{fill}" stroke="{stroke}" '
           f'stroke-width="1.4"/>',
           f'<path d="M {x} {y + 30} H {x + w}" stroke="{stroke}" stroke-width="1.1"/>',
           text(x + 12, y + 20, name, 12.5, INK, weight="700", mono=True),
           text(x + w - 12, y + 20, note, 10, stroke, anchor="end")]
    fits(name, 12.5, w - 40 - tw(note, 10), f"class {name}")
    ay = y + 30 + 20
    for a in attrs:
        fits(a, 10.5, w - 24, f"class {name} attr")
        out.append(text(x + 12, ay, a, 10.5, "#39434f"))
        ay += 15.5
    return out, h


def edge_label(x, y, s, size=10):
    return [f'<rect x="{x - tw(s, size) / 2 - 4:.1f}" y="{y - 9}" width="{tw(s, size) + 8:.1f}" height="13" '
            f'fill="{PAPER}"/>', text(x, y, s, size, MUTED, anchor="middle")]


def domain() -> str:
    b = []
    b += uml_class(24, 90, 280, "Agent", "merchant issues the key", [
        "id · name",
        "api_key_hash: sha256, shown once",
        "status: active | revoked",
        "public_key: Ed25519, optional — then every request is signed",
        "revoking one stops G01 dead"])[0]
    b += uml_class(24, 246, 280, "Product", "the catalog", [
        "id · title · description",
        "price_paise: int — always paise",
        "availability: in_stock | out_of_stock",
        "category: the merchant approved it",
        "attributes · tags · recommend_when",
        "snapshot(): what the gate may see"])[0]
    b.append(f'<path d="M 24 404 h 264 l 16 16 v 40 h -280 Z" fill="#fffdf5" stroke="#c9a227" '
             f'stroke-width="1.2"/>')
    b.append(text(38, 424, "The gate is priced from our own snapshot,", 10.5, "#7a5c00"))
    b.append(text(38, 440, "never from anything the agent or model sent.", 10.5, "#7a5c00"))
    b += uml_class(24, 486, 280, "Event", "append-only", [
        "seq: int · ts · type · actor",
        "session_id · payload",
        "payload holds the gate's exact input",
        "hash = sha256(prev_hash + body)"], kind="ledger")[0]

    b += uml_class(382, 90, 280, "Mandate", "per agent", [
        "id · agent_id · currency = INR",
        "per_txn_cap_paise: int",
        "daily_cap_paise: int",
        "total_cap_paise: int",
        "categories: list[str] — empty = any",
        "starts_at · expires_at · status"])[0]
    b += uml_class(382, 296, 280, "Session", "the state machine", [
        "id · agent_id · mandate_id",
        "status: one of the six states",
        "line_items · totals · messages · offers",
        "last_decision: the gate's verdict",
        "link_id · link_url · order_id · attempt",
        "idempotency_key · create_body_hash"])[0]

    b += uml_class(740, 90, 280, "Reservation", "the accounting", [
        "id · session_id · mandate_id",
        "amount_paise: int",
        "state: reserved | committed | released",
        "spend = reserved + committed"])[0]
    b += uml_class(740, 246, 280, "Decision", "what the gate returns", [
        "verdict: ALLOW | DENY | REVIEW",
        "rule_id · reason",
        "checks: list[Check]",
        "lines: list[Line] — priced by us",
        "total_paise: int"], kind="core")[0]
    b += uml_class(740, 398, 280, "Check", "one rule, one answer", [
        "rule: the rule id",
        "ok: bool",
        "detail: plain English, for a human"], kind="core")[0]
    b += uml_class(740, 520, 280, "Payment · Refund", "what really happened", [
        "razorpay_payment_id · attempt: int",
        "status: captured | failed",
        "refund: amount · reason · reference",
        "a refund gives total-cap budget back"])[0]

    rels = [
        ([(304, 143), (382, 143)], (343, 139), "issues", ("1", 310, 137), ("*", 376, 137)),
        ([(662, 143), (740, 143)], (701, 139), "holds", ("1", 668, 137), ("*", 734, 137)),
        ([(304, 175), (343, 175), (343, 350), (382, 350)], (343, 262), "opens", ("1", 310, 169), ("*", 376, 344)),
        ([(662, 330), (701, 330), (701, 196)], (701, 264), "0..1 open", None, None),
        ([(662, 300), (740, 300)], (701, 296), "records", None, None),
        ([(880, 367), (880, 398)], (912, 386), "1 .. *", None, None),
        ([(522, 433), (522, 470), (164, 470), (164, 486)], (410, 466), "every decision, every money action",
         None, None),
        ([(740, 560), (690, 560), (690, 450), (620, 450), (620, 433)], (690, 500), "attempts", None, None),
    ]
    for pts, (lx, ly), label, m1, m2 in rels:
        b += arrow(pts, head=False)
        b += edge_label(lx, ly, label)
        for m in (m1, m2):
            if m:
                b.append(text(m[1], m[2], m[0], 10, MUTED, weight="600"))
    b.append('<path d="M 880 367 l 7 10 l -7 10 l -7 -10 Z" fill="#2b5f8e" stroke="#2b5f8e"/>')

    return page(1044, 660, "Domain model — what the ledger can prove",
                "Money is an integer number of paise everywhere. Every box below is a SQLite table or the pure "
                "value the gate returns.",
                b, [("state we store", "store"), ("the gate's answer", "core"), ("audit trail", "ledger")])


DIAGRAMS = {
    "01-system-context.svg": system_context,
    "02-checkout-sequence.svg": sequence,
    "03-gate.svg": gate,
    "04-session-states.svg": states,
    "05-domain-model.svg": domain,
}


def main() -> int:
    for name, fn in DIAGRAMS.items():
        write(name, fn())
    for w in WARN:
        print(f"warning: {w}", file=sys.stderr)
    return 1 if WARN else 0


if __name__ == "__main__":
    raise SystemExit(main())
