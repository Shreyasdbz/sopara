<!-- @format -->

# Sopara Resume Contract

**Checkpoint:** Work Package 4 complete

**Checkpoint date:** 2026-09-14

**Next package:** Work Package 5 — minimum complete UI slice

**Authorization state:** Work Package 5 is not authorized

**Stable checkpoint tag:** `wp4-control-plane`

This is the first document to read when work resumes. It reconstructs the current state without chat history, a particular worktree, or memory outside this repository. Detailed evidence remains in `docs/implementation-plan.md`; this file is the compact routing contract.

## 1. Non-negotiable scope

Sopara is a private, single-owner, non-commercial research and simulation system.

- ES is the information market.
- NES is the only simulated execution instrument.
- The implementation ceiling is live simulation.
- There is no broker adapter, broker credential, account funding, real-order route, or production-money state.
- Synthetic proxy evidence cannot be represented as observed NES evidence and cannot count toward qualification.
- GCP is the intended production boundary, but no GCP resource has been created or deployed from this repository.
- Licensed market data, paid services, Cloud Build submission, and GCP provisioning remain separately gated.
- Do not begin a work package until the Chairman explicitly authorizes it.

If a proposed change weakens any of these statements, stop and request a new Chairman decision before editing code.

## 2. Authoritative reading order

Read these files before changing scope or implementation:

1. `AGENTS.md` and any machine-level agent instructions;
2. `docs/resume.md`;
3. `docs/idea.md` for the original thesis only;
4. `docs/product.md` for functional authority and non-goals;
5. `docs/architecture.md` for system, data, security, and GCP boundaries;
6. `docs/design.md` for responsive, theme, keyboard, accessibility, and interface state machines;
7. `docs/business.md` for private-operation, cost, entitlement, and compliance gates;
8. `docs/implementation-plan.md` for package order, exit criteria, and observed execution evidence;
9. every ADR under `docs/decisions/` in numeric order.

The approved specifications override broader ambitions in `docs/idea.md`. Plans and mocks are not runtime evidence. The latest completed execution record is section 29 of `docs/implementation-plan.md`.

## 3. Implemented checkpoint

Work Packages 0 through 4 are complete.

### WP0 — foundation

- uv-managed Python 3.14 modular monolith;
- Bun build-only frontend toolchain;
- TanStack Start SPA generated from ShadCN preset `b3ZheXgQEs` with Base UI, Hugeicons, and pointer cursors;
- Terraform validation-only skeleton;
- Python/Uvicorn plus static-assets final container, with no JavaScript runtime.

### WP1 — deterministic domain kernel

- exact ES and NES instrument/tick identity;
- deterministic market, calendar, eligibility, execution, evidence, cost, qualification, and state-machine rules;
- simulation-only and evidence-class boundaries enforced in domain types.

### WP2 — transactional evidence spine

- PostgreSQL command inbox, append-only ledgers, transactional outbox, projections, leases, fencing, and evidence metadata;
- async SQLAlchemy Core over psycopg;
- content-addressed Parquet/GCS adapter contract and independent reconciliation;
- forward-only Alembic migrations through `0002_transactional_evidence`.

### WP3 — synthetic historical vertical slice

- dataset admission and quarantine;
- ordered deterministic replay with injected logical time;
- hash-chained durable checkpoints, interruption/resume, results, comparison, reports, and reconciliation;
- migration `0003_historical_replay`;
- temporary local `argparse` diagnostic adapter.

### WP4 — private FastAPI control plane

- one FastAPI/Uvicorn server authority;
- production signed-IAP validator and explicit injected test validator;
- exact owner allowlist, cached IAP keys, recent authentication, exact-origin and subject-bound CSRF;
- durable command receipts with idempotency and expected versions;
- bounded read models and opaque stable cursors;
- PostgreSQL-outbox SSE with `Last-Event-ID`, reconnect, duplicate, gap, expiry, and schema-change handling;
- RFC 9457 problem responses;
- static TanStack delivery and script-free `/safe/halt`;
- deterministic `contracts/openapi.json` and `web/src/api/schema.d.ts` freshness gates.

## 4. Approved technology and authority boundaries

| Concern | Approved choice | Must not become authority |
| --- | --- | --- |
| Server | FastAPI on Python/Uvicorn | TanStack server functions, Node, or Bun |
| Browser | TanStack Start SPA, TanStack libraries where sensible | Client validation or client command state |
| Components | ShadCN generated with Base UI and Hugeicons | Radix primitives or a mixed primitive base |
| Durable state | PostgreSQL/Cloud SQL | Process memory, browser state, Pub/Sub delivery |
| Progress | Transactional-outbox SSE; database polling is correctness | `LISTEN/NOTIFY` or transient notifications |
| Authentication | Direct Cloud Run IAP plus in-process signed-assertion verification | Unsigned identity headers or app passwords |
| Object evidence | Content-addressed GCS contract with generation preconditions | Mutable object names or local canonical evidence |
| Live execution | Bounded Cloud Run Jobs per approved window | Long-running worker or broker connectivity |
| Deployment | One private GCP project when separately authorized | Public ingress or multi-project expansion |

Major departures require research, at least two candidate lifecycle walkthroughs, red-team critique, a new or superseding ADR, and Chairman approval.

## 5. Last verified evidence

The publication checkpoint was reverified locally on 2026-09-14:

- `make check` returned zero;
- 170 foundation/unit, 4 property, 14 PostgreSQL integration, 20 contract, and 1 frontend test passed;
- Pyright reported zero errors;
- Import Linter kept the domain boundary across 84 Python files and 298 dependencies;
- deterministic OpenAPI and generated TypeScript freshness checks passed;
- Terraform validation, repository policy, 75 Python licenses, 611 web licenses, and SBOM generation passed;
- local runtime image: `sha256:51d39d83c3f027d4eb5e506f3d7dcd79ee9706f8585fe315211686b3ade9147d`, 186,480,438 bytes, running as `65532:65532`;
- the 2026-09-10 held local Uvicorn smoke returned `/healthz` 200 and protected routes 401 without IAP;
- no Sopara test container remained running.

This evidence is historical, not a promise about a future checkout. Re-run the gates after cloning or changing dependencies.

## 6. Exact resume procedure

From the repository root:

1. Run `/Users/shreyas/.agents/scripts/bearings.sh` when that machine-level tool is available. Otherwise run `pwd`, `git status --short`, `git log --oneline --decorate -10`, and `git remote -v`.
2. Confirm `main` is at or descended from the private remote's `wp4-control-plane` tag.
3. Confirm the worktree is clean before creating a branch or worktree.
4. Read the documents in section 2.
5. Install exactly the versions in `.python-version`, `.uv-version`, `.bun-version`, and `.terraform-version`.
6. Run `make toolchain`.
7. Run `make check`. This starts a digest-pinned disposable local PostgreSQL container and performs a local TanStack prerender; it does not call GCP.
8. Run `make container-check` when Docker is available. Record a new image identity rather than expecting the historical digest to remain stable after source or dependency changes.
9. Confirm no real credentials, `.env` files, state files, licensed datasets, or generated build directories are staged.
10. Present the Work Package 5 scope and exit criteria to the Chairman and wait for explicit authorization.

Do not run Terraform apply, submit Cloud Build, create GCP resources, purchase data, or deploy during resume verification.

## 7. Next authorized decision point

No further work is authorized. The next proposed package is Work Package 5, defined in section 11 of `docs/implementation-plan.md`.

Its minimum slice is one complete replay investigation across mobile, tablet, and desktop, including:

- startup/IAP failure;
- readiness;
- experiment creation;
- replay progress and result;
- experiment comparison;
- decision trace;
- system health and audit summary;
- light, system, and dark themes;
- keyboard-first interaction and visible focus;
- Base UI/ShadCN primitives and TanStack routing, query, form, table, and virtualization where appropriate;
- route, panel, command, and stream error boundaries;
- no JavaScript server authority.

Before implementation, revalidate current TanStack, Base UI, ShadCN, browser accessibility, and testing guidance from primary sources. Walk at least two viable interaction and data-flow paths through failure, maintenance, 10x load, and migration cost. Stop for the Chairman's decision if the candidate changes an approved contract.

## 8. Known unproven boundaries

- No live IAP, Cloud Run, Cloud SQL, GCS, Cloud Build, IAM, VPC, or GCP failure behavior has been exercised.
- No external or licensed ES/NES dataset has been admitted.
- No observed-NES evidence exists in the repository.
- No load, proxy-buffer, Cloud Run timeout, Cloud SQL failover, or browser network-partition benchmark has run.
- The UI beyond the generated foundation and safe-halt link is not implemented.
- WP6 and later ingestion, live simulation, qualification, observability, deployment, and cost gates remain unauthorized.
- There is intentionally no broker or real-order path.

## 9. Publication and recovery

- The authoritative shared branch is `origin/main` in a private repository.
- The immutable resume point is the annotated tag `wp4-control-plane`.
- No generated build output, virtual environment, local cache, Terraform state, environment file, or credential belongs in Git.
- Do not force-push `main` or move the checkpoint tag.
- If later work must be abandoned, create a new branch or worktree from `wp4-control-plane`; do not reset an active dirty checkout destructively.

The repository is the complete handoff. Chat transcripts and external memory may help retrieval, but they are not required to determine scope, authority, current implementation, verification limits, or the next decision.
