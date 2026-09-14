import hmac
import html
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from sopara.adapters.database.errors import IdempotencyConflictError
from sopara.api.auth import (
    AuthenticationError,
    AuthorizationError,
    GoogleIAPIdentityValidator,
    Identity,
    IdentityValidator,
)
from sopara.api.config import ControlPlaneConfig
from sopara.api.database import PostgresControlPlaneRepository
from sopara.api.models import (
    BuildManifest,
    Capability,
    CommandReceiptResponse,
    CommandRequest,
    CommandState,
    HealthResponse,
    Page,
    PageSize,
    ProblemDetail,
    ReadinessSnapshot,
    SessionBootstrap,
)
from sopara.api.ports import (
    ControlPlanePort,
    ControlPlaneUnavailableError,
    InvalidCommandError,
    ResourceNotFoundError,
    StaleResourceVersionError,
)
from sopara.api.security import (
    CsrfProtector,
    CursorCodec,
    InvalidSecurityTokenError,
    SignedTokenCodec,
    payload_sha256,
)
from sopara.api.sse import (
    EventStreamer,
    StreamCursorExpiredError,
    StreamGapError,
    StreamSchemaChangedError,
)

IAP_HEADER = "x-goog-iap-jwt-assertion"
CSRF_COOKIE = "__Host-sopara-csrf"
SYSTEM_ID = UUID("00000000-0000-0000-0000-000000000001")
SAFE_CSP = "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ProblemDetail, "content": {"application/problem+json": {}}}
    for status in (401, 403, 404, 409, 412, 422, 503)
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable
        self.current_version = current_version


def _problem(request: Request, error: ApiError) -> JSONResponse:
    correlation_id = cast("UUID", request.state.correlation_id)
    body = ProblemDetail(
        type=f"urn:sopara:problem:{error.code.lower()}",
        title=error.title,
        status=error.status,
        detail=error.detail,
        instance=request.url.path,
        code=error.code,
        correlation_id=correlation_id,
        retryable=error.retryable,
        current_version=error.current_version,
    )
    return JSONResponse(
        status_code=error.status,
        content=body.model_dump(mode="json", by_alias=True),
        media_type="application/problem+json",
        headers={"X-Correlation-ID": str(correlation_id), "Cache-Control": "no-store"},
    )


def _correlation_id(raw: str | None) -> UUID:
    if raw is not None:
        try:
            return UUID(raw)
        except ValueError:
            pass
    return uuid4()


def create_app(
    config: ControlPlaneConfig,
    *,
    identity_validator: IdentityValidator,
    repository: ControlPlanePort,
) -> FastAPI:
    app = FastAPI(
        title="Sopara private control plane",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    csrf = CsrfProtector(SignedTokenCodec(config.csrf_secret, "csrf"), config.csrf_max_age_seconds)
    cursors = CursorCodec(SignedTokenCodec(config.cursor_secret, "page-cursor"))
    streamer = EventStreamer(
        repository,
        config.api_schema_version,
        config.sse_poll_seconds,
        config.sse_heartbeat_seconds,
        config.sse_connection_seconds,
    )

    @app.middleware("http")
    async def response_contract(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith(("/api/", "/safe/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return _problem(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            request,
            ApiError(
                422,
                "REQUEST_INVALID",
                "Request rejected",
                "The request did not match the published API contract.",
            ),
        )

    async def current_identity(request: Request) -> Identity:
        assertion = request.headers.get(IAP_HEADER)
        if assertion is None:
            raise ApiError(
                401,
                "IAP_ASSERTION_REQUIRED",
                "Authentication required",
                "A signed IAP assertion is required.",
            )
        try:
            return await run_in_threadpool(identity_validator.validate, assertion)
        except AuthenticationError as error:
            raise ApiError(
                401,
                "IAP_ASSERTION_INVALID",
                "Authentication failed",
                "The signed IAP assertion could not be validated.",
            ) from error
        except AuthorizationError as error:
            raise ApiError(
                403,
                "OWNER_REQUIRED",
                "Access denied",
                "The authenticated identity is not authorized for this private system.",
            ) from error

    def require_recent(identity: Identity) -> None:
        age = (datetime.now(UTC) - identity.issued_at.astimezone(UTC)).total_seconds()
        if age < -30 or age > config.recent_auth_seconds:
            raise ApiError(
                401,
                "RECENT_AUTH_REQUIRED",
                "Recent authentication required",
                "Reauthenticate through IAP before issuing a command.",
            )

    def verify_csrf(request: Request, identity: Identity, supplied: str | None) -> None:
        cookie = request.cookies.get(CSRF_COOKIE)
        if supplied is None or cookie is None or not hmac.compare_digest(supplied, cookie):
            raise ApiError(
                403, "CSRF_INVALID", "Request origin rejected", "A matching CSRF token is required."
            )
        if request.headers.get("Origin") != config.public_origin:
            raise ApiError(
                403,
                "ORIGIN_INVALID",
                "Request origin rejected",
                "The request origin is not allowed.",
            )
        try:
            csrf.verify(supplied, subject=identity.subject, now=datetime.now(UTC))
        except InvalidSecurityTokenError as error:
            raise ApiError(
                403,
                "CSRF_INVALID",
                "Request origin rejected",
                "The CSRF token is invalid or expired.",
            ) from error

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/build", response_model=BuildManifest, responses=PROBLEM_RESPONSES)
    async def build(
        _identity: Annotated[Identity, Depends(current_identity)],
    ) -> BuildManifest:
        return BuildManifest(
            revision=config.build_revision,
            image_digest=config.build_image_digest,
            api_schema_version=config.api_schema_version,
        )

    @app.get("/api/v1/session", response_model=SessionBootstrap, responses=PROBLEM_RESPONSES)
    async def session(
        identity: Annotated[Identity, Depends(current_identity)], response: Response
    ) -> SessionBootstrap:
        token = csrf.issue(subject=identity.subject, now=datetime.now(UTC))
        response.set_cookie(
            CSRF_COOKIE,
            token,
            secure=config.secure_cookies,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return SessionBootstrap(
            subject=identity.subject,
            csrf_token=token,
            authenticated_at=identity.issued_at,
        )

    @app.get("/api/v1/readiness", response_model=ReadinessSnapshot, responses=PROBLEM_RESPONSES)
    async def readiness(
        _identity: Annotated[Identity, Depends(current_identity)],
    ) -> ReadinessSnapshot:
        try:
            row = await repository.readiness()
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The durable control-plane store is unavailable.",
                retryable=True,
            ) from error
        return ReadinessSnapshot(
            schema_version=config.api_schema_version,
            revision=row.revision,
            stream_cursor=str(row.stream_sequence),
            generated_at=row.generated_at,
            last_domain_event_at=row.last_domain_event_at,
            last_ingest_at=row.last_ingest_at,
            operability="RESEARCH_READY" if row.source_health == "READY" else "NOT_READY",
            evidence_class=row.evidence_class,
            source_health=row.source_health,
            window_state=row.window_state,
            incident_count=row.incident_count,
            capabilities=[
                Capability(
                    name="historicalReplay",
                    allowed=row.source_health == "READY",
                    reason_code=(
                        "DATASET_READY" if row.source_health == "READY" else "DATASET_REQUIRED"
                    ),
                ),
                Capability(
                    name="liveSimulation",
                    allowed=False,
                    reason_code="WORK_PACKAGE_NOT_AUTHORIZED",
                ),
                Capability(
                    name="productionTrading",
                    allowed=False,
                    reason_code="NON_COMMERCIAL_PRIVATE_SYSTEM",
                ),
            ],
        )

    async def page_response(collection: str, cursor: str | None, page_size: int) -> Page:
        after = None
        if cursor is not None:
            try:
                after = cursors.decode(cursor, collection=collection)
            except InvalidSecurityTokenError as error:
                raise ApiError(
                    422,
                    "CURSOR_INVALID",
                    "Cursor rejected",
                    "The pagination cursor is invalid for this collection.",
                ) from error
        try:
            result = await repository.page(collection, after=after, limit=page_size)
        except (ValueError, InvalidSecurityTokenError) as error:
            raise ApiError(
                422, "CURSOR_INVALID", "Cursor rejected", "The pagination cursor is malformed."
            ) from error
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The durable control-plane store is unavailable.",
                retryable=True,
            ) from error
        next_cursor = (
            cursors.encode(collection=collection, value=result.next_value)
            if result.next_value
            else None
        )
        return Page(
            items=[dict(item) for item in result.items],
            next_cursor=next_cursor,
            snapshot_revision=result.snapshot_revision,
            schema_version=config.api_schema_version,
        )

    def add_collection_route(collection: str) -> None:
        async def collection_page(
            _identity: Annotated[Identity, Depends(current_identity)],
            cursor: Annotated[str | None, Query()] = None,
            page_size: Annotated[PageSize, Query()] = 50,
        ) -> Page:
            return await page_response(collection, cursor, page_size)

        collection_page.__name__ = f"list_{collection}"
        app.add_api_route(
            f"/api/v1/{collection}",
            collection_page,
            methods=["GET"],
            response_model=Page,
            responses=PROBLEM_RESPONSES,
            tags=[collection],
        )

    for collection_name in (
        "datasets",
        "entitlements",
        "experiments",
        "replays",
        "sessions",
        "decisions",
        "incidents",
        "reconciliations",
        "reports",
    ):
        add_collection_route(collection_name)

    @app.get(
        "/api/v1/{collection}/{resource_id}",
        response_model=dict[str, object],
        responses=PROBLEM_RESPONSES,
    )
    async def resource_detail(
        collection: str,
        resource_id: UUID,
        _identity: Annotated[Identity, Depends(current_identity)],
    ) -> dict[str, object]:
        if collection not in {
            "experiments",
            "replays",
            "sessions",
            "decisions",
            "incidents",
            "reconciliations",
            "reports",
            "datasets",
        }:
            raise ApiError(
                404,
                "RESOURCE_NOT_FOUND",
                "Resource not found",
                "The requested resource does not exist.",
            )
        try:
            return dict(await repository.detail(collection, resource_id))
        except ResourceNotFoundError as error:
            raise ApiError(
                404,
                "RESOURCE_NOT_FOUND",
                "Resource not found",
                "The requested resource does not exist.",
            ) from error
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The durable control-plane store is unavailable.",
                retryable=True,
            ) from error

    @app.post(
        "/api/v1/commands",
        status_code=202,
        response_model=CommandReceiptResponse,
        responses=PROBLEM_RESPONSES,
    )
    async def submit_command(
        request: Request,
        command: CommandRequest,
        identity: Annotated[Identity, Depends(current_identity)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
        ],
        expected_version: Annotated[int, Header(alias="If-Match-Version", ge=0)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> CommandReceiptResponse:
        require_recent(identity)
        verify_csrf(request, identity, csrf_token)
        digest = payload_sha256(command.model_dump(mode="json", by_alias=True))
        try:
            receipt = await repository.submit_command(
                command_id=uuid4(),
                idempotency_key=idempotency_key,
                payload_hash=digest,
                command_type=command.command_type,
                target_type=command.target_type,
                target_id=command.target_id,
                expected_version=expected_version,
                actor_email_hash=identity.email_hash,
            )
        except IdempotencyConflictError as error:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "Command conflict",
                "The idempotency key was already used for a different command payload.",
            ) from error
        except StaleResourceVersionError as error:
            raise ApiError(
                412,
                "RESOURCE_VERSION_STALE",
                "Resource version changed",
                "Refresh the resource before retrying this command.",
                current_version=error.current_version,
            ) from error
        except (InvalidCommandError, ResourceNotFoundError) as error:
            raise ApiError(422, "COMMAND_INVALID", "Command rejected", str(error)) from error
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The command was not durably accepted.",
                retryable=True,
            ) from error
        return CommandReceiptResponse(
            command_id=receipt.command_id,
            state=cast(CommandState, receipt.state),
            duplicate=receipt.duplicate,
            correlation_id=request.state.correlation_id,
        )

    @app.get(
        "/api/v1/events",
        response_class=StreamingResponse,
        responses={**PROBLEM_RESPONSES, 200: {"content": {"text/event-stream": {}}}},
    )
    async def events(
        _identity: Annotated[Identity, Depends(current_identity)],
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        try:
            after = int(last_event_id) if last_event_id else 0
            if after < 0:
                raise ValueError
        except ValueError as error:
            raise ApiError(
                422,
                "EVENT_CURSOR_INVALID",
                "Event cursor rejected",
                "Last-Event-ID must be a non-negative integer.",
            ) from error
        try:
            initial = await streamer.preflight(after)
        except StreamCursorExpiredError as error:
            raise ApiError(
                409,
                "EVENT_CURSOR_EXPIRED",
                "Resnapshot required",
                "The requested event cursor is no longer retained.",
            ) from error
        except StreamGapError as error:
            raise ApiError(
                409,
                "EVENT_GAP",
                "Resnapshot required",
                "A gap was detected in the durable event sequence.",
            ) from error
        except StreamSchemaChangedError as error:
            raise ApiError(
                409,
                "EVENT_SCHEMA_CHANGED",
                "Resnapshot required",
                "The event schema changed; fetch a new readiness snapshot.",
            ) from error
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The durable event source is unavailable.",
                retryable=True,
            ) from error
        return StreamingResponse(
            streamer.stream(after=after, initial=initial),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    def safe_page(*, token: str, state: str, receipt: str | None = None) -> str:
        receipt_markup = f"<p role=status>{html.escape(receipt)}</p>" if receipt else ""
        return (
            "<!doctype html><html lang=en><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            "<title>Sopara safe halt</title><main><h1>Safe halt</h1>"
            f"<p>Current state: <strong>{html.escape(state)}</strong></p>{receipt_markup}"
            '<form method=post action="/safe/halt">'
            f'<input type=hidden name=csrf_token value="{html.escape(token)}">'
            f'<input type=hidden name=idempotency_key value="safe-halt-{uuid4()}">'
            '<input type=hidden name=expected_version value="0">'
            "<label for=reason>Reason (optional)</label><input id=reason name=reason maxlength=200>"
            "<button type=submit>Confirm halt</button></form>"
            '<p><a href="/_shell.html">Return to application</a></p></main></html>'
        )

    @app.get("/safe/halt", response_class=HTMLResponse, responses=PROBLEM_RESPONSES)
    async def safe_halt(
        identity: Annotated[Identity, Depends(current_identity)], response: Response
    ) -> HTMLResponse:
        token = csrf.issue(subject=identity.subject, now=datetime.now(UTC))
        content = safe_page(token=token, state="UNKNOWN — refresh is unavailable in degraded mode")
        result = HTMLResponse(
            content,
            headers={"Content-Security-Policy": SAFE_CSP},
        )
        result.set_cookie(
            CSRF_COOKIE,
            token,
            secure=config.secure_cookies,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return result

    @app.post("/safe/halt", response_class=HTMLResponse, responses=PROBLEM_RESPONSES)
    async def safe_halt_submit(
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> HTMLResponse:
        require_recent(identity)
        if (
            request.headers.get("content-type", "").split(";", maxsplit=1)[0]
            != "application/x-www-form-urlencoded"
        ):
            raise ApiError(
                422,
                "REQUEST_INVALID",
                "Request rejected",
                "The safe halt form encoding is invalid.",
            )
        raw = await request.body()
        if len(raw) > 8_192:
            raise ApiError(
                422, "REQUEST_INVALID", "Request rejected", "The safe halt request is too large."
            )
        values = parse_qs(raw.decode("utf-8", errors="strict"), strict_parsing=True)
        supplied = values.get("csrf_token", [None])[0]
        idempotency_key = values.get("idempotency_key", [None])[0]
        expected_raw = values.get("expected_version", [None])[0]
        reason = values.get("reason", [""])[0]
        if (
            not idempotency_key
            or len(idempotency_key) > 128
            or expected_raw != "0"
            or len(reason) > 200
        ):
            raise ApiError(
                422, "REQUEST_INVALID", "Request rejected", "The safe halt request is invalid."
            )
        verify_csrf(request, identity, supplied)
        digest = payload_sha256({"commandType": "HALT_SIMULATION", "reason": reason})
        try:
            receipt = await repository.submit_command(
                command_id=uuid4(),
                idempotency_key=idempotency_key,
                payload_hash=digest,
                command_type="HALT_SIMULATION",
                target_type="SYSTEM",
                target_id=SYSTEM_ID,
                expected_version=0,
                actor_email_hash=identity.email_hash,
            )
        except IdempotencyConflictError as error:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "Command conflict",
                "The safe halt idempotency key conflicts with another payload.",
            ) from error
        except ControlPlaneUnavailableError as error:
            raise ApiError(
                503,
                "DATABASE_UNAVAILABLE",
                "Service unavailable",
                "The halt was not durably accepted.",
                retryable=True,
            ) from error
        token = csrf.issue(subject=identity.subject, now=datetime.now(UTC))
        result = HTMLResponse(
            safe_page(
                token=token,
                state="HALT REQUEST RECEIVED",
                receipt=(
                    f"Durable command receipt: {receipt.command_id}. Completion is not implied."
                ),
            ),
            status_code=202,
            headers={"Content-Security-Policy": SAFE_CSP},
        )
        result.set_cookie(
            CSRF_COOKIE,
            token,
            secure=config.secure_cookies,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return result

    static_root = config.static_root
    assets = static_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    def shell_response() -> Response:
        shell = static_root / "_shell.html"
        if not shell.is_file():
            return HTMLResponse(
                "<!doctype html><title>Sopara unavailable</title>"
                "<p>Application shell is unavailable. "
                "<a href=/safe/halt>Safe halt</a></p>",
                status_code=503,
            )
        return FileResponse(shell, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/_shell.html", include_in_schema=False)
    async def shell() -> Response:
        return shell_response()

    @app.get("/{browser_path:path}", include_in_schema=False)
    async def browser_fallback(browser_path: str) -> Response:
        if browser_path.startswith(("api/", "safe/", "assets/")) or "." in Path(browser_path).name:
            return Response(status_code=404)
        return shell_response()

    return app


def production_app(config: ControlPlaneConfig) -> FastAPI:
    from sopara.adapters.database.engine import WEB_POOL, Database, create_database_engine

    database = Database(create_database_engine(config.database_url, WEB_POOL))
    return create_app(
        config,
        identity_validator=GoogleIAPIdentityValidator(
            audience=config.iap_audience, owner_email=config.owner_email
        ),
        repository=PostgresControlPlaneRepository(database),
    )
