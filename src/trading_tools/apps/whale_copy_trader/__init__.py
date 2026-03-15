"""Whale copy-trading service for Polymarket prediction markets.

Monitor a whale's trades in real-time via the whale_trades database table,
detect directional bias signals on BTC/ETH Up/Down 5-minute markets, and
copy them using temporal spread arbitrage.

Strategy:
1. Detect the whale's favoured direction and enter a directional position.
2. Monitor the opposite side's price each poll cycle.
3. When both sides can be acquired for less than $1.00 combined, place a
   hedge order to lock in guaranteed profit regardless of market outcome.
4. If no hedge opportunity arises before expiry, the position resolves
   directionally (profitable when the whale is correct ~80% of the time).

Both paper mode (virtual P&L tracking) and live mode (real orders via
PolymarketClient) are supported. Performance is critical: the service uses
incremental polling (only fetching new trades since the last seen timestamp)
and keeps a rolling window of trades in memory.
"""
