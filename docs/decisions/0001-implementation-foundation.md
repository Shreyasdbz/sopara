# ADR-020: Implementation foundation

**Status:** Approved

**Date:** 2026-09-10

## Context

Sopara is an approved modular monolith with a Python/FastAPI runtime, a build-only TanStack Start frontend, PostgreSQL control state, and GCP deployment. The repository initially contained documentation only.

## Decision

- Manage one Python project with uv `0.12.11` and a universal lockfile.
- Use Python `3.14.7`.
- Use SQLAlchemy Core `2.x` over psycopg `3.x`; do not map domain entities as ORM objects.
- Use Alembic for reviewed forward-only PostgreSQL migrations.
- Use a small Makefile as the stable local and CI task interface.
- Use Bun `1.4.2` only for frontend installation, checks, tests, and builds.
- Generate the frontend from preset `b3ZheXgQEs` with Base UI, Hugeicons, pointer cursors, and TanStack Start.
- Build TanStack Start in SPA mode and copy only static output into the Python runtime image.
- Pin Terraform `1.16.2`; Work Package 0 defines no provider or resource.

## Rejected alternatives

- Multiple Python workspace packages before an independent packaging need exists.
- Direct psycopg with a custom migration ledger.
- SQLAlchemy ORM domain entities or implicit lazy loading.
- TanStack server functions, SSR runtime, or a Node/Bun production process.
- Radix or mixed component primitive bases.
- GCP provisioning before the historical vertical slice passes.

## Consequences

The repository has one dependency and release unit. Import checks, explicit ports, and integration tests—not package-manager boundaries—enforce modularity. The final container may contain Python and static assets only. Major changes require a superseding ADR.
