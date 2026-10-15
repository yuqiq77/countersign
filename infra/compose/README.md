# Compose

`docker-compose.yml` runs the whole stack on one host, with every port
bound to 127.0.0.1:

| Service | Role | Host port |
|---|---|---|
| `postgres` | system of record, also holding Temporal's databases | 5435 |
| `temporal`, `temporal-ui` | workflow server and its UI | 7233, 8080 |
| `migrate` | one-shot `alembic upgrade head` | — |
| `api` | FastAPI | 8000 |
| `worker` | Temporal worker; mounts `config/operator/` read-only | — |
| `web` | Next.js UI | 3001 |

PostgreSQL starts on a health check, and the API and worker wait for the
migration to finish. The ports are shifted from the defaults on purpose
(see `AGENTS.md`). One host shows recovery from a worker crash, not high
availability. Running it is described in
[docs/development.md](../../docs/development.md).
