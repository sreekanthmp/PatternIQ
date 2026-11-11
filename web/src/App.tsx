import { useCallback, useEffect, useMemo, useState } from "react";
import { getJSON } from "./api";
import ChartPane, { type Focus, type Theme } from "./components/ChartPane";
import NowCard from "./components/NowCard";
import PatternPicker from "./components/PatternPicker";
import Scorecard from "./components/Scorecard";
import SignalList, { DetectionDetail } from "./components/SignalList";
import { fmtIso, fmtPrice, fmtTime, safeGet, safeSet } from "./format";
import type { Analysis, Candle, Category, Detection, Meta, Timeframe } from "./types";

interface Prefs {
  tf: Timeframe;
  bars: number;
  minQuality: number;
  cats: Category[];
  hidden: string[];
  showLevels: boolean;
  showSwings: boolean;
  showForming: boolean;
}

const ALL_CATS: Category[] = ["candlestick", "chart", "level", "structure"];
const CAT_LABEL: Record<Category, string> = {
  candlestick: "Candles", chart: "Chart patterns", level: "S/R events", structure: "Structure",
};
const DEFAULTS: Prefs = {
  tf: "15m", bars: 600, minQuality: 0.6, cats: ALL_CATS, hidden: [],
  showLevels: true, showSwings: false, showForming: true,
};
const MIN_QUALITY_FLOOR = 0.35; // the server is asked for everything above this; the slider filters locally

function useTheme(): Theme {
  const q = "(prefers-color-scheme: light)";
  const [light, setLight] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setLight(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return light ? "light" : "dark";
}

export default function App() {
  const theme = useTheme();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [prefs, setPrefs] = useState<Prefs>(() => ({ ...DEFAULTS, ...safeGet<Partial<Prefs>>("piq.prefs", {}) }));
  const [nav, setNav] = useState<{ end?: string; start?: string }>({});
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<Candle | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [tab, setTab] = useState<"signals" | "scorecard">("signals");

  const patch = useCallback((p: Partial<Prefs>) => setPrefs((old) => ({ ...old, ...p })), []);
  useEffect(() => safeSet("piq.prefs", prefs), [prefs]);

  // ---- metadata (available timeframes, pattern catalogue) ----
  useEffect(() => {
    const ctl = new AbortController();
    getJSON<Meta>("/api/meta", {}, ctl.signal)
      .then((m) => {
        setMeta(m);
        const have = m.timeframes.map((t) => t.timeframe);
        setPrefs((p) => (have.includes(p.tf) || !have.length ? p : { ...p, tf: have.includes("15m") ? "15m" : have[0] }));
      })
      .catch((e: Error) => e.name !== "AbortError" && setError(`Cannot reach the PatternIQ server: ${e.message}`));
    return () => ctl.abort();
  }, []);

  // ---- analysis for the current window ----
  useEffect(() => {
    if (!meta) return;
    const ctl = new AbortController();
    setLoading(true);
    getJSON<Analysis>(
      "/api/analysis",
      { timeframe: prefs.tf, bars: prefs.bars, min_quality: MIN_QUALITY_FLOOR, ...nav },
      ctl.signal,
    )
      .then((a) => {
        setData(a);
        setError(null);
        setLoading(false);
        setSelectedId(null);
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        setError(e.message);
        setLoading(false);
      });
    return () => ctl.abort();
  }, [meta, prefs.tf, prefs.bars, nav, reload]);

  const patternMap = useMemo(() => new Map((meta?.patterns ?? []).map((p) => [p.name, p])), [meta]);
  const intraday = prefs.tf !== "1d";
  const isDemo = meta?.source === "demo";

  const visible = useMemo(() => {
    const hidden = new Set(prefs.hidden);
    return (data?.detections ?? []).filter(
      (d) =>
        prefs.cats.includes(d.category) &&
        !hidden.has(d.pattern) &&
        d.quality >= prefs.minQuality &&
        (prefs.showForming || d.status === "confirmed"),
    );
  }, [data, prefs.cats, prefs.hidden, prefs.minQuality, prefs.showForming]);

  const selected = useMemo(() => visible.find((d) => d.id === selectedId) ?? null, [visible, selectedId]);
  const datasetKey = `${prefs.tf}|${prefs.bars}|${nav.end ?? ""}|${nav.start ?? ""}|${reload}|${data?.meta.first ?? ""}`;

  const select = useCallback((d: Detection) => {
    setSelectedId(d.id);
    setTab("signals");
    setFocus({ from: d.start_time, to: d.time, nonce: Date.now() });
  }, []);

  const pickTime = useCallback(
    (t: number) => {
      const here = visible.filter((d) => d.time === t).sort((a, b) => b.quality - a.quality);
      setSelectedId(here[0]?.id ?? null);
    },
    [visible],
  );

  const candles = data?.candles ?? [];
  const mid = candles[Math.floor(candles.length / 2)];
  const shown = hover ?? candles[candles.length - 1] ?? null;
  const prev = shown ? candles[candles.findIndex((c) => c.time === shown.time) - 1] : undefined;
  const chg = shown && prev ? ((shown.close - prev.close) / prev.close) * 100 : null;
  const tfs = meta?.timeframes ?? [];

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden><path d="M5 22l6-8 5 5 6-10 5 7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <h1>PatternIQ</h1>
          <span className="symbol">{meta?.symbol ?? ""}</span>
        </div>
        <div className="seg" role="group" aria-label="Timeframe">
          {tfs.map((t) => (
            <button key={t.timeframe} type="button" aria-pressed={prefs.tf === t.timeframe}
              onClick={() => { patch({ tf: t.timeframe }); setNav({}); }}>
              {t.timeframe}
            </button>
          ))}
        </div>
        <div className="row gap nav" role="group" aria-label="Navigate in time">
          <button type="button" className="btn" disabled={!data?.meta.has_earlier || !mid}
            onClick={() => mid && setNav({ end: fmtIso(mid.time) })} title="Show earlier bars">◀</button>
          <button type="button" className="btn" disabled={!data?.meta.has_later || !mid}
            onClick={() => mid && setNav({ start: fmtIso(mid.time) })} title="Show later bars">▶</button>
          <button type="button" className="btn" onClick={() => { setNav({}); setReload((r) => r + 1); }} title="Jump to the latest bar and refresh">Latest</button>
          <label className="field inline">Go to
            <input type="date" value={nav.end?.slice(0, 10) ?? ""}
              min={data?.meta.data_first?.slice(0, 10)} max={data?.meta.data_last?.slice(0, 10)}
              onChange={(e) => e.target.value && setNav({ end: e.target.value })} />
          </label>
          <label className="field inline">Bars
            <select value={prefs.bars} onChange={(e) => patch({ bars: Number(e.target.value) })}>
              {[300, 600, 1500, 3000].map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
        </div>
      </header>

      {isDemo && (
        <div className="banner" role="status">
          Showing <b>synthetic demo data</b>, not your market. Point PatternIQ at your database (see README) to use real Nifty candles.
        </div>
      )}
      {error && <div className="banner error" role="alert">{error}</div>}

      <div className="filters">
        <div className="chips" role="group" aria-label="Pattern categories">
          {ALL_CATS.map((c) => (
            <button key={c} type="button" className="chip" aria-pressed={prefs.cats.includes(c)}
              onClick={() => patch({ cats: prefs.cats.includes(c) ? prefs.cats.filter((x) => x !== c) : [...prefs.cats, c] })}>
              {CAT_LABEL[c]}
            </button>
          ))}
        </div>
        <label className="field inline slider">Min quality {Math.round(prefs.minQuality * 100)}%
          <input type="range" min={MIN_QUALITY_FLOOR} max={0.95} step={0.05} value={prefs.minQuality}
            onChange={(e) => patch({ minQuality: Number(e.target.value) })} />
        </label>
        <label className="check"><input type="checkbox" checked={prefs.showLevels} onChange={(e) => patch({ showLevels: e.target.checked })} />Levels</label>
        <label className="check"><input type="checkbox" checked={prefs.showSwings} onChange={(e) => patch({ showSwings: e.target.checked })} />Swings</label>
        <label className="check"><input type="checkbox" checked={prefs.showForming} onChange={(e) => patch({ showForming: e.target.checked })} />Forming</label>
        <PatternPicker patterns={meta?.patterns ?? []} hidden={prefs.hidden} onChange={(hidden) => patch({ hidden })} />
      </div>

      <main className="main">
        <section className="chart-wrap" aria-busy={loading}>
          <div className="legend" aria-live="off">
            {shown ? (
              <>
                <b>{data?.meta.symbol} · {prefs.tf}</b>
                <span>{fmtTime(shown.time, intraday)}</span>
                <span>O {fmtPrice(shown.open)}</span><span>H {fmtPrice(shown.high)}</span>
                <span>L {fmtPrice(shown.low)}</span><span>C {fmtPrice(shown.close)}</span>
                {chg !== null && <span className={chg >= 0 ? "bullish" : "bearish"}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span>}
                <span className="muted">{visible.length} patterns shown</span>
              </>
            ) : <span className="muted">{loading ? "Loading…" : "No data"}</span>}
          </div>
          <ChartPane
            candles={candles} detections={visible} levels={data?.levels ?? []} reference={data?.reference ?? {}}
            swings={data?.swings ?? []} showLevels={prefs.showLevels} showSwings={prefs.showSwings}
            selectedId={selectedId} datasetKey={datasetKey} focus={focus} intraday={intraday} theme={theme}
            onHover={setHover} onPickTime={pickTime}
          />
          {loading && <div className="loading" role="status">Loading…</div>}
        </section>

        <aside className="side">
          <div className="tabs" role="tablist">
            <button role="tab" type="button" aria-selected={tab === "signals"} onClick={() => setTab("signals")}>Signals</button>
            <button role="tab" type="button" aria-selected={tab === "scorecard"} onClick={() => setTab("scorecard")}>Scorecard</button>
          </div>
          <div className="side-body" role="tabpanel">
            {tab === "signals" ? (
              <>
                {data && <NowCard trend={data.trend} levels={data.levels} reference={data.reference} atr={data.atr}
                  last={candles.length ? candles[candles.length - 1].close : null} />}
                {selected && <DetectionDetail d={selected} patterns={patternMap} intraday={intraday} />}
                <SignalList detections={visible} selectedId={selectedId} intraday={intraday} patterns={patternMap} onSelect={select} />
              </>
            ) : (
              <Scorecard timeframe={prefs.tf} isDemo={!!isDemo} />
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
