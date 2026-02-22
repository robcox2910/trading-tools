"""Polymarket automated paper trading bot.

Provide an async polling engine that feeds prediction market snapshots to
pluggable strategies, sizes positions with Kelly criterion, and tracks
virtual P&L. Paper trading only — no real orders are placed.
"""
