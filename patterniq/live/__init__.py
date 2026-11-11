"""Live market data: pull closed candles from Angel One SmartAPI and append them to the candle table.

Strictly read-only towards the broker (login + historical candles; no order code exists in this package)
and append-only towards the database (rows are only ever inserted, never updated or deleted).
"""
