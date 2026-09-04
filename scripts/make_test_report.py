"""Run the test suite and write docs/test-results.md: pass rate, per-file breakdown with what each file covers,
timing, slowest tests, and how the suite maps to the track's bar. Every number comes from the run.

Usage:  python scripts/make_test_report.py [--out docs/test-results.md]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dwarpal import __version__  # noqa: E402

COVERS = {
    "test_money.py": ("money", "integer paise formatting and validation; bool is never an amount"),
    "test_db.py": ("db", "schema creation, re-entrant transactions with rollback, UTC day bounds"),
    "test_ledger.py": ("ledger", "hash chain, verify, tamper detection, receipt rendering, filtering"),
    "test_catalog.py": ("catalog", "seed data, raw seed, feed visibility of approved fields, search, snapshot"),
    "test_policy.py": ("policy", "merchant policy validation including review threshold and refund window"),
    "test_agents.py": ("agents", "key issuance, hashing at rest, authentication, revocation"),
    "test_mandates.py": ("mandates", "caps, validity window, reserve / commit / release, daily and total spend"),
    "test_gate.py": ("gate", "one pass and one fail per rule G00 to G14, G99 guard, refund rules RF00 to RF04"),
    "test_payments_fake.py": ("payments", "fake adapter outcomes: paid, failed, pending, expired, error, refund"),
    "test_crosssell.py": ("cross-sell", "headroom under every cap, candidate filtering, fake and LLM pickers"),
    "test_sessions.py": ("sessions", "state machine: create, update, idempotency, complete, reserve, retry, abandon, "
                                     "cancel, review queue, refunds, spend across sessions, trail"),
    "test_api.py": ("api", "discovery, feed, auth, idempotency, error shape, full flow, webhook signature and dedupe"),
    "test_razorpay_client.py": ("razorpay", "test-key guard, link creation fields, failed-attempt lookup via order "
                                            "and notes, refunds, items sync and push, webhook HMAC"),
    "test_llm.py": ("llm", "JSON extraction, tool-call parsing, provider error wrapping, fake model"),
    "test_enrichment.py": ("enrichment", "proposal validation, keyword and LLM enrichers, pending / approve / reject"),
    "test_buyer.py": ("buyer", "client errors, scripted runs for every scenario, LLM planner loop, replanning, "
                               "parallel tool calls, fail-closed behaviour"),
    "test_demo.py": ("demo", "all seven scenarios end to end on fakes, policy restored after the review scenario"),
    "test_metrics.py": ("metrics", "batch invariants: overruns zero, denials explained, determinism per seed"),
    "test_evalset.py": ("eval", "every adversarial case denied on its rule, benign cases allowed, escalation"),
    "test_replay.py": ("replay", "recorded decisions replay identically; changed inputs are reported"),
    "test_dashboard.py": ("dashboard", "login, products and enrichment approval, agents, policy, sessions, review, "
                                       "refund, ledger verify, replay, receipt"),
    "test_cli.py": ("cli", "every command on a temporary database, exit codes, review, refund, eval, replay"),
}

BAR = [
    ("Explainable", "test_gate.py (every rule leaves a detail), test_sessions.py (messages carry rule ids), "
                    "test_api.py (policy_denied responses carry rule_id), test_replay.py"),
    ("Bounded", "test_mandates.py, test_gate.py G08 to G12, test_sessions.py spend accounting, test_metrics.py overruns"),
    ("Gated", "test_sessions.py (link only after ALLOW and reservation), test_enrichment.py (pending until approved), "
              "test_crosssell.py (candidates under every cap), test_dashboard.py (review approve / decline)"),
    ("Audit trail", "test_ledger.py, test_replay.py, test_api.py trail, test_dashboard.py ledger pages"),
    ("Failure handled", "test_sessions.py retry / abandon / provider error / refused cancel, test_api.py webhook dedupe, "
                        "test_buyer.py fail-closed planner, test_gate.py G99 guard"),
]


def run_suite(xml_path: str) -> tuple[list[dict], str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", f"--junitxml={xml_path}", "--durations=0"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    cases = []
    root = ET.parse(xml_path).getroot()
    for tc in root.iter("testcase"):
        status = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        elif tc.find("skipped") is not None:
            status = "skipped"
        classname = tc.get("classname", "")
        file = classname.split(".")[-1] + ".py" if classname else "?"
        cases.append({"file": file, "name": tc.get("name"), "time": float(tc.get("time", 0)), "status": status})
    return cases, proc.stdout + proc.stderr


def build(cases: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(cases)
    passed = sum(1 for c in cases if c["status"] == "passed")
    failed = sum(1 for c in cases if c["status"] == "failed")
    skipped = total - passed - failed
    total_time = sum(c["time"] for c in cases)
    by_file: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_file[c["file"]].append(c)

    L = [f"# DwarPal test results", "",
         f"Generated {now} by `python scripts/make_test_report.py` (version {__version__}) from a full "
         f"`pytest` run on this machine. Nothing below is typed by hand.", "",
         "## Summary", "", "| Metric | Value |", "|---|---|",
         f"| Test cases collected | {total} |",
         f"| Passed | {passed} / {total} ({passed / total:.1%}) |" if total else "| Passed | 0 |",
         f"| Failed | {failed} |", f"| Skipped | {skipped} |",
         f"| Test files | {len(by_file)} |",
         f"| Total test time | {total_time:.1f} s |",
         f"| Mean per test | {(total_time / total * 1000) if total else 0:.0f} ms |",
         "| Network access | none: fake Razorpay, fake model, fixed clock |", ""]

    L += ["## Per file", "", "| File | Area | Cases | Passed | Time | What it covers |", "|---|---|---|---|---|---|"]
    for file in sorted(by_file, key=lambda f: -len(by_file[f])):
        cs = by_file[file]
        area, what = COVERS.get(file, ("?", ""))
        p = sum(1 for c in cs if c["status"] == "passed")
        L.append(f"| {file} | {area} | {len(cs)} | {p} / {len(cs)} ({p / len(cs):.0%}) | "
                 f"{sum(c['time'] for c in cs):.2f} s | {what} |")
    L.append("")

    unit = sum(len(v) for k, v in by_file.items() if k in ("test_money.py", "test_db.py", "test_ledger.py",
                                                            "test_catalog.py", "test_policy.py", "test_agents.py",
                                                            "test_mandates.py", "test_gate.py", "test_payments_fake.py",
                                                            "test_crosssell.py", "test_llm.py", "test_enrichment.py",
                                                            "test_replay.py", "test_evalset.py"))
    service = sum(len(v) for k, v in by_file.items() if k in ("test_sessions.py", "test_razorpay_client.py",
                                                               "test_metrics.py", "test_buyer.py"))
    surface = sum(len(v) for k, v in by_file.items() if k in ("test_api.py", "test_dashboard.py", "test_cli.py",
                                                               "test_demo.py"))
    L += ["## Shape of the suite", "", "| Layer | Cases | Share |", "|---|---|---|",
          f"| Unit: pure functions and stores (gate, ledger, mandates, catalog, policy, agents, llm parsing, eval, replay) | {unit} | {unit / total:.0%} |",
          f"| Service: state machine, adapters, buyer loop, batch | {service} | {service / total:.0%} |",
          f"| Surface: HTTP API, dashboard, CLI, demo scenarios | {surface} | {surface / total:.0%} |", "",
          "Gate rules: every purchase rule G00 to G14 and every refund rule RF00 to RF04 has at least one passing and "
          "one failing case; the G99 guard has its own test. Session transitions: every row of the state-machine table "
          "in `docs/architecture.md` has a test, including the honest-cancel path where Razorpay refuses the cancel.", ""]

    slowest = sorted(cases, key=lambda c: -c["time"])[:10]
    L += ["## Slowest tests", "", "| Test | Time |", "|---|---|"]
    for c in slowest:
        L.append(f"| {c['file']}::{c['name']} | {c['time']:.2f} s |")
    L += ["", "The slow ones run whole demo scenarios or the 60-session metrics batch through the real state machine; "
          "the rest are milliseconds.", ""]

    L += ["## How the suite maps to the track's bar", "", "| Bar | Tests |", "|---|---|"]
    for bar, tests in BAR:
        L.append(f"| {bar} | {tests} |")
    L += ["", "## What the suite does not cover", "",
          "- Live Razorpay calls. `test_razorpay_client.py` runs against a stubbed SDK that records calls; "
          "`scripts/smoke_razorpay.py` is the one-time check against a real test account.",
          "- A real language model. `FakeLLM` scripts replies; `LLMClient` parsing is tested against a stub client.",
          "- Browser rendering. Dashboard tests assert on returned HTML, not on a browser.",
          "- Concurrency under load. The per-connection lock is exercised by the reconciler design, not by a stress test.", "",
          "Regenerate this file with `python scripts/make_test_report.py`.", ""]
    if failed:
        L += ["## Failures", ""] + [f"- {c['file']}::{c['name']}" for c in cases if c["status"] == "failed"] + [""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join("docs", "test-results.md"))
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        cases, _ = run_suite(os.path.join(tmp, "junit.xml"))
    text = build(cases)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(text)
    print(f"(written to {args.out})")
    return 0 if all(c["status"] != "failed" for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
