import type { Direction } from "./types";

// Chart times are exchange-local wall clock encoded as if UTC, so format with UTC fields.
const pad = (n: number) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtTime(t: number, intraday: boolean): string {
  const d = new Date(t * 1000);
  const date = `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  return intraday ? `${date} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` : date;
}

export function fmtIso(t: number): string {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
}

export function fmtPrice(p: number | null | undefined, digits = 2): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "–";
  return p.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export const dirSymbol = (d: Direction) => (d === "bullish" ? "▲" : d === "bearish" ? "▼" : "◆");

export const ABBR: Record<string, string> = {
  doji: "Doji", dragonfly_doji: "DrgDoji", gravestone_doji: "GrvDoji", hammer: "Hammer",
  hanging_man: "HangMan", inverted_hammer: "InvHam", shooting_star: "ShootStar",
  bullish_marubozu: "Maru", bearish_marubozu: "Maru", bullish_engulfing: "Engulf",
  bearish_engulfing: "Engulf", piercing_line: "Pierce", dark_cloud_cover: "DarkCloud",
  bullish_harami: "Harami", bearish_harami: "Harami", tweezer_bottom: "TwzBot", tweezer_top: "TwzTop",
  morning_star: "MornStar", evening_star: "EveStar", three_white_soldiers: "3Soldiers",
  three_black_crows: "3Crows", double_top: "DblTop", double_bottom: "DblBot",
  head_and_shoulders: "H&S", inverse_head_and_shoulders: "iH&S", bull_flag: "BullFlag",
  bear_flag: "BearFlag", ascending_triangle: "AscTri", descending_triangle: "DescTri",
  symmetrical_triangle: "SymTri", rising_wedge: "RisWedge", falling_wedge: "FallWedge",
  rectangle: "Range", ascending_channel: "AscChan", descending_channel: "DescChan",
  resistance_breakout: "R-Break", support_breakdown: "S-Break", resistance_rejection: "R-Reject",
  support_rejection: "S-Reject", bos_bullish: "BOS", bos_bearish: "BOS", choch_bullish: "CHoCH",
  choch_bearish: "CHoCH",
};

export const abbr = (pattern: string) => ABBR[pattern] ?? pattern;

export function safeGet<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function safeSet(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable (private mode etc.): settings just won't persist */
  }
}
