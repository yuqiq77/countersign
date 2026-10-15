# Database Migrations

Alembic migrations for the PostgreSQL system of record. `0001_initial`
creates the full schema. Compose applies `alembic upgrade head` as the
one-shot `migrate` service, and the API and worker start only after it
succeeds. `tests/integration/test_migrations.py` upgrades a fresh database.
