"""Tick collector service for capturing real-time Polymarket trade data.

Stream every trade from the Polymarket CLOB WebSocket market channel and
persist tick records to a database (SQLite or PostgreSQL via SQLAlchemy).
Designed for continuous operation as a systemd service on EC2, isolated from
the trading bot via resource limits.
"""
