from dwarpal.metrics import Report, main, render_markdown, run_batch


def test_run_batch_counts_and_invariants():
    r = run_batch(n=30, seed=3)
    assert isinstance(r, Report) and r.sessions == 30 and sum(r.outcomes.values()) == 30
    assert r.mandate_overruns == 0
    assert all(k.startswith("G") for k in r.denials_by_rule)
    assert sum(r.denials_by_rule.values()) == r.outcomes.get("refused", 0)
    assert r.ledger_ok and r.ledger_events > 30
    assert r.retried >= r.recovered and r.offers_made >= r.offers_accepted
    assert 0.0 <= r.attach_rate <= 1.0
    assert set(r.to_dict()) >= {"sessions", "outcomes", "denials_by_rule", "mandate_overruns", "attach_rate"}


def test_run_batch_is_deterministic_for_a_seed():
    assert run_batch(n=20, seed=5).to_dict() == run_batch(n=20, seed=5).to_dict()
    assert run_batch(n=20, seed=5).to_dict() != run_batch(n=20, seed=6).to_dict()


def test_batch_exercises_every_path():
    r = run_batch(n=60, seed=11)
    assert r.retried > 0 and r.recovered > 0 and r.released > 0
    assert len(r.denials_by_rule) >= 3 and r.offers_accepted > 0
    assert r.outcomes.get("completed", 0) > 0 and r.outcomes.get("canceled", 0) > 0
    assert r.scenario_counts and sum(r.scenario_counts.values()) == 60


def test_render_markdown_has_the_honest_sections():
    md = render_markdown(run_batch(n=10, seed=1))
    for needle in ("Attach rate", "Mandate overruns", "Ledger chain", "scripted", "| G"):
        assert needle in md, needle


def test_main_writes_a_report_file(tmp_path):
    out = tmp_path / "metrics.md"
    assert main(["--n", "5", "--seed", "2", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Dwarpal batch metrics" in text and "5 sessions" in text
