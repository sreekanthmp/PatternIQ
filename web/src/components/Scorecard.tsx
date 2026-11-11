import { useEffect, useMemo, useState } from "react";
import { getJSON } from "../api";
import type { Stats, StatRow, Timeframe } from "../types";

type SortKey = "label" | "n" | "hit_rate" | "mean_atr" | "edge_atr";

/** Edge more than two standard errors from zero (still not proof: many patterns are tested at once). */
const significant = (r: StatRow) => r.edge_se !== null && Math.abs(r.edge_atr) > 2 * r.edge_se;

export default function Scorecard({ timeframe, isDemo }: { timeframe: Timeframe; isDemo: boolean }) {
  const [bars, setBars] = useState(20000);
  const [horizon, setHorizon] = useState(10);
  const [minN, setMinN] = useState(30);
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "edge_atr", dir: -1 });

  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    setErr(null);
    getJSON<Stats>("/api/stats", { timeframe, bars, min_n: 5 }, ctl.signal)
      .then((s) => {
        setStats(s);
        setLoading(false);
        if (!s.horizons.includes(horizon) && s.horizons.length) setHorizon(s.horizons[Math.floor(s.horizons.length / 2)]);
      })
      .catch((e: Error) => {
        if (e.name !== "AbortError") {
          setErr(e.message);
          setLoading(false);
        }
      });
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe, bars]);

  const rows = useMemo(() => {
    const r = (stats?.rows ?? []).filter((x) => x.horizon === horizon && x.n >= minN);
    const k = sort.key;
    return [...r].sort((a: StatRow, b: StatRow) =>
      k === "label" ? sort.dir * a.label.localeCompare(b.label) : sort.dir * ((a[k] as number) - (b[k] as number)),
    );
  }, [stats, horizon, minN, sort]);

  const head = (key: SortKey, label: string) => (
    <th scope="col" aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="link" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  return (
    <div className="scorecard">
      {isDemo && <p className="warn">Demo data is random: any numbers below are noise by construction.</p>}
      <div className="row wrap gap">
        <label className="field">Window
          <select value={bars} onChange={(e) => setBars(Number(e.target.value))}>
            {[5000, 20000, 50000, 100000].map((b) => <option key={b} value={b}>last {b.toLocaleString()} bars</option>)}
          </select>
        </label>
        <label className="field">Hold
          <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
            {(stats?.horizons ?? [5, 10, 20]).map((h) => <option key={h} value={h}>{h} bars</option>)}
          </select>
        </label>
        <label className="field">Min samples
          <select value={minN} onChange={(e) => setMinN(Number(e.target.value))}>
            {[10, 30, 100].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      {loading && <p className="muted">Computing…</p>}
      {err && <p className="error" role="alert">{err}</p>}
      {stats && !loading && (
        <>
          <p className="muted small">{stats.bars.toLocaleString()} bars · {stats.first.slice(0, 10)} → {stats.last.slice(0, 10)} · entry = open of the bar after confirmation</p>
          {rows.length === 0 ? (
            <p className="empty">No pattern has {minN}+ samples in this window.</p>
          ) : (
            <table>
              <thead>
                <tr>{head("label", "Pattern")}{head("n", "n")}{head("hit_rate", "Win %")}{head("mean_atr", "Avg ATR")}{head("edge_atr", "Edge")}</tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.pattern}-${r.direction}`} className={significant(r) ? "" : "dim"}>
                    <td><span className={r.direction}>{r.direction === "bullish" ? "▲" : "▼"}</span> {r.label}</td>
                    <td>{r.n}</td>
                    <td>{(r.hit_rate * 100).toFixed(0)}</td>
                    <td>{r.mean_atr.toFixed(2)}</td>
                    <td className={significant(r) ? (r.edge_atr > 0 ? "bullish" : "bearish") : ""}>
                      {r.edge_atr > 0 ? "+" : ""}{r.edge_atr.toFixed(2)}
                      <span className="muted small"> ±{(r.edge_se ?? 0).toFixed(2)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted small">
            Moves are in ATR units in the pattern's direction. <b>Edge</b> = average move minus the same-direction
            average over all bars (drift removed), <b>±</b> its standard error. Greyed rows are within 2 standard errors
            of zero, i.e. indistinguishable from chance. With this many patterns tested, a few will still look
            significant by luck. Signals cluster and samples are small: a sanity check on your own data, not a backtest
            and not a recommendation.
          </p>
        </>
      )}
    </div>
  );
}
