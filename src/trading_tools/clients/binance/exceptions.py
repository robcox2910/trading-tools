"""Exceptions for Binance API client."""


class BinanceError(Exception):
    """Base exception for Binance errors."""


class BinanceAPIError(BinanceError):
    """API error with Binance error code and message.

    Binance returns errors as ``{"code": -1121, "msg": "Invalid symbol."}``.
    """

    def __init__(self, code: int, msg: str) -> None:
        """Initialize Binance API error.

        Args:
            code: Binance-specific error code (negative integer).
            msg: Human-readable error message from the API.

        """
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
