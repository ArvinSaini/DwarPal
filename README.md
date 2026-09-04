# DwarPal

**Makes a Razorpay merchant sellable to AI buyer agents, safely.**

*DwarPal* (द्वारपाल) is the gatekeeper: the one who decides who comes through the merchant's door, and on what terms.

> The model proposes. Deterministic code disposes. Only the payments adapter can reach Razorpay.

[![CI](https://github.com/ArvinSaini/DwarPal/actions/workflows/ci.yml/badge.svg)](https://github.com/ArvinSaini/DwarPal/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce · Python · Razorpay test mode · zero-cost stack

[Quickstart](#quickstart) · [How it fits together](#how-it-fits-together) · [What an agent sees](#what-an-agent-sees) ·
[The gate](#the-gate) · [Sessions and failure recovery](#sessions-and-failure-recovery) · [Data model](#data-model) ·
[Evaluation](#evaluation) · [Scope](#scope-and-guarantees)

AI assistants are starting to shop on people's behalf. NPCI's Unified Agent Protocol, Razorpay's agentic-payments
pilots and the OpenAI/Stripe Agentic Commerce Protocol all point the same way: merchants will be asked to sell to
machines. DwarPal is the merchant-side gateway that lets them do it without losing control: an agent-readable
catalog, an ACP-shaped checkout API, a deterministic policy gate with a human review queue, per-agent spend mandates
with reserve/commit/release accounting, Razorpay Payment Links with one gated retry, gated refunds, a hash-chained
audit ledger that can be replayed, and a bounded cross-sell that grows the basket.

## How it fits together

![DwarPal system context. The buyer agent and the merchant sit outside a dashed trust boundary. Inside it, the agent
API and the merchant console both feed one session state machine, which is the only code path that creates a Payment
Link; under it sit the pure gate, the mandate accounting, the append-only ledger and the Razorpay adapter, then the
catalog stores and the two model-facing components, all over one SQLite file. Razorpay and the LLM provider are
outside on the right.](docs/img/01-system-context.svg)

One process, one SQLite file, one place where money moves. Blue is deterministic code, amber is where the model
proposes and something else disposes, and `razorpay_client.py` is the only module that holds a Razorpay key. The
five diagrams in this README are generated, not drawn: `python scripts/make_diagrams.py`.

## The bar, and where it lives

Track 01 asks: *"Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully."*

| Bar | Where it lives |
|---|---|
| **Explainable** | Every checkout runs the 15-rule gate and records every rule with a plain-English detail (`gate.decision` in the ledger, `decision.checks[]` in the API, the session page in the dashboard). A refused cart tells the agent which rule and why. Refunds get the same treatment (`refund.decision`). |
| **Bounded** | Merchant policy (categories sold to agents, max order, stock, blocked SKUs, quantity per line, review threshold, refund window) plus a per-agent mandate (per order, per day, total, categories, expiry). Spend counts reserved *and* committed money, so a pending payment cannot be double-spent. `dwarpal/gate.py` is a pure function. |
| **Gated** | The LLM never decides about money. It proposes catalog metadata (merchant approves), picks cross-sell offers from a pre-filtered candidate set (agent may accept, gate re-judges), and plans the demo buyer. `SessionService.complete` is the only path that creates a Payment Link, and only after ALLOW plus a reservation. Orders above the review threshold wait for a human. Refunds pass rules RF00 to RF04. |
| **Audit trail** | Append-only ledger, `sha256(prev_hash + event)`. `ledger verify` proves nothing was edited; `ledger replay` re-runs every recorded decision from its recorded input and proves the reasoning; `ledger receipt <session>` exports one session; `ledger anchor` prints the head as `seq:hash` and `ledger verify --anchor` proves the tail was not cut or rewritten since; `ledger tamper <seq>` for the camera. |
| **Failure handled** | Payment fails on the bank page → recorded, gate re-run in retry mode, old link cancelled, fresh link issued; second failure → abandon and release. Also: cap denials at complete, provider errors (release, 502, agent may retry), duplicate webhooks, a cancel Razorpay refuses (final poll; a late capture is never recorded as cancelled), revoked agents, poisoned catalog text, model outages (fail closed). |

## Quickstart

```powershell
git clone https://github.com/ArvinSaini/DwarPal.git; cd DwarPal
python -m pip install -e .                 # Python 3.11+
copy .env.example .env                     # add rzp_test_ keys to use Razorpay; leave blank to stay fully offline
python -m dwarpal init
python -m dwarpal seed                   # Trail & Turf, 10 demo products (add --raw to see enrichment work)
python -m dwarpal agent add shopbot --per-txn 4000 --daily 8000 --total 20000
python -m dwarpal demo --scenario replan --payments fake     # refused, replans, pays: whole trail printed
python -m dwarpal serve                  # API + dashboard at http://127.0.0.1:8000
```

Everything above runs offline. Tests never touch the network:

```powershell
python -m pytest -q                        # 311 tests, ~10 s
python -m dwarpal eval                   # adversarial gate eval: block rate, false-positive rate
python -m dwarpal metrics --n 50         # honest batch report
python -m dwarpal ledger replay          # re-run every recorded decision and compare
```

With Razorpay **test** keys in `.env`: `seed --push` creates the demo products as Razorpay Items, `sync-items`
pulls them back, `demo --scenario payfail --payments real` prints a real Payment Link, and `refund` calls the real
refund API. Pay a link on the test checkout: Netbanking's mock bank page has **Success** and **Failure** buttons
(UPI ids `success@razorpay` / `failure@razorpay` appear when UPI is enabled on the account). Live keys are refused.

With an LLM key (`LLM_*` in `.env`; Gemini's free tier by default, or Groq, NVIDIA NIM, Ollama): `enrich`
uses the model, cross-sell uses the model, and `demo --planner llm` runs a real tool-calling buyer.

## What an agent sees

```
GET  /.well-known/agent-commerce.json            discovery: feed, checkout URL, policy summary, deviations
GET  /agent/v1/products?q=&category=              agent-readable catalog (only merchant-approved fields)
POST /agent/v1/checkout_sessions                  {items:[{id, quantity}]}  -> session (+ offers, or messages[] with rule_id)
POST /agent/v1/checkout_sessions/{id}             replace items; include an offered id to accept it
POST /agent/v1/checkout_sessions/{id}/complete    authoritative gate run, reservation, Razorpay link -> payment_pending
GET  /agent/v1/checkout_sessions/{id}             current state (polls Razorpay when pending)
POST /agent/v1/checkout_sessions/{id}/cancel      cancel link, release reservation
GET  /agent/v1/checkout_sessions/{id}/trail       the session's ledger events, chain verification, ledger_head anchor
POST /webhooks/razorpay                           optional; polling covers everything it does
```

A file snapshot of the discovery document, the feed and the policy for the demo merchant lives in `data/`,
written by `python -m dwarpal export` so it never drifts from what the server says.

![Sequence diagram of one checkout. The buyer agent reads the feed, creates a session, the API asks the pure gate
in preview mode and writes session.created and gate.decision to the ledger, returns ready_for_payment with offers;
on complete the gate runs authoritatively, the mandate reserves the total, the adapter creates one Razorpay Payment
Link, a human pays it, polling sees the capture, the reservation commits and the ledger records
session.completed.](docs/img/02-checkout-sequence.svg)

Auth is `Authorization: Bearer agk_...` (issued per agent by the merchant). An agent that registers an Ed25519
public key (`agent add --pubkey`, or the dashboard) must also sign every request: `X-Agent-Timestamp` within
300 s of the merchant's clock, an `X-Agent-Nonce` accepted once, and an `X-Agent-Signature` over the method, the
path with its query string and the sha256 of the body, so a leaked key alone buys nothing and a captured request
cannot be replayed. The discovery document spells out the canonical string. `Idempotency-Key` is required on
create and complete. Errors are `{type, code, message, param?}`; a policy denial carries `rule_id`; an order
waiting for the merchant answers `requires_review`; a signing failure is a typed 401 (`signature_required`,
`stale_timestamp`, `bad_signature`, `replayed_nonce`).

```powershell
$H = @{ Authorization = "Bearer agk_..."; "Idempotency-Key" = "k1" }
Invoke-RestMethod -Method Post http://127.0.0.1:8000/agent/v1/checkout_sessions -Headers $H -ContentType application/json `
  -Body '{"items":[{"id":"prod_shoes","quantity":1}]}'
```

## Where the AI is, and where it is not

| Component | Model does | Deterministic code does |
|---|---|---|
| Catalog enrichment | proposes category, attributes, tags, "recommend when" from raw Razorpay Item text | validates the JSON, parks it as *pending*; a human approves before the gate can see the category |
| Cross-sell | picks at most two add-ons and a one-line reason | builds the candidate set (in stock, allowed by policy and mandate, priced under the headroom left by every cap); validates the picks; the gate re-judges the cart |
| Buyer agent (demo client) | plans tool calls from the user's intent; replans after a refusal | executes the calls, wraps catalog text as untrusted, narrates |
| Gate, mandates, sessions, review, refunds, payments, ledger | nothing | everything |

Every model call goes through one OpenAI-compatible client, and every AI component has a deterministic fake, so the
suite runs offline and the demo works without a paid key. One seed product carries a prompt injection on purpose.

## The gate

`evaluate(GateInput) -> Decision`: fifteen ordered rules, first failure decides, every rule recorded. Verdicts are
ALLOW, DENY, or REVIEW (a human decides).

![The gate. A GateInput of agent, mandate, merchant policy, catalog snapshot, cart, reserved plus committed spend,
clock, session status and mode runs through fifteen rules in a fixed order, G00 to G14 plus the G99 guard, grouped by
where each one comes from; the outcomes are ALLOW (reserve, then one Payment Link), DENY (the first failing rule, its
id and its reason) and REVIEW (a human decides). Refunds run the same way through RF00 to
RF04.](docs/img/03-gate.svg)

The same rules as text, with what each one refuses:

| Rule | Checks |
|---|---|
| G00_WELL_FORMED | non-empty cart, string ids, integer quantities ≥ 1, no duplicates, ≤ 20 lines |
| G01_AGENT_ACTIVE | registered, not revoked |
| G02_MANDATE_ACTIVE | active INR mandate inside its window |
| G03_ITEMS_KNOWN | every item exists; lines priced from the merchant's own catalog |
| G04_IN_STOCK | in stock (policy) |
| G05_SKU_NOT_BLOCKED | not blocked (policy) |
| G06_MERCHANT_CATEGORY | category approved for agents; uncategorised items fail here |
| G07_QTY_PER_LINE | per-line quantity limit (policy) |
| G08_ORDER_MAX | store's maximum order (policy) |
| G09_MANDATE_CATEGORY | within the mandate's categories, if any |
| G10_PER_TXN_CAP | per-transaction cap |
| G11_DAILY_CAP | today's reserved + committed spend + cart |
| G12_TOTAL_CAP | all reserved + committed spend + cart (refunds give budget back) |
| G13_SESSION_STATE | replay guard: preview / authoritative / retry each need the right session state |
| G14_REVIEW_THRESHOLD | above the merchant's review threshold → REVIEW, unless the merchant approved this exact total |
| G99_GATE_ERROR | guard: an internal error is a DENY with the exception type in the trail; the gate never raises |

Refunds run `evaluate_refund`: RF00 well-formed, RF01 session completed and captured, RF02 within the refundable
balance, RF03 no duplicate reference, RF04 inside the refund window, same G99 guard.

`python -m dwarpal eval` runs 25 hand-built cases (16 abusive, 8 benign boundaries, 1 escalation to review) through
the gate with no model: block rate 100%, false-positive rate 0%, 14 distinct rules firing. Report in `docs/gate-eval.md`.

## Sessions and failure recovery

`not_ready_for_payment | requires_review → ready_for_payment → payment_pending → completed | canceled`

![Session state machine. Create runs the gate: ALLOW goes to ready_for_payment, DENY to not_ready_for_payment with
the rule id, REVIEW to requires_review until the merchant decides. Complete reserves and issues a link, moving to
payment_pending; a capture commits and completes; a first failed attempt loops back through a retry-mode gate with a
fresh link; a second failure or a cancel releases the reservation and cancels the session; a completed session can
still be refunded. Twelve numbered transitions are listed under the
diagram.](docs/img/04-session-states.svg)

- **create / update** run the gate in preview mode. A denial keeps the session open with `messages[]` so the agent can fix the cart. A REVIEW parks it in `requires_review` until the merchant approves or declines in the dashboard.
- **complete** re-runs the gate authoritatively, reserves the amount on the mandate, creates the Payment Link.
- **capture** commits the reservation. **Failed first attempt** (or an expired link) re-runs the gate in retry mode, cancels the old link and issues a fresh one; a second failure abandons and releases. A provider error at complete releases and returns 502 so the agent can try again.
- **cancel** releases; when Razorpay refuses the cancel, one final poll decides honestly between "paid late" and `cancel_failed`.
- **refund** (merchant, dashboard or CLI) passes RF00 to RF04, calls Razorpay, records `refund.created`, and returns budget against the mandate's total cap.

Full tables in `docs/architecture.md`; every threat and its control in `docs/threat-model.md`.

## Data model

![Domain model. Agent issues mandates; a Mandate holds reservations; a Session opens one reservation and records the
gate's Decision, which is made of Checks; payments and refunds hang off the session; every decision and every money
action is appended to the hash-chained Event log. Money is an integer number of paise
everywhere.](docs/img/05-domain-model.svg)

Every box is a SQLite table except `Decision` and `Check`, which are the pure values the gate returns and the ledger
records. That is what makes `ledger replay` possible: each `gate.decision` event carries the exact input the gate
consumed, so the decision can be re-run years later and compared.

## Demo scenarios

```powershell
python -m dwarpal demo --scenario happy      # shoes + bottle, paid
python -m dwarpal demo --scenario refused    # smartwatch: G06_MERCHANT_CATEGORY, nothing moves
python -m dwarpal demo --scenario replan     # refused, switches to shoes, pays
python -m dwarpal demo --scenario payfail    # first attempt fails, fresh link, paid on attempt 2
python -m dwarpal demo --scenario crosssell  # accepts the socks offer, pays
python -m dwarpal demo --scenario review     # above the review threshold, merchant approves, pays
python -m dwarpal demo --scenario refund     # paid, then the merchant refunds a short-shipped bottle
```

Add `--planner llm` for the real tool-calling buyer, `--payments real` for real test-mode links, and `--sign`
for a buyer that registers an Ed25519 public key and signs every request. Each run registers a fresh demo agent
and prints the narrative and the session's ledger trail.

## Evaluation

`Evaluation.md` at the repo root is the consolidated evaluation with every figure as a fraction and a percentage: test pass rate,
the gate eval as a detector (recall, precision, false-positive rate, specificity, accuracy, rule coverage), batch
completion, refusal and abandon rates, explained-denial rate, overrun rate, payment recovery rate, budget release
rate, cross-sell attach rate and basket uplift, ledger integrity and decision replay agreement. It is regenerated,
not typed, by `python scripts/make_evaluation.py`.

`docs/test-results.md` is the test suite analysis from a real `pytest` run: pass rate, per-file counts with what each
file covers, unit / service / surface split, timing, slowest tests, how the suite maps to the track's bar, and what it
does not cover. Regenerated by `python scripts/make_test_report.py`.

## Metrics (scripted batch, fake payments)

The batch generates its own test data: three agents with weekly mandates (renewed on expiry), a seeded mix of
allowed carts, refused carts, failed first payments and abandoned payments, one order an hour, against the real gate
and state machine with fake payments. Reports are written as Markdown: `docs/metrics-2026-09-03.md` (50 sessions)
and `docs/metrics-500-sessions.md` (500 sessions).

| Metric | 50 sessions (seed 7) | 500 sessions (seed 11) |
|---|---|---|
| Outcomes | 41 completed, 7 refused, 2 canceled | 308 completed, 174 refused, 18 canceled |
| Denials by rule | G03 2, G04 2, G06 2, G10 1 | G03 22, G04 24, G06 25, G07 23, G10 29, G12 51 |
| Mandate overruns (reserved + committed vs per-order, daily, total caps) | **0** | **0** |
| Failed first attempts retried with a fresh link / recovered on attempt 2 | 7 / 5 | 84 / 66 |
| Reservations released after abandon | 2 | 18 |
| Cross-sell: sessions offered / accepted / attach rate | 43 / 27 / **63%** | 303 / 165 / **54%** |
| Ledger chain | verified, 550 events | verified, 4,341 events |

These are scripted inputs against deterministic code, so zero overruns and fully explained denials are expected by
construction. The evidence is that the failure paths really fire and the accounting holds across many sessions. The
G12 denials in the large run are the small-budget agent running its weekly total down, which is the rule doing its
job. Nothing here measures a particular language model.

## Merchant dashboard

`python -m dwarpal serve`, then open `/dashboard/login?token=<MERCHANT_TOKEN>`. Pages: overview (with the review
queue count), products (sync from Razorpay, propose enrichment, approve or reject each proposal side by side with the
raw text), agents (register with caps, key shown once, revoke), policy (JSON editor), sessions (decision trail,
payment attempts, approve/decline review, refund, cancel), ledger (verify, replay, receipt).

## Scope and guarantees

What DwarPal promises, what holds each promise, and where it stops. The middle column is a mechanism, not a hope.

| Boundary | What holds it | Where it stops |
|---|---|---|
| **Razorpay test mode only** | the adapter refuses any key that is not `rzp_test_`; this is a control, not a gap | going live is one guard in `razorpay_client.py`, after a real security review |
| **A human pays the link** | Razorpay has no public delegated-payment token for agents yet; the agent does everything up to and after the authorisation, and `payment_pending` / `requires_review` are documented deviations from ACP | when a token exists it is one method on `PaymentsPort`; the gate and the state machine do not change |
| **Bearer keys, signed requests** | keys hashed at rest; an agent that registers an Ed25519 public key must sign every request (timestamp, single-use nonce, method, path, body hash), so a leaked key alone buys nothing | the *mandate* is still a merchant-issued row, not a user-signed AP2 credential; run behind TLS |
| **Tamper-evident ledger** | the hash chain catches edits, insertions, deletions and reordering; an anchor (`ledger anchor`, every `/trail` response) catches a cut or rewritten tail | only if someone kept an anchor; tamper-evident, not tamper-proof |
| **Polling by default** | the reconciler polls every 3 s and on every `GET`; it works without a public URL, which is what a demo has | the webhook is optional and does nothing polling does not |
| **About 30 Payment Links per test account** | tests and metrics run on `FakePayments`; real links are for the smoke script and the recorded demo | Razorpay's limit, not ours |
| **One merchant, one process, SQLite** | WAL mode and a re-entrant lock keep API threads and the reconciler from interleaving writes | Postgres replaces the connection layer without touching the domain code; multi-merchant is a schema change |
| **No protocol conformance claimed** | ACP-shaped endpoints and AP2 mandate vocabulary; `docs/protocol-mapping.md` says exactly what is borrowed | none of ACP, AP2, UCP or UAP has a conformance suite this was run against |

## Future work

Signed mandates (AP2 verifiable credentials); an MCP server over the same API; a policy compiler (plain English →
policy JSON, merchant confirms); read-only ledger Q&A; Postgres and multi-merchant; UAP integration when a
merchant-facing API exists.

## Repo map

```
dwarpal/
  gate.py            the thesis: 15 purchase rules + 5 refund rules, pure functions, never raise
  sessions.py        state machine, reservations, review queue, retry, abandon, honest cancel, refunds
  mandates.py        per-agent caps; reserve / commit / release; refunds give budget back
  ledger.py          hash chain; verify, receipt, tamper
  replay.py          re-run every recorded decision from its recorded input
  evalset.py         adversarial gate eval
  catalog.py         products, seed data, agent feed
  policy.py          merchant policy document
  agents.py          agent keys (hashed at rest), optional Ed25519 public key, nonce memory
  signing.py         Ed25519 keypairs, canonical request string, sign / verify; pure
  crosssell.py       candidates (deterministic) + fake / LLM picker
  enrichment.py      proposals (pending until approved) + fake / LLM enricher
  llm.py             one OpenAI-compatible client + FakeLLM
  payments.py        payments port + FakePayments
  razorpay_client.py the only Razorpay SDK importer; links, refunds, items sync/push; webhook signature
  api.py             ACP-shaped endpoints, discovery, webhook
  dashboard.py       merchant pages (templates/, static/)
  buyer/             demo buyer: client (signs when given a key), planners (scripted, LLM), agent loop
  export.py          writes the discovery document, feed and policy to data/
  demo.py            seven scenarios
  metrics.py         batch report
  cli.py             python -m dwarpal ...
tests/               311 offline tests
Evaluation.md        consolidated evaluation with computed percentages
CONTRIBUTING.md      setup, the money-path rule, adding a gate rule, regenerating reports
SECURITY.md          what is in scope, how to report privately, key handling
CHANGELOG.md         version history
Makefile             make test / eval / demos / reports / serve
.github/             CI (tests, eval, batch invariants, every scenario), issue and PR templates
docs/                test results, architecture, threat model, decisions, protocol mapping,
                     demo script, form answers, gate eval, metrics reports, design spec and plan
docs/img/            the five README diagrams (SVG, generated)
data/                discovery document, agent feed and policy as files (generated by `export`)
scripts/smoke_razorpay.py   one-time real test-mode check
scripts/make_evaluation.py  regenerates Evaluation.md from live runs
scripts/make_test_report.py regenerates docs/test-results.md from a pytest run
scripts/make_diagrams.py    regenerates docs/img/*.svg
```

MIT licensed.
