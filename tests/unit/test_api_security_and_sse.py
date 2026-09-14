from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sopara.api.auth import (
    AuthenticationError,
    AuthorizationError,
    GoogleIAPIdentityValidator,
)
from sopara.api.ports import StreamEvent, StreamWindow
from sopara.api.security import (
    CsrfProtector,
    CursorCodec,
    InvalidSecurityTokenError,
    SignedTokenCodec,
)
from sopara.api.sse import (
    StreamCursorExpiredError,
    StreamGapError,
    StreamSchemaChangedError,
    encode_event,
    validate_window,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
SECRET = b"unit-test-secret-that-is-at-least-32-bytes"


def event(sequence: int, *, schema_version: int = 1) -> StreamEvent:
    return StreamEvent(sequence, "replay.changed", {"runId": "run-1"}, NOW, schema_version)


def test_signed_cursor_is_scoped_and_tamper_evident() -> None:
    codec = CursorCodec(SignedTokenCodec(SECRET, "cursor"))
    token = codec.encode(collection="datasets", value="00000000-0000-0000-0000-000000000001")
    assert codec.decode(token, collection="datasets").endswith("0001")
    with pytest.raises(InvalidSecurityTokenError):
        codec.decode(token, collection="reports")
    with pytest.raises(InvalidSecurityTokenError):
        codec.decode(token + "x", collection="datasets")


def test_csrf_binds_subject_and_age() -> None:
    protector = CsrfProtector(SignedTokenCodec(SECRET, "csrf"), 900)
    token = protector.issue(subject="owner", now=NOW)
    protector.verify(token, subject="owner", now=NOW + timedelta(minutes=14))
    with pytest.raises(InvalidSecurityTokenError):
        protector.verify(token, subject="other", now=NOW)
    with pytest.raises(InvalidSecurityTokenError):
        protector.verify(token, subject="owner", now=NOW + timedelta(minutes=16))


def test_sse_ignores_duplicate_at_cursor_and_encodes_monotonic_id() -> None:
    accepted = validate_window(StreamWindow(1, 3, (event(2), event(3))), after=2, schema_version=1)
    assert [item.sequence for item in accepted] == [3]
    encoded = encode_event(accepted[0], schema_version=1)
    assert encoded.startswith(b"id: 3\nevent: replay.changed\n")
    assert b'"schemaVersion":1' in encoded


def test_sse_expired_cursor_forces_resnapshot() -> None:
    with pytest.raises(StreamCursorExpiredError):
        validate_window(StreamWindow(5, 5, (event(5),)), after=2, schema_version=1)


def test_sse_gap_forces_resnapshot() -> None:
    with pytest.raises(StreamGapError):
        validate_window(StreamWindow(1, 4, (event(4),)), after=2, schema_version=1)


def test_sse_schema_change_forces_resnapshot() -> None:
    with pytest.raises(StreamSchemaChangedError):
        validate_window(
            StreamWindow(1, 3, (event(3, schema_version=2),)), after=2, schema_version=1
        )


def test_iap_adapter_enforces_issuer_and_exact_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    validator = GoogleIAPIdentityValidator(
        audience="expected-audience", owner_email="owner@example.invalid"
    )

    def accepted(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "iss": "https://cloud.google.com/iap",
            "sub": "accounts.google.com:owner",
            "email": "OWNER@example.invalid",
            "iat": int(NOW.timestamp()),
        }

    monkeypatch.setattr("sopara.api.auth.id_token.verify_token", accepted)
    identity = validator.validate("signed-jwt")
    assert identity.subject == "accounts.google.com:owner"
    assert identity.email_hash != "owner@example.invalid"

    def wrong_issuer(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {**accepted(), "iss": "https://attacker.invalid"}

    monkeypatch.setattr("sopara.api.auth.id_token.verify_token", wrong_issuer)
    with pytest.raises(AuthenticationError):
        validator.validate("signed-jwt")

    def wrong_owner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {**accepted(), "email": "other@example.invalid"}

    monkeypatch.setattr("sopara.api.auth.id_token.verify_token", wrong_owner)
    with pytest.raises(AuthorizationError):
        validator.validate("signed-jwt")
