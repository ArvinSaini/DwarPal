# Form answers (draft; paste into the Razorpay AI Buildathon form)

**Project name**
Dwarpal: makes a Razorpay merchant sellable to AI buyer agents, safely.

**Track**
Track 01, AI Growth & Agentic Commerce.

**Project objectives (what does it solve?)**
AI assistants are starting to shop on people's behalf, and NPCI's Unified Agent Protocol, Razorpay's own
agentic-payments pilots and the OpenAI/Stripe Agentic Commerce Protocol all point the same way: merchants will
soon be asked to sell to machines. A Razorpay merchant today has a website built for humans and no safe way to
let an agent buy. Dwarpal is the merchant-side gateway that fixes that on Razorpay test mode.

It gives the merchant an agent-readable catalog, synced from their Razorpay Items and enriched by a language
model whose proposals the merchant approves before the store trusts them. It exposes ACP-shaped checkout-session
endpoints with per-agent bearer keys. Every checkout passes a deterministic policy gate: fifteen ordered rules
over the store's policy (categories sold to agents, max order, stock, blocked SKUs) and the agent's spend mandate
(per order, per day, total, categories, expiry), each recorded with a plain-English reason. Money is reserved when
a checkout completes, committed when Razorpay captures the payment, and released on cancel or failure, so an
agent can never over-commit. Payment goes through a Razorpay Payment Link; a failed first attempt gets one gated
retry with a fresh link, then a clean abandon. Every step lands in a hash-chained ledger with verify, receipt and
a tamper demo. A bounded cross-sell grows the basket: the model picks at most two add-ons from a candidate set
that already fits every cap, the agent may accept, the gate judges the result.

Two more money actions are gated the same way. Orders above a merchant-set threshold get the verdict REVIEW and wait
for a human in the dashboard's review queue; an approval covers that exact total only. Refunds pass their own five
rules before the Razorpay refund API is called, and give budget back to the agent's mandate. Every decision event
records the exact input the gate consumed, so `ledger replay` can re-run the gate offline and prove the recorded
reasoning, on top of `ledger verify` proving the record itself.

Where the model is: catalog enrichment, cross-sell picks, the demo buyer agent. Where it is not: any decision
about money. An adversarial eval of 25 hand-built cases blocks 16 of 16 abusive carts with 0 of 8 benign carts
wrongly blocked, across 14 distinct rules. A batch of fifty scripted sessions reports denials by rule, zero mandate
overruns, failures retried and recovered, attach rate and a verified chain. The repo has 286 offline tests, a
merchant dashboard, a CLI with seven demo scenarios, and a buyer agent that replans after a refusal.

**Build challenges and technical obstacles**
- Razorpay has no public delegated-payment token for agents yet, so "end to end" has to be honest: the agent
  does everything up to and after the payment authorisation, and a human pays the link. The design records
  that as a documented deviation from ACP (an extra `payment_pending` status) rather than pretending.
- A Payment Link's `payments` array lists only captured payments, so a failed attempt never shows up there.
  The adapter reads failed attempts from the link's order once it exists and from the payments list matched on
  notes before that, so the retry path fires within seconds without webhooks.
- Test accounts allow only about thirty Payment Links, so every test and the metrics batch run on a fake
  payments adapter with the same interface; real calls are reserved for the smoke script and the recorded demo.
- Making the LLM useful without putting it on the money path: enrichment output is validated and parked as
  pending until a human approves it; cross-sell offers are validated against a deterministic candidate set;
  the buyer's tool results wrap catalog text as untrusted, and one seed product carries a prompt injection on
  purpose so the demo shows the gate, not the prompt, is the control.
- Keeping accounting honest under failure: reservations count as spend while a payment is pending, the retry
  re-runs the gate with the session's own reservation excluded, and a cancel that Razorpay refuses ends in one
  final poll so a late capture is never recorded as cancelled.
- No paid model credits: every model call goes through one OpenAI-compatible client so Gemini's free tier,
  Groq, NVIDIA NIM or a local Ollama all work, and every AI component has a deterministic fake for tests.

**Links**
Repo: (public GitHub URL). Video: (link). Architecture: `docs/architecture.md`.
