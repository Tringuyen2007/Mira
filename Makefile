VENV ?= .venv
PY := $(VENV)/bin/python

.PHONY: run test db-local

# Exactly one server process: the hub, Guardian timers and ring buffer live in memory (TRI.md §3.1).
run:
	$(VENV)/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1

test:
	$(PY) -m pytest

# Local Docker TimescaleDB fallback (filled in by the database feature).
db-local:
	@echo "db-local: not implemented yet (database feature F1)"; exit 1
