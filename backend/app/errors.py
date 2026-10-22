"""Exceptions whose messages are safe and useful to show to end users."""

from __future__ import annotations


class UserFacingError(Exception):
    """Message is shown verbatim in the UI."""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class FileFormatError(UserFacingError):
    """The uploaded file could not be read or does not have headers in the first row."""


class SpecError(UserFacingError):
    """A dashboard spec is malformed or incompatible with the data."""

    status_code = 422


class LLMUnavailableError(UserFacingError):
    status_code = 503
