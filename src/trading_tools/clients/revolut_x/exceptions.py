"""Exceptions for Revolut X API client."""


class RevolutXError(Exception):
    """Base exception for Revolut X errors."""


class RevolutXAPIError(RevolutXError):
    """Generic API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize API error.

        Args:
            message: Error message.
            status_code: HTTP status code if available.
        """
        super().__init__(message)
        self.status_code = status_code


class RevolutXAuthenticationError(RevolutXAPIError):
    """Authentication error (401)."""


class RevolutXRateLimitError(RevolutXAPIError):
    """Rate limit exceeded error (429)."""


class RevolutXValidationError(RevolutXAPIError):
    """Validation error (400)."""


class RevolutXNotFoundError(RevolutXAPIError):
    """Resource not found error (404)."""
