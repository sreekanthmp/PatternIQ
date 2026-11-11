import { fmtPrice } from "../format";
import type { LevelOut, Reference, Trend } from "../types";

interface Props {
  trend: Trend;
  levels: LevelOut[];
  reference: Reference;
  atr: number | null;
  last: number | null;
}

const STATE_LABEL = { uptrend: "Uptrend", downtrend: "Downtrend", range: "Range / transition" } as const;

export default function NowCard({ trend, levels, reference, atr, last }: Props) {
  const below = last === null ? [] : levels.filter((l) => l.price <= last).sort((a, b) => b.price - a.price);
  const above = last === null ? [] : levels.filter((l) => l.price > last).sort((a, b) => a.price - b.price);
  const dist = (p: number) => {
    if (last === null) return "";
    const pts = Math.abs(p - last);
    return atr ? `${fmtPrice(pts, 0)} pts away · ${(pts / atr).toFixed(1)} ATR` : `${fmtPrice(pts, 0)} pts away`;
  };
  const levelCell = (lv: LevelOut | undefined) =>
    lv ? (
      <>
        {fmtPrice(lv.price)}
        <span className="sub">tested ×{lv.touches} · {dist(lv.price)}</span>
      </>
    ) : (
      "–"
    );
  return (
    <section className="card now" aria-label="Current market state">
      <div className="row">
        <span className={`badge ${trend.state}`}>{STATE_LABEL[trend.state]}</span>
        <span className="muted small">from swing highs / lows</span>
      </div>
      <dl className="kv">
        <dt>Last swing high</dt><dd>{fmtPrice(trend.last_swing_high)}</dd>
        <dt>Last swing low</dt><dd>{fmtPrice(trend.last_swing_low)}</dd>
        {trend.invalidation !== null && (
          <>
            <dt>Trend breaks at</dt><dd>{fmtPrice(trend.invalidation)}</dd>
          </>
        )}
        <dt>Resistance above</dt>
        <dd>{levelCell(above[0])}</dd>
        <dt>Support below</dt>
        <dd>{levelCell(below[0])}</dd>
        {reference.pdh !== undefined && (
          <>
            <dt>Prev day H / L / C</dt>
            <dd>{fmtPrice(reference.pdh, 0)} / {fmtPrice(reference.pdl, 0)} / {fmtPrice(reference.pdc, 0)}</dd>
          </>
        )}
        <dt>ATR(14)</dt><dd>{fmtPrice(atr)}</dd>
      </dl>
    </section>
  );
}
