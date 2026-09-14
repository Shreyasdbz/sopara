from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sopara.domain.canonical import canonical_json


class InvalidSecurityTokenError(Exception):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class SignedTokenCodec:
    secret: bytes
    purpose: str

    def encode(self, payload: dict[str, object]) -> str:
        body = canonical_json({"purpose": self.purpose, **payload}).encode()
        signature = hmac.digest(self.secret, body, "sha256")
        return f"{_b64encode(body)}.{_b64encode(signature)}"

    def decode(self, token: str) -> dict[str, object]:
        try:
            body_token, signature_token = token.split(".", maxsplit=1)
            body = _b64decode(body_token)
            signature = _b64decode(signature_token)
        except (ValueError, TypeError) as error:
            raise InvalidSecurityTokenError("token encoding is invalid") from error
        expected = hmac.digest(self.secret, body, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise InvalidSecurityTokenError("token signature is invalid")
        try:
            value: object = json.loads(body)
        except json.JSONDecodeError as error:
            raise InvalidSecurityTokenError("token payload is invalid") from error
        if not isinstance(value, dict):
            raise InvalidSecurityTokenError("token purpose is invalid")
        payload = cast("dict[str, object]", value)
        if payload.get("purpose") != self.purpose:
            raise InvalidSecurityTokenError("token purpose is invalid")
        return payload


@dataclass(frozen=True, slots=True)
class CsrfProtector:
    codec: SignedTokenCodec
    max_age_seconds: int

    def issue(self, *, subject: str, now: datetime) -> str:
        return self.codec.encode(
            {"sub": subject, "iat": int(now.timestamp()), "nonce": secrets.token_urlsafe(18)}
        )

    def verify(self, token: str, *, subject: str, now: datetime) -> None:
        payload = self.codec.decode(token)
        issued_at = payload.get("iat")
        token_subject = payload.get("sub")
        if not isinstance(issued_at, int) or not isinstance(token_subject, str):
            raise InvalidSecurityTokenError("CSRF token claims are invalid")
        age = (now.astimezone(UTC) - datetime.fromtimestamp(issued_at, UTC)).total_seconds()
        if age < -30 or age > self.max_age_seconds:
            raise InvalidSecurityTokenError("CSRF token has expired")
        if not hmac.compare_digest(token_subject, subject):
            raise InvalidSecurityTokenError("CSRF token identity is invalid")


@dataclass(frozen=True, slots=True)
class CursorCodec:
    codec: SignedTokenCodec

    def encode(self, *, collection: str, value: str) -> str:
        return self.codec.encode({"collection": collection, "value": value})

    def decode(self, token: str, *, collection: str) -> str:
        payload = self.codec.decode(token)
        if payload.get("collection") != collection or not isinstance(payload.get("value"), str):
            raise InvalidSecurityTokenError("cursor scope is invalid")
        return cast("str", payload["value"])


def payload_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
