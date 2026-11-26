# Shortcuts for macOS/Linux. Run `make setup` once, then `make run` or `make test`.
PY := .venv/bin/python

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) -m cft_model run

run-monthly:
	$(PY) -m cft_model run -c config/monthly.yaml

validate:
	$(PY) -m cft_model validate

test:
	$(PY) -m pytest -q

data:
	$(PY) -m cft_model generate-data

clean:
	rm -rf output .pytest_cache

.PHONY: setup run run-monthly validate test data clean
