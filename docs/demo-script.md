# Demo script (5-minute video)

Record at 1280x720 or larger. Two windows: a terminal and the dashboard in a browser. Every command below
runs from the repo root. Prepare `.env` with Razorpay **test** keys; optionally an LLM key (Gemini free tier).

## Before recording

```powershell
python -m pip install -e .
python -m dwarpal init
python -m dwarpal seed --raw --push      # --push needs test keys; drop it to stay offline
python -m dwarpal serve                  # leave this running; note the dashboard login URL it prints
```

Open the dashboard login URL in the browser. Keep the terminal ready for a second shell.

## Beat 1: why now (30 s)

Say it over the overview page: NPCI's Unified Agent Protocol is being unveiled at Global Fintech Fest this
week; Razorpay already pilots agentic payments inside Claude; OpenAI and Stripe's ACP defines the endpoints a
merchant needs to be sellable to an AI buyer. The open question is the same everywhere: how does a merchant
let a machine buy without letting it go rogue? Dwarpal is the merchant's answer, on Razorpay test mode.

## Beat 2: merchant setup (60 s)

- Dashboard, Products: the catalog synced from Razorpay Items. Every product is *uncategorised*, so agents
  cannot buy anything yet (rule G06).
- Click **Propose enrichment**. Show one proposal side by side with the raw text: category, tags, attributes,
  "recommend when". Point out the energy gel description carrying a prompt injection and that the proposal
  ignored it. Approve the proposals (or `python -m dwarpal approve --all`).
- Dashboard, Agents: register `shopbot` with 4,000 per order, 8,000 per day, 20,000 total. Copy the key.
- Dashboard, Policy: show the store rules: categories sold to agents, max order, in-stock only.

## Beat 3: an agent buys, with a cross-sell (60 s)

```powershell
python -m dwarpal demo --scenario crosssell --planner llm --payments real
```

Narrate the agent output: it browses, creates a session, the gate returns ALLOW with the 15-check trail, the
store offers socks, the agent accepts within budget, complete returns a Razorpay link. Open the link, pay on the
test checkout (Netbanking → Success). The demo prints the trail: `payment.captured`, `mandate.committed`,
`session.completed`. Show the session page in the dashboard with the decision trail.

## Beat 4: a refused purchase and a replan (45 s)

```powershell
python -m dwarpal demo --scenario replan --planner llm --payments fake
```

The agent tries the smartwatch. The gate denies on `G06_MERCHANT_CATEGORY` with a sentence saying why.
The agent reads the rule, switches to shoes, and completes. No money moved on the refusal; the ledger shows both
decisions.

## Beat 5: a failed payment recovered (45 s)

```powershell
python -m dwarpal demo --scenario payfail --payments real
```

Pay the first link with **Failure** on the mock bank page. The reconciler records `payment.failed`, re-runs
the gate in retry mode, cancels the old link and issues a fresh one (`payment.retry`). Pay the second link
with **Success**. One reservation, committed once, two attempts on record.

## Beat 5b: a human in the loop, and money back (40 s, optional if time is short)

```powershell
python -m dwarpal demo --scenario review --payments fake     # order above the review threshold
python -m dwarpal demo --scenario refund --payments fake     # paid, then the merchant refunds the bottle
```

Show the review queue on the dashboard overview, approve the order, and the agent completes. Then the refund:
`refund.decision` with rules RF00 to RF04, `refund.created`, and the agent's mandate getting budget back.

## Beat 6: audit and honest metrics (40 s)

```powershell
python -m dwarpal ledger verify
python -m dwarpal ledger replay        # every recorded decision re-run from its recorded input
python -m dwarpal ledger tamper 12
python -m dwarpal ledger verify        # BROKEN at seq 12, exit code 2
python -m dwarpal eval                 # 25 adversarial and benign cases, block rate and false positives
python -m dwarpal metrics --n 50
```

Show the eval table and the metrics table: denials by rule, zero mandate overruns, retries recovered, attach rate,
chain verified, and the paragraph on what the batch does and does not prove.

## Closing (20 s)

"Every money action explainable, bounded, gated and audited; the model proposes, deterministic code disposes;
the merchant grows the basket without giving up control." Point at the repo and the README.
