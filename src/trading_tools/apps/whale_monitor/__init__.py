"""Whale trade monitor service for tracking high-frequency Polymarket traders.

Poll the Polymarket Data API to capture trades from tracked whale addresses,
persist them to a database, and provide strategy analysis. Designed for
continuous operation as a systemd service, polling at configurable intervals
to stay within the API's 4000-record result cap.
"""
