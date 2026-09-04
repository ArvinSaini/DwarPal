# Contributing to Dwarpal

Thanks for looking. Dwarpal is a Razorpay AI Buildathon 2026 entry (Track 01), so the code is written to be read:
small modules, one responsibility each, and a test for every rule.

## Set up

```powershell
git clone <this repo>; cd dwarpal
python -m pip install -e ".[dev]"     # Python 3.11+
copy .env.example .env                # optional: only needed for real Razorpay or a real model
python -m pytest -q                   # 286 tests, offline, about 10 seconds
```

Nothing above needs a network connection, an API key, or Docker. The test suite uses a fake Razorpay adapter, a fake
language model and a fixed clock.

## Run it

```powershell
python -m dwarpal init
python -m dwarpal seed
python -m dwarpal demo --scenario replan --payments fake
python -m dwarpal serve                # API and dashboard on http://127.0.0.1:8000
```

`python -m dwarpal --help` lists every command. See `docs/demo-script.md` for the guided walkthrough.

## The rule that shapes the code

**The model proposes, deterministic code disposes.** A language model may propose catalog metadata, suggest
cross-sell add-ons, and plan the demo buyer's actions. It must never decide whether money moves. If a change would
let model output reach the gate, the mandate accounting, a session transition, a Razorpay call or the ledger without
passing deterministic validation or a human approval, it does not belong in Dwarpal.

Concretely:

- `dwarpal/gate.py` is a pure function. No I/O, no clock reads, no imports of the database or a model client.
- Only `dwarpal/razorpay_client.py` imports the Razorpay SDK and holds the keys, and it refuses any key that is not
  `rzp_test_`.
- Only `SessionService.complete` and its retry path create a Payment Link, and only after an ALLOW plus a reservation.
- Money is integer paise everywhere. `require_paise` rejects floats, negatives and booleans.
- Every store and service takes an injected `clock`, so tests are deterministic.

## Adding a gate rule

1. Write the failing tests first: one that passes the rule and one that fails it, in `tests/test_gate.py`.
2. Add the rule id to `RULE_IDS` in `dwarpal/gate.py`, in evaluation order.
3. Implement it in `_evaluate`, recording a plain-English detail through `ok()` or `fail()`. First failure decides.
4. Add an adversarial case and, if it has an interesting boundary, a benign case to `dwarpal/evalset.py`.
5. Update the rule table in `README.md` and `docs/architecture.md`.
6. Regenerate the reports (below) and run the suite.

## Regenerating the reports

Every number in the documentation is computed, never typed:

```powershell
python scripts/make_evaluation.py      # Evaluation.md
python scripts/make_test_report.py     # docs/test-results.md
python -m dwarpal eval --out docs/gate-eval.md
python -m dwarpal metrics --n 50  --seed 7  --out docs/metrics-2026-09-03.md
python -m dwarpal metrics --n 500 --seed 11 --out docs/metrics-500-sessions.md
python scripts/make_diagrams.py        # docs/img/*.svg, the README diagrams
```

If you change the gate, the state machine or the batch, regenerate all of them in the same commit so the documents
and the code never disagree.

## Testing against real Razorpay

Only test mode. Put `rzp_test_` keys in `.env`, then:

```powershell
python scripts/smoke_razorpay.py       # one 1-rupee link; confirms how failed attempts surface
python -m dwarpal seed --push          # creates the demo products as Razorpay Items
python -m dwarpal demo --scenario payfail --payments real
```

A test account allows roughly 30 Payment Links in total, so real calls are rationed: development and CI always use
the fake adapter.

## Style

- Follow the surrounding code. Lines up to 120 characters, standard library first in imports.
- Docstrings explain *why*, not *what*. The what is in the code.
- No new runtime dependencies without a reason in the pull request description.
- Commits are imperative and scoped: `feat(gate): ...`, `fix(sessions): ...`, `docs: ...`.

## Pull requests

CI runs the suite on Python 3.11, 3.12 and 3.13, the adversarial eval, the batch invariants, and every demo scenario
end to end. All of it must be green. Say in the description what you changed and how you verified it.
