## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Why

<!-- The problem or the gap. Link an issue if there is one. -->

## How it was verified

<!-- Commands you ran and what they printed. -->

- [ ] `python -m pytest -q` passes
- [ ] `python -m dwarpal eval` passes (every adversarial case still denied on its rule)
- [ ] `python -m dwarpal metrics --n 50` shows zero mandate overruns and every denial naming a rule
- [ ] Generated reports regenerated in this commit if the gate, the state machine or the batch changed
      (`scripts/make_evaluation.py`, `scripts/make_test_report.py`)

## Money path

- [ ] No language-model output can reach a money decision without deterministic validation or merchant approval
- [ ] `dwarpal/gate.py` is still a pure function: no I/O, no clock reads, no model
- [ ] Any new money action is gated by a rule and lands in the ledger
- [ ] Razorpay test keys only; the live-key guard is untouched
