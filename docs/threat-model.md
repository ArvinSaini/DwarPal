# Threat model

Who can do harm, what they would try, and which control stops it. Every control is a rule in the gate, a
state-machine transition, or a ledger property; none depends on a model behaving.

| Threat | Actor | What they try | Control | Evidence |
|---|---|---|---|---|
| Rogue or buggy agent overspends | agent | a cart above its per-order, daily or total cap | G10, G11, G12; spend counts reserved + committed money | `tests/test_gate.py`, `test_reserved_but_unpaid_spend_counts_too` |
| Double-spend while a payment is pending | agent | open a second checkout before the first link is paid | reservations on complete; G11/G12 include them | `test_reserved_but_unpaid_spend_counts_too` |
| Replay of a completed checkout | agent | call complete again, or complete a completed session | idempotency key returns the stored session; G13 refuses the wrong state | `test_complete_replay_returns_same_link`, eval `replay_complete_on_completed_session` |
| Prompt injection through the catalog | merchant data, or an attacker who edits it | product text tells the agent to add 50 units or ignore its budget | catalog text is wrapped as untrusted for the model; whatever the model proposes still meets G07/G08/G10 | eval `catalog_injection_over_quantity`, seed product `prod_gel` |
| Selling what the merchant did not approve | agent | buy an uncategorised or off-category item | G06; enrichment is pending until a human approves | `test_g06_merchant_category_and_uncategorised`, `test_enrichment.py` |
| Model proposes a bad category | model | enrichment says a smartwatch is footwear | validation against the allowed set; merchant approval; "other" is never sold | `test_validate_proposal_rejects` |
| Cross-sell pushes a cart over a cap | model | suggest an expensive add-on | candidates are pre-filtered under every cap; the agent must accept via update; the gate re-judges | `test_candidates_respect_headroom`, metrics overruns = 0 |
| Revoked agent keeps buying | ex-agent | reuse an old key | G01 on every evaluation | `test_revoked_agent_is_denied_by_the_gate_not_by_auth` |
| Stolen API key | attacker | present a valid key | keys are hashed at rest; an agent registered with an Ed25519 public key must also sign every request, so the key alone is useless; every purchase is still bounded by the mandate and visible in the ledger; the merchant revokes | `test_key_is_hashed_at_rest`, `test_signing_agent_must_sign_every_request` |
| Captured signed request replayed, or a signature reused on another body | attacker on the wire | resend a valid request; move a signature to a different cart | each nonce is accepted once per agent; the timestamp must be within 300 s; the signature covers the method, the path with its query and the body hash | `test_replay_stale_wrong_key_wrong_body_and_garbage_are_refused` |
| Forged webhook | attacker | post a fake `payment.captured` | HMAC signature check; duplicate event ids ignored; the webhook only triggers a poll of Razorpay, it never completes a session by itself | `test_webhook_signature_dedupe_and_reconcile` |
| Provider says the cancel failed because the customer just paid | Razorpay timing | the ledger would say "cancelled" while money moved | one final poll; a late capture completes the session; `cancel_failed` otherwise | `test_cancel_when_provider_cancel_fails` |
| Over-refund or duplicate refund | merchant operator error | refund more than captured, or twice for the same shortfall | RF02, RF03; refunds are gated and logged like payments | `test_refund_is_gated` |
| Large order slips through unseen | agent | a single big cart within caps | review threshold: G14 holds it for a human; approval covers that exact total | `test_review_approval_is_tied_to_the_total` |
| Ledger edited after the fact | insider | change an amount, delete or reorder an event | hash chain; `verify` names the first bad sequence | `test_tamper_breaks_verify_at_that_seq` |
| Ledger tail cut or rewritten | insider | delete the last events, or replace the last one and re-hash | anchor `<seq>:<hash>` handed to every agent in `/trail` and printed by `ledger anchor`; `verify --anchor` fails when the chain is shorter than it or differs at it | `test_verify_with_anchor_detects_a_truncated_tail`, `test_verify_with_anchor_detects_a_rewritten_tail`, `test_ledger_anchor_detects_a_cut_tail` |
| Ledger records a decision the gate would not make | insider or bug | write a fake ALLOW | every decision carries its input; `replay` re-runs the pure gate and reports divergences | `tests/test_replay.py` |
| Internal error becomes an accidental ALLOW | bug | a malformed input crashes a rule | G99 guard: any exception is a DENY with the exception type in the trail | `test_g99_guard_never_raises` |
| Model outage or garbage output | provider | the LLM is down or returns prose | enrichment skips and records it; cross-sell returns no offers; the buyer fails closed with `no_plan` | `test_skipped_proposals_are_recorded`, `test_llm_planner_provider_error_fails_closed` |

## Out of scope, stated plainly

- The merchant token is a bearer secret over plain HTTP in the demo, and so is an agent key unless the agent
  registered a public key. Run behind TLS in any real deployment; signing narrows what a captured key or request
  is worth, it does not replace transport security.
- Signed mandates (AP2-style verifiable credentials) are future work; today the merchant's database is the root
  of trust for what an agent may spend.
- The ledger is tamper-evident, not tamper-proof: a cut or rewritten tail is only caught by comparing against an
  anchor held outside the database (an agent's last `ledger_head`, or a `ledger anchor` value kept elsewhere).
  Nothing detects it if no one kept an anchor.
- One process, one SQLite file. Concurrency is handled with a per-connection lock, not with a database that
  serialises writers across processes.
