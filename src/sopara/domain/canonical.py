from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import cast

from sopara.domain.errors import DomainError


def canonical_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DomainError("canonical decimals must be finite")
        return format(value, "f")
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        if not all(isinstance(key, str) for key in mapping):
            raise DomainError("canonical mappings require string keys")
        return {
            cast("str", key): canonical_value(item)
            for key, item in sorted(mapping.items(), key=lambda pair: cast("str", pair[0]))
        }
    if isinstance(value, (tuple, list)):
        sequence = cast("tuple[object, ...] | list[object]", value)
        return [canonical_value(item) for item in sequence]
    if isinstance(value, (set, frozenset)):
        collection = cast("set[object] | frozenset[object]", value)
        normalized = [canonical_value(item) for item in collection]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    raise DomainError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    return json.dumps(canonical_value(value), separators=(",", ":"), sort_keys=True)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def stable_idempotency_key(namespace: str, *parts: object) -> str:
    if not namespace.strip():
        raise DomainError("idempotency namespace is required")
    return f"{namespace}:{canonical_sha256(parts)}"
