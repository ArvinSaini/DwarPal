# Dwarpal test results

Generated 2026-09-03 18:45 UTC by `python scripts/make_test_report.py` (version 0.1.0) from a full `pytest` run on this machine. Nothing below is typed by hand.

## Summary

| Metric | Value |
|---|---|
| Test cases collected | 286 |
| Passed | 286 / 286 (100.0%) |
| Failed | 0 |
| Skipped | 0 |
| Test files | 22 |
| Total test time | 15.8 s |
| Mean per test | 55 ms |
| Network access | none: fake Razorpay, fake model, fixed clock |

## Per file

| File | Area | Cases | Passed | Time | What it covers |
|---|---|---|---|---|---|
| test_gate.py | gate | 54 | 54 / 54 (100%) | 0.07 s | one pass and one fail per rule G00 to G14, G99 guard, refund rules RF00 to RF04 |
| test_sessions.py | sessions | 38 | 38 / 38 (100%) | 0.57 s | state machine: create, update, idempotency, complete, reserve, retry, abandon, cancel, review queue, refunds, spend across sessions, trail |
| test_api.py | api | 20 | 20 / 20 (100%) | 1.35 s | discovery, feed, auth, idempotency, error shape, full flow, webhook signature and dedupe |
| test_policy.py | policy | 17 | 17 / 17 (100%) | 0.03 s | merchant policy validation including review threshold and refund window |
| test_buyer.py | buyer | 16 | 16 / 16 (100%) | 1.39 s | client errors, scripted runs for every scenario, LLM planner loop, replanning, parallel tool calls, fail-closed behaviour |
| test_enrichment.py | enrichment | 15 | 15 / 15 (100%) | 0.09 s | proposal validation, keyword and LLM enrichers, pending / approve / reject |
| test_cli.py | cli | 14 | 14 / 14 (100%) | 6.57 s | every command on a temporary database, exit codes, review, refund, eval, replay |
| test_dashboard.py | dashboard | 11 | 11 / 11 (100%) | 2.50 s | login, products and enrichment approval, agents, policy, sessions, review, refund, ledger verify, replay, receipt |
| test_demo.py | demo | 11 | 11 / 11 (100%) | 1.35 s | all seven scenarios end to end on fakes, policy restored after the review scenario |
| test_ledger.py | ledger | 11 | 11 / 11 (100%) | 0.05 s | hash chain, verify, tamper detection, receipt rendering, filtering |
| test_mandates.py | mandates | 11 | 11 / 11 (100%) | 0.06 s | caps, validity window, reserve / commit / release, daily and total spend |
| test_razorpay_client.py | razorpay | 11 | 11 / 11 (100%) | 0.03 s | test-key guard, link creation fields, failed-attempt lookup via order and notes, refunds, items sync and push, webhook HMAC |
| test_crosssell.py | cross-sell | 8 | 8 / 8 (100%) | 0.01 s | headroom under every cap, candidate filtering, fake and LLM pickers |
| test_catalog.py | catalog | 7 | 7 / 7 (100%) | 0.04 s | seed data, raw seed, feed visibility of approved fields, search, snapshot |
| test_money.py | money | 7 | 7 / 7 (100%) | 0.01 s | integer paise formatting and validation; bool is never an amount |
| test_payments_fake.py | payments | 7 | 7 / 7 (100%) | 0.01 s | fake adapter outcomes: paid, failed, pending, expired, error, refund |
| test_agents.py | agents | 6 | 6 / 6 (100%) | 0.01 s | key issuance, hashing at rest, authentication, revocation |
| test_db.py | db | 5 | 5 / 5 (100%) | 0.01 s | schema creation, re-entrant transactions with rollback, UTC day bounds |
| test_llm.py | llm | 5 | 5 / 5 (100%) | 0.01 s | JSON extraction, tool-call parsing, provider error wrapping, fake model |
| test_metrics.py | metrics | 5 | 5 / 5 (100%) | 1.52 s | batch invariants: overruns zero, denials explained, determinism per seed |
| test_replay.py | replay | 4 | 4 / 4 (100%) | 0.09 s | recorded decisions replay identically; changed inputs are reported |
| test_evalset.py | eval | 3 | 3 / 3 (100%) | 0.01 s | every adversarial case denied on its rule, benign cases allowed, escalation |

## Shape of the suite

| Layer | Cases | Share |
|---|---|---|
| Unit: pure functions and stores (gate, ledger, mandates, catalog, policy, agents, llm parsing, eval, replay) | 160 | 56% |
| Service: state machine, adapters, buyer loop, batch | 70 | 24% |
| Surface: HTTP API, dashboard, CLI, demo scenarios | 56 | 20% |

Gate rules: every purchase rule G00 to G14 and every refund rule RF00 to RF04 has at least one passing and one failing case; the G99 guard has its own test. Session transitions: every row of the state-machine table in `docs/architecture.md` has a test, including the honest-cancel path where Razorpay refuses the cancel.

## Slowest tests

| Test | Time |
|---|---|
| test_cli.py::test_ledger_verify_show_tamper_receipt | 0.95 s |
| test_cli.py::test_demo_refused_and_happy_on_fake_payments | 0.72 s |
| test_metrics.py::test_run_batch_is_deterministic_for_a_seed | 0.69 s |
| test_cli.py::test_review_commands | 0.69 s |
| test_cli.py::test_refund_command | 0.60 s |
| test_cli.py::test_enrich_approve_reject | 0.59 s |
| test_cli.py::test_ledger_tamper_without_a_seq | 0.54 s |
| test_cli.py::test_ledger_replay_command | 0.54 s |
| test_metrics.py::test_batch_exercises_every_path | 0.44 s |
| test_dashboard.py::test_login_wrong_token_shows_error_and_overview_renders | 0.40 s |

The slow ones run whole demo scenarios or the 60-session metrics batch through the real state machine; the rest are milliseconds.

## How the suite maps to the track's bar

| Bar | Tests |
|---|---|
| Explainable | test_gate.py (every rule leaves a detail), test_sessions.py (messages carry rule ids), test_api.py (policy_denied responses carry rule_id), test_replay.py |
| Bounded | test_mandates.py, test_gate.py G08 to G12, test_sessions.py spend accounting, test_metrics.py overruns |
| Gated | test_sessions.py (link only after ALLOW and reservation), test_enrichment.py (pending until approved), test_crosssell.py (candidates under every cap), test_dashboard.py (review approve / decline) |
| Audit trail | test_ledger.py, test_replay.py, test_api.py trail, test_dashboard.py ledger pages |
| Failure handled | test_sessions.py retry / abandon / provider error / refused cancel, test_api.py webhook dedupe, test_buyer.py fail-closed planner, test_gate.py G99 guard |

## What the suite does not cover

- Live Razorpay calls. `test_razorpay_client.py` runs against a stubbed SDK that records calls; `scripts/smoke_razorpay.py` is the one-time check against a real test account.
- A real language model. `FakeLLM` scripts replies; `LLMClient` parsing is tested against a stub client.
- Browser rendering. Dashboard tests assert on returned HTML, not on a browser.
- Concurrency under load. The per-connection lock is exercised by the reconciler design, not by a stress test.

Regenerate this file with `python scripts/make_test_report.py`.
