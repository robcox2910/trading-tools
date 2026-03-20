"""Spread capture bot for Polymarket Up/Down rotating markets.

Scan BTC/ETH/SOL/XRP Up/Down 5-minute and 15-minute markets for spread
opportunities where the combined cost of buying both sides is below $1.00.
When ``up_price + down_price < max_combined_cost``, buy both sides to lock
in a guaranteed profit at settlement regardless of outcome.

Key features:
    - Autonomous market discovery via series slug expansion.
    - No whale dependency — scans CLOB prices directly.
    - Simultaneous entry on both sides (not directional + hedge).
    - Single-leg timeout handling for partial fills.
    - Settlement via Binance candles for single-leg fallback.
    - Supports both paper and live trading modes.
"""
