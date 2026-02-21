"""Exception hierarchy for Polymarket client errors.

Follow the same pattern as the Binance client: a base exception class with
a specialised API error that carries status code and message attributes.
"""


class PolymarketError(Exception):
    """Base exception for all Polymarket client errors."""


class PolymarketAPIError(PolymarketError):
    """Error returned by a Polymarket API call.

    Carry a human-readable message and an HTTP status code so callers
    can distinguish transient failures from client errors.

    Args:
        msg: Human-readable description of the error.
        status_code: HTTP status code from the API response.

    """

    def __init__(self, msg: str, status_code: int) -> None:
        """Initialize Polymarket API error.

        Args:
            msg: Human-readable description of the error.
            status_code: HTTP status code from the API response.

        """
        super().__init__(f"[{status_code}] {msg}")
        self.msg = msg
        self.status_code = status_code
