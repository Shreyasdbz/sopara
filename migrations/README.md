# Database migrations

Alembic owns reviewed, forward-only PostgreSQL revisions. `0001_pre_wp2` is the preserved prior-schema fixture; `0002_transactional_evidence` creates the Work Package 2 schema, append-only ledgers, fencing state, role policy, and evidence metadata.

Run migrations only against PostgreSQL:

```bash
SOPARA_DATABASE_URL=postgresql+psycopg://... uv run alembic upgrade head
```

Downgrades intentionally fail. Repair an applied environment with a new forward revision. `make test-integration` starts a digest-pinned disposable PostgreSQL 18.6 container, proves blank and prior-schema upgrades converge on one head, and removes the container afterward.
