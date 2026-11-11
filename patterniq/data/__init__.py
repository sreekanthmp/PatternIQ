from __future__ import annotations

from ..config import Settings
from .base import Bounds, DataLayoutError, DataSource, clean_ohlc
from .frames import CsvSource, DemoSource, read_ohlcv_csv


def make_source(settings: Settings) -> DataSource:
    if settings.source == "postgres":
        from .sql import SqlSource

        return SqlSource(settings)
    if settings.source == "csv":
        return CsvSource(settings)
    if settings.source == "demo":
        return DemoSource(settings)
    raise ValueError(f"unknown source {settings.source!r} (expected postgres, csv or demo)")


__all__ = ["Bounds", "CsvSource", "DataLayoutError", "DataSource", "DemoSource", "clean_ohlc", "make_source", "read_ohlcv_csv"]
