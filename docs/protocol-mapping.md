# Protocol mapping

As of 3 September 2026. Nothing here claims conformance; it says which vocabulary and which design DwarPal
borrows, and where it deliberately differs.

## OpenAI / Stripe Agentic Commerce Protocol (ACP)

ACP defines the five endpoints a merchant implements to be sellable to an AI buyer: create, update,
retrieve, complete and cancel a checkout session, with idempotency keys, session statuses, `messages[]`
for buyer-facing errors and a standard error shape. DwarPal's agent API has the same shape and the same
statuses plus one extra.

| ACP | DwarPal | Note |
|---|---|---|
| `POST /checkout_sessions` | `POST /agent/v1/checkout_sessions` | same body shape `{items:[{id, quantity}]}`; always returns a session id |
| `POST /checkout_sessions/{id}` | same | replaces items; also how an offer is accepted |
| `GET /checkout_sessions/{id}` | same | triggers one reconciliation poll when payment is pending |
| `POST /checkout_sessions/{id}/complete` | same | ACP charges a delegated payment token synchronously; DwarPal returns a Razorpay Payment Link and the status `payment_pending` |
| `POST /checkout_sessions/{id}/cancel` | same | cancels the link and releases the reservation |
| `Idempotency-Key`, `Request-Id` | same headers | replay returns the stored session; a different body is `request_not_idempotent` |
| statuses | `not_ready_for_payment`, `ready_for_payment`, `completed`, `canceled` plus `payment_pending` | the extra status exists because the payer completes the link |
| errors `{type, code, message, param}` | same shape | plus `rule_id` on `policy_denied` |
| shared payment token | merchant-issued bearer key per agent | Razorpay has no public delegated payment token for agents yet |

Deviations are listed in the discovery document at `/.well-known/agent-commerce.json`.

## Google Agent Payments Protocol (AP2)

AP2 chains signed mandates: intent, cart and payment. DwarPal borrows the *mandate* vocabulary for the
per-agent spend limits (per transaction, per day, total, categories, expiry) but issues them from the merchant
side as database rows tied to a bearer key, not as user-signed verifiable credentials. Signed mandates are
future work; the gate would gain signature rules without changing anything else.

## Google Universal Commerce Protocol (UCP)

UCP describes the conversation shape across discovery, checkout and post-purchase and plugs into AP2 for
authorization and MCP for tools. DwarPal's discovery document and feed play the discovery role; an MCP server
wrapping the same API is future work.

## NPCI Unified Agent Protocol (UAP)

Reported design as of this date (no public spec or sandbox): agents are registered, verified and authorized
to transact on UPI rails, on top of UPI Circle delegation and Reserve Pay fund blocking, with user-set limits
and audit trails. DwarPal models the merchant's side of that picture: a registry of agents with revocation
(rules G01), per-agent limits (G09 to G12), and an audit ledger. When UAP ships a merchant-facing API,
the payments adapter is the only module that would change.

## UPI Circle

Delegated payments with a cap set by the account holder. The mandate's daily and total caps are the
merchant-side mirror of that idea; reserving on complete and releasing on failure is what keeps the cap honest
while a payment is in flight.

## x402

HTTP 402 machine payments in stablecoins. Landscape only; off the INR and Razorpay rails.
