"""Regenerate Evaluation.md (repo root) from the actual generators: gate eval, batch metrics (50 and 500 sessions),
decision replay, and the test suite. Every number in the document comes from this run.

Usage:  python scripts/make_evaluation.py [--skip-tests] [--out Evaluation.md]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run from anywhere

from dwarpal import __version__  # noqa: E402
from dwarpal.evalset import run_eval  # noqa: E402
from dwarpal.gate import RULE_IDS  # noqa: E402
from dwarpal.metrics import run_batch  # noqa: E402
from dwarpal.money import rupees  # noqa: E402


def pct(a: int | float, b: int | float, digits: int = 1) -> str:
    return f"{a / b:.{digits}%}" if b else "n/a"


def run_tests() -> tuple[int, int]:
    """Returns (passed, failed) from a full pytest run."""
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"], capture_output=True, text=True)
    text = out.stdout + out.stderr
    passed = sum(int(m) for m in re.findall(r"(\d+) passed", text))
    failed = sum(int(m) for m in re.findall(r"(\d+) failed", text))
    if not passed:  # quiet mode prints only dots; count them
        dots = "".join(line for line in text.splitlines() if set(line.strip()) <= set(".FEsx% []0123456789"))
        passed = dots.count(".")
        failed = dots.count("F") + dots.count("E")
    return passed, failed


def build(skip_tests: bool) -> str:
    L: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # -- gate eval ---------------------------------------------------------------------------------
    results = run_eval()
    abusive = [r for r in results if r.kind == "abusive"]
    benign = [r for r in results if r.kind == "benign"]
    review = [r for r in results if r.kind == "review"]
    tp = sum(1 for r in abusive if r.verdict == "DENY")
    fn = len(abusive) - tp
    fp = sum(1 for r in benign if r.verdict != "ALLOW")
    tn = len(benign) - fp
    esc = sum(1 for r in review if r.verdict == "REVIEW")
    rules_fired = {r.rule_id for r in results if r.verdict == "DENY"}
    purchase_rules = [x for x in RULE_IDS if x != "G14_REVIEW_THRESHOLD"]
    exercised = [x for x in purchase_rules if x in rules_fired]

    # -- batches -----------------------------------------------------------------------------------
    b50 = run_batch(50, 7)
    b500 = run_batch(500, 11)

    # -- tests -------------------------------------------------------------------------------------
    passed, failed = (0, 0) if skip_tests else run_tests()

    L += [f"# Dwarpal evaluation", "",
          f"Generated {now} by `python scripts/make_evaluation.py` (version {__version__}). Every number below is "
          f"computed in that run; nothing is typed in by hand.", "",
          "**What was evaluated:** the deterministic parts of Dwarpal, the policy gate, the checkout state machine, "
          "the mandate accounting, the ledger, on data the repo generates for itself: hand-built adversarial and "
          "benign carts, and seeded batches of scripted sessions against a fake Razorpay. **What was not:** real "
          "Razorpay calls (need test keys), a real language model (need a key), real merchant traffic (none exists "
          "for this track). The numbers are honest about that: 100% and 0% are expected by construction for a pure "
          "function under test; the evidence is which rules fire, that the failure paths really run, and that the "
          "accounting holds across thousands of ledger events.", ""]

    # -- 1. tests ----------------------------------------------------------------------------------
    L += ["## 1. Automated test suite", ""]
    if skip_tests:
        L += ["Skipped in this run (`--skip-tests`). See `docs/test-results.md` for the full analysis.", ""]
    else:
        L += ["| Metric | Value |", "|---|---|",
              f"| Tests passed | {passed} / {passed + failed} ({pct(passed, passed + failed)}) |",
              f"| Tests failed | {failed} |",
              "| Network access during tests | none (fake Razorpay, fake model, fixed clock) |", "",
              "Per-file breakdown, timing and coverage notes: `docs/test-results.md`.", ""]

    # -- 2. gate eval -------------------------------------------------------------------------------
    L += ["## 2. Gate eval: adversarial and benign carts", "",
          f"{len(results)} hand-built cases run through `gate.evaluate` with no model: {len(abusive)} abusive, "
          f"{len(benign)} benign boundary cases, {len(review)} escalation to merchant review. Treating the gate as a "
          "detector of carts that must not go through:", "",
          "| Metric | Formula | Value |", "|---|---|---|",
          f"| True positives (abusive denied) | | {tp} |",
          f"| False negatives (abusive allowed) | | {fn} |",
          f"| False positives (benign denied) | | {fp} |",
          f"| True negatives (benign allowed) | | {tn} |",
          f"| Block rate / recall | TP / (TP + FN) | {tp} / {tp + fn} = **{pct(tp, tp + fn)}** |",
          f"| Precision | TP / (TP + FP) | {tp} / {tp + fp} = **{pct(tp, tp + fp)}** |",
          f"| False-positive rate | FP / (FP + TN) | {fp} / {fp + tn} = **{pct(fp, fp + tn)}** |",
          f"| Specificity | TN / (TN + FP) | {tn} / {tn + fp} = **{pct(tn, tn + fp)}** |",
          f"| Accuracy | (TP + TN) / all | {tp + tn} / {tp + tn + fp + fn} = **{pct(tp + tn, tp + tn + fp + fn)}** |",
          f"| Escalations to review handled | REVIEW / expected | {esc} / {len(review)} = {pct(esc, len(review))} |",
          f"| Purchase rules exercised by a denial | fired / total | {len(exercised)} / {len(purchase_rules)} = "
          f"{pct(len(exercised), len(purchase_rules))} |", "",
          "Rules that fired: " + ", ".join(sorted(rules_fired)) + ".",
          "Not fired by a denial: " + (", ".join(x for x in purchase_rules if x not in rules_fired) or "none") +
          " (G14 escalates to REVIEW rather than denying; G99 is a guard and is covered by a unit test).", "",
          "Full table: `docs/gate-eval.md`.", ""]

    # -- 3. batches --------------------------------------------------------------------------------
    def batch_rows(r):
        completed = r.outcomes.get("completed", 0)
        refused = r.outcomes.get("refused", 0)
        canceled = r.outcomes.get("canceled", 0)
        denials = sum(r.denials_by_rule.values())
        uplift = ((r.avg_basket_with_offer_paise - r.avg_basket_without_offer_paise) / r.avg_basket_without_offer_paise
                  if r.avg_basket_without_offer_paise else 0)
        return {
            "sessions": r.sessions,
            "completion": f"{completed} / {r.sessions} = {pct(completed, r.sessions)}",
            "refusal": f"{refused} / {r.sessions} = {pct(refused, r.sessions)}",
            "abandon": f"{canceled} / {r.sessions} = {pct(canceled, r.sessions)}",
            "explained": f"{denials} / {refused} = {pct(denials, refused)}",
            "overruns": f"{r.mandate_overruns} (overrun rate {pct(r.mandate_overruns, r.sessions)})",
            "recovery": f"{r.recovered} / {r.retried} = {pct(r.recovered, r.retried)}",
            "released": f"{r.released} / {r.abandoned} = {pct(r.released, r.abandoned)}",
            "attach": f"{r.offers_accepted} / {r.offers_made} = **{pct(r.offers_accepted, r.offers_made)}**",
            "uplift": f"{rupees(r.avg_basket_without_offer_paise)} to {rupees(r.avg_basket_with_offer_paise)} "
                      f"(**{uplift:+.1%}**)",
            "revenue": rupees(r.completed_revenue_paise),
            "chain": f"{'verified' if r.ledger_ok else 'BROKEN'}, {r.ledger_events} events",
            "replay": f"{r.replay_identical} / {r.replay_decisions} = {pct(r.replay_identical, r.replay_decisions)}",
            "mix": ", ".join(f"{k} {v}" for k, v in sorted(r.scenario_counts.items())),
        }

    a, b = batch_rows(b50), batch_rows(b500)
    L += ["## 3. Batch metrics: scripted sessions against the real gate and state machine", "",
          "Three agents with weekly mandates (renewed on expiry), a seeded mix of allowed carts, refused carts, "
          "failed first payments and abandoned payments, one order an hour, fake Razorpay. Reports: "
          "`docs/metrics-2026-09-03.md` and `docs/metrics-500-sessions.md`.", "",
          "| Metric | 50 sessions (seed 7) | 500 sessions (seed 11) |", "|---|---|---|",
          f"| Intended scenario mix | {a['mix']} | {b['mix']} |",
          f"| Completion rate | {a['completion']} | {b['completion']} |",
          f"| Refusal rate | {a['refusal']} | {b['refusal']} |",
          f"| Abandon rate (two failed payments) | {a['abandon']} | {b['abandon']} |",
          f"| Denials that name a rule | {a['explained']} | {b['explained']} |",
          f"| Mandate overruns (reserved + committed vs every cap) | **{a['overruns']}** | **{b['overruns']}** |",
          f"| Payment recovery (paid on attempt 2 / retried) | {a['recovery']} | {b['recovery']} |",
          f"| Budget released after abandon | {a['released']} | {b['released']} |",
          f"| Cross-sell attach rate (accepted / offered) | {a['attach']} | {b['attach']} |",
          f"| Average completed basket, without to with an accepted offer | {a['uplift']} | {b['uplift']} |",
          f"| Completed revenue | {a['revenue']} | {b['revenue']} |",
          f"| Ledger chain | {a['chain']} | {b['chain']} |",
          f"| Decisions replayed identically from recorded input | {a['replay']} | {b['replay']} |", ""]

    L += ["### Denials by rule", "", "| Rule | 50 sessions | share | 500 sessions | share |", "|---|---|---|---|---|"]
    d50, d500 = b50.denials_by_rule, b500.denials_by_rule
    t50, t500 = sum(d50.values()), sum(d500.values())
    for rule in sorted(set(d50) | set(d500)):
        L.append(f"| {rule} | {d50.get(rule, 0)} | {pct(d50.get(rule, 0), t50)} | {d500.get(rule, 0)} | "
                 f"{pct(d500.get(rule, 0), t500)} |")
    L += ["", "The G12 denials in the large run are the small-budget agent running its weekly total down; that is the "
          "rule doing its job, and every one of those sessions tells the agent so.", ""]

    # -- 4. failure paths ---------------------------------------------------------------------------
    L += ["## 4. Failure paths exercised", "",
          "| Failure | How it is handled | Where it is proven |", "|---|---|---|",
          "| Payment fails on the bank page | recorded, gate re-run in retry mode, old link cancelled, fresh link | "
          f"batch: {b500.retried} retries, {b500.recovered} recovered; `test_failed_first_attempt_retries_with_fresh_link` |",
          "| Second failure | abandon, link cancelled, reservation released | "
          f"batch: {b500.abandoned} abandoned, {b500.released} released; `test_failed_twice_abandons_and_releases` |",
          "| Cap breached between create and complete | complete denied with the rule; nothing reserved | "
          "`test_complete_denied_by_authoritative_gate_run` |",
          "| Payment provider error at complete | reservation released, HTTP 502, agent may retry | "
          "`test_complete_provider_error_releases_and_stays_ready` |",
          "| Cancel refused by Razorpay (customer paid at that moment) | final poll; late capture completes, else "
          "`cancel_failed` | `test_cancel_when_provider_cancel_fails` |",
          "| Duplicate or forged webhook | HMAC check; event id dedupe | `test_webhook_signature_dedupe_and_reconcile` |",
          "| Model down or returns garbage | enrichment skipped and recorded; no offers; buyer fails closed | "
          "`test_skipped_proposals_are_recorded`, `test_llm_planner_provider_error_fails_closed` |",
          "| Internal error inside a rule | DENY on G99 with the exception type; never an accidental ALLOW | "
          "`test_g99_guard_never_raises` |",
          "| Ledger edited after the fact | `verify` names the first bad sequence | `test_tamper_breaks_verify_at_that_seq` |",
          "| Recorded decision that the gate would not make | `replay` reports the divergence | "
          "`test_replay_detects_a_changed_recorded_input` |", ""]

    # -- 5. limits -----------------------------------------------------------------------------------
    L += ["## 5. What these numbers do not show", "",
          "- No real Razorpay traffic was measured. The adapter is unit-tested against a stubbed SDK; "
          "`scripts/smoke_razorpay.py` confirms it against a test account in one run.",
          "- No language model was measured. The eval and the batches use scripted carts and a deterministic "
          "cross-sell picker, so attach rate here measures the mechanism, not a model's taste.",
          "- No real merchant data exists for this track; the catalog is a ten-product demo store.",
          "- 100% block rate and 0% false positives on hand-built cases are expected for a pure function; the "
          "meaningful signal is coverage (which rules fire) and the benign boundary cases (exactly at a cap, prior "
          "spend just under a cap, a retry, an approved review).", "",
          "Regenerate this file with `python scripts/make_evaluation.py`.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="Evaluation.md")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    text = build(args.skip_tests)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(text)
    print(f"(written to {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
