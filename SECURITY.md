# Security policy

Dwarpal decides whether an AI agent may spend a merchant's money. Please treat findings here as you would in any
payments codebase.

## Reporting a vulnerability

Open a **private** security advisory through GitHub: the repository's *Security* tab, then *Report a vulnerability*.
Do not open a public issue for anything exploitable.

Please include what you found, the smallest steps that reproduce it, and what an attacker gains. Expect an
acknowledgement within a few days. This is a hackathon project maintained by one person, so there is no formal
service-level commitment and no bug bounty.

Do not include real credentials in a report. If you believe a live Razorpay key has been exposed anywhere, rotate it
in the Razorpay dashboard first, then tell us.

## Scope

This project is **test mode only**. `RazorpayPayments` raises on any key id that does not start with `rzp_test_`, so
it cannot move real money by design. Reports that require disabling that guard are out of scope.

In scope:

- A cart that should be denied but is allowed, or spend that exceeds a mandate cap.
- A path that creates a Payment Link, a refund or any other money action without an ALLOW from the gate.
- A way to make model output reach a money decision without merchant approval or deterministic validation.
- Ledger tampering that `ledger verify` or `ledger replay` fails to detect, beyond the documented limits below.
- Authentication bypass on the agent API or the merchant dashboard.
- Anything that makes the audit trail lie about what happened.

Out of scope, because they are documented limitations rather than defects:

- The demo runs over plain HTTP with bearer secrets. Deploy behind TLS.
- The ledger is tamper **evident**, not tamper proof: it detects modification, insertion, deletion and reordering,
  but not truncation of the tail or a re-hashed final event. Anchor the head hash elsewhere.
- The merchant's database is the root of trust for what an agent may spend. Signed AP2-style mandates are future work.
- One process, one SQLite file. Multi-process concurrency is not supported.
- Denial of service through resource exhaustion.

## Defense only

Dwarpal is a defensive control. Issues and pull requests that add offensive capability, such as tooling to attack
merchants, evade another store's controls, or generate fraudulent transactions, will be closed.

## Handling keys

Keep `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `MERCHANT_TOKEN` and `LLM_API_KEY` in
`.env`, which is git-ignored. `.env.example` holds placeholders only. Agent API keys are shown once at registration
and stored as SHA-256 hashes; revoking an agent takes effect on its next checkout through rule `G01_AGENT_ACTIVE`.
