export type Direction = "bullish" | "bearish" | "neutral";
export type Category = "candlestick" | "chart" | "level" | "structure";
export type Timeframe = "1m" | "5m" | "15m" | "1d";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Seg {
  t1: number;
  p1: number;
  t2: number;
  p2: number;
  role: string;
}

export interface Detection {
  id: string;
  pattern: string;
  label: string;
  category: Category;
  direction: Direction;
  status: "confirmed" | "forming";
  quality: number;
  start_time: number;
  end_time: number;
  time: number;
  price: number;
  points: { time: number; price: number; label: string }[];
  lines: Seg[];
  meta: Record<string, number | string | boolean | null>;
}

export interface LevelOut {
  price: number;
  touches: number;
  strength: number;
  kind: "support" | "resistance";
  from_time: number;
}

export interface Swing {
  time: number;
  price: number;
  kind: "H" | "L";
  label: string;
}

export interface Trend {
  state: "uptrend" | "downtrend" | "range";
  last_swing_high: number | null;
  last_swing_low: number | null;
  invalidation: number | null;
  close: number | null;
}

export interface Reference {
  date?: string;
  pdh?: number;
  pdl?: number;
  pdc?: number;
}

export interface AnalysisMeta {
  symbol: string;
  timeframe: Timeframe;
  source: string;
  bars: number;
  first: string;
  last: string;
  has_earlier: boolean;
  has_later: boolean;
  data_first: string | null;
  data_last: string | null;
  now: number;
  market_open: boolean;
}

export interface Analysis {
  candles: Candle[];
  detections: Detection[];
  levels: LevelOut[];
  swings: Swing[];
  trend: Trend;
  reference: Reference;
  atr: number | null;
  meta: AnalysisMeta;
}

export interface PatternInfo {
  name: string;
  label: string;
  category: Category;
  direction: Direction | "dynamic";
  description: string;
}

export interface Meta {
  symbol: string;
  source: string;
  display_tz: string;
  timeframes: { timeframe: Timeframe; first: string; last: string; count: number | null }[];
  categories: Category[];
  patterns: PatternInfo[];
}

export interface StatRow {
  pattern: string;
  label: string;
  category: Category | "";
  direction: Direction;
  n: number;
  horizon: number;
  hit_rate: number;
  mean_atr: number;
  median_atr: number;
  edge_atr: number;
  edge_se: number | null;
}

export interface Stats {
  timeframe: Timeframe;
  bars: number;
  first: string;
  last: string;
  horizons: number[];
  rows: StatRow[];
}
