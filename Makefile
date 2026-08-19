.PHONY: setup test lint fix seed api web dev docs docs-check snapshot demo-db demo-snapshot site clean

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

snapshot:                    ## Rebuild the Pages snapshot from the committed state database
	CIB_DB_PATH=state/cib.sqlite3 $(VENV)/bin/cib export snapshot

site:                        ## Build the static site exactly as GitHub Pages does
	cd web && NEXT_PUBLIC_DATA_MODE=static DEPLOY_BASE_PATH=/sentimenttracker npm run build
	@echo "Built web/out. Preview it with:  make site-serve"

site-serve:                  ## Serve the built site under the Pages base path
	@rm -rf /tmp/cib-site && mkdir -p /tmp/cib-site
	@cp -r web/out /tmp/cib-site/sentimenttracker
	@echo "→ http://127.0.0.1:4011/sentimenttracker/"
	@cd /tmp/cib-site && python3 -m http.server 4011

demo-db:                     ## Build a LOCAL synthetic database from the test fixture (gitignored)
	@rm -f data/demo.sqlite3*
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib init
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib campaign add --name "Demo campaign A" \
	  --publisher "Demo publisher" --type ngo_report --status archived \
	  --published-at 2019-03-04T00:00:00 --slug demo-a
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib campaign add --name "Demo campaign B" \
	  --publisher "Demo publisher" --type coalition --status archived \
	  --published-at 2019-03-04T00:00:00 --slug demo-b
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib import csv --campaign demo-a \
	  --file backend/tests/fixtures/syndication_sample.csv --default-tier national_general
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib import csv --campaign demo-b \
	  --file backend/tests/fixtures/syndication_sample.csv --default-tier national_general
	@echo
	@echo "Built data/demo.sqlite3 from the TEST FIXTURE. The articles in it are invented."
	@echo "It is gitignored and must never be deployed as if it were measurement."

demo-snapshot: demo-db       ## Snapshot the synthetic database, labelled as demo data on every page
	CIB_DB_PATH=data/demo.sqlite3 $(VENV)/bin/cib export snapshot \
	  --warn "SYNTHETIC DEMO DATA. Every article, outlet and figure on this page was generated from the project's test fixture. Nothing here is real coverage and no number here may be quoted."
	@echo
	@echo "Snapshot now describes SYNTHETIC data. Run 'make snapshot' to restore the real one."

docs:                        ## Regenerate docs/METRICS.md from the metric definitions
	$(PY) scripts/render_metrics_doc.py

docs-check:                  ## Fail if docs/METRICS.md is stale
	$(PY) scripts/render_metrics_doc.py --check
