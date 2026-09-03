# Dwarpal task runner. Every target is offline unless it says otherwise.
# Windows without make: run the commands under each target directly, or use PowerShell equivalents in CONTRIBUTING.md.

PYTHON ?= python
DB     ?= dwarpal.db

.DEFAULT_GOAL := help
.PHONY: help install test eval metrics demo demos serve reports ledger clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package and dev dependencies
	$(PYTHON) -m pip install -e ".[dev]"

test:  ## Run the test suite (offline)
	$(PYTHON) -m pytest -q

eval:  ## Adversarial gate eval: block rate and false-positive rate
	$(PYTHON) -m dwarpal eval

metrics:  ## Batch metrics over 50 scripted sessions
	$(PYTHON) -m dwarpal metrics --n 50 --seed 7

init:  ## Create the database and seed the demo store
	$(PYTHON) -m dwarpal --db $(DB) init
	$(PYTHON) -m dwarpal --db $(DB) seed

demo:  ## One scenario, fake payments: make demo SCENARIO=replan
	$(PYTHON) -m dwarpal --db $(DB) demo --scenario $(or $(SCENARIO),replan) --payments fake

demos:  ## Every scenario end to end on fake payments
	@for s in happy refused replan payfail crosssell review refund; do \
		echo "=== $$s ==="; \
		$(PYTHON) -m dwarpal --db $(DB) demo --scenario $$s --payments fake --wait 30 || exit 1; \
	done

serve:  ## Run the API and dashboard on http://127.0.0.1:8000
	$(PYTHON) -m dwarpal --db $(DB) serve

ledger:  ## Verify the hash chain and replay every recorded decision
	$(PYTHON) -m dwarpal --db $(DB) ledger verify
	$(PYTHON) -m dwarpal --db $(DB) ledger replay

reports:  ## Regenerate every computed document
	$(PYTHON) scripts/make_evaluation.py
	$(PYTHON) scripts/make_test_report.py
	$(PYTHON) -m dwarpal eval --out docs/gate-eval.md
	$(PYTHON) -m dwarpal metrics --n 50  --seed 7  --out docs/metrics-2026-09-03.md
	$(PYTHON) -m dwarpal metrics --n 500 --seed 11 --out docs/metrics-500-sessions.md

smoke:  ## One real Razorpay test-mode Payment Link (needs rzp_test_ keys in .env)
	$(PYTHON) scripts/smoke_razorpay.py

clean:  ## Remove databases, run output and caches
	rm -rf .pytest_cache runs *.egg-info build dist
	rm -f $(DB) $(DB)-wal $(DB)-shm ci.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
