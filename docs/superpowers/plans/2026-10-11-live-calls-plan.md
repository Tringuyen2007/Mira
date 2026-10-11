# Live emergency calls by default — implementation plan

Spec: `docs/superpowers/specs/2026-10-11-live-calls-design.md`. Branch `calls`. Each task: failing test → code →
green, then commit. Full suite once at the end (deselect the known `test_scheduler` lag test).

1. **`/health` reports call mode.** Test in `server/tests/test_health.py` (new): default client → `calls ==
   "log_only"`; `make_client(CALLS_DISABLED="0", RETELL_* set)` → `"live"`. Code: `server/api/health.py` reads
   `get_state().notifier.mode` (absent → `"log_only"`).
2. **`RetellNotifier.test_call`.** Tests in `server/tests/test_retell_client.py`: live mode with `FakeCalls`
   creates one call to the number with `role == "test"` metadata and a test opening in the variables; a second call
   in the same hour is deduped by the ledger (`created` stays 1); log-only mode returns a `sim_test_` id and creates
   nothing; a create failure returns `None`. Code in `server/notify/retell_client.py`.
3. **`/me/test-alert` dials.** Test in `server/tests/test_care_api.py`: `make_client(CALLS_DISABLED="0",
   ALLOW_TEST_CALLS="1", RETELL_*)`, swap `notifier._client_obj` for `FakeCalls`, pair with a one-shot code minted by
   `POST /patients/maya/pairing-code` (not the demo code: `locked_demo` must not dial), assert `called: true` and one
   created call; the demo-code phone still gets `called: false`. Code: `server/api/care.py` handles `None` from
   `test_call`.
4. **Remote fall → call, end to end.** `server/tests/test_remote_fall_calls.py` (three tests from the spec §3).
   Expect no production change; if the Guardian or bridge drops the event, fix that.
5. **Contract doc.** `docs/vision-media-contract.md` "Falls and safety events" section (spec §2).
6. **Config and docs flip.** `.env`, `.env.example`, `README.md` (section rename + flags table + operator steps).
7. **Deploy guard.** `tools/deploy_vultr.sh`: after the health loop, parse `calls` from `/health`; exit 1 unless
   `live` or `ALLOW_LOG_ONLY=1`.
8. Full test suite, then PR.
