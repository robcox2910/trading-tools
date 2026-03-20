"""Whale copy trading bot for Polymarket Up/Down markets.

Mirror the net directional positioning of tracked whale addresses in
real time.  Unlike the spread capture strategy that locks in a signal
early, this bot re-reads the whale's current direction every poll cycle
and accumulates tokens on whichever side they currently favour.
"""
