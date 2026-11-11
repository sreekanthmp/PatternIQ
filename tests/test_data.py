import os
import textwrap
from datetime import datetime

import pandas as pd
import pytest
import sqlalchemy as sa

from patterniq.config import ColumnMap, Settings
from patterniq.data import CsvSource, DemoSource, clean_ohlc, make_source, read_ohlcv_csv
from patterniq.data.base import local_to_stored, to_local_naive
from patterniq.data.introspect import inspect_database, suggest_config
from patterniq.data.sql import SqlSource, normalize_db_url
from patterniq.data.synthetic import generate_demo_1m, resample_ohlc

M1 = generate_demo_1m(days=12, seed=3)
REF = {tf: resample_ohlc(M1, tf) for tf in ("5m", "15m", "1d")} | {"1m": M1}


def test_resample_matches_manual_aggregation():
    r = REF["15m"]
    first = M1.loc["2024-11-01 09:15":"2024-11-01 09:29"]
    assert r.index[0] == pd.Timestamp("2024-11-01 09:15")
    row = r.iloc[0]
    assert (row.open, row.high, row.low, row.close) == (first.open.iloc[0], first.high.max(), first.low.min(), first.close.iloc[-1])
    assert len(REF["1d"]) == 12 and REF["1d"].index[0] == pd.Timestamp("2024-11-01")


def test_clean_ohlc_repairs_and_drops():
    idx = pd.to_datetime(["2025-01-01 09:15", "2025-01-01 09:16", "2025-01-01 09:16", "2025-01-01 09:17", "2025-01-01 09:18"])
    df = pd.DataFrame({"open": [10, 10, 11, None, 12], "high": [9, 12, 12, 13, 13], "low": [9, 9, 10, 9, 0.0],
                       "close": [11, 11, 11.5, 11, 12.5], "volume": [1, None, 2, 3, 4]}, index=idx)
    out = clean_ohlc(df)
    assert out.index.is_unique and out.index.is_monotonic_increasing
    assert len(out) == 2                                  # NaN row and zero-price row dropped, duplicate collapsed
    assert (out.high >= out[["open", "close"]].max(axis=1)).all() and (out.low <= out[["open", "close"]].min(axis=1)).all()


def test_timestamp_conversions():
    naive = to_local_naive(["2025-01-06 09:15:00"], "Asia/Kolkata", "Asia/Kolkata")
    assert naive[0] == pd.Timestamp("2025-01-06 09:15")
    utc_naive = to_local_naive(["2025-01-06 03:45:00"], "UTC", "Asia/Kolkata")
    assert utc_naive[0] == pd.Timestamp("2025-01-06 09:15")
    aware = to_local_naive(pd.to_datetime(["2025-01-06 03:45:00+00:00"]), "Asia/Kolkata", "Asia/Kolkata")
    assert aware[0] == pd.Timestamp("2025-01-06 09:15")
    assert to_local_naive([1736135100], "UTC", "Asia/Kolkata")[0] == pd.Timestamp("2025-01-06 09:15")        # seconds
    assert to_local_naive([1736135100000], "UTC", "Asia/Kolkata")[0] == pd.Timestamp("2025-01-06 09:15")     # milliseconds
    t = datetime(2025, 1, 6, 9, 15)
    assert local_to_stored(t, "Asia/Kolkata", "Asia/Kolkata", "epoch_s") == 1736135100
    assert local_to_stored(t, "UTC", "Asia/Kolkata", "naive") == datetime(2025, 1, 6, 3, 45)
    assert local_to_stored(t, "UTC", "Asia/Kolkata", "aware").utcoffset().total_seconds() == 5.5 * 3600


def test_normalize_db_url_pins_psycopg2():
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert normalize_db_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


def test_csv_reader_accepts_tradingview_style_headers(tmp_path):
    f = tmp_path / "15m.csv"
    f.write_text(textwrap.dedent("""\
        Time,Open,High,Low,Close,Volume
        2025-01-06 09:15:00,100,101,99,100.5,10
        2025-01-06 09:30:00,100.5,102,100,101.5,20
        """))
    df = read_ohlcv_csv(f)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"] and len(df) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="no timestamp"):
        read_ohlcv_csv(bad)


def test_csv_source_derives_missing_timeframes_from_finer_ones(tmp_path):
    M1.reset_index().to_csv(tmp_path / "1m.csv", index=False)
    src = CsvSource(Settings(source="csv", csv_dir=str(tmp_path)))
    assert src.timeframes() == ["1m", "5m", "15m", "1d"]
    got = src.load("15m", limit=10)
    pd.testing.assert_frame_equal(got, REF["15m"].tail(10), check_freq=False)


@pytest.mark.parametrize("tf", ["1m", "15m", "1d"])
def test_frame_source_slicing_semantics(tf):
    src = DemoSource(Settings(source="demo", demo_days=12))
    ref = src.frame(tf)
    k = len(ref) // 2
    assert (src.load(tf, limit=5).index == ref.index[-5:]).all()
    assert (src.load(tf, end=ref.index[k].to_pydatetime(), limit=4).index == ref.index[k - 3 : k + 1]).all()
    assert (src.load(tf, start=ref.index[2].to_pydatetime(), limit=3).index == ref.index[2:5]).all()
    assert src.bounds(tf).count == len(ref)


# ------------------------------------------------------------------ SQL (SQLite, always available)
@pytest.fixture()
def sqlite_engine(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path/'prism.db'}")
    with eng.begin() as c:
        for tf, name in {"15m": "nifty_15min", "1d": "nifty_daily"}.items():
            REF[tf].reset_index().rename(columns={"time": "ts"}).to_sql(name, c, index=False)
        e = REF["15m"].reset_index()
        e["Time"] = e["time"].dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC").dt.as_unit("s").astype("int64")
        e.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})[
            ["Time", "Open", "High", "Low", "Close"]].to_sql("epoch_15", c, index=False)
    return eng


def _roundtrip(src, tf):
    ref = REF[tf]
    k = len(ref) // 2
    assert (src.load(tf, limit=5).index == ref.index[-5:]).all()
    win = src.load(tf, end=ref.index[k].to_pydatetime(), limit=6)
    assert (win.index == ref.index[k - 5 : k + 1]).all()
    rng = src.load(tf, start=ref.index[3].to_pydatetime(), end=ref.index[8].to_pydatetime(), limit=None)
    assert (rng.index == ref.index[3:9]).all()
    assert abs(src.load(tf, limit=1).close.iloc[0] - ref.close.iloc[-1]) < 1e-9
    b = src.bounds(tf)
    assert (b.first, b.last, b.count) == (ref.index[0], ref.index[-1], len(ref))


def test_sql_source_per_timeframe_tables(sqlite_engine):
    s = Settings(source="postgres", db_url="sqlite://", tables={"15m": "nifty_15min", "1d": "nifty_daily"},
                 columns=ColumnMap(ts="ts"))
    src = SqlSource(s, engine=sqlite_engine)
    assert src.timeframes() == ["15m", "1d"]
    _roundtrip(src, "15m")
    _roundtrip(src, "1d")


def test_sql_source_epoch_seconds_and_capitalised_columns(sqlite_engine):
    s = Settings(source="postgres", db_url="sqlite://", tables={"15m": "epoch_15"},
                 columns=ColumnMap(ts="Time", open="Open", high="High", low="Low", close="Close", volume=None))
    _roundtrip(SqlSource(s, engine=sqlite_engine), "15m")


def test_sql_source_symbol_and_timeframe_columns(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path/'one.db'}")
    frames = []
    for sym in ("NIFTY 50", "BANKNIFTY"):
        for tf, code in (("15m", "15"), ("1d", "D")):
            d = REF[tf].reset_index().rename(columns={"time": "ts"})
            d["symbol"], d["interval"] = sym, code
            if sym == "BANKNIFTY":
                d[["open", "high", "low", "close"]] *= 2
            frames.append(d)
    pd.concat(frames).to_sql("candles", eng, index=False)
    s = Settings(source="postgres", db_url="sqlite://", single_table="candles", symbol="NIFTY 50",
                 timeframe_values={"15m": "15", "1d": "D"}, columns=ColumnMap(ts="ts", symbol="symbol", timeframe="interval"))
    src = SqlSource(s, engine=eng)
    assert src.timeframes() == ["15m", "1d"]
    _roundtrip(src, "15m")
    assert SqlSource(Settings(**{**s.__dict__, "symbol": "NOPE"}), engine=eng).timeframes() == []


def test_sql_source_refuses_hostile_identifiers(sqlite_engine):
    """Table / column names come from config but must never be able to inject SQL."""
    s = Settings(source="postgres", db_url="sqlite://", tables={"15m": 'nifty_15min"; DROP TABLE nifty_15min; --'},
                 columns=ColumnMap(ts="ts"))
    src = SqlSource(s, engine=sqlite_engine)
    assert src.timeframes() == []
    with pytest.raises(sa.exc.SQLAlchemyError):
        src.load("15m", limit=1)
    assert sa.inspect(sqlite_engine).has_table("nifty_15min")


def test_inspect_database_finds_and_maps_ohlc_tables(sqlite_engine):
    tabs = inspect_database(sqlite_engine)
    by = {t.name: t for t in tabs}
    assert by["nifty_15min"].ohlc and by["nifty_15min"].timeframe_guess == "15m" and by["nifty_15min"].ts_column == "ts"
    assert by["nifty_daily"].timeframe_guess == "1d" and by["nifty_daily"].rows == len(REF["1d"])
    cfg = suggest_config(tabs)
    assert '15m = "nifty_15min"' in cfg and '1d = "nifty_daily"' in cfg and "PATTERNIQ_DB_URL" in cfg


def test_settings_load_toml_and_env(tmp_path, monkeypatch):
    f = tmp_path / "p.toml"
    f.write_text(textwrap.dedent("""\
        symbol = "NIFTY 50"
        [tables]
        1m = "a"
        15m = "b"
        [columns]
        ts = "dt"
        """))
    monkeypatch.delenv("PATTERNIQ_DB_URL", raising=False)
    monkeypatch.delenv("PATTERNIQ_SOURCE", raising=False)
    s = Settings.load(f)
    assert s.source == "demo" and s.tables == {"1m": "a", "15m": "b"} and s.columns.ts == "dt" and s.columns.open == "open"
    monkeypatch.setenv("PATTERNIQ_DB_URL", "postgresql://u:p@h/db")
    assert Settings.load(f).source == "postgres"
    with pytest.raises(ValueError):
        make_source(Settings(source="mongo"))


# ------------------------------------------------------------------ SQL (real PostgreSQL, opt-in)
PG = os.environ.get("PATTERNIQ_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set PATTERNIQ_TEST_PG_URL to run against a real PostgreSQL")
def test_postgres_naive_and_timestamptz_columns():
    eng = sa.create_engine(normalize_db_url(PG))
    ref = REF["15m"].reset_index().rename(columns={"time": "ts"})
    tz = ref.copy()
    tz["ts"] = tz["ts"].dt.tz_localize("Asia/Kolkata")
    try:
        with eng.begin() as c:
            ref.to_sql("piq_t_naive", c, index=False, if_exists="replace", dtype={"ts": sa.DateTime(timezone=False)})
            tz.to_sql("piq_t_tz", c, index=False, if_exists="replace", dtype={"ts": sa.DateTime(timezone=True)})
        for table in ("piq_t_naive", "piq_t_tz"):
            s = Settings(source="postgres", db_url=PG, tables={"15m": table}, columns=ColumnMap(ts="ts"))
            src = SqlSource(s)
            _roundtrip(src, "15m")
            with pytest.raises(sa.exc.DBAPIError):           # the connection must be read-only
                with src.engine.connect() as c:
                    c.execute(sa.text("create table piq_should_not_exist(x int)"))
    finally:
        with eng.begin() as c:
            c.execute(sa.text("drop table if exists piq_t_naive, piq_t_tz"))


# ------------------------------------------------------------------ single-table layout with symbol + timeframe columns
from patterniq.data import DataLayoutError  # noqa: E402
from patterniq.data.introspect import canonical_timeframe  # noqa: E402


@pytest.mark.parametrize("stored,expected", [
    ("1m", "1m"), ("1", "1m"), ("1min", "1m"), ("M1", "1m"), ("5", "5m"), ("5min", "5m"), ("m5", "5m"),
    ("15", "15m"), ("15 minute", "15m"), ("15minutes", "15m"), ("M15", "15m"),
    ("1d", "1d"), ("D", "1d"), ("1D", "1d"), ("day", "1d"), ("daily", "1d"), ("1day", "1d"), ("EOD", "1d"), ("1440", "1d"),
    ("60", None), ("1h", None), ("3m", None), ("W", None), ("weekly", None), ("", "1m"),
])
def test_canonical_timeframe(stored, expected):
    assert canonical_timeframe(stored) == expected


def _ohlcv_engine(tmp_path, symbols=("NIFTY50",), tf_codes=None, db="ohlcv.db"):
    tf_codes = tf_codes or {"15m": "15", "1d": "D"}
    eng = sa.create_engine(f"sqlite:///{tmp_path/db}")
    frames = []
    for sym in symbols:
        for tf, code in tf_codes.items():
            d = REF[tf].reset_index().rename(columns={"time": "datetime"})
            d["symbol"], d["timeframe"] = sym, code
            if sym != "NIFTY50":
                d[["open", "high", "low", "close"]] *= 2.2
            frames.append(d)
    pd.concat(frames).to_sql("ohlcv", eng, index=False)
    return eng


def test_suggested_config_for_single_table_is_valid_and_works_unedited(tmp_path):
    eng = _ohlcv_engine(tmp_path, symbols=("NIFTY50", "BANKNIFTY"))
    cfg_text = suggest_config(inspect_database(eng))
    assert 'single_table = "ohlcv"' in cfg_text
    assert '15m = "15"' in cfg_text and '1d = "D"' in cfg_text         # stored labels were mapped
    assert '# 1m = "<stored value>"' in cfg_text                        # missing timeframes are called out
    assert 'timeframe = "timeframe"' in cfg_text and '\nsymbol = "symbol"' in cfg_text   # symbol filter is NOT left commented out
    assert 'symbol = "NIFTY50"' in cfg_text and "BANKNIFTY" in cfg_text and "several symbols" in cfg_text
    f = tmp_path / "patterniq.toml"
    f.write_text(cfg_text)
    s = Settings.load(f)
    src = SqlSource(Settings(**{**s.__dict__, "db_url": "sqlite://"}), engine=eng)
    assert src.timeframes() == ["15m", "1d"]
    got = src.load("15m", limit=5)
    assert (got.index == REF["15m"].index[-5:]).all()
    assert abs(got.close.iloc[-1] - REF["15m"].close.iloc[-1]) < 1e-9     # NIFTY50 rows, not BANKNIFTY's x2.2


def test_inspect_database_reports_stored_timeframe_and_symbol_values(tmp_path):
    t = next(x for x in inspect_database(_ohlcv_engine(tmp_path, symbols=("NIFTY50", "BANKNIFTY"))) if x.name == "ohlcv")
    assert t.timeframe_values == ["15", "D"] and t.symbol_values == ["BANKNIFTY", "NIFTY50"]


def test_mixed_symbols_without_symbol_mapping_raise_instead_of_silently_picking_rows(tmp_path):
    eng = _ohlcv_engine(tmp_path, symbols=("NIFTY50", "BANKNIFTY"))
    base = dict(source="postgres", db_url="sqlite://", single_table="ohlcv", timeframe_values={"15m": "15", "1d": "D"})
    unfiltered = SqlSource(Settings(**base, columns=ColumnMap(ts="datetime", timeframe="timeframe")), engine=eng)
    with pytest.raises(DataLayoutError, match="several instruments"):
        unfiltered.load("15m", limit=500)
    filtered = SqlSource(Settings(**base, symbol="BANKNIFTY", columns=ColumnMap(ts="datetime", timeframe="timeframe", symbol="symbol")), engine=eng)
    assert filtered.load("15m", limit=3).close.iloc[-1] == pytest.approx(REF["15m"].close.iloc[-1] * 2.2)


def test_a_few_repeated_bars_are_tolerated(tmp_path):
    eng = _ohlcv_engine(tmp_path)
    with eng.begin() as c:                                   # re-ingested candle: same timestamp twice
        c.execute(sa.text("insert into ohlcv select * from ohlcv where timeframe='15' limit 2"))
    s = Settings(source="postgres", db_url="sqlite://", single_table="ohlcv", timeframe_values={"15m": "15"},
                 columns=ColumnMap(ts="datetime", timeframe="timeframe", symbol="symbol"))
    got = SqlSource(s, engine=eng).load("15m", limit=None)
    assert got.index.is_unique and len(got) == len(REF["15m"])


@pytest.mark.parametrize("values,expected", [
    (["BANKNIFTY", "NIFTY50"], "NIFTY50"), (["FINNIFTY", "NIFTY 50", "NIFTYNEXT50"], "NIFTY 50"),
    (["BANKNIFTY", "NIFTY"], "NIFTY"), (["Nifty_50"], "Nifty_50"), (["RELIANCE", "TCS"], "RELIANCE"),
    (["BANKNIFTY", "FINNIFTY"], "BANKNIFTY"),
])
def test_symbol_guess_never_prefers_a_lookalike_index(values, expected):
    from patterniq.data.introspect import _pick_symbol
    assert _pick_symbol(values) == expected
