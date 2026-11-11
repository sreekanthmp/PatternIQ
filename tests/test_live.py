import base64
import json
import logging
import os
import subprocess
import sys
import textwrap

import pandas as pd
import pytest
import sqlalchemy as sa

from patterniq.config import ColumnMap, Settings
from patterniq.data.synthetic import generate_demo_1m, resample_ohlc
from patterniq.live import clock
from patterniq.live.angel import (INTERVALS, AngelFeed, FeedAuthError, FeedError, FeedRateLimited, parse_candles,
                                  resolve_token)
from patterniq.live.bars import aggregate, closed_only
from patterniq.live.env import AngelCredentials, LiveConfigError, load_dotenv, redact
from patterniq.live.ingest import Ingestor
from patterniq.live.sink import SqlSink
from patterniq.live.totp import totp

T = pd.Timestamp
FAKE = AngelCredentials(api_key="KEY-abc123XYZ", client_id="A1234567", password="9876PIN", totp_secret="JBSWY3DPEHPK3PXP")
SERIES = generate_demo_1m(days=4, seed=21)            # Fri 2024-11-01 .. Wed 2024-11-06, 375 bars a day
DAYS = sorted({t.normalize() for t in SERIES.index})


# ------------------------------------------------------------------------------------ primitives
def test_totp_matches_rfc6238_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, at=59, digits=8) == "94287082"
    assert totp(secret, at=1111111109, digits=8) == "07081804"
    assert totp("jbsw y3dp-ehpk 3pxp", at=0) == totp("JBSWY3DPEHPK3PXP", at=0)       # spaces / case tolerated
    with pytest.raises(ValueError, match="base32"):
        totp("not base32!!")


def test_dotenv_sets_only_missing_vars_and_never_returns_values(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# c\nANGEL_API_KEY="quoted key"\nexport PG_USER=bob\nPG_HOST=db.local # trailing\n\nBAD LINE\nPG_PASSWORD = has=equals\n')
    for k in ("ANGEL_API_KEY", "PG_USER", "PG_HOST", "PG_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PG_USER", "already-set")
    loaded = load_dotenv(f)
    assert sorted(loaded) == ["ANGEL_API_KEY", "PG_HOST", "PG_PASSWORD"] and "bob" not in str(loaded)
    assert os.environ["ANGEL_API_KEY"] == "quoted key" and os.environ["PG_HOST"] == "db.local"
    assert os.environ["PG_PASSWORD"] == "has=equals" and os.environ["PG_USER"] == "already-set"
    assert load_dotenv(tmp_path / "missing.env") == []


def test_credentials_never_render_and_missing_names_only():
    assert "9876PIN" not in repr(FAKE) and "9876PIN" not in str(FAKE) and "KEY-abc" not in f"{FAKE}"
    with pytest.raises(LiveConfigError) as e:
        AngelCredentials.from_env({"ANGEL_API_KEY": "SECRETKEYVALUE", "ANGEL_CLIENT_ID": "X"})
    assert "ANGEL_PASSWORD" in str(e.value) and "ANGEL_TOTP" in str(e.value) and "SECRETKEYVALUE" not in str(e.value)


def test_redact_hides_secrets_and_jwts():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VybmFtZSI6IkExMjM0NTY3In0.c2lnbmF0dXJl"
    out = redact(f"password 9876PIN key KEY-abc123XYZ tok Bearer {jwt}", FAKE.secrets())
    assert "9876PIN" not in out and "KEY-abc123XYZ" not in out and jwt not in out and "***" in out


def test_clock_helpers():
    assert clock.market_open(T("2024-11-04 09:15")) and not clock.market_open(T("2024-11-04 15:30"))
    assert not clock.market_open(T("2024-11-02 11:00"))                                  # Saturday
    assert clock.next_open(T("2024-11-01 16:00")) == T("2024-11-04 09:15")               # Fri after close -> Monday
    assert clock.next_open(T("2024-11-04 08:00")) == T("2024-11-04 09:15")
    assert clock.bucket_start(T("2024-11-04 10:07"), 15) == T("2024-11-04 10:00")
    assert clock.bar_is_closed(T("2024-11-04 10:00"), pd.Timedelta(minutes=1), T("2024-11-04 10:01:06")) is True
    assert clock.bar_is_closed(T("2024-11-04 10:00"), pd.Timedelta(minutes=1), T("2024-11-04 10:01:02")) is False


def test_parse_candles_converts_to_naive_ist_and_tolerates_empty():
    df = parse_candles([["2024-11-04T09:15:00+05:30", 100, 101, 99, 100.5, 7], ["2024-11-04T09:16:00+05:30", 100.5, 102, 100, 101, 0]])
    assert list(df.index) == [T("2024-11-04 09:15"), T("2024-11-04 09:16")] and df.close.iloc[-1] == 101
    assert parse_candles(None).empty and parse_candles([]).empty
    utc = parse_candles([["2024-11-04T03:45:00+00:00", 1, 1, 1, 1, 0]])
    assert utc.index[0] == T("2024-11-04 09:15")


def test_resolve_token():
    assert resolve_token("NIFTY") == ("NSE", "99926000") and resolve_token("Nifty 50") == ("NSE", "99926000")
    assert resolve_token("anything", "BSE", "123") == ("BSE", "123")
    with pytest.raises(LiveConfigError, match="--token"):
        resolve_token("RELIANCE")


# ------------------------------------------------------------------------------------ bars
M1 = SERIES.loc["2024-11-04"]                           # a full session of 1-minute bars


def test_closed_only_drops_the_bar_in_progress():
    part = M1.loc[:"2024-11-04 10:00"]                  # includes the 10:00 bar
    assert closed_only(part, clock.MINUTE, T("2024-11-04 10:00:30")).index[-1] == T("2024-11-04 09:59")
    assert closed_only(part, clock.MINUTE, T("2024-11-04 10:01:10")).index[-1] == T("2024-11-04 10:00")


def test_aggregate_matches_resample_for_complete_buckets_only():
    now = T("2024-11-04 10:07:30")
    got = aggregate(M1.loc[:"2024-11-04 10:06"], 5, now)
    want = resample_ohlc(M1, "5m").loc[:"2024-11-04 10:00"]
    pd.testing.assert_frame_equal(got, want.loc[got.index], check_freq=False)
    assert got.index[-1] == T("2024-11-04 10:00")                                # 10:05 bucket still open
    assert aggregate(M1.loc[:"2024-11-04 10:06"], 15, now).index[-1] == T("2024-11-04 09:45")


def test_aggregate_waits_for_a_late_last_minute_then_gives_up_after_grace():
    m = M1.loc[:"2024-11-04 10:03"].drop(T("2024-11-04 10:04"), errors="ignore")      # 10:04 (last minute of 10:00 bucket) missing
    m = M1.loc["2024-11-04 09:15":"2024-11-04 10:03"]
    assert T("2024-11-04 10:00") not in aggregate(m, 5, T("2024-11-04 10:05:20")).index   # API lagging: wait
    assert T("2024-11-04 10:00") in aggregate(m, 5, T("2024-11-04 10:11:00")).index        # gap persists: write what we have


# ------------------------------------------------------------------------------------ the feed (fake SDK)
class FakeSDK:
    """Only the three methods the integration may call exist: anything else raises AttributeError."""

    def __init__(self, login=None, candles=None):
        self.login_results = list(login or [])
        self.candle_results = list(candles or [])
        self.calls = []

    def generateSession(self, client, password, code):
        self.calls.append(("login", client, password, code))
        r = self.login_results.pop(0) if self.login_results else {"status": True, "data": {"jwtToken": "JWT1", "refreshToken": "RT1", "feedToken": "FT1"}}
        if isinstance(r, Exception):
            raise r
        return r

    def getCandleData(self, params):
        self.calls.append(("candles", params))
        r = self.candle_results.pop(0) if self.candle_results else {"status": True, "data": []}
        if isinstance(r, Exception):
            raise r
        return r

    def terminateSession(self, client):
        self.calls.append(("logout", client))


def feed_with(sdk, **kw):
    sleeps = []
    f = AngelFeed(FAKE, sdk_factory=lambda key: sdk, sleep=sleeps.append, now=lambda: 1_700_000_005.0, min_interval=0, **kw)
    f.sleeps = sleeps
    return f


def test_login_then_candles_and_logout():
    sdk = FakeSDK(candles=[{"status": True, "data": [["2024-11-04T09:15:00+05:30", 1, 2, 0.5, 1.5, 10]]}])
    f = feed_with(sdk)
    f.login()
    df = f.candles("NSE", "99926000", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:30"))
    assert len(df) == 1 and sdk.calls[1][1] == {"exchange": "NSE", "symboltoken": "99926000", "interval": INTERVALS["1m"],
                                                 "fromdate": "2024-11-04 09:15", "todate": "2024-11-04 09:30"}
    f.close()
    assert sdk.calls[-1][0] == "logout" and {c[0] for c in sdk.calls} == {"login", "candles", "logout"}


def test_login_retries_once_on_expired_totp_then_stops():
    bad = {"status": False, "message": "Invalid Totp", "errorcode": "AB1050"}
    f = feed_with(FakeSDK(login=[bad]))
    f.login()                                              # second attempt (fresh code) succeeds
    sdk = FakeSDK(login=[bad, bad, bad])
    with pytest.raises(FeedAuthError, match="Invalid Totp"):
        feed_with(sdk).login()
    assert sum(c[0] == "login" for c in sdk.calls) == 2    # never more than two attempts: no account lock-out risk


def test_login_failure_message_contains_no_secret():
    msg = f"bad request for {FAKE.password} using {FAKE.api_key} on {FAKE.client_id}"
    with pytest.raises(FeedAuthError) as e:
        feed_with(FakeSDK(login=[RuntimeError(msg)])).login()
    for secret in FAKE.secrets():
        assert secret not in str(e.value)


def test_expired_session_relogs_in_once_and_retries():
    expired = {"status": False, "message": "Invalid Token", "errorcode": "AG8001"}
    sdk = FakeSDK(candles=[expired, {"status": True, "data": [["2024-11-04T09:15:00+05:30", 1, 1, 1, 1, 0]]}])
    f = feed_with(sdk)
    assert len(f.candles("NSE", "1", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:16"))) == 1
    assert [c[0] for c in sdk.calls].count("login") == 2
    sdk2 = FakeSDK(candles=[expired, expired])
    with pytest.raises(FeedAuthError):
        feed_with(sdk2).candles("NSE", "1", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:16"))
    assert [c[0] for c in sdk2.calls].count("login") == 2


def test_rate_limit_and_other_errors_are_classified():
    class DataException(Exception):
        pass

    with pytest.raises(FeedRateLimited):
        feed_with(FakeSDK(candles=[DataException("Access denied because of exceeding access rate")])).candles("NSE", "1", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:16"))
    with pytest.raises(FeedError, match="Invalid Symbol"):
        feed_with(FakeSDK(candles=[{"status": False, "message": "Invalid Symbol", "errorcode": "AB1007"}])).candles("NSE", "1", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:16"))
    with pytest.raises(FeedError) as e:
        feed_with(FakeSDK(candles=[ConnectionError(f"timeout calling with {FAKE.password}")])).candles("NSE", "1", "1m", T("2024-11-04 09:15"), T("2024-11-04 09:16"))
    assert FAKE.password not in str(e.value)


def test_fetch_range_splits_long_windows_and_dedupes():
    sdk = FakeSDK(candles=[{"status": True, "data": [["2024-10-01T09:15:00+05:30", 1, 1, 1, 1, 0]]}] * 3)
    f = feed_with(sdk)
    out = f.fetch_range("NSE", "1", "1m", T("2024-10-01 09:15"), T("2024-12-05 15:30"))      # 65 days of 1m data: >1 request
    n = sum(c[0] == "candles" for c in sdk.calls)
    assert n >= 3 and len(out) == 1
    for c in (c for c in sdk.calls if c[0] == "candles"):
        span = T(c[1]["todate"]) - T(c[1]["fromdate"])
        assert span <= pd.Timedelta(days=30)


# ------------------------------------------------------------------------------------ no secret can reach output (real SDK, fake network)
def _files_containing(root, needles):
    hits = []
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes().decode("utf-8", "ignore")
            hits += [(str(path.relative_to(root)), n) for n in needles if n in data]
    return hits


def test_real_sdk_failed_login_leaks_nothing_to_screen_logs_or_disk(monkeypatch, capsys, caplog, tmp_path):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: type("R", (), {"text": "1.2.3.4"})())   # the SDK asks ipify at import
    pytest.importorskip("logzero")
    pytest.importorskip("websocket")
    pytest.importorskip("SmartApi")
    code_seen = []

    def fake_request(method, url, data=None, headers=None, **kw):
        code_seen.append(json.loads(data).get("totp"))
        raise requests.ConnectionError(f"boom {url} {headers} {data}")

    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.chdir(tmp_path)                                                      # where the SDK would drop logs/<date>/app.log
    caplog.set_level(logging.DEBUG)
    f = AngelFeed(FAKE, sleep=lambda s: None, now=lambda: 1_700_000_005.0)           # default factory = the real SDK
    with pytest.raises(FeedAuthError) as e:
        f.login()
    out = capsys.readouterr()
    everything = out.out + out.err + caplog.text + str(e.value)
    secrets = [*FAKE.secrets(), *code_seen]
    assert code_seen                                                                  # the real SDK code path really ran
    for secret in secrets:
        assert secret and secret not in everything, f"{secret!r} leaked to the screen"
    assert _files_containing(tmp_path, secrets) == []                                 # ... and nothing written to disk
    assert not (tmp_path / "logs").exists()                                           # not even the empty log folder


def test_stock_sdk_writes_secrets_to_screen_and_a_log_file_which_is_why_we_neutralise_it(tmp_path):
    """Control experiment in a clean interpreter: the SDK used directly leaks the PIN to stderr AND to disk."""
    pytest.importorskip("logzero")
    pytest.importorskip("websocket")
    pytest.importorskip("SmartApi")
    script = textwrap.dedent("""
        import requests
        requests.get = lambda *a, **k: type("R", (), {"text": "1.2.3.4"})()
        requests.request = lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down"))
        from SmartApi import SmartConnect
        try:
            SmartConnect(api_key="KEY-abc123XYZ").generateSession("A1234567", "9876PIN", "123456")
        except Exception:
            pass
    """)
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert "9876PIN" in r.stdout + r.stderr
    assert ("logs/", "9876PIN") in [(h[0].split("/")[0] + "/", h[1]) for h in _files_containing(tmp_path, ["9876PIN"])]


# ------------------------------------------------------------------------------------ sink + ingestor on SQLite
class FakeFeed:
    """Serves a fixed series like the real API: includes the bar still in progress, nothing for holidays."""

    def __init__(self, series, holidays=()):
        self.series, self.holidays, self.calls = series, {T(h) for h in holidays}, []

    def fetch_range(self, exchange, token, tf, start, end):
        self.calls.append((tf, start, end))
        s = self.series[~self.series.index.normalize().isin(self.holidays)]
        if tf == "1m":
            return s.loc[start:end]
        if tf == "1d":
            d = resample_ohlc(s, "1d")
            return d.loc[start.normalize():end]
        raise AssertionError(tf)

    def close(self):
        pass


class Clock:
    def __init__(self, now):
        self.now = T(now)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += pd.Timedelta(seconds=seconds)


def make_sink(tmp_path, preload_until=None, name="live.db"):
    eng = sa.create_engine(f"sqlite:///{tmp_path/name}")
    with eng.begin() as c:
        c.execute(sa.text("create table ohlcv (datetime DATETIME, symbol TEXT, timeframe TEXT, open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume INTEGER)"))
    s = Settings(source="postgres", db_url="sqlite://", symbol="NIFTY", single_table="ohlcv",
                 columns=ColumnMap(ts="datetime", timeframe="timeframe", symbol="symbol"),
                 timeframe_values={"1m": "1m", "5m": "5m", "15m": "15m", "1d": "1d"})
    sink = SqlSink(s, engine=eng)
    if preload_until is not None:
        m = SERIES.loc[:preload_until]
        sink.insert("1m", m)
        for tf, mins in (("5m", 5), ("15m", 15)):                # only buckets that were complete at that moment
            r = resample_ohlc(m, tf)
            sink.insert(tf, r[r.index + pd.Timedelta(minutes=mins) <= m.index[-1] + clock.MINUTE])
        sink.insert("1d", resample_ohlc(SERIES.loc[: pd.Timestamp(preload_until).normalize() - pd.Timedelta(days=1)], "1d"))
    return sink


def stored(sink, tf):
    return sink.src.load(tf, limit=None, start=T("2000-01-01"))


def test_sink_inserts_only_new_rows_with_symbol_and_timeframe_labels(tmp_path):
    sink = make_sink(tmp_path)
    df = M1.iloc[:10]
    assert sink.insert("1m", df, dry_run=True) == 10 and stored(sink, "1m").empty         # dry run writes nothing
    assert sink.insert("1m", df) == 10
    assert sink.insert("1m", M1.iloc[5:15]) == 5                                          # only the 5 unseen bars
    assert sink.insert("1m", M1.iloc[:15]) == 0                                           # idempotent
    got = stored(sink, "1m")
    assert len(got) == 15 and got.close.round(2).equals(M1.close.iloc[:15].round(2))
    raw = pd.read_sql("select distinct symbol, timeframe from ohlcv", sink.engine)
    assert raw.values.tolist() == [["NIFTY", "1m"]]


def test_sink_never_issues_anything_but_select_and_insert(tmp_path):
    sink = make_sink(tmp_path)
    statements = []
    sa.event.listen(sink.engine, "before_cursor_execute", lambda c, cur, stmt, *a: statements.append(stmt.strip().split()[0].upper()))
    sink.insert("1m", M1.iloc[:20])
    sink.insert("1m", M1.iloc[10:30])
    assert set(statements) <= {"SELECT", "INSERT", "PRAGMA", "BEGIN", "COMMIT"}, statements
    assert "UPDATE" not in statements and "DELETE" not in statements


def run_ingestor(tmp_path, now, preload_until=None, **kw):
    clk = Clock(now)
    sink = kw.pop("sink", None) or make_sink(tmp_path, preload_until)
    feed = kw.pop("feed", None) or FakeFeed(SERIES)
    ing = Ingestor(feed, sink, exchange="NSE", token="99926000", now_fn=clk, sleep=clk.sleep, log=lambda m: None, **kw)
    return ing, sink, feed, clk


def test_catch_up_inserts_only_closed_bars_for_every_timeframe(tmp_path):
    ing, sink, feed, _ = run_ingestor(tmp_path, "2024-11-04 10:07:30", preload_until="2024-11-04 09:40")
    res = ing.sync_once()
    last1 = stored(sink, "1m").index[-1]
    assert last1 == T("2024-11-04 10:06")                                                 # 10:07 bar still forming: excluded
    assert stored(sink, "5m").index[-1] == T("2024-11-04 10:00")                          # 10:05 bucket not finished
    assert stored(sink, "15m").index[-1] == T("2024-11-04 09:45")
    want = resample_ohlc(SERIES, "5m").loc[: T("2024-11-04 10:00")]
    got = stored(sink, "5m")
    assert (got.index == want.index).all()
    assert (got[["open", "high", "low", "close"]] - want[["open", "high", "low", "close"]]).abs().max().max() < 0.006
    assert (got["volume"] - want["volume"]).abs().max() <= 0.5 + 1e-9                    # BIGINT column rounds the demo's fractional volumes
    assert res.inserted["1m"] == 26 and ing.sync_once().total == 0                        # second pass: nothing new


def test_time_moving_on_appends_the_next_bars(tmp_path):
    ing, sink, _, clk = run_ingestor(tmp_path, "2024-11-04 10:07:30", preload_until="2024-11-04 10:00")
    ing.sync_once()
    clk.now = T("2024-11-04 10:16:10")
    res = ing.sync_once()
    assert stored(sink, "1m").index[-1] == T("2024-11-04 10:15") and res.inserted["5m"] == 2 and res.inserted["15m"] == 1
    assert stored(sink, "15m").index[-1] == T("2024-11-04 10:00") and stored(sink, "15m").shape[0] == len(resample_ohlc(SERIES, "15m").loc[: T("2024-11-04 10:00")])


def test_daily_bar_only_after_the_close_and_holidays_are_not_refetched(tmp_path):
    feed = FakeFeed(SERIES, holidays=["2024-11-05"])
    ing, sink, _, clk = run_ingestor(tmp_path, "2024-11-04 15:20", preload_until="2024-11-04 15:00", feed=feed)
    ing.sync_once()
    assert T("2024-11-04") not in stored(sink, "1d").index                               # session not over yet
    clk.now = T("2024-11-04 15:31")
    ing.sync_once()
    assert stored(sink, "1d").index[-1] == T("2024-11-04")
    clk.now = T("2024-11-06 15:31")
    ing.sync_once()                                                                      # 5 Nov was a holiday: no bar, no error
    assert list(stored(sink, "1d").index[-2:]) == [T("2024-11-04"), T("2024-11-06")]
    before = sum(c[0] == "1d" for c in feed.calls)
    ing.sync_once(); ing.sync_once()
    assert sum(c[0] == "1d" for c in feed.calls) == before                               # not asked again for the holiday


def test_dry_run_writes_nothing_but_reports(tmp_path):
    ing, sink, _, _ = run_ingestor(tmp_path, "2024-11-04 10:30", preload_until="2024-11-04 09:40", dry_run=True)
    before = len(stored(sink, "1m"))
    res = ing.sync_once()
    assert res.inserted["1m"] > 0 and len(stored(sink, "1m")) == before


def test_backfill_is_bounded(tmp_path):
    ing, _, feed, _ = run_ingestor(tmp_path, "2024-11-06 10:00", max_backfill_days=1)
    ing.sync_once()
    first_1m_start = next(c[1] for c in feed.calls if c[0] == "1m")
    assert first_1m_start >= T("2024-11-05 00:00")


def test_transient_failures_are_survived_auth_failures_are_not(tmp_path):
    ing, _, feed, clk = run_ingestor(tmp_path, "2024-11-04 10:30", preload_until="2024-11-04 09:40")
    calls = {"n": 0}
    orig = feed.fetch_range

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FeedError("network blip")
        return orig(*a, **k)

    feed.fetch_range = flaky
    assert ing.safe_sync(0) == 1 and ing.safe_sync(1) == 0                                # one failure, then recovery resets the count
    feed.fetch_range = lambda *a, **k: (_ for _ in ()).throw(FeedAuthError("session could not be renewed"))
    with pytest.raises(FeedAuthError):
        ing.safe_sync(0)
    feed.fetch_range = lambda *a, **k: (_ for _ in ()).throw(FeedError("down"))
    with pytest.raises(FeedError, match="10 consecutive"):
        ing.safe_sync(9)


def test_run_forever_idles_outside_market_hours_without_calling_the_feed(tmp_path):
    ing, _, feed, clk = run_ingestor(tmp_path, "2024-11-02 12:00", preload_until="2024-11-01 15:29")   # Saturday
    ing.run_forever(max_loops=0)
    startup_calls = len(feed.calls)
    ing.run_forever(max_loops=5)
    assert startup_calls >= 1 and sum(c[0] == "1m" for c in feed.calls) == 2             # one catch-up per start, nothing while idle


def test_run_forever_polls_once_a_minute_during_the_session(tmp_path):
    ing, sink, feed, clk = run_ingestor(tmp_path, "2024-11-04 10:00:10", preload_until="2024-11-04 09:58")
    ing.run_forever(max_loops=3)
    assert stored(sink, "1m").index[-1] >= T("2024-11-04 10:01")
    polls = [c for c in feed.calls if c[0] == "1m"]
    assert len(polls) == 4 and all(b[2] - a[2] >= pd.Timedelta(seconds=50) for a, b in zip(polls[1:], polls[2:]))


# ------------------------------------------------------------------------------------ CLI
def test_cli_live_check_without_credentials_explains_and_leaks_nothing(monkeypatch, capsys):
    from patterniq.cli import main

    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATTERNIQ_SOURCE", "demo")
    assert main(["live-check"]) == 2
    err = capsys.readouterr().err
    assert "ANGEL_API_KEY" in err and ".env" in err


# ------------------------------------------------------------------------------------ real PostgreSQL (opt-in)
PG = os.environ.get("PATTERNIQ_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set PATTERNIQ_TEST_PG_URL to run against a real PostgreSQL")
def test_sink_on_postgres_timestamptz_numeric_exact_schema():
    from patterniq.data.sql import normalize_db_url

    eng = sa.create_engine(normalize_db_url(PG))
    table = "piq_live_ohlcv"
    try:
        with eng.begin() as c:
            c.execute(sa.text(f"drop table if exists {table}"))
            c.execute(sa.text(f"""create table {table} (datetime timestamptz not null, symbol text, timeframe text,
                open numeric(12,2), high numeric(12,2), low numeric(12,2), close numeric(12,2), volume bigint)"""))
        s = Settings(source="postgres", db_url=PG, symbol="NIFTY", single_table=table,
                     columns=ColumnMap(ts="datetime", timeframe="timeframe", symbol="symbol"),
                     timeframe_values={"1m": "1m", "5m": "5m", "15m": "15m", "1d": "1d"})
        sink = SqlSink(s)
        assert sink.can_insert() is True
        assert sink.insert("1m", M1.iloc[:30]) == 30 and sink.insert("1m", M1.iloc[:40]) == 10 and sink.insert("1m", M1.iloc[:40]) == 0
        got = sink.src.load("1m", limit=None, start=T("2000-01-01"))
        assert (got.index == M1.index[:40]).all() and abs(got.close.values - M1.close.values[:40]).max() < 0.006
        raw = pd.read_sql(f"select datetime, volume from {table} order by datetime limit 1", eng)
        assert str(raw.datetime[0].tz) != "None" and raw.datetime[0].tz_convert("Asia/Kolkata").hour == 9     # stored as a real timestamptz
    finally:
        with eng.begin() as c:
            c.execute(sa.text(f"drop table if exists {table}"))


# ------------------------------------------------------------------------------------ PG_* environment -> connection URL
def test_pg_env_builds_an_escaped_url_and_explicit_url_wins(tmp_path, monkeypatch):
    for k in ("PATTERNIQ_DB_URL", "PG_USER", "PG_PASSWORD", "PG_HOST", "PG_PORT", "PG_DATABASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)                                    # no .env, no patterniq.toml here
    assert Settings.load().db_url is None
    monkeypatch.setenv("PG_USER", "postgres")
    assert Settings.load().db_url is None                         # a user without a password is not enough
    monkeypatch.setenv("PG_PASSWORD", "p@ss:w/rd#1")
    url = Settings.load().db_url
    parsed = sa.engine.make_url(url)
    assert parsed.username == "postgres" and parsed.password == "p@ss:w/rd#1"          # round-trips: it was escaped
    assert (parsed.host, parsed.port, parsed.database) == ("localhost", 5432, "prism")
    monkeypatch.setenv("PG_HOST", "db.example"); monkeypatch.setenv("PG_PORT", "6543"); monkeypatch.setenv("PG_DATABASE", "other")
    parsed = sa.engine.make_url(Settings.load().db_url)
    assert (parsed.host, parsed.port, parsed.database) == ("db.example", 6543, "other")
    monkeypatch.setenv("PATTERNIQ_DB_URL", "postgresql://explicit/db")
    assert Settings.load().db_url == "postgresql://explicit/db"


def test_dotenv_next_to_the_working_directory_is_picked_up(tmp_path, monkeypatch):
    for k in ("PATTERNIQ_DB_URL", "PG_USER", "PG_PASSWORD", "PG_HOST", "PG_PORT", "PG_DATABASE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text("PG_USER=bob\nPG_PASSWORD=secret\nPG_DATABASE=mydb\n")
    monkeypatch.chdir(tmp_path)
    parsed = sa.engine.make_url(Settings.load().db_url)
    assert (parsed.username, parsed.database) == ("bob", "mydb")
    for k in ("PG_USER", "PG_PASSWORD", "PG_DATABASE"):
        monkeypatch.delenv(k, raising=False)
