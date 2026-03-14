"""Whale copy-trading service for Polymarket prediction markets.

Monitor a whale's trades in real-time via the whale_trades database table,
detect directional bias signals on BTC/ETH Up/Down 5-minute markets, and
copy them automatically. Supports paper mode (virtual P&L tracking) and
live mode (real orders via PolymarketClient).

Performance is critical: the service uses incremental polling (only fetching
new trades since the last seen timestamp) and keeps a rolling window of
trades in memory to minimise latency between whale trade and copy execution.
"""
