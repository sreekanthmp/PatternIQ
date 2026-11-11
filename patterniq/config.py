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

def _url_from_pg_env() -> str | None:
    """Build a connection URL from PG_USER / PG_PASSWORD (+ PG_HOST, PG_PORT, PG_DATABASE) if present."""
    user, password = os.environ.get("PG_USER"), os.environ.get("PG_PASSWORD")
    if not (user and password):
        return None
    from sqlalchemy.engine import URL

    return URL.create(
        "postgresql+psycopg2", username=user, password=password,
        host=os.environ.get("PG_HOST") or "localhost", port=int(os.environ.get("PG_PORT") or 5432),
        database=os.environ.get("PG_DATABASE") or "prism",
    ).render_as_string(hide_password=False)


class ConfigError(ValueError):
    """The configuration is missing or unusable; the message says what to do about it."""


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
    config_path: str | None = None  # where the config file was looked for (set by load())
    config_found: bool = False

    def where_is_config(self) -> str:
        """One sentence on which config file is in use, with the usual Windows pitfall called out."""
        if self.config_found:
            return f"Config file in use: {self.config_path}."
        msg = f"No config file was found at {self.config_path or 'patterniq.toml'} (it is read from the folder you run patterniq in)."
        if self.config_path and Path(self.config_path + ".txt").exists():
            msg += f" But {Path(self.config_path + '.txt').name} exists: Notepad added '.txt'. Rename it to patterniq.toml."
        return msg

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        db = data.get("database", {})
        return cls(
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

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Settings":
        from .live.env import load_dotenv

        load_dotenv()  # a git-ignored .env next to where patterniq runs; real environment variables win
        data: dict = {}
        cfg_path = Path(path or os.environ.get("PATTERNIQ_CONFIG", "patterniq.toml"))
        found = cfg_path.is_file()
        if found:
            with cfg_path.open("rb") as fh:
                data = tomllib.load(fh)
        s = cls.from_dict(data)
        s.config_path, s.config_found = str(cfg_path.resolve()), found
        env = os.environ.get
        s.db_url = env("PATTERNIQ_DB_URL") or s.db_url or _url_from_pg_env()
        s.source = env("PATTERNIQ_SOURCE") or s.source
        s.symbol = env("PATTERNIQ_SYMBOL") or s.symbol
        s.csv_dir = env("PATTERNIQ_CSV_DIR") or s.csv_dir
        if not s.source:
            s.source = "postgres" if s.db_url else ("csv" if s.csv_dir else "demo")
        return s

    def has_layout(self) -> bool:
        """True when the config says which table(s) hold the candles."""
        return any(self.table_for(tf) for tf in TIMEFRAMES)

    def table_for(self, tf: str) -> str | None:
        if tf in self.tables:
            return self.tables[tf]
        if self.table_template:
            return self.table_template.format(tf=tf)
        return self.single_table
