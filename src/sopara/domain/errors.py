from __future__ import annotations


class DomainError(ValueError):
    """Raised when an input cannot represent a valid Sopara domain value."""


class InvalidTransitionError(DomainError):
    """Raised when a state-machine transition is not allowed."""
