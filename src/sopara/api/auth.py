from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import requests
from cachecontrol import CacheControl
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    email_hash: str
    issued_at: datetime


class IdentityValidator(Protocol):
    def validate(self, assertion: str) -> Identity: ...


class GoogleIAPIdentityValidator:
    def __init__(self, *, audience: str, owner_email: str) -> None:
        self._audience = audience
        self._owner_email = owner_email.strip().casefold()
        self._session = CacheControl(requests.Session())
        self._request = google_requests.Request(session=self._session)
        self._lock = threading.Lock()

    def validate(self, assertion: str) -> Identity:
        try:
            with self._lock:
                claims = cast(
                    "dict[str, object]",
                    id_token.verify_token(
                        assertion,
                        self._request,
                        audience=self._audience,
                        certs_url=IAP_CERTS_URL,
                    ),
                )
        except Exception as error:
            raise AuthenticationError("IAP assertion validation failed") from error
        if claims.get("iss") != IAP_ISSUER:
            raise AuthenticationError("IAP assertion issuer is invalid")
        subject = claims.get("sub")
        email = claims.get("email")
        issued_at = claims.get("iat")
        if not isinstance(subject, str) or not isinstance(email, str):
            raise AuthenticationError("IAP assertion identity claims are invalid")
        if not isinstance(issued_at, int):
            raise AuthenticationError("IAP assertion issued-at claim is invalid")
        if not hmac.compare_digest(email.strip().casefold(), self._owner_email):
            raise AuthorizationError("authenticated identity is not the configured owner")
        return Identity(
            subject=subject,
            email_hash=hashlib.sha256(email.strip().casefold().encode()).hexdigest(),
            issued_at=datetime.fromtimestamp(issued_at, UTC),
        )


class StaticIdentityValidator:
    """Explicit test adapter; never selected by environment configuration."""

    def __init__(self, *, assertion: str, identity: Identity) -> None:
        self._assertion = assertion
        self._identity = identity

    def validate(self, assertion: str) -> Identity:
        if not hmac.compare_digest(assertion, self._assertion):
            raise AuthenticationError("test assertion is invalid")
        return self._identity
