# Sopara

Sopara is a private, single-owner, non-commercial research system for deterministic historical replay and live NES simulation informed by ES market data.

It cannot connect to a broker or transmit a real order. Every implemented surface must remain inside the scope defined by:

- `docs/product.md`
- `docs/architecture.md`
- `docs/design.md`
- `docs/business.md`
- `docs/implementation-plan.md`

Start a resumed session with `docs/resume.md`; it records the authoritative reading order, completed checkpoint, verification procedure, unproven boundaries, and next approval gate.

## Implementation status

Work Packages 0–3 establish the pinned foundation, pure deterministic domain kernel, transactional evidence spine, and one synthetic historical vertical slice. Work Package 4 adds the private FastAPI control plane, signed-IAP production adapter, CSRF-protected durable command receipts, bounded read models, resumable outbox SSE, deterministic OpenAPI/TypeScript contracts, static TanStack delivery, and a script-free safe-halt path. It still has no external dataset, deployment, provisioned cloud resource, or real-order path.

## Required tools

- Python `3.14.7`, resolved through uv
- uv `0.12.11`
- Bun `1.4.2`
- Terraform `1.16.2`
- GNU Make
- Docker `29.x` for PostgreSQL integration and final-image verification

Tool versions are intentionally exact. Use isolated installations or a version manager; do not silently substitute a nearby version.

## Checks

```bash
make check
```

Individual checks are available through `make help`. Cloud infrastructure commands are validation-only in Work Package 0; there is no apply target.

`make check` includes a digest-pinned PostgreSQL 18.6 container and requires a running Docker daemon. The container is disposable and uses synthetic credentials. It does not contact GCP. The explicit final-image proof remains separate:

```bash
make container-check
```

No command in the current repository provisions infrastructure or submits Cloud Build. The GCS adapter requires an injected client-owned bucket object; tests use contract doubles and do not authenticate to Google Cloud.

## Synthetic historical proof

The complete register → experiment → interrupt → reconcile checkpoint → resume → reconcile result → report → `NO_TRADE` comparison journey is exercised by:

```bash
make test-integration
```

The temporary CLI is installed with the Python package:

```bash
sopara --help
```

It requires an explicit PostgreSQL URL and a synthetic filesystem-object root. It rejects non-synthetic evidence, cannot connect to a broker, and does not call GCP. The CLI remains a local diagnostic adapter; FastAPI is the browser control-plane transport and neither owns domain rules.

## Control-plane contracts

`contracts/openapi.json` is generated from the FastAPI application without opening a database or network connection. `web/src/api/schema.d.ts` is generated from that artifact. Both are deterministic and freshness-checked by `make check`.

The production composition root requires `SOPARA_DATABASE_URL`, `SOPARA_IAP_AUDIENCE`, `SOPARA_OWNER_EMAIL`, `SOPARA_PUBLIC_ORIGIN`, `SOPARA_CSRF_SECRET`, and `SOPARA_CURSOR_SECRET`. Secret values must come from the deployment secret mechanism; no development bypass is selected from environment variables. Starting a production-like server or provisioning GCP remains outside the completed local proof.

## Frontend provenance

The frontend was generated from the Chairman-approved command:

```bash
bunx --bun shadcn@latest init --preset b3ZheXgQEs --template start --pointer
```

The command was run with project name `web`. Generated output was reviewed and constrained to Base UI, TanStack Start SPA mode, and build-time-only Bun.
