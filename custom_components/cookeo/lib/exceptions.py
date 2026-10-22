"""Exceptions raised by the Cookeo library."""


class CookeoError(Exception):
    """Base error for the SEB cloud."""


class LoginError(CookeoError):
    """SEB refused the email and password."""

    def __init__(self, reason: str) -> None:
        """Initialize the error with the refusal reason, e.g. ``account_locked``."""
        super().__init__(reason)
        self.reason = reason
