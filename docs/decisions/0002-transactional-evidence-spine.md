# ADR-021: Transactional evidence spine

**Status:** Approved

**Date:** 2026-09-10

## Context

Sopara needs durable commands, aggregate state, immutable event history, execution fencing, and content-addressed evidence. PostgreSQL and Cloud Storage do not share an atomic commit protocol. Work Package 2 must prove the persistence rules without creating paid GCP resources.

## Decision

- Use PostgreSQL 18.6 in digest-pinned disposable Docker containers for canonical integration tests; do not use SQLite as a correctness substitute.
- Use async SQLAlchemy Core over psycopg 3 with one bounded pool per deployable composition root.
- Serialize aggregate writes with PostgreSQL transaction-level advisory locks, then enforce expected versions and unique aggregate sequences.
- Persist domain event, projection, and outbox effects in one database transaction.
- Fence live writes with a lease row, monotonically increasing epoch, expiry check, and a shared row lock held through the write transaction.
- Keep domain and audit ledgers append-only with database triggers and privilege denial.
- Store evidence as Zstandard-compressed Parquet at content-addressed object keys. Create objects and manifests with generation-match zero.
- Record object generation, metageneration, size, CRC32C, SHA-256, schema, source range, and evidence class in PostgreSQL.
- Accept evidence only after independent reconciliation detects missing, changed, and unreferenced objects.
- Restrict memory and filesystem object stores to synthetic proxy evidence. The production GCS adapter requires an injected bucket and is not exercised against GCP in this package.

## Rejected alternatives

- SQLite, mocked PostgreSQL transactions, or an embedded database for canonical concurrency claims.
- Long-lived developer PostgreSQL instances with uncontrolled versions or residual state.
- Treating PostgreSQL and object storage as a distributed transaction.
- Mutable object keys, last-write-wins uploads, or checksum-free collision handling.
- Unbounded connection pools or process-local mutexes as distributed fencing.

## Consequences

Every integration run pays a small local container startup cost and leaves no database state. Object/SQL disagreement remains possible during failure, but it is explicit, enumerable, and never accepted as reconciled evidence. No cloud spend or credential is needed until a later authorized package provisions development infrastructure.
