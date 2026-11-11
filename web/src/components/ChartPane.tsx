import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { abbr } from "../format";
import type { Candle, Detection, LevelOut, Reference, Swing } from "../types";

export type Theme = "dark" | "light";

const PALETTE = {
  dark: {
    bg: "#0d1117", text: "#9da7b3", grid: "#161b22", border: "#30363d",
    up: "#26a69a", down: "#ef5350", neutral: "#8b949e", forming: "#e3b341",
    support: "#3fb950", resistance: "#f85149", ref: "#58a6ff", selected: "#ffd33d", swing: "#6e7681",
  },
  light: {
    bg: "#ffffff", text: "#57606a", grid: "#eef1f4", border: "#d0d7de",
    up: "#1a8f83", down: "#d1362f", neutral: "#6e7781", forming: "#bf8700",
    support: "#1a7f37", resistance: "#cf222e", ref: "#0969da", selected: "#bf8700", swing: "#8c959f",
  },
} as const;

export interface Focus {
  from: number;
  to: number;
  nonce: number;
}

interface Props {
  candles: Candle[];
  detections: Detection[];
  levels: LevelOut[];
  reference: Reference;
  swings: Swing[];
  showLevels: boolean;
  showSwings: boolean;
  selectedId: string | null;
  datasetKey: string;
  focus: Focus | null;
  intraday: boolean;
  theme: Theme;
  onHover: (candle: Candle | null) => void;
  onPickTime: (time: number) => void;
}

const asTime = (t: number) => t as UTCTimestamp;

function directionColor(d: Detection, theme: Theme): string {
  const p = PALETTE[theme];
  if (d.status === "forming" || d.direction === "neutral") return d.status === "forming" ? p.forming : p.neutral;
  return d.direction === "bullish" ? p.up : p.down;
}

function buildMarkers(dets: Detection[], selectedId: string | null, theme: Theme): SeriesMarker<Time>[] {
  const p = PALETTE[theme];
  const groups = new Map<string, Detection[]>();
  for (const d of dets) {
    const below = d.direction === "bullish";
    const key = `${d.time}:${below ? "b" : "a"}`;
    const g = groups.get(key);
    if (g) g.push(d);
    else groups.set(key, [d]);
  }
  const out: SeriesMarker<Time>[] = [];
  for (const [key, list] of groups) {
    list.sort((a, b) => b.quality - a.quality);
    const best = list[0];
    const selected = list.some((d) => d.id === selectedId);
    const below = key.endsWith(":b");
    out.push({
      time: asTime(best.time),
      position: below ? "belowBar" : "aboveBar",
      shape: best.direction === "bullish" ? "arrowUp" : best.direction === "bearish" ? "arrowDown" : "circle",
      color: selected ? p.selected : directionColor(best, theme),
      size: selected || best.quality >= 0.75 ? 2 : 1,
      text: abbr(best.pattern) + (best.status === "forming" ? "?" : "") + (list.length > 1 ? ` +${list.length - 1}` : ""),
    });
  }
  return out.sort((a, b) => (a.time as number) - (b.time as number));
}

export default function ChartPane(props: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const swingSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const swingMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlaysRef = useRef<ISeriesApi<"Line">[]>([]);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const lastKeyRef = useRef<string>("");
  const cb = useRef({ onHover: props.onHover, onPickTime: props.onPickTime });
  cb.current = { onHover: props.onHover, onPickTime: props.onPickTime };
  const candlesRef = useRef<Candle[]>([]);
  candlesRef.current = props.candles;

  // ---- create the chart once ----
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const p = PALETTE[props.theme];
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: p.bg }, textColor: p.text, fontSize: 12 },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: p.border, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: p.border, rightOffset: 6, timeVisible: props.intraday, secondsVisible: false },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: p.up, downColor: p.down, borderVisible: false, wickUpColor: p.up, wickDownColor: p.down,
      priceLineVisible: false,
    });
    const swing = chart.addSeries(LineSeries, {
      lineVisible: false, pointMarkersVisible: false, lastValueVisible: false, priceLineVisible: false,
      crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
    });
    chartRef.current = chart;
    candleRef.current = candle;
    swingSeriesRef.current = swing;
    markersRef.current = createSeriesMarkers(candle, []);
    swingMarkersRef.current = createSeriesMarkers(swing, []);

    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return cb.current.onHover(null);
      const d = param.seriesData.get(candle) as CandlestickData<Time> | undefined;
      if (!d) return cb.current.onHover(null);
      const t = param.time as number;
      cb.current.onHover(candlesRef.current.find((c) => c.time === t) ?? null);
    });
    chart.subscribeClick((param) => {
      if (param.time) cb.current.onPickTime(param.time as number);
    });

    return () => {
      chart.remove();
      chartRef.current = candleRef.current = swingSeriesRef.current = null;
      markersRef.current = swingMarkersRef.current = null;
      overlaysRef.current = [];
      priceLinesRef.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- theme + time axis options ----
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return;
    const p = PALETTE[props.theme];
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: p.bg }, textColor: p.text },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.border },
      timeScale: { borderColor: p.border, timeVisible: props.intraday },
    });
    candle.applyOptions({ upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down });
  }, [props.theme, props.intraday]);

  // ---- candles ----
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return;
    candle.setData(props.candles.map((c) => ({ ...c, time: asTime(c.time) })));
    if (lastKeyRef.current !== props.datasetKey && props.candles.length) {
      lastKeyRef.current = props.datasetKey;
      const n = props.candles.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 170), to: n + 6 });
    }
  }, [props.candles, props.datasetKey]);

  // ---- markers ----
  useEffect(() => {
    markersRef.current?.setMarkers(buildMarkers(props.detections, props.selectedId, props.theme));
  }, [props.detections, props.selectedId, props.theme]);

  // ---- chart-pattern geometry (+ the selected detection of any category) ----
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    for (const s of overlaysRef.current) chart.removeSeries(s);
    overlaysRef.current = [];
    const p = PALETTE[props.theme];
    for (const d of props.detections) {
      const selected = d.id === props.selectedId;
      if (d.category !== "chart" && !selected) continue;
      const base = selected ? p.selected : directionColor(d, props.theme);
      for (const seg of d.lines) {
        if (seg.t1 >= seg.t2) continue;
        const s = chart.addSeries(LineSeries, {
          color: base,
          lineWidth: selected ? 3 : 2,
          lineStyle: d.status === "forming" ? LineStyle.Dashed : seg.role === "pole" ? LineStyle.Dotted : LineStyle.Solid,
          lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        });
        s.setData([
          { time: asTime(seg.t1), value: seg.p1 },
          { time: asTime(seg.t2), value: seg.p2 },
        ]);
        overlaysRef.current.push(s);
      }
    }
  }, [props.detections, props.selectedId, props.theme]);

  // ---- support / resistance + previous-day levels ----
  useEffect(() => {
    const candle = candleRef.current;
    if (!candle) return;
    for (const l of priceLinesRef.current) candle.removePriceLine(l);
    priceLinesRef.current = [];
    const p = PALETTE[props.theme];
    if (props.showLevels) {
      for (const lv of props.levels) {
        priceLinesRef.current.push(
          candle.createPriceLine({
            price: lv.price,
            color: (lv.kind === "support" ? p.support : p.resistance) + (lv.strength >= 0.6 ? "cc" : "77"),
            lineWidth: lv.touches >= 4 ? 2 : 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: false,
            title: `${lv.kind === "support" ? "S" : "R"}×${lv.touches}`,
          }),
        );
      }
      const ref = props.reference;
      const refs: [string, number | undefined][] = [["PDH", ref.pdh], ["PDL", ref.pdl], ["PDC", ref.pdc]];
      for (const [title, price] of refs) {
        if (price === undefined) continue;
        priceLinesRef.current.push(
          candle.createPriceLine({
            price, color: p.ref, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title,
          }),
        );
      }
    }
  }, [props.levels, props.reference, props.showLevels, props.theme]);

  // ---- swing labels (HH / HL / LH / LL) ----
  useEffect(() => {
    const series = swingSeriesRef.current;
    const plugin = swingMarkersRef.current;
    if (!series || !plugin) return;
    const swings = props.showSwings ? props.swings : [];
    const uniq = new Map<number, Swing>();
    for (const s of swings) uniq.set(s.time, s);
    const ordered = [...uniq.values()].sort((a, b) => a.time - b.time);
    series.setData(ordered.map((s) => ({ time: asTime(s.time), value: s.price })));
    const p = PALETTE[props.theme];
    plugin.setMarkers(
      ordered.map((s) => ({
        time: asTime(s.time),
        position: s.kind === "H" ? ("aboveBar" as const) : ("belowBar" as const),
        shape: "circle" as const,
        color: p.swing,
        size: 0,
        text: s.label,
      })),
    );
  }, [props.swings, props.showSwings, props.theme]);

  // ---- programmatic focus (clicking a row in the signal list) ----
  useEffect(() => {
    const chart = chartRef.current;
    const f = props.focus;
    if (!chart || !f) return;
    const cs = candlesRef.current;
    if (!cs.length) return;
    const idxAt = (t: number) => {
      const i = cs.findIndex((c) => c.time >= t);
      return i < 0 ? cs.length - 1 : i;
    };
    const a = idxAt(f.from);
    const b = idxAt(f.to);
    const pad = Math.max(25, Math.round((b - a) * 0.35));
    const span = Math.max(80, b - a + 2 * pad);
    const mid = (a + b) / 2;
    chart.timeScale().setVisibleLogicalRange({ from: mid - span / 2, to: mid + span / 2 });
  }, [props.focus]);

  return <div ref={containerRef} className="chart-surface" role="img" aria-label="Candlestick chart with detected patterns" />;
}
