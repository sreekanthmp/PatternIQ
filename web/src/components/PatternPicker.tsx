import type { Category, PatternInfo } from "../types";

const CAT_LABEL: Record<Category, string> = {
  candlestick: "Candlesticks",
  chart: "Chart patterns",
  level: "Support / resistance",
  structure: "Trend structure",
};

interface Props {
  patterns: PatternInfo[];
  hidden: string[];
  onChange: (hidden: string[]) => void;
}

export default function PatternPicker({ patterns, hidden, onChange }: Props) {
  const hiddenSet = new Set(hidden);
  const toggle = (name: string) =>
    onChange(hiddenSet.has(name) ? hidden.filter((h) => h !== name) : [...hidden, name]);
  const setGroup = (names: string[], show: boolean) => {
    const rest = hidden.filter((h) => !names.includes(h));
    onChange(show ? rest : [...rest, ...names]);
  };
  const groups = (Object.keys(CAT_LABEL) as Category[]).map((c) => ({
    cat: c,
    items: patterns.filter((p) => p.category === c),
  }));
  const enabled = patterns.length - patterns.filter((p) => hiddenSet.has(p.name)).length;

  return (
    <details className="picker">
      <summary className="btn">
        Patterns <span className="muted">{enabled}/{patterns.length}</span>
      </summary>
      <div className="picker-pop" role="group" aria-label="Choose which patterns are shown">
        {groups.map(({ cat, items }) => {
          const names = items.map((i) => i.name);
          return (
            <fieldset key={cat}>
              <legend>
                {CAT_LABEL[cat]}
                <span className="picker-links">
                  <button type="button" className="link" onClick={() => setGroup(names, true)}>all</button>
                  <button type="button" className="link" onClick={() => setGroup(names, false)}>none</button>
                </span>
              </legend>
              {items.map((p) => (
                <label key={p.name} title={p.description}>
                  <input type="checkbox" checked={!hiddenSet.has(p.name)} onChange={() => toggle(p.name)} />
                  {p.label}
                </label>
              ))}
            </fieldset>
          );
        })}
      </div>
    </details>
  );
}
