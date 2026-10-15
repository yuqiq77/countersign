# Integration Tests

Tests that cross a real boundary: the API against a database with Temporal
stubbed at the client, Alembic upgrades, PostgreSQL-only behavior (when
`COUNTERSIGN_TEST_POSTGRES_URL` names a disposable database) and a real
browser render (when `COUNTERSIGN_CHROMIUM_PATH` is set). Sources and model
providers stay fixtures.
