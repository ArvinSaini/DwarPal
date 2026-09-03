# Dwarpal batch metrics

500 sessions, seed 11, 3 agents, scripted carts against the real gate and state machine with fake payments. Generated 2026-09-03 18:26 UTC.

## Outcomes

| Outcome | Sessions |
|---|---|
| canceled | 18 |
| completed | 308 |
| refused | 174 |

Intended scenario mix: abandoned 22, allowed_paid 281, payfail_then_paid 74, refused 123.

## Denials by rule (every denial names its rule)

| Rule | Count |
|---|---|
| G03_ITEMS_KNOWN | 22 |
| G04_IN_STOCK | 24 |
| G06_MERCHANT_CATEGORY | 25 |
| G07_QTY_PER_LINE | 23 |
| G10_PER_TXN_CAP | 29 |
| G12_TOTAL_CAP | 51 |

## Bounded

- Mandate overruns (committed + reserved vs per-transaction, daily and total caps): **0**

## Failure recovery

- First payment attempt failed and a fresh link was issued: 84
- Of those, paid on the second attempt: 66
- Sessions whose reserved budget was released (cancel, abandon, provider error): 18

## Cross-sell

- Sessions with an offer: 303
- Offers accepted: 165
- Attach rate: **55%**
- Average completed basket with an accepted offer: INR 2,828.39
- Average completed basket without: INR 2,020.12
- Completed revenue: INR 746,672.00

## Audit

- Ledger chain: verified (4341 events)
- Decisions replayed from their recorded inputs: 1075 / 1075 identical

## What this does and does not prove

These are scripted inputs run through deterministic code, so zero overruns and fully explained denials are expected by construction; the evidence is that the failure paths really fire and that the accounting holds across many sessions. Nothing here measures a particular language model: the cross-sell picker and the buyer are scripted in this batch.
