"""Directional trading algorithm for Polymarket 5-min crypto Up/Down markets.

Buy only the predicted winning side of binary crypto markets using
momentum, volatility, volume, and order-book features to estimate
the probability of an Up outcome.  Size positions via Kelly criterion
for optimal growth.  Fully independent from the spread capture bot —
share only common infrastructure (clients, indicators, fee computation).
"""
