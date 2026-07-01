"""Application-wide exception hierarchy.

Domain and service layers raise these framework-agnostic errors; the API layer
translates them into HTTP responses (see ``app.core.error_handlers`` later).
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors."""

    message: str = "An application error occurred."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class NotFoundError(AppError):
    """A requested resource does not exist."""

    message = "Resource not found."


class ConflictError(AppError):
    """The request conflicts with current state."""

    message = "Resource conflict."


class ValidationError(AppError):
    """Domain-level validation failed (distinct from request schema validation)."""

    message = "Validation failed."


class ExternalServiceError(AppError):
    """An upstream provider or dependency failed."""

    message = "External service error."
