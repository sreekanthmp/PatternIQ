"""Look at an unknown database and propose a ``patterniq.toml``.

Read-only: only reads catalog metadata, min/max/count of the candidate timestamp column and the
distinct values of any timeframe / symbol column (so the suggested config can map them).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace

import sqlalchemy as sa

from ..config import ConfigError, Settings

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
    timeframe_values: list[str] = field(default_factory=list)
    symbol_values: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_TF_RX = re.compile(r"^(?:m(?P<pre>\d+)|(?P<n>\d+)?\s*(?P<unit>[a-z]*))$")


def canonical_timeframe(value: str) -> str | None:
    """Map a stored timeframe label ('1', '5min', '15 minute', 'D', '1day', ...) to 1m/5m/15m/1d."""
    m = _TF_RX.match(str(value).strip().lower())
    if not m:
        return None
    if m.group("pre"):
        n, unit = int(m.group("pre")), "m"
    else:
        n = int(m.group("n")) if m.group("n") else 1
        unit = m.group("unit") or "m"
    if unit in ("m", "min", "mins", "minute", "minutes", "t"):
        return {1: "1m", 5: "5m", 15: "15m", 1440: "1d"}.get(n)
    if unit in ("d", "day", "days", "daily", "eod") and n == 1:
        return "1d"
    return None


def _distinct(engine: sa.Engine, table: str, column: str, schema: str | None, limit: int = 25) -> list[str]:
    tbl = sa.table(table, sa.column(column), schema=schema)
    stmt = sa.select(tbl.c[column]).distinct().order_by(tbl.c[column]).limit(limit)
    with engine.connect() as conn:
        return [str(v) for (v,) in conn.execute(stmt).all() if v is not None]


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
            if probe and info.timeframe_column:
                info.timeframe_values = _distinct(engine, name, info.timeframe_column, schema)
            if probe and info.symbol_column:
                info.symbol_values = _distinct(engine, name, info.symbol_column, schema)
            if info.ts_type and "WITH TIME ZONE" in info.ts_type.upper():
                info.notes.append("timestamptz column: values are converted to display_tz automatically")
            elif info.ts_type and "TIMESTAMP" in info.ts_type.upper():
                info.notes.append("naive timestamp: assumed to be in stored_tz (default Asia/Kolkata) - check first/last above")
        out.append(info)
    return out


def _pick_symbol(values: list[str]) -> str:
    """Best guess among stored symbols: one that *is* the Nifty 50 index (NIFTY, NIFTY50, NIFTY 50,
    NIFTY_50, ...), never one that merely contains "nifty" (BANKNIFTY, FINNIFTY, NIFTYNEXT50). Else the first."""
    plain = lambda v: re.sub(r"[\s_\-.]+", "", v).lower()
    return next((v for v in values if plain(v) in ("nifty", "nifty50")), values[0])


def _layout(ohlc: list[TableInfo]):
    """(single-table layout?, reference table, {timeframe: table}) for the OHLC tables found."""
    per_tf = {t.timeframe_guess: t for t in ohlc if t.timeframe_guess}
    tf_col_tables = [t for t in ohlc if t.timeframe_column]
    use_single = not (len(per_tf) >= 2 or not tf_col_tables)
    ref = tf_col_tables[0] if use_single else next(iter(per_tf.values()), ohlc[0])
    return use_single, ref, per_tf


def suggest_config(tables: list[TableInfo], schema: str | None = None) -> str:
    ohlc = [t for t in tables if t.ohlc]
    if not ohlc:
        return "# No table with open/high/low/close columns was found."

    use_single, ref, per_tf = _layout(ohlc)

    symbol_label, symbol_note = "NIFTY50", []
    if ref.symbol_values:
        symbol_label = _pick_symbol(ref.symbol_values)
        if len(ref.symbol_values) > 1:
            symbol_note = [f"# the table holds several symbols: {', '.join(ref.symbol_values)}  <- check this is the one you want"]

    lines = ['source = "postgres"', f'symbol = "{symbol_label}"', *symbol_note]
    if schema:
        lines += ["", "[database]", f'schema = "{schema}"']
    lines += ["# put the connection string in the PATTERNIQ_DB_URL environment variable, not here", ""]

    if use_single:
        lines.append(f'single_table = "{ref.name}"')
        stored = {canonical_timeframe(v): v for v in ref.timeframe_values if canonical_timeframe(v)}
        lines += ["", "[timeframe_values]   # how each timeframe is stored in the timeframe column"]
        for tf in ("1m", "5m", "15m", "1d"):
            if tf in stored:
                lines.append(f'{tf} = "{stored[tf]}"')
            else:
                lines.append(f'# {tf} = "<stored value>"   # not found among the stored values')
        unmapped = [v for v in ref.timeframe_values if not canonical_timeframe(v)]
        if unmapped:
            lines.append(f"# other stored timeframe values (not used): {', '.join(unmapped)}")
    else:
        lines.append("[tables]")
        for tf in ("1m", "5m", "15m", "1d"):
            lines.append(f'{tf} = "{per_tf[tf].name}"' if tf in per_tf else f'# {tf} = "<table name>"   # could not be guessed')

    lines += ["", "[columns]", f'ts = "{ref.ts_column or "timestamp"}"']
    for k in ("open", "high", "low", "close"):
        lines.append(f'{k} = "{next((c for c in ref.columns if c.lower() == k), k)}"')
    if ref.volume_column:
        lines.append(f'volume = "{ref.volume_column}"')
    if use_single and ref.timeframe_column:
        lines.append(f'timeframe = "{ref.timeframe_column}"')
    if ref.symbol_column:
        lines.append(f'symbol = "{ref.symbol_column}"   # filters rows to the symbol above')
    return "\n".join(lines)


def autoconfigure(settings: Settings, engine: sa.Engine) -> tuple[Settings, str]:
    """Work out the table layout from the database itself, so no config file is needed.

    Applies the same rules as ``inspect-db``'s suggestion. It refuses (with a message saying what
    to set) instead of guessing when the choice would be a coin toss: several candidate tables, or
    several stored symbols with none of them the Nifty 50.
    """
    tables = inspect_database(engine, settings.db_schema)
    ohlc = [t for t in tables if t.ohlc]
    where = settings.where_is_config()
    if not ohlc:
        raise ConfigError(
            "No table with open/high/low/close columns was found in the database"
            + (f" (schema {settings.db_schema!r})" if settings.db_schema else "")
            + f". Set the layout in patterniq.toml. {where}"
        )
    use_single, ref, per_tf = _layout(ohlc)
    tf_col_tables = [t for t in ohlc if t.timeframe_column]
    if (use_single and len(tf_col_tables) > 1) or (not use_single and len(per_tf) < 2 and len(ohlc) > 1):
        raise ConfigError(
            "Several tables look like candle data (" + ", ".join(t.name for t in ohlc) + ") and it is not clear which "
            f"to use. Choose one with `single_table = ...` or `[tables]` in patterniq.toml. {where}"
        )

    data = tomllib.loads(suggest_config(tables, settings.db_schema))
    auto = Settings.from_dict(data)
    symbol = auto.symbol
    if ref.symbol_values:
        plain = lambda v: re.sub(r"[\s_\-.]+", "", v).lower()
        if settings.symbol in ref.symbol_values:
            symbol = settings.symbol                                   # the user named one that exists
        elif len(ref.symbol_values) == 1 or any(plain(v) in ("nifty", "nifty50") for v in ref.symbol_values):
            symbol = _pick_symbol(ref.symbol_values)
        else:
            raise ConfigError(
                f"Table {ref.name!r} holds several symbols ({', '.join(ref.symbol_values)}) and none of them is "
                f"{settings.symbol!r}. Set `symbol = \"...\"` in patterniq.toml. {where}"
            )
    merged = replace(
        auto,
        source=settings.source, db_url=settings.db_url, symbol=symbol,
        stored_tz=settings.stored_tz, display_tz=settings.display_tz,
        config_path=settings.config_path, config_found=settings.config_found,
    )
    if use_single:
        layout = f"single table {ref.name!r} with columns time={merged.columns.ts}, timeframe={merged.columns.timeframe}"
        stored = ", ".join(f"{k}={v!r}" for k, v in merged.timeframe_values.items()) or "none matched"
        layout += f"; timeframe labels: {stored}"
    else:
        layout = "one table per timeframe: " + ", ".join(f"{k}={v!r}" for k, v in merged.tables.items())
    note = f"auto-detected from the database: {layout}; symbol {merged.symbol!r}"
    if len(ref.symbol_values) > 1:
        note += f" (stored symbols: {', '.join(ref.symbol_values)})"
    return merged, note
