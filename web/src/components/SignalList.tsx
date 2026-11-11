import { dirSymbol, fmtPrice, fmtTime } from "../format";
import type { Detection, PatternInfo } from "../types";

interface Props {
  detections: Detection[];
  selectedId: string | null;
  intraday: boolean;
  patterns: Map<string, PatternInfo>;
  onSelect: (d: Detection) => void;
}

const META_LABELS: [string, string][] = [
  ["neckline", "Neckline"], ["level", "Level"], ["at_level", "At level"], ["target", "Measured target"], ["stop", "Invalidation"],
];

export function DetectionDetail({ d, patterns, intraday }: { d: Detection; patterns: Map<string, PatternInfo>; intraday: boolean }) {
  const info = patterns.get(d.pattern);
  return (
    <section className="card detail" aria-label="Selected pattern">
      <div className="row between">
        <strong className={d.direction}>{dirSymbol(d.direction)} {d.label}</strong>
        <span className="muted small">{d.status === "forming" ? "forming (unconfirmed)" : "confirmed"}</span>
      </div>
      <p className="small">{info?.description}</p>
      <dl className="kv">
        <dt>Confirmed on bar close</dt><dd>{fmtTime(d.time, intraday)}</dd>
        <dt>Price at signal</dt><dd>{fmtPrice(d.price)}</dd>
        <dt>Quality</dt><dd>{Math.round(d.quality * 100)}%</dd>
        {META_LABELS.filter(([k]) => d.meta[k] !== undefined && d.meta[k] !== null).map(([k, label]) => (
          <div className="kv-row" key={k}>
            <dt>{label}</dt><dd>{fmtPrice(Number(d.meta[k]))}</dd>
          </div>
        ))}
        {typeof d.meta.bias === "string" && d.meta.bias !== "neutral" && (
          <>
            <dt>Usual bias</dt>
            <dd>{d.meta.bias}{d.meta.with_bias === false ? " (this one broke the other way)" : ""}</dd>
          </>
        )}
      </dl>
      <p className="muted small">Quality is a heuristic fit score (shape, trend context, level confluence), not a probability.</p>
    </section>
  );
}

export default function SignalList({ detections, selectedId, intraday, patterns, onSelect }: Props) {
  const rows = [...detections].sort((a, b) => b.time - a.time || b.quality - a.quality).slice(0, 300);
  if (!rows.length) return <p className="empty">No patterns match the current filters in this window.</p>;
  return (
    <ul className="signals" aria-label="Detected patterns, newest first">
      {rows.map((d) => (
        <li key={d.id}>
          <button
            type="button"
            className={`signal ${d.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(d)}
            aria-pressed={d.id === selectedId}
          >
            <span className={`dir ${d.direction}`} aria-hidden>{dirSymbol(d.direction)}</span>
            <span className="grow">
              <span className="name">{d.label}{d.status === "forming" ? " (forming)" : ""}</span>
              <span className="muted small">
                {fmtTime(d.time, intraday)} · {patterns.get(d.pattern)?.category}
                {d.meta.at_level !== undefined ? " · at level" : ""}
              </span>
            </span>
            <span className="q" title={`quality ${Math.round(d.quality * 100)}%`}>
              <span style={{ width: `${Math.round(d.quality * 100)}%` }} />
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
