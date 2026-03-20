"""Re-export shim — ``OrderBookFeed`` now lives in ``data.providers``.

Import from :mod:`trading_tools.data.providers.order_book_feed` for the
canonical location.  This module exists solely to keep existing spread
capture imports working.
"""

from trading_tools.data.providers.order_book_feed import OrderBookFeed, parse_book_event

__all__ = ["OrderBookFeed", "parse_book_event"]
