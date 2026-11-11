"""PostgreSQL (or any SQLAlchemy-supported database) candle source.

The ``prism`` layout is mapped through :class:`~patterniq.config.Settings`, so no table or
column names are hard-coded. Identifiers go through SQLAlchemy's quoting; values are always
bound parameters. The connection is opened read-only where the dialect supports it.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import sqlalchemy as sa

from ..config import TIMEFRAMES, Settings
from .base import Bounds, clean_ohlc, local_to_stored, to_local_naive


def normalize_db_url(url: str) -> str:
    """Pin a bare ``postgresql://`` URL to psycopg2 (SQLAlchemy >= 2.1 would pick psycopg3)."""
    for bare in ("postgresql://", "postgres://"):
        if url.startswith(bare):
            return "postgresql+psycopg2://" + url[len(bare):]
    return url


class SqlSource:
    kind = "postgres"

    def __init__(self, settings: Settings, engine: sa.Engine | None = None):
        if not settings.db_url and engine is None:
            raise ValueError("no database URL: set PATTERNIQ_DB_URL or [database] url in patterniq.toml")
        self.s = settings
        self.engine = engine or sa.create_engine(normalize_db_url(settings.db_url), pool_pre_ping=True)
        if self.engine.dialect.name == "postgresql":
            self.engine = self.engine.execution_options(postgresql_readonly=True)
        self._kind_cache: dict[str, str] = {}

    # ---- schema helpers ----------------------------------------------------------------
    def _table(self, tf: str) -> sa.TableClause | None:
        name = self.s.table_for(tf)
        if not name:
            return None
        c = self.s.columns
        names = [c.ts, c.open, c.high, c.low, c.close]
        for extra in (c.volume, c.symbol, c.timeframe):
            if extra:
                names.append(extra)
        return sa.table(name, *[sa.column(n) for n in names], schema=self.s.db_schema)

    def _exists(self, name: str) -> bool:
        return sa.inspect(self.engine).has_table(name, schema=self.s.db_schema)

    def _ts_kind(self, tf: str, table: sa.TableClause) -> str:
        key = f"{table.schema}.{table.name}"
        if key in self._kind_cache:
            return self._kind_cache[key]
        kind = "naive"
        for col in sa.inspect(self.engine).get_columns(table.name, schema=table.schema):
            if col["name"] != self.s.columns.ts:
                continue
            t = col["type"]
            if isinstance(t, sa.DateTime):
                kind = "aware" if getattr(t, "timezone", False) else "naive"
            elif isinstance(t, sa.Date):
                kind = "date"
            elif isinstance(t, (sa.Integer, sa.Numeric)):
                with self.engine.connect() as conn:
                    mx = conn.execute(sa.select(sa.func.max(table.c[self.s.columns.ts]))).scalar()
                kind = "epoch_ms" if mx is not None and float(mx) > 1e11 else "epoch_s"
            break
        self._kind_cache[key] = kind
        return kind

    def _filters(self, tf: str, table: sa.TableClause) -> list:
        c = self.s.columns
        conds = []
        if c.symbol:
            conds.append(table.c[c.symbol] == self.s.symbol)
        if c.timeframe:
            conds.append(table.c[c.timeframe] == self.s.timeframe_values.get(tf, tf))
        return conds

    # ---- DataSource API ----------------------------------------------------------------
    def timeframes(self) -> list[str]:
        out = []
        for tf in TIMEFRAMES:
            t = self._table(tf)
            if t is None or not self._exists(t.name):
                continue
            if self.s.columns.timeframe and self.bounds(tf) is None:
                continue
            out.append(tf)
        return out

    def bounds(self, timeframe: str) -> Bounds | None:
        t = self._table(timeframe)
        if t is None:
            return None
        ts = t.c[self.s.columns.ts]
        stmt = sa.select(sa.func.min(ts), sa.func.max(ts), sa.func.count()).select_from(t)
        for cond in self._filters(timeframe, t):
            stmt = stmt.where(cond)
        with self.engine.connect() as conn:
            lo, hi, n = conn.execute(stmt).one()
        if lo is None or not n:
            return None
        idx = to_local_naive([lo, hi], self.s.stored_tz, self.s.display_tz)
        return Bounds(first=idx[0], last=idx[1], count=int(n))

    def load(
        self,
        timeframe: str,
        *,
        end: datetime | None = None,
        limit: int | None = 500,
        start: datetime | None = None,
    ) -> pd.DataFrame:
        t = self._table(timeframe)
        if t is None:
            raise KeyError(f"no table configured for timeframe {timeframe!r}")
        c = self.s.columns
        ts = t.c[c.ts]
        kind = self._ts_kind(timeframe, t)
        cols = [ts.label("time")] + [t.c[getattr(c, k)].label(k) for k in ("open", "high", "low", "close")]
        if c.volume:
            cols.append(t.c[c.volume].label("volume"))
        stmt = sa.select(*cols).select_from(t)
        for cond in self._filters(timeframe, t):
            stmt = stmt.where(cond)
        conv = lambda x: local_to_stored(x, self.s.stored_tz, self.s.display_tz, kind)
        if end is not None:
            stmt = stmt.where(ts <= conv(end))
        if start is not None:
            stmt = stmt.where(ts >= conv(start)).order_by(ts.asc())
            if limit:
                stmt = stmt.limit(limit)
        else:
            stmt = stmt.order_by(ts.desc())
            if limit:
                stmt = stmt.limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        if not rows:
            return clean_ohlc(pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                                           index=pd.DatetimeIndex([], name="time")))
        df = pd.DataFrame(rows, columns=[k for k in ("time", "open", "high", "low", "close", "volume") if k != "volume" or c.volume])
        df.index = to_local_naive(df.pop("time"), self.s.stored_tz, self.s.display_tz)
        return clean_ohlc(df)
