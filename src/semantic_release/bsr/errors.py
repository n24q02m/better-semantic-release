from __future__ import annotations


class BsrGuardError(Exception):
    """Raised when a bsr safety guard trips. Carries a user-facing message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
