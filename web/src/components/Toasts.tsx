import { dirSymbol, fmtPrice } from "../format";
import type { Detection } from "../types";

export interface Toast {
  id: string;
  det: Detection;
  timeframe: string;
}

interface Props {
  toasts: Toast[];
  onOpen: (d: Detection) => void;
  onDismiss: (id: string) => void;
}

export default function Toasts({ toasts, onOpen, onDismiss }: Props) {
  if (!toasts.length) return null;
  return (
    <div className="toasts" role="status" aria-live="polite" aria-label="New patterns">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.det.direction}`}>
          <button type="button" className="toast-main" onClick={() => onOpen(t.det)}>
            <strong>{dirSymbol(t.det.direction)} {t.det.label}</strong>
            <span className="small">
              {t.timeframe} · {fmtPrice(t.det.price)} · quality {Math.round(t.det.quality * 100)}%
              {t.det.meta.at_level !== undefined ? " · at level" : ""}
            </span>
          </button>
          <button type="button" className="toast-x" aria-label="Dismiss" onClick={() => onDismiss(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
