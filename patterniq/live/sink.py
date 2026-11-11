"""Append-only writer for the candle table.

Only ``INSERT`` is ever issued, only for bars that are not already stored (matched on timestamp,
symbol and timeframe), and only for the configured symbol. Table and column names come from the same
detected / configured layout the dashboard reads, so the two always agree.
"""

from __future__ import annotations

import pandas as pd
import sqlalchemy as sa

from ..config import Settings
from ..data.base import local_to_stored, to_local_naive
from ..data.sql import SqlSource, normalize_db_url


class SqlSink:
    def __init__(self, settings: Settings, engine: sa.Engine | None = None):
        if engine is None:
            if not settings.db_url:
                raise ValueError("no database URL: set PATTERNIQ_DB_URL (or PG_USER and PG_PASSWORD)")
            engine = sa.create_engine(normalize_db_url(settings.db_url), pool_pre_ping=True)
        self.src = SqlSource(settings, engine=engine, read_only=False)
        self.s = self.src.s
        self.engine = self.src.engine
        self.auto_note = self.src.auto_note

    # ---- reads -----------------------------------------------------------------------------
    def last_bar(self, tf: str) -> pd.Timestamp | None:
        b = self.src.bounds(tf)
        return b.last if b else None

    def _stored(self, tf: str, ts: pd.Timestamp):
        t = self.src._table(tf)
        return local_to_stored(ts.to_pydatetime(), self.s.stored_tz, self.s.display_tz, self.src._ts_kind(tf, t))

    def existing(self, tf: str, lo: pd.Timestamp, hi: pd.Timestamp) -> set[pd.Timestamp]:
        t = self.src._table(tf)
        ts = t.c[self.s.columns.ts]
        stmt = sa.select(ts).where(ts >= self._stored(tf, lo), ts <= self._stored(tf, hi))
        for cond in self.src._filters(tf, t):
            stmt = stmt.where(cond)
        with self.engine.connect() as conn:
            vals = [v for (v,) in conn.execute(stmt).all()]
        return set(to_local_naive(vals, self.s.stored_tz, self.s.display_tz)) if vals else set()

    def can_insert(self, tf: str = "1m") -> bool | None:
        """True / False on PostgreSQL, None when it cannot be determined."""
        if self.engine.dialect.name != "postgresql":
            return None
        t = self.src._table(tf)
        qualified = f'{t.schema + "." if t.schema else ""}"{t.name}"'
        try:
            with self.engine.connect() as conn:
                return bool(conn.execute(sa.text("select has_table_privilege(current_user, :t, 'INSERT')"), {"t": qualified}).scalar())
        except sa.exc.SQLAlchemyError:
            return None

    # ---- writes ----------------------------------------------------------------------------
    def new_rows(self, tf: str, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        have = self.existing(tf, df.index.min(), df.index.max())
        return df[[t not in have for t in df.index]]

    def insert(self, tf: str, df: pd.DataFrame, dry_run: bool = False) -> int:
        fresh = self.new_rows(tf, df)
        if fresh.empty or dry_run:
            return len(fresh)
        t = self.src._table(tf)
        c = self.s.columns
        kind = self.src._ts_kind(tf, t)
        if kind in ("naive", "aware"):  # let SQLAlchemy convert datetimes instead of relying on driver adapters
            cols = [sa.column(c.ts, sa.DateTime(timezone=(kind == "aware")))] + [sa.column(x.name) for x in t.c if x.name != c.ts]
            t = sa.table(t.name, *cols, schema=t.schema)
        rows = []
        for ts, r in fresh.iterrows():
            row = {
                c.ts: self._stored(tf, ts),
                c.open: round(float(r["open"]), 2), c.high: round(float(r["high"]), 2),
                c.low: round(float(r["low"]), 2), c.close: round(float(r["close"]), 2),
            }
            if c.volume:
                row[c.volume] = int(round(float(r["volume"]))) if pd.notna(r["volume"]) else 0
            if c.symbol:
                row[c.symbol] = self.s.symbol
            if c.timeframe:
                row[c.timeframe] = self.s.timeframe_values.get(tf, tf)
            rows.append(row)
        with self.engine.begin() as conn:
            conn.execute(sa.insert(t), rows)
        return len(rows)
