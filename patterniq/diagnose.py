"""``patterniq check``: explain what the configuration actually finds, and why when it finds nothing."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from .config import TIMEFRAMES, ConfigError, Settings
from .data import make_source
from .data.introspect import _distinct
from .data.sql import SqlSource, normalize_db_url


def _first_line(exc: Exception) -> str:
    text = str(getattr(exc, "orig", None) or exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def diagnose(s: Settings, source=None) -> tuple[list[str], bool]:
    """Return (report lines, ok). ``ok`` means at least one timeframe has data."""
    out: list[str] = [s.where_is_config(), f"source: {s.source}"]

    if s.source != "postgres":
        try:
            src = source or make_source(s)
            found = False
            for tf in TIMEFRAMES:
                b = src.bounds(tf)
                out.append(f"  {tf:>3}: " + (f"{b.count} bars, {b.first} -> {b.last}" if b else "no data"))
                found = found or b is not None
            return out, found
        except (ConfigError, ValueError, KeyError) as e:
            return out + [f"PROBLEM: {e}"], False

    if s.db_url:
        out.append("database: " + sa.engine.make_url(normalize_db_url(s.db_url)).render_as_string(hide_password=True))
    else:
        return out + ["PROBLEM: PATTERNIQ_DB_URL is not set in this terminal window."], False
    try:
        src = source or SqlSource(s)
    except ConfigError as e:
        return out + [f"PROBLEM: {e}"], False
    except SQLAlchemyError as e:
        return out + [f"connection: FAILED - {_first_line(e)}", "  check host, port, user, password and database name"], False
    s = getattr(src, "s", s)
    if getattr(src, "auto_note", None):
        out.append(src.auto_note)
    c = s.columns
    layout = (f"single table {s.single_table!r}" if s.single_table else
              f"tables {s.tables}" if s.tables else f"template {s.table_template!r}" if s.table_template else "none")
    out += [
        f"layout: {layout}",
        f"columns: time={c.ts} open={c.open} high={c.high} low={c.low} close={c.close} volume={c.volume}"
        + (f" timeframe={c.timeframe}" if c.timeframe else "") + (f" symbol={c.symbol}" if c.symbol else ""),
        f"symbol: {s.symbol!r} " + ("(rows are filtered on this)" if c.symbol else "(no symbol column mapped: every row is used)"),
    ]
    try:
        with src.engine.connect() as conn:
            conn.execute(sa.text("select 1"))
        out.append("connection: OK")
    except SQLAlchemyError as e:
        return out + [f"connection: FAILED - {_first_line(e)}", "  check host, port, user, password and database name"], False

    any_data, empty_with_table = False, None
    for tf in TIMEFRAMES:
        table = src._table(tf)
        if table is None:
            out.append(f"  {tf:>3}: no table configured")
            continue
        try:
            if not src._exists(table.name):
                names = ", ".join(sa.inspect(src.engine).get_table_names(schema=s.db_schema)) or "none"
                out.append(f"  {tf:>3}: table {table.name!r} NOT FOUND (tables here: {names})")
                continue
            b = src.bounds(tf)
        except SQLAlchemyError as e:
            out.append(f"  {tf:>3}: query failed - {_first_line(e)}")
            continue
        if b is None:
            label = s.timeframe_values.get(tf, tf) if c.timeframe else None
            what = f"timeframe {label!r}" if label else "any row"
            out.append(f"  {tf:>3}: 0 rows for {what}" + (f" and symbol {s.symbol!r}" if c.symbol else ""))
            empty_with_table = table
        else:
            any_data = True
            out.append(f"  {tf:>3}: {b.count} rows, {b.first} -> {b.last}")

    if empty_with_table is not None:
        try:
            if c.timeframe:
                out.append(f"stored {c.timeframe} values: {', '.join(_distinct(src.engine, empty_with_table.name, c.timeframe, s.db_schema)) or 'none'}")
            if c.symbol:
                out.append(f"stored {c.symbol} values: {', '.join(_distinct(src.engine, empty_with_table.name, c.symbol, s.db_schema)) or 'none'}")
        except SQLAlchemyError:
            pass
        out.append("fix: make `symbol` and the [timeframe_values] entries in patterniq.toml match the stored values above exactly (case matters).")
    return out, any_data
