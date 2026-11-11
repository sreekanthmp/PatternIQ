"""Look at an unknown database and propose a ``patterniq.toml``.

Read-only: only reads catalog metadata plus min/max/count of the candidate timestamp column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlalchemy as sa

_TS_HINTS = ("timestamp", "datetime", "date_time", "time", "date", "ts", "dt")
_TF_HINTS = ("timeframe", "interval", "tf", "period", "resolution")
_SYM_HINTS = ("symbol", "ticker", "instrument", "name", "scrip")
_TF_IN_NAME = [
    ("1m", re.compile(r"(^|[^0-9])(1|one)[ _-]?(m|min|minute)s?($|[^a-z])|_1m($|_)", re.I)),
    ("5m", re.compile(r"(^|[^0-9])(5|five)[ _-]?(m|min|minute)s?($|[^a-z])|_5m($|_)", re.I)),
    ("15m", re.compile(r"(^|[^0-9])(15|fifteen)[ _-]?(m|min|minute)s?($|[^a-z])|_15m($|_)", re.I)),
    ("1d", re.compile(r"(daily|day|1d|eod|_d$)", re.I)),
]


@dataclass
class TableInfo:
    name: str
    columns: dict[str, str]
    ohlc: bool
    ts_column: str | None = None
    rows: int | None = None
    first: str | None = None
    last: str | None = None
    timeframe_guess: str | None = None
    timeframe_column: str | None = None
    symbol_column: str | None = None
    volume_column: str | None = None
    ts_type: str | None = None
    notes: list[str] = field(default_factory=list)


def _pick(cols: dict[str, str], hints: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for h in hints:
        if h in lower:
            return lower[h]
    return None


def inspect_database(engine: sa.Engine, schema: str | None = None, probe: bool = True) -> list[TableInfo]:
    insp = sa.inspect(engine)
    out: list[TableInfo] = []
    for name in sorted(insp.get_table_names(schema=schema)):
        raw_cols = insp.get_columns(name, schema=schema)
        cols = {c["name"]: str(c["type"]) for c in raw_cols}
        aware = {c["name"]: bool(getattr(c["type"], "timezone", False)) for c in raw_cols}
        for cname, is_aware in aware.items():  # str(TIMESTAMP(timezone=True)) omits the zone on some dialects
            if is_aware and "TIME ZONE" not in cols[cname].upper():
                cols[cname] += " WITH TIME ZONE"
        lower = {c.lower() for c in cols}
        info = TableInfo(name=name, columns=cols, ohlc={"open", "high", "low", "close"} <= lower)
        if info.ohlc:
            info.ts_column = _pick(cols, _TS_HINTS) or next(
                (c for c, t in cols.items() if "TIMESTAMP" in t.upper() or t.upper() == "DATE"), None
            )
            info.timeframe_column = _pick(cols, _TF_HINTS)
            info.symbol_column = _pick(cols, _SYM_HINTS)
            info.volume_column = _pick(cols, ("volume", "vol", "qty"))
            info.ts_type = cols.get(info.ts_column) if info.ts_column else None
            for tf, rx in _TF_IN_NAME:
                if rx.search(name):
                    info.timeframe_guess = tf
                    break
            if probe and info.ts_column:
                tbl = sa.table(name, sa.column(info.ts_column), schema=schema)
                with engine.connect() as conn:
                    lo, hi, n = conn.execute(
                        sa.select(sa.func.min(tbl.c[info.ts_column]), sa.func.max(tbl.c[info.ts_column]), sa.func.count())
                    ).one()
                info.rows, info.first, info.last = int(n), str(lo), str(hi)
            if info.ts_type and "WITH TIME ZONE" in info.ts_type.upper():
                info.notes.append("timestamptz column: values are converted to display_tz automatically")
            elif info.ts_type and "TIMESTAMP" in info.ts_type.upper():
                info.notes.append("naive timestamp: assumed to be in stored_tz (default Asia/Kolkata) - check first/last above")
        out.append(info)
    return out


def suggest_config(tables: list[TableInfo], schema: str | None = None) -> str:
    ohlc = [t for t in tables if t.ohlc]
    if not ohlc:
        return "# No table with open/high/low/close columns was found."
    lines = ['source = "postgres"', 'symbol = "NIFTY50"']
    if schema:
        lines += ["", "[database]", f'schema = "{schema}"']
    lines += ["# put the connection string in the PATTERNIQ_DB_URL environment variable, not here", ""]

    per_tf = {t.timeframe_guess: t for t in ohlc if t.timeframe_guess}
    tf_col_tables = [t for t in ohlc if t.timeframe_column]
    if len(per_tf) >= 2 or not tf_col_tables:
        lines.append("[tables]")
        for tf in ("1m", "5m", "15m", "1d"):
            if tf in per_tf:
                lines.append(f'{tf} = "{per_tf[tf].name}"')
            else:
                lines.append(f'# {tf} = "<table name>"   # could not be guessed')
        ref = next(iter(per_tf.values()), ohlc[0])
    else:
        ref = tf_col_tables[0]
        lines += [f'single_table = "{ref.name}"', "", "# [timeframe_values]   # only if stored values differ from 1m/5m/15m/1d",
                  '# 1m = "1"', '# 1d = "D"']
    lines += ["", "[columns]", f'ts = "{ref.ts_column or "timestamp"}"']
    for k in ("open", "high", "low", "close"):
        real = next((c for c in ref.columns if c.lower() == k), k)
        lines.append(f'{k} = "{real}"')
    if ref.volume_column:
        lines.append(f'volume = "{ref.volume_column}"')
    if ref.timeframe_column and ref in tf_col_tables and not per_tf:
        lines.append(f'timeframe = "{ref.timeframe_column}"')
    if ref.symbol_column:
        lines.append(f'# symbol = "{ref.symbol_column}"   # uncomment if the table holds several instruments')
    return "\n".join(lines)
