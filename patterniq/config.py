"""Settings: where the candles live and how they are laid out.

Resolution order: defaults < ``patterniq.toml`` (path from ``PATTERNIQ_CONFIG``, else the
current directory) < environment variables. Keep credentials in ``PATTERNIQ_DB_URL`` rather
than in the file.

Supported database layouts (the ``prism`` schema is unknown to this code, so it is mapped):

* one table per timeframe::

      [tables]
      1m = "nifty50_1min"
      5m = "nifty50_5min"
      15m = "nifty50_15min"
      1d = "nifty50_daily"

  or a template: ``table_template = "nifty50_{tf}"``

* one table with a timeframe column: ``single_table = "candles"`` plus ``timeframe = "interval"``
  under ``[columns]`` (and ``[timeframe_values]`` if the stored values differ from 1m/5m/...).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TIMEFRAMES = ("1m", "5m", "15m", "1d")
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1d": 1440}


@dataclass
class ColumnMap:
    ts: str = "timestamp"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: str | None = "volume"
    symbol: str | None = None  # only if one table holds several instruments
    timeframe: str | None = None  # only for the single-table layout


@dataclass
class Settings:
    source: str = "demo"  # postgres | csv | demo
    symbol: str = "NIFTY50"
    db_url: str | None = None
    db_schema: str | None = None
    tables: dict[str, str] = field(default_factory=dict)
    table_template: str | None = None
    single_table: str | None = None
    timeframe_values: dict[str, str] = field(default_factory=dict)
    columns: ColumnMap = field(default_factory=ColumnMap)
    stored_tz: str = "Asia/Kolkata"  # how naive timestamps in the DB / CSV should be read
    display_tz: str = "Asia/Kolkata"  # timezone shown on the chart
    csv_dir: str | None = None
    demo_days: int = 500

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Settings":
        data: dict = {}
        cfg_path = Path(path or os.environ.get("PATTERNIQ_CONFIG", "patterniq.toml"))
        if cfg_path.is_file():
            with cfg_path.open("rb") as fh:
                data = tomllib.load(fh)

        db = data.get("database", {})
        s = cls(
            source=data.get("source", ""),
            symbol=data.get("symbol", "NIFTY50"),
            db_url=db.get("url"),
            db_schema=db.get("schema"),
            tables={str(k): str(v) for k, v in data.get("tables", {}).items()},
            table_template=data.get("table_template"),
            single_table=data.get("single_table"),
            timeframe_values={str(k): str(v) for k, v in data.get("timeframe_values", {}).items()},
            columns=ColumnMap(**{**ColumnMap().__dict__, **data.get("columns", {})}),
            stored_tz=data.get("stored_tz", "Asia/Kolkata"),
            display_tz=data.get("display_tz", "Asia/Kolkata"),
            csv_dir=data.get("csv_dir"),
            demo_days=int(data.get("demo_days", 500)),
        )
        env = os.environ.get
        s.db_url = env("PATTERNIQ_DB_URL") or s.db_url
        s.source = env("PATTERNIQ_SOURCE") or s.source
        s.symbol = env("PATTERNIQ_SYMBOL") or s.symbol
        s.csv_dir = env("PATTERNIQ_CSV_DIR") or s.csv_dir
        if not s.source:
            s.source = "postgres" if s.db_url else ("csv" if s.csv_dir else "demo")
        return s

    def table_for(self, tf: str) -> str | None:
        if tf in self.tables:
            return self.tables[tf]
        if self.table_template:
            return self.table_template.format(tf=tf)
        return self.single_table
