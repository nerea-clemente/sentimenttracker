.PHONY: setup test lint fix seed api web dev clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

setup:                       ## Create the venv and install everything
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[api,ingest,watch,dev]"
	@test -f .env || cp .env.example .env
	$(VENV)/bin/cib init
	@echo "Ready. Try: make seed"

test:                        ## Run the test suite
	$(PY) -m pytest backend/tests -p no:warnings

lint:                        ## Lint
	$(VENV)/bin/ruff check backend/

fix:                         ## Lint and auto-fix
	$(VENV)/bin/ruff check --fix backend/

seed:                        ## Create the three placeholder campaign records
	$(VENV)/bin/cib seed

api:                         ## Run the HTTP API on :8000
	$(VENV)/bin/cib serve --reload

web:                         ## Run the dashboard on :3000
	cd web && npm run dev

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__
