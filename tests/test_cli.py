import json

import pytest

from agentgate.cli import main
from agentgate.db import connect


@pytest.fixture
def run(tmp_path, capsys):
    db = str(tmp_path / "cli.db")

    def _run(*args, env=None):
        code = main(["--db", db, *args], env=env if env is not None else {})
        out = capsys.readouterr()
        return code, out.out + out.err

    _run.db = db
    return _run


def test_init_seed_and_policy(run):
    code, out = run("init")
    assert code == 0 and "initialised" in out.lower()
    code, out = run("seed")
    assert code == 0 and "10 products" in out
    code, out = run("policy", "show")
    assert code == 0 and "max_order_paise" in out


def test_policy_set_valid_and_invalid(run, tmp_path):
    run("init")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, out = run("policy", "set", str(bad))
    assert code == 1 and "error" in out.lower()
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"max_order_paise": 100, "allowed_categories": ["footwear"], "blocked_skus": [],
                                "max_qty_per_line": 1, "in_stock_only": True}), encoding="utf-8")
    code, out = run("policy", "set", str(good))
    assert code == 0 and "saved" in out.lower()
    code, out = run("policy", "show")
    assert '"max_order_paise": 100' in out


def test_agent_add_list_revoke(run):
    run("init")
    code, out = run("agent", "add", "shopbot", "--per-txn", "2500", "--daily", "5000", "--total", "20000",
                    "--categories", "footwear,apparel", "--days", "3")
    assert code == 0 and "agk_" in out and "shown once" in out.lower()
    agent_id = [w for w in out.split() if w.startswith("agt_")][0]
    code, out = run("agent", "list")
    assert code == 0 and "shopbot" in out and "INR 2,500.00" in out and "footwear" in out
    code, out = run("agent", "revoke", agent_id)
    assert code == 0 and "revoked" in out
    code, out = run("agent", "revoke", "agt_nope")
    assert code == 1


def test_enrich_approve_reject(run):
    run("init")
    run("seed", "--raw")
    code, out = run("enrich", "--fake-llm")
    assert code == 0 and "10 proposal" in out
    code, out = run("enrich", "--fake-llm")
    assert code == 0 and "0 proposal" in out
    code, out = run("approve", "--all")
    assert code == 0 and "10 approved" in out
    code, out = run("reject", "--id", "enr_nope")
    assert code == 1


def test_demo_refused_and_happy_on_fake_payments(run):
    run("init")
    run("seed")
    code, out = run("demo", "--scenario", "refused", "--payments", "fake")
    assert code == 0 and "G06_MERCHANT_CATEGORY" in out and "Outcome: refused" in out
    code, out = run("demo", "--scenario", "payfail", "--payments", "fake", "--wait", "30")
    assert code == 0 and "Outcome: paid" in out and "payment.retry" in out


def test_demo_needs_config_for_llm_and_real_payments(run):
    run("init")
    run("seed")
    code, out = run("demo", "--scenario", "happy", "--planner", "llm")
    assert code == 1 and "LLM" in out
    code, out = run("demo", "--scenario", "happy", "--payments", "real")
    assert code == 1 and "RAZORPAY" in out
    code, out = run("demo", "--scenario", "nope")
    assert code == 2


def test_ledger_verify_show_tamper_receipt(run):
    run("init")
    run("seed")
    run("demo", "--scenario", "happy", "--payments", "fake")
    code, out = run("ledger", "verify")
    assert code == 0 and "verified" in out
    code, out = run("ledger", "show", "--limit", "50")
    assert code == 0 and "session.created" in out
    code, out = run("ledger", "show", "--limit", "2")
    assert code == 0 and "session.completed" in out and "session.created" not in out
    conn = connect(run.db)
    seq = conn.execute("select seq from ledger where type = 'session.created' limit 1").fetchone()[0]
    sid = conn.execute("select id from sessions limit 1").fetchone()[0]
    conn.close()
    code, out = run("ledger", "receipt", sid)
    assert code == 0 and "Receipt for session" in out
    code, out = run("ledger", "tamper", str(seq))
    assert code == 0
    code, out = run("ledger", "verify")
    assert code == 2 and "BROKEN" in out
    code, out = run("ledger", "receipt", "cs_nope")
    assert code == 1


def test_reconcile_once_and_metrics(run, tmp_path):
    run("init")
    run("seed")
    code, out = run("reconcile", "--once")
    assert code == 0 and "0 session" in out
    out_file = tmp_path / "m.md"
    code, out = run("metrics", "--n", "5", "--out", str(out_file))
    assert code == 0 and out_file.exists() and "AgentGate batch metrics" in out


def test_sync_and_push_need_keys(run):
    run("init")
    code, out = run("sync-items")
    assert code == 1 and "RAZORPAY" in out
    code, out = run("seed", "--push")
    assert code == 1 and "RAZORPAY" in out


def test_eval_prints_table(run, tmp_path):
    out_file = tmp_path / "eval.md"
    code, out = run("eval", "--out", str(out_file))
    assert code == 0 and "Block rate" in out and "100%" in out and out_file.exists()


def test_review_commands(run, tmp_path):
    run("init")
    run("seed")
    policy = tmp_path / "p.json"
    policy.write_text(json.dumps({"max_order_paise": 500000, "allowed_categories": ["footwear", "apparel",
                                  "accessories", "fitness"], "blocked_skus": [], "max_qty_per_line": 5,
                                  "in_stock_only": True, "review_above_paise": 200000}), encoding="utf-8")
    assert run("policy", "set", str(policy))[0] == 0
    code, out = run("demo", "--scenario", "happy", "--payments", "fake", "--wait", "0")
    assert code == 0 and "Outcome: requires_review" in out
    code, out = run("review", "list")
    assert code == 0 and "cs_" in out
    sid = [w for w in out.split() if w.startswith("cs_")][0]
    code, out = run("review", "approve", sid, "--note", "fine")
    assert code == 0 and "ready_for_payment" in out
    code, out = run("review", "decline", sid)
    assert code == 1


def test_refund_command(run):
    run("init")
    run("seed")
    code, out = run("demo", "--scenario", "happy", "--payments", "fake")
    sid = [w.strip(":,") for w in out.split() if w.startswith("cs_")][0]
    code, out = run("refund", sid, "--amount", "699", "--reason", "bottle short-shipped", "--reference", "sf-1")
    assert code == 0 and "rfnd_" in out and "INR 699.00" in out
    code, out = run("refund", sid, "--amount", "99999", "--reason", "x", "--reference", "sf-2")
    assert code == 1 and "RF02" in out
