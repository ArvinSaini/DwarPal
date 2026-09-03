# Dwarpal evaluation

Generated 2026-09-03 18:33 UTC by `python scripts/make_evaluation.py` (version 0.1.0). Every number below is computed in that run; nothing is typed in by hand.

**What was evaluated:** the deterministic parts of Dwarpal, the policy gate, the checkout state machine, the mandate accounting, the ledger, on data the repo generates for itself: hand-built adversarial and benign carts, and seeded batches of scripted sessions against a fake Razorpay. **What was not:** real Razorpay calls (need test keys), a real language model (need a key), real merchant traffic (none exists for this track). The numbers are honest about that: 100% and 0% are expected by construction for a pure function under test; the evidence is which rules fire, that the failure paths really run, and that the accounting holds across thousands of ledger events.

## 1. Automated test suite

| Metric | Value |
|---|---|
| Tests passed | 282 / 282 (100.0%) |
| Tests failed | 0 |
| Network access during tests | none (fake Razorpay, fake model, fixed clock) |

Per-file breakdown, timing and coverage notes: `docs/test-results.md`.

## 2. Gate eval: adversarial and benign carts

25 hand-built cases run through `gate.evaluate` with no model: 16 abusive, 8 benign boundary cases, 1 escalation to merchant review. Treating the gate as a detector of carts that must not go through:

| Metric | Formula | Value |
|---|---|---|
| True positives (abusive denied) | | 16 |
| False negatives (abusive allowed) | | 0 |
| False positives (benign denied) | | 0 |
| True negatives (benign allowed) | | 8 |
| Block rate / recall | TP / (TP + FN) | 16 / 16 = **100.0%** |
| Precision | TP / (TP + FP) | 16 / 16 = **100.0%** |
| False-positive rate | FP / (FP + TN) | 0 / 8 = **0.0%** |
| Specificity | TN / (TN + FP) | 8 / 8 = **100.0%** |
| Accuracy | (TP + TN) / all | 24 / 24 = **100.0%** |
| Escalations to review handled | REVIEW / expected | 1 / 1 = 100.0% |
| Purchase rules exercised by a denial | fired / total | 14 / 14 = 100.0% |

Rules that fired: G00_WELL_FORMED, G01_AGENT_ACTIVE, G02_MANDATE_ACTIVE, G03_ITEMS_KNOWN, G04_IN_STOCK, G05_SKU_NOT_BLOCKED, G06_MERCHANT_CATEGORY, G07_QTY_PER_LINE, G08_ORDER_MAX, G09_MANDATE_CATEGORY, G10_PER_TXN_CAP, G11_DAILY_CAP, G12_TOTAL_CAP, G13_SESSION_STATE.
Not fired by a denial: none (G14 escalates to REVIEW rather than denying; G99 is a guard and is covered by a unit test).

Full table: `docs/gate-eval.md`.

## 3. Batch metrics: scripted sessions against the real gate and state machine

Three agents with weekly mandates (renewed on expiry), a seeded mix of allowed carts, refused carts, failed first payments and abandoned payments, one order an hour, fake Razorpay. Reports: `docs/metrics-2026-09-03.md` and `docs/metrics-500-sessions.md`.

| Metric | 50 sessions (seed 7) | 500 sessions (seed 11) |
|---|---|---|
| Intended scenario mix | abandoned 2, allowed_paid 36, payfail_then_paid 5, refused 7 | abandoned 22, allowed_paid 281, payfail_then_paid 74, refused 123 |
| Completion rate | 41 / 50 = 82.0% | 308 / 500 = 61.6% |
| Refusal rate | 7 / 50 = 14.0% | 174 / 500 = 34.8% |
| Abandon rate (two failed payments) | 2 / 50 = 4.0% | 18 / 500 = 3.6% |
| Denials that name a rule | 7 / 7 = 100.0% | 174 / 174 = 100.0% |
| Mandate overruns (reserved + committed vs every cap) | **0 (overrun rate 0.0%)** | **0 (overrun rate 0.0%)** |
| Payment recovery (paid on attempt 2 / retried) | 5 / 7 = 71.4% | 66 / 84 = 78.6% |
| Budget released after abandon | 2 / 2 = 100.0% | 18 / 18 = 100.0% |
| Cross-sell attach rate (accepted / offered) | 27 / 43 = **62.8%** | 165 / 303 = **54.5%** |
| Average completed basket, without to with an accepted offer | INR 2,011.40 to INR 2,550.88 (**+26.8%**) | INR 2,020.12 to INR 2,828.39 (**+40.0%**) |
| Completed revenue | INR 96,494.00 | INR 746,672.00 |
| Ledger chain | verified, 550 events | verified, 4341 events |
| Decisions replayed identically from recorded input | 127 / 127 = 100.0% | 1075 / 1075 = 100.0% |

### Denials by rule

| Rule | 50 sessions | share | 500 sessions | share |
|---|---|---|---|---|
| G03_ITEMS_KNOWN | 2 | 28.6% | 22 | 12.6% |
| G04_IN_STOCK | 2 | 28.6% | 24 | 13.8% |
| G06_MERCHANT_CATEGORY | 2 | 28.6% | 25 | 14.4% |
| G07_QTY_PER_LINE | 0 | 0.0% | 23 | 13.2% |
| G10_PER_TXN_CAP | 1 | 14.3% | 29 | 16.7% |
| G12_TOTAL_CAP | 0 | 0.0% | 51 | 29.3% |

The G12 denials in the large run are the small-budget agent running its weekly total down; that is the rule doing its job, and every one of those sessions tells the agent so.

## 4. Failure paths exercised

| Failure | How it is handled | Where it is proven |
|---|---|---|
| Payment fails on the bank page | recorded, gate re-run in retry mode, old link cancelled, fresh link | batch: 84 retries, 66 recovered; `test_failed_first_attempt_retries_with_fresh_link` |
| Second failure | abandon, link cancelled, reservation released | batch: 18 abandoned, 18 released; `test_failed_twice_abandons_and_releases` |
| Cap breached between create and complete | complete denied with the rule; nothing reserved | `test_complete_denied_by_authoritative_gate_run` |
| Payment provider error at complete | reservation released, HTTP 502, agent may retry | `test_complete_provider_error_releases_and_stays_ready` |
| Cancel refused by Razorpay (customer paid at that moment) | final poll; late capture completes, else `cancel_failed` | `test_cancel_when_provider_cancel_fails` |
| Duplicate or forged webhook | HMAC check; event id dedupe | `test_webhook_signature_dedupe_and_reconcile` |
| Model down or returns garbage | enrichment skipped and recorded; no offers; buyer fails closed | `test_skipped_proposals_are_recorded`, `test_llm_planner_provider_error_fails_closed` |
| Internal error inside a rule | DENY on G99 with the exception type; never an accidental ALLOW | `test_g99_guard_never_raises` |
| Ledger edited after the fact | `verify` names the first bad sequence | `test_tamper_breaks_verify_at_that_seq` |
| Recorded decision that the gate would not make | `replay` reports the divergence | `test_replay_detects_a_changed_recorded_input` |

## 5. What these numbers do not show

- No real Razorpay traffic was measured. The adapter is unit-tested against a stubbed SDK; `scripts/smoke_razorpay.py` confirms it against a test account in one run.
- No language model was measured. The eval and the batches use scripted carts and a deterministic cross-sell picker, so attach rate here measures the mechanism, not a model's taste.
- No real merchant data exists for this track; the catalog is a ten-product demo store.
- 100% block rate and 0% false positives on hand-built cases are expected for a pure function; the meaningful signal is coverage (which rules fire) and the benign boundary cases (exactly at a cap, prior spend just under a cap, a retry, an approved review).

Regenerate this file with `python scripts/make_evaluation.py`.
