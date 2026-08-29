# Compose Infrastructure

Slice 1 will add the reproducible runtime profile:

```text
postgres
temporal
temporal-ui
api
worker
web
```

Telemetry is an optional profile. Raw source bodies stay in PostgreSQL initially, so MinIO is not part of the first stack. Services must use health/readiness checks; startup order is not a substitute for readiness.

The deployed single-host demo demonstrates process recovery, not host high availability.

