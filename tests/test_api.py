import json

import pytest
from fastapi.testclient import TestClient

from patterniq.api import create_app
from patterniq.config import Settings


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(Settings(source="demo", demo_days=60)))


def test_meta_lists_timeframes_and_pattern_catalog(client):
    m = client.get("/api/meta").json()
    assert [t["timeframe"] for t in m["timeframes"]] == ["1m", "5m", "15m", "1d"]
    assert m["source"] == "demo" and len(m["patterns"]) >= 40
    assert {"name", "label", "category", "direction", "description"} <= set(m["patterns"][0])


def test_analysis_payload_shape_and_consistency(client):
    r = client.get("/api/analysis", params={"timeframe": "15m", "bars": 300, "min_quality": 0.5})
    assert r.status_code == 200
    j = r.json()
    json.dumps(j)
    assert len(j["candles"]) == 300 and j["meta"]["bars"] == 300
    times = [c["time"] for c in j["candles"]]
    assert times == sorted(set(times))
    first, last = times[0], times[-1]
    assert j["detections"], "demo data should contain detections"
    for d in j["detections"]:
        assert d["quality"] >= 0.5 and first <= d["time"] <= last     # nothing from the warm-up region leaks out
        for ln in d["lines"]:
            assert ln["t1"] >= first and ln["t1"] < ln["t2"] or ln["t1"] == ln["t2"]
    assert j["trend"]["state"] in {"uptrend", "downtrend", "range"}
    assert j["reference"]["pdh"] >= j["reference"]["pdl"]


def test_chart_times_are_exchange_local_wall_clock(client):
    j = client.get("/api/analysis", params={"timeframe": "15m", "bars": 50}).json()
    import datetime as dt

    t = dt.datetime.fromtimestamp(j["candles"][0]["time"], dt.timezone.utc)
    assert (t.hour, t.minute) >= (9, 15) and t.hour < 16               # 09:15-15:30 session read as UTC


def test_paging_end_start_and_date_only(client):
    a = client.get("/api/analysis", params={"timeframe": "15m", "bars": 100}).json()
    mid = a["meta"]["first"].replace(" ", "T")
    earlier = client.get("/api/analysis", params={"timeframe": "15m", "bars": 100, "end": mid}).json()
    assert earlier["candles"][-1]["time"] == a["candles"][0]["time"] and earlier["meta"]["has_later"]
    later = client.get("/api/analysis", params={"timeframe": "15m", "bars": 100, "start": mid}).json()
    assert later["candles"][0]["time"] == a["candles"][0]["time"]
    day = a["meta"]["last"][:10]
    assert client.get("/api/analysis", params={"timeframe": "15m", "bars": 50, "end": day}).json()["meta"]["last"].startswith(day)


@pytest.mark.parametrize("params,code", [
    ({"timeframe": "7m"}, 404), ({"end": "garbage"}, 400), ({"categories": "nope"}, 400), ({"bars": 10}, 422),
    ({"min_quality": 2}, 422), ({"start": "2024-12-01", "end": "2024-12-02"}, 400), ({"end": "1999-01-01"}, 404),
])
def test_bad_requests_are_rejected_cleanly(client, params, code):
    assert client.get("/api/analysis", params=params).status_code == code


def test_category_filter(client):
    j = client.get("/api/analysis", params={"timeframe": "15m", "bars": 300, "categories": "chart,structure"}).json()
    assert {d["category"] for d in j["detections"]} <= {"chart", "structure"}


def test_stats_endpoint(client):
    j = client.get("/api/stats", params={"timeframe": "15m", "bars": 3000, "horizons": "5,10", "min_n": 5}).json()
    assert j["horizons"] == [5, 10] and j["rows"]
    assert {"label", "n", "hit_rate", "edge_atr", "edge_se"} <= set(j["rows"][0])
    assert client.get("/api/stats", params={"horizons": "a,b"}).status_code == 400
    assert client.get("/api/stats", params={"horizons": "0"}).status_code == 400


def test_database_errors_do_not_leak_credentials():
    app = create_app(Settings(source="postgres", db_url="postgresql://user:SECRETPW@127.0.0.1:1/nope", tables={"15m": "x"}))
    r = TestClient(app).get("/api/meta")
    assert r.status_code == 502 and "SECRETPW" not in r.text


def test_mixed_instruments_surface_a_readable_error_not_wrong_data(tmp_path):
    import pandas as pd
    import sqlalchemy as sa
    from patterniq.config import ColumnMap
    from patterniq.data import DemoSource
    from patterniq.data.sql import SqlSource

    ref = DemoSource(Settings(source="demo", demo_days=20)).frame("15m")
    eng = sa.create_engine(f"sqlite:///{tmp_path/'m.db'}")
    d = ref.reset_index().rename(columns={"time": "datetime"})
    pd.concat([d.assign(symbol="A", timeframe="15m"), d.assign(symbol="B", timeframe="15m")]).to_sql("ohlcv", eng, index=False)
    s = Settings(source="postgres", db_url="sqlite://", single_table="ohlcv", columns=ColumnMap(ts="datetime", timeframe="timeframe"))
    r = TestClient(create_app(s, source=SqlSource(s, engine=eng))).get("/api/analysis", params={"timeframe": "15m"})
    assert r.status_code == 500 and "symbol" in r.json()["detail"] and "several instruments" in r.json()["detail"]
