# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sopara.api.app import create_app
from sopara.api.auth import AuthorizationError, Identity, StaticIdentityValidator
from sopara.api.config import ControlPlaneConfig
from sopara.api.ports import StreamEvent
from tests.contract.api_helpers import FakeControlPlaneRepository

ASSERTION = "owner-test-assertion"
OWNER = Identity(
    "accounts.google.com:owner",
    "owner-email-hash",
    datetime.now(UTC) - timedelta(minutes=1),
)
TARGET_ID = "00000000-0000-0000-0000-000000000020"


class RejectingOwnerValidator:
    def validate(self, assertion: str) -> Identity:
        raise AuthorizationError("not owner")


@pytest.fixture
def repository() -> FakeControlPlaneRepository:
    return FakeControlPlaneRepository()


@pytest.fixture
def config(tmp_path: Path) -> ControlPlaneConfig:
    static = tmp_path / "static"
    static.mkdir()
    (static / "_shell.html").write_text(
        "<!doctype html><title>Sopara</title><a href=/safe/halt>Safe halt</a>",
        encoding="utf-8",
    )
    return ControlPlaneConfig(
        database_url="postgresql+psycopg://test.invalid/sopara",
        iap_audience="test-audience",
        owner_email="owner@example.invalid",
        public_origin="https://sopara.test",
        csrf_secret=b"contract-csrf-secret-that-is-32-bytes",
        cursor_secret=b"contract-cursor-secret-that-is-32-bytes",
        static_root=static,
        secure_cookies=False,
        sse_poll_seconds=0.001,
        sse_heartbeat_seconds=0.001,
        sse_connection_seconds=0.003,
    )


@pytest.fixture
def client(config: ControlPlaneConfig, repository: FakeControlPlaneRepository) -> TestClient:
    app = create_app(
        config,
        identity_validator=StaticIdentityValidator(assertion=ASSERTION, identity=OWNER),
        repository=repository,
    )
    return TestClient(app, base_url="https://sopara.test")


def auth() -> dict[str, str]:
    return {"X-Goog-IAP-JWT-Assertion": ASSERTION}


def mutation_headers(client: TestClient) -> dict[str, str]:
    bootstrap = client.get("/api/v1/session", headers=auth()).json()
    return {
        **auth(),
        "Origin": "https://sopara.test",
        "X-CSRF-Token": bootstrap["csrfToken"],
        "Idempotency-Key": "test-command-0001",
        "If-Match-Version": "1",
    }


def command() -> dict[str, object]:
    return {
        "commandType": "START_REPLAY",
        "targetType": "EXPERIMENT",
        "targetId": TARGET_ID,
        "payload": {},
    }


def test_health_is_the_only_unauthenticated_contract(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/api/v1/build")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "IAP_ASSERTION_REQUIRED"


def test_non_owner_is_forbidden(
    config: ControlPlaneConfig, repository: FakeControlPlaneRepository
) -> None:
    client = TestClient(
        create_app(config, identity_validator=RejectingOwnerValidator(), repository=repository),
        base_url="https://sopara.test",
    )
    response = client.get("/api/v1/build", headers=auth())
    assert response.status_code == 403
    assert response.json()["code"] == "OWNER_REQUIRED"


def test_build_bootstrap_and_readiness_contracts(client: TestClient) -> None:
    build = client.get("/api/v1/build", headers=auth())
    assert build.status_code == 200
    assert build.json()["apiSchemaVersion"] == 1
    session = client.get("/api/v1/session", headers=auth())
    assert session.status_code == 200
    assert session.json()["csrfToken"]
    assert "HttpOnly" in session.headers["set-cookie"]
    readiness = client.get("/api/v1/readiness", headers=auth())
    assert readiness.status_code == 200
    assert readiness.json()["capabilities"][2] == {
        "name": "productionTrading",
        "allowed": False,
        "reasonCode": "NON_COMMERCIAL_PRIVATE_SYSTEM",
    }


def test_pages_use_scoped_opaque_cursors(
    client: TestClient, repository: FakeControlPlaneRepository
) -> None:
    repository.page_items = ({"manifest_id": UUID(TARGET_ID)},)
    repository.next_value = TARGET_ID
    page = client.get("/api/v1/datasets?page_size=1", headers=auth())
    assert page.status_code == 200
    cursor = page.json()["nextCursor"]
    assert TARGET_ID not in cursor
    assert client.get(f"/api/v1/datasets?cursor={cursor}", headers=auth()).status_code == 200
    wrong_scope = client.get(f"/api/v1/reports?cursor={cursor}", headers=auth())
    assert wrong_scope.status_code == 422
    assert wrong_scope.json()["code"] == "CURSOR_INVALID"


def test_missing_detail_is_404_problem(client: TestClient) -> None:
    response = client.get(f"/api/v1/replays/{TARGET_ID}", headers=auth())
    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"


def test_command_receipt_is_202_and_never_implies_completion(
    client: TestClient, repository: FakeControlPlaneRepository
) -> None:
    response = client.post("/api/v1/commands", headers=mutation_headers(client), json=command())
    assert response.status_code == 202
    assert response.json()["state"] == "RECEIVED"
    assert response.json()["completionImplied"] is False
    assert len(repository.command_calls) == 1


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("conflict", 409, "IDEMPOTENCY_CONFLICT"),
        ("stale", 412, "RESOURCE_VERSION_STALE"),
        ("invalid", 422, "COMMAND_INVALID"),
        ("unavailable", 503, "DATABASE_UNAVAILABLE"),
    ],
)
def test_command_error_classes(
    client: TestClient,
    repository: FakeControlPlaneRepository,
    failure: str,
    status: int,
    code: str,
) -> None:
    headers = mutation_headers(client)
    repository.failure = failure
    response = client.post("/api/v1/commands", headers=headers, json=command())
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["content-type"].startswith("application/problem+json")
    if status == 412:
        assert response.json()["currentVersion"] == 9


def test_command_requires_csrf_and_exact_origin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/commands",
        headers={**auth(), "Idempotency-Key": "test-command-0001", "If-Match-Version": "1"},
        json=command(),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"
    headers = mutation_headers(client)
    headers["Origin"] = "https://attacker.invalid"
    assert (
        client.post("/api/v1/commands", headers=headers, json=command()).json()["code"]
        == "ORIGIN_INVALID"
    )


def test_validation_error_does_not_echo_payload(client: TestClient) -> None:
    response = client.post(
        "/api/v1/commands",
        headers=mutation_headers(client),
        json={**command(), "payload": {"secret": "do-not-echo"}, "extra": "invalid"},
    )
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    assert response.json()["code"] == "REQUEST_INVALID"


def test_readiness_unavailable_is_503(
    client: TestClient, repository: FakeControlPlaneRepository
) -> None:
    repository.failure = "unavailable"
    response = client.get("/api/v1/readiness", headers=auth())
    assert response.status_code == 503
    assert response.json()["retryable"] is True


def test_sse_resume_and_resnapshot_contracts(
    client: TestClient, repository: FakeControlPlaneRepository
) -> None:
    repository.minimum_sequence = 1
    repository.maximum_sequence = 2
    repository.events = (
        StreamEvent(1, "replay.changed", {"state": "RUNNING"}, datetime.now(UTC)),
        StreamEvent(2, "replay.changed", {"state": "COMPLETED"}, datetime.now(UTC)),
    )
    response = client.get("/api/v1/events", headers={**auth(), "Last-Event-ID": "1"})
    assert response.status_code == 200
    assert "id: 2" in response.text
    assert "id: 1" not in response.text
    assert "event: reconnect" in response.text

    repository.minimum_sequence = 5
    repository.maximum_sequence = 5
    repository.events = (StreamEvent(5, "replay.changed", {}, datetime.now(UTC)),)
    expired = client.get("/api/v1/events", headers={**auth(), "Last-Event-ID": "1"})
    assert expired.status_code == 409
    assert expired.json()["code"] == "EVENT_CURSOR_EXPIRED"


@pytest.mark.parametrize(
    ("events", "code"),
    [
        ((StreamEvent(4, "replay.changed", {}, datetime.now(UTC)),), "EVENT_GAP"),
        (
            (StreamEvent(3, "replay.changed", {}, datetime.now(UTC), schema_version=2),),
            "EVENT_SCHEMA_CHANGED",
        ),
    ],
)
def test_sse_gap_and_schema_change_require_resnapshot(
    client: TestClient,
    repository: FakeControlPlaneRepository,
    events: tuple[StreamEvent, ...],
    code: str,
) -> None:
    repository.minimum_sequence = 1
    repository.maximum_sequence = events[0].sequence
    repository.events = events
    response = client.get("/api/v1/events", headers={**auth(), "Last-Event-ID": "2"})
    assert response.status_code == 409
    assert response.json()["code"] == code


def test_safe_halt_works_without_javascript(
    client: TestClient, repository: FakeControlPlaneRepository
) -> None:
    page = client.get("/safe/halt", headers=auth())
    assert page.status_code == 200
    assert "<script" not in page.text
    assert "Confirm halt" in page.text
    token = page.cookies["__Host-sopara-csrf"]
    response = client.post(
        "/safe/halt",
        headers={**auth(), "Origin": "https://sopara.test"},
        data={
            "csrf_token": token,
            "idempotency_key": "safe-halt-contract-1",
            "expected_version": "0",
            "reason": "operator requested",
        },
    )
    assert response.status_code == 202
    assert "Completion is not implied" in response.text
    assert repository.command_calls[-1]["command_type"] == "HALT_SIMULATION"
