import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getJSON } from "./api";
import ChartPane, { type Focus, type Theme } from "./components/ChartPane";
import NowCard from "./components/NowCard";
import PatternPicker from "./components/PatternPicker";
import Scorecard from "./components/Scorecard";
import SignalList, { DetectionDetail } from "./components/SignalList";
import Toasts, { type Toast } from "./components/Toasts";
import { fmtIso, fmtPrice, fmtTime, safeGet, safeSet } from "./format";
import { askNotifyPermission, beep, feedStatus, primeAudio, systemNotify } from "./live";
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
  live: boolean;
  alerts: boolean;
}

const ALL_CATS: Category[] = ["candlestick", "chart", "level", "structure"];
const CAT_LABEL: Record<Category, string> = {
  candlestick: "Candles", chart: "Chart patterns", level: "S/R events", structure: "Structure",
};
const DEFAULTS: Prefs = {
  tf: "15m", bars: 600, minQuality: 0.6, cats: ALL_CATS, hidden: [],
  showLevels: true, showSwings: false, showForming: true, live: false, alerts: false,
};
const MIN_QUALITY_FLOOR = 0.35; // the server is asked for everything above this; the slider filters locally
const LIVE_REFRESH_MS = 15000;
const TOAST_MS = 20000;

/** The one place that decides whether a detection is shown (and so whether it may raise an alert). */
function passes(d: Detection, p: Prefs): boolean {
  return (
    p.cats.includes(d.category) &&
    !p.hidden.includes(d.pattern) &&
    d.quality >= p.minQuality &&
    (p.showForming || d.status === "confirmed")
  );
}

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
  const [loadedKey, setLoadedKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<Candle | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [tab, setTab] = useState<"signals" | "scorecard">("signals");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [notifyNote, setNotifyNote] = useState<string | null>(null);

  const patch = useCallback((p: Partial<Prefs>) => setPrefs((old) => ({ ...old, ...p })), []);
  useEffect(() => safeSet("piq.prefs", prefs), [prefs]);
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;

  const requestKey = `${prefs.tf}|${prefs.bars}|${nav.end ?? ""}|${nav.start ?? ""}|${reload}`;
  const following = !nav.end && !nav.start;

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

  // ---- analysis for the requested window ----
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
        setLoadedKey(requestKey);
        setError(null);
        setLiveError(null);
        setLoading(false);
        setSelectedId(null);
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        setError(e.message);
        setLoading(false);
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, requestKey]);

  // ---- live mode: re-read the latest window every few seconds, without disturbing the view ----
  useEffect(() => {
    if (!prefs.live || !meta || !following) return;
    const ctl = new AbortController();
    const tick = () =>
      getJSON<Analysis>("/api/analysis", { timeframe: prefs.tf, bars: prefs.bars, min_quality: MIN_QUALITY_FLOOR }, ctl.signal)
        .then((a) => {
          setData(a);
          setLiveError(null);
        })
        .catch((e: Error) => e.name !== "AbortError" && setLiveError(e.message));
    const id = window.setInterval(tick, LIVE_REFRESH_MS);
    return () => {
      window.clearInterval(id);
      ctl.abort();
    };
  }, [prefs.live, prefs.tf, prefs.bars, meta, following]);

  const patternMap = useMemo(() => new Map((meta?.patterns ?? []).map((p) => [p.name, p])), [meta]);
  const intraday = prefs.tf !== "1d";
  const isDemo = meta?.source === "demo";

  const visible = useMemo(
    () => (data?.detections ?? []).filter((d) => passes(d, prefs)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, prefs.cats, prefs.hidden, prefs.minQuality, prefs.showForming],
  );

  const selected = useMemo(() => visible.find((d) => d.id === selectedId) ?? null, [visible, selectedId]);

  const select = useCallback((d: Detection) => {
    setSelectedId(d.id);
    setTab("signals");
    setFocus({ from: d.start_time, to: d.time, nonce: Date.now() });
  }, []);

  // ---- alerts: a *confirmed* pattern that was not in the previous snapshot of the same window ----
  const seenRef = useRef<Set<string>>(new Set());
  const seenKeyRef = useRef("");
  useEffect(() => {
    if (!data) return;
    const ids = new Set(data.detections.map((d) => d.id));
    if (seenKeyRef.current !== loadedKey) {
      seenKeyRef.current = loadedKey; // a different window was loaded: take a baseline, alert on nothing
      seenRef.current = ids;
      return;
    }
    const fresh = data.detections.filter((d) => !seenRef.current.has(d.id) && d.status === "confirmed");
    seenRef.current = ids;
    const p = prefsRef.current;
    if (!p.live || !p.alerts) return;
    const alertable = fresh.filter((d) => passes(d, p));
    if (!alertable.length) return;
    const tf = data.meta.timeframe;
    setToasts((old) => [...alertable.map((d) => ({ id: `${d.id}@${Date.now()}`, det: d, timeframe: tf })), ...old].slice(0, 6));
    beep();
    for (const d of alertable) systemNotify(`${d.label} (${tf})`, `${d.direction} · ${fmtPrice(d.price)} · quality ${Math.round(d.quality * 100)}%`);
  }, [data, loadedKey]);

  useEffect(() => {
    if (!toasts.length) return;
    const id = window.setTimeout(() => setToasts((old) => old.slice(0, -1)), TOAST_MS);
    return () => window.clearTimeout(id);
  }, [toasts]);

  const toggleAlerts = async (on: boolean) => {
    patch({ alerts: on });
    setNotifyNote(null);
    if (!on) return;
    primeAudio();
    const perm = await askNotifyPermission();
    if (perm === "denied") setNotifyNote("Desktop notifications are blocked for this site; alerts will still show on screen and beep.");
    if (perm === "unsupported") setNotifyNote("This browser has no desktop notifications; alerts will show on screen and beep.");
  };

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
  const feed = data ? feedStatus(data.meta) : null;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden><path d="M5 22l6-8 5 5 6-10 5 7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <h1>PatternIQ</h1>
          <span className="symbol">{data?.meta.symbol ?? meta?.symbol ?? ""}</span>
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
      {notifyNote && <div className="banner" role="status">{notifyNote}</div>}

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
        <span className="sep" aria-hidden />
        <label className="check live-toggle" title={`Re-read the latest bars every ${LIVE_REFRESH_MS / 1000} s`}>
          <input type="checkbox" checked={prefs.live} onChange={(e) => patch({ live: e.target.checked })} />Live
        </label>
        <label className="check" title="Toast, beep and desktop notification when a new confirmed pattern appears (needs Live; uses the filters above)">
          <input type="checkbox" checked={prefs.alerts} onChange={(e) => void toggleAlerts(e.target.checked)} disabled={!prefs.live} />Alerts
        </label>
        {feed && (
          <span className={`feed ${feed.state}`} role="status" data-state={feed.state} title="Whether new candles are arriving in the database">
            <i aria-hidden /> {feed.text}
            {prefs.live && !following && " · live paused while browsing history"}
            {liveError && " · reconnecting…"}
          </span>
        )}
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
            selectedId={selectedId} datasetKey={loadedKey} focus={focus} intraday={intraday} theme={theme}
            onHover={setHover} onPickTime={pickTime}
          />
          {loading && <div className="loading" role="status">Loading…</div>}
          <Toasts toasts={toasts} onOpen={(d) => select(d)} onDismiss={(id) => setToasts((old) => old.filter((t) => t.id !== id))} />
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
