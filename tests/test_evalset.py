from agentgate.evalset import CASES, EvalResult, render_markdown, run_eval


def test_every_case_has_the_expected_verdict_and_rule():
    results = run_eval()
    assert len(results) == len(CASES) >= 16
    for r in results:
        assert isinstance(r, EvalResult)
        assert r.verdict == r.expected_verdict, f"{r.name}: got {r.verdict} ({r.rule_id}: {r.reason})"
        if r.expected_rule:
            assert r.rule_id == r.expected_rule, f"{r.name}: expected {r.expected_rule}, got {r.rule_id}"


def test_block_rate_and_false_positive_rate_are_reported():
    results = run_eval()
    abusive = [r for r in results if r.kind == "abusive"]
    benign = [r for r in results if r.kind == "benign"]
    assert len(abusive) >= 11 and len(benign) >= 5
    assert all(r.verdict == "DENY" for r in abusive)
    assert all(r.verdict == "ALLOW" for r in benign)
    md = render_markdown(results)
    assert "Block rate" in md and "False-positive rate" in md and "100%" in md and "0%" in md
    assert "catalog_injection_over_quantity" in md and "benign_exactly_at_cap" in md


def test_eval_covers_many_distinct_rules():
    rules = {r.rule_id for r in run_eval() if r.verdict == "DENY"}
    assert len(rules) >= 10
