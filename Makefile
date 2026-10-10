VENV ?= .venv
PY := $(VENV)/bin/python

# Local Docker TimescaleDB fallback (§5.16). Same image major version as Tiger (PG 18).
DB_CONTAINER ?= mira-db
DB_IMAGE ?= timescale/timescaledb-ha:pg18
DB_LOCAL_PORT ?= 55432
DB_LOCAL_DSN ?= postgresql://postgres:mira@localhost:$(DB_LOCAL_PORT)/mira

.PHONY: run test db-local db-setup evals

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
