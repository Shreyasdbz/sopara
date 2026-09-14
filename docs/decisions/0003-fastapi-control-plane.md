# ADR-022: Private FastAPI control plane

**Status:** Approved

**Date:** 2026-09-10

## Context

Sopara needs one browser-facing control plane for read models, durable commands, resumable progress, and an emergency halt path. It remains a private, single-owner, non-commercial system. FastAPI must be the only server runtime, PostgreSQL must remain the authority for commands and domain events, and the production topology must fit direct IAP protection on Cloud Run without adding a JavaScript server or paid messaging service.

## Decision

- Run one FastAPI process behind direct Cloud Run IAP. Validate the signed `x-goog-iap-jwt-assertion`, including signature, audience, expiry, issuer, subject, and exact owner email; never trust unsigned identity headers.
- Cache rotating IAP public keys through a process-local HTTP cache and serialize access to the shared verifier session. A bad or unavailable assertion fails closed.
- Leave only `/healthz` unauthenticated at the application layer. IAP protects the entire production ingress; API and `/safe/**` routes additionally enforce the assertion in process.
- Protect mutations with exact-origin validation and a short-lived, subject-bound, HMAC-signed double-submit CSRF token in a `Secure`, `HttpOnly`, `SameSite=Strict`, `__Host-` cookie.
- Require recent authentication, `Idempotency-Key`, and `If-Match-Version` for commands. Return `202` only after the command inbox transaction commits; never imply command completion.
- Keep command compatibility and expected-version checks in the PostgreSQL transaction that inserts the command. Return RFC 9457 problem details for `401`, `403`, `404`, `409`, `412`, `422`, and `503` without echoing payloads or internal exceptions.
- Read resumable SSE events from monotonically sequenced transactional outbox rows. PostgreSQL polling is the correctness path; notifications may later be a wake-up optimization only. Duplicate IDs are ignored, while gaps, expired cursors, and schema changes require a fresh snapshot.
- Generate and check in deterministic OpenAPI 3.1 JSON and `openapi-typescript` declarations. Stale artifacts fail the normal repository gate.
- Serve the prerendered TanStack Start SPA as static files from FastAPI. Keep `/safe/halt` as server-rendered HTML with no script dependency and a restrictive content-security policy.

## Rejected alternatives

- Trusting `x-goog-authenticated-user-*` headers, accepting any project member, or making a test-token environment switch available in production composition.
- Storing CSRF sessions in PostgreSQL for this one-owner deployment; it adds a database dependency to token issuance and a new cleanup/revocation lifecycle without improving the IAP reauthentication boundary materially.
- Using TanStack server functions, a Node/Bun runtime, API Gateway, or a separate backend-for-frontend. Each creates a second server authority or redundant routing/authentication layer.
- Using PostgreSQL `LISTEN/NOTIFY` as delivery authority. Notifications are not a durable replay log and can be lost across disconnects.
- Adding Pub/Sub for browser progress in the initial deployment. It adds cost, IAM, delivery semantics, and another retention model while PostgreSQL already commits the authoritative outbox.
- Hand-maintaining TypeScript request and response interfaces.

## Consequences

The control plane has one deployable runtime and one durable ordering source. At this system's one-owner load, polling is intentionally boring and bounded. At 10x concurrent browser sessions, database polls become the first pressure point; add a notification wake-up or fan-out tier only while preserving outbox sequence as replay authority. Process-local IAP key caches are cold after instance start, so the first authenticated request still depends on Google's key endpoint. No live IAP, Cloud Run, Cloud SQL, or GCP network behavior is proven until a separately authorized deployment package runs.
