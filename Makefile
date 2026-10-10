VENV ?= .venv
PY := $(VENV)/bin/python

# Local Docker TimescaleDB fallback (§5.16). Same image major version as Tiger (PG 18).
DB_CONTAINER ?= mira-db
DB_IMAGE ?= timescale/timescaledb-ha:pg18
DB_LOCAL_PORT ?= 55432
DB_LOCAL_DSN ?= postgresql://postgres:mira@localhost:$(DB_LOCAL_PORT)/mira

.PHONY: run test db-local db-setup evals demo-data

# Exactly one server process: the hub, Guardian timers and ring buffer live in memory (TRI.md §3.1).
run:
	$(VENV)/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1

test:
	$(PY) -m pytest

# Start (or reuse) the local container, then rebuild the `mira` database from our scripts (§5.11).
db-local:
	@docker start $(DB_CONTAINER) >/dev/null 2>&1 || \
		docker run -d --name $(DB_CONTAINER) -p $(DB_LOCAL_PORT):5432 -e POSTGRES_PASSWORD=mira $(DB_IMAGE)
	$(PY) -m server.db.setup --dsn "$(DB_LOCAL_DSN)" --fresh --wait

# Tiger Cloud (or any DSN): make db-setup DSN="postgres://tsdbadmin:...@host:port/tsdb?sslmode=require"
db-setup:
	@test -n "$(DSN)" || (echo 'usage: make db-setup DSN="postgres://..."'; exit 2)
	@$(PY) -m server.db.setup --dsn "$(DSN)"

# Care Copilot evals (not part of `make test`): MIRA_EVAL_DSN / MIRA_EVAL_RO_DSN, GEMINI_MODE=fake|live.
evals:
	$(PY) -m server.tests.copilot_evals $(EVAL_ARGS)

# Day of the demo (history, expected.json and the cached demo answer are relative to TODAY). One command:
# rebuild the local database with today's simulated history (rewrites server/sim/expected.json), then
# regenerate data/copilot_demo.json through the real tool loop (fake model, guarded SQL on copilot_ro). It refuses
# to write a cache that doesn't rank like expected.json. Needs COPILOT_RO_PASSWORD. Commit both files.
demo-data: db-local
	GEMINI_MODE=fake COPILOT_CACHED_DEMO=0 $(PY) -m server.agents.copilot --write-demo-cache --dsn "$(DB_LOCAL_DSN)"
