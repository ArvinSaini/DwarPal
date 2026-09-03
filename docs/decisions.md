# Decision records

## D1. The merchant is the customer, not the buyer
Track 01 says "grow the merchant's revenue and make them sellable to AI buyers". AgentGate therefore lives on the
merchant's side: the store's policy, the store's view of each agent's mandate, the store's catalog and cross-sell.
Consequence: the buyer agent in the repo is a demo client, deliberately outside the trust boundary.

## D2. The model proposes; a pure function disposes
`gate.evaluate` and `gate.evaluate_refund` take every input explicitly and never read the clock, the database or a
model. Reason: one unit test per rule, offline replay from the ledger, and no way for a prompt to influence a
money decision. Consequence: the session service builds a `GateInput` and records it verbatim on every decision.

## D3. Three gate modes instead of three gates
`preview` (create/update, nothing reserved), `authoritative` (complete: reserve and create the link), `retry`
(before a second attempt, with the session's own reservation excluded). Same rules, one G13 check that the
session is in the right state. Reason: the replay guard and the retry fairness fall out of one table.

## D4. Reserve on complete, commit on capture, release on failure
Spend counts reserved and committed money. Reason: a pending Payment Link is money the agent has already
committed to; without reservations two concurrent checkouts could both pass the daily cap. Consequence: every
failure path must release, and the tests assert it.

## D5. Payment Links and polling, webhooks optional
The demo has no public URL, and Razorpay lists only captured payments on a link. The adapter reads failed
attempts from the link's order and, before an order exists, from the payments list matched on notes. The webhook
only verifies a signature, dedupes, and triggers the same poll. Reason: one code path for payment results.

## D6. Merchant review instead of user step-up
A user-signed step-up token is the buyer-side answer to an over-cap cart. The merchant-side answer is a review
queue: orders above `review_above_paise` get verdict REVIEW, wait in `requires_review`, and an approval covers
that exact total only. Reason: the merchant is the party at risk here, and a dashboard button is something a
shop owner will actually use.

## D7. Refunds are gated money actions
Rules RF00 to RF04 mirror the purchase gate: well-formed, session completed and captured, within the refundable
balance, no duplicate reference, inside the refund window. Refunds return budget against the mandate's total cap
but not the daily cap. Reason: the daily cap limits outflow velocity; giving it back would let a refund unlock a
second large order the same day.

## D8. Enrichment is pending until a human approves
The model's category feeds rule G06, so it must not reach `products` on the model's say-so. Proposals are
validated, stored, and approved or rejected in the dashboard. Reason: the only way an LLM can influence the gate
is through a human click.

## D9. Cross-sell candidates are deterministic; the model only ranks
Candidates are in stock, allowed by policy and mandate, and priced under the headroom left by every cap. The
model picks at most two and writes a reason; invalid ids are dropped. Reason: an offer can suggest, never push a
cart over a limit, and the metrics batch checks it.

## D10. Every decision carries its input
`gate.decision` and `refund.decision` events include the agent, mandate, policy, the catalog entries used,
the cart, prior spend, the clock and the mode. `ledger replay` re-runs the gate and compares. Reason: `verify`
proves nobody edited the record; `replay` proves the record was reasoned correctly.

## D11. Bearer keys now, signed mandates later
Per-agent keys hashed at rest were enough to make the mandate model real in the time available. AP2-style
signed mandates would add signature rules without changing anything else. Stated in the README as future work.

## D12. One OpenAI-compatible client, fakes everywhere
Gemini's free tier, Groq, NVIDIA NIM and Ollama all speak the same API, so the model is interchangeable and the
demo costs nothing. Every AI component has a deterministic fake, so the test suite is offline and the batch
metrics measure the deterministic parts honestly.
