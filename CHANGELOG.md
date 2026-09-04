# Changelog

All notable changes to this project. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Ledger anchoring. `ledger anchor` prints the head as `<seq>:<hash>`; `ledger verify --anchor <seq>:<hash>` fails
  when the chain is shorter than the anchor or the event at that seq has a different hash, which the chain alone
  could not see. Every `GET …/trail` response and `/health` carry the same pair as `ledger_head`, so an agent that
  keeps its last trail holds an anchor the merchant cannot shrink the ledger past. The dashboard's ledger page shows
  the current anchor.
- Ed25519 request signing. An agent registered with a public key (`agent add --pubkey`, the dashboard form) must
  sign every request: `X-Agent-Timestamp` within 300 s, a nonce accepted once per agent, and a signature over the
  method, the path with its query and the sha256 of the body. Typed 401s name what failed. Bearer-only agents are
  unchanged. `agent keygen` makes a keypair for an agent operator, `GateClient(signing_key=...)` signs, and
  `demo --sign` runs a signing buyer. The discovery document advertises `request_signing`.
- `agents.public_key` column (added to older databases on `init`) and an `agent_nonces` table.
- New dependency: `cryptography`.

## [0.1.0] - 2026-09-03

First complete build, submitted to the Razorpay AI Buildathon 2026 (Track 01: AI Growth & Agentic Commerce).

### Added

**Policy gate**
- `gate.evaluate`, a pure function running fifteen ordered rules (`G00` to `G14`) with a `G99` guard that turns any
  internal error into a denial. Verdicts are ALLOW, DENY or REVIEW; every rule that ran is recorded with a
  plain-English reason.
- `gate.evaluate_refund`, five rules (`RF00` to `RF04`) so refunds are gated like purchases.
- Three evaluation modes: preview on create and update, authoritative on complete, retry before a second payment
  attempt.

**Money and mandates**
- Per-agent spend mandates: per transaction, per day, in total, optional categories, expiry.
- Reserve on complete, commit on capture, release on cancel, abandon or provider error, so a pending payment cannot
  be double spent. Refunds return budget against the total cap.

**Checkout**
- Agent-facing HTTP API shaped after the Agentic Commerce Protocol: discovery document, product feed, and checkout
  sessions with create, update, complete, cancel, get and trail. Bearer keys per agent, idempotency keys, a standard
  error shape carrying `rule_id`.
- Session state machine with one gated retry after a failed payment, honest abandon, and a final poll when Razorpay
  refuses a cancel so a late capture is never recorded as cancelled.
- Merchant review queue: orders above a policy threshold wait in `requires_review` for a human; an approval covers
  that exact total only.

**Razorpay integration (test mode only)**
- Payment Links, polling reconciliation that reads failed attempts from the link's order, cancels, refunds, Items
  sync and push, and an optional webhook with HMAC verification and event deduplication.
- The adapter refuses any key that is not `rzp_test_`.

**Audit**
- Append-only hash-chained ledger with `verify`, `receipt` and a `tamper` demo; `ledger tamper` with no
  sequence number picks the earliest event carrying an amount, so the demo needs no hunting on camera.
- Decision replay: every gate and refund decision records the exact input it consumed, so `ledger replay` re-runs the
  gate offline and reports divergences.

**AI, kept off the money path**
- Catalog enrichment: a model proposes category, attributes, tags and a recommendation hint; output is schema
  validated and stays pending until the merchant approves it.
- Cross-sell: a model picks at most two add-ons from a deterministic candidate set already priced under every cap.
- Demo buyer agent with scripted and tool-calling planners that replans after a refusal.
- One OpenAI-compatible client, so Gemini's free tier, Groq, NVIDIA NIM or a local Ollama all work; every component
  has a deterministic fake.

**Merchant dashboard** over products and enrichment approval, agents and mandates, policy, sessions with the decision
trail, review decisions, refunds, and the ledger with verify and replay.

**Project files** for a public repository: CI running the suite on Python 3.11 to 3.13 plus the eval, the
batch invariants and every demo scenario end to end; contributing and security guides; a code of conduct;
issue and pull-request templates; Dependabot; an editorconfig; and a Makefile.

**Command line** `python -m dwarpal`: init, seed, sync-items, enrich, approve, reject, agent, policy, serve,
reconcile, ledger, review, refund, eval, metrics, demo.

**Evidence**
- 286 offline tests; no network, fake Razorpay, fake model, fixed clock.
- Adversarial gate eval over 25 hand-built cases.
- Batch metrics over 50 and 500 scripted sessions.
- Generated reports: `Evaluation.md`, `docs/test-results.md`, `docs/gate-eval.md`, and two metrics reports.
- Documentation: architecture, threat model, decision records, protocol mapping, demo script, form answers.

### Notes

- The project was named AgentGate during development and renamed to DwarPal (द्वारपाल, gatekeeper) before release.
- Razorpay has no public delegated-payment token for agents yet, so a human completes the Payment Link. The extra
  `payment_pending` and `requires_review` statuses are documented deviations from ACP.

[Unreleased]: https://github.com/ArvinSaini/DwarPal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ArvinSaini/DwarPal/releases/tag/v0.1.0
