PYTHON ?= python3
export PYTHONPATH := src

.PHONY: demo test lint reproduce-demo reproduce-budget clean

demo:
	$(PYTHON) -m flotilla demo

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m compileall -q src tests scripts
	$(PYTHON) scripts/lint.py

reproduce-demo:
	$(PYTHON) -m flotilla demo --summary-json reports/demo-summary.json

reproduce-budget:
	$(PYTHON) scripts/reproduce_budget.py

clean:
	$(PYTHON) -m flotilla clean

