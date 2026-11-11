import type { AnalysisMeta, Timeframe } from "./types";

const BAR_SEC: Record<Timeframe, number> = { "1m": 60, "5m": 300, "15m": 900, "1d": 86400 };
const SESSION_OPEN_SEC = 9 * 3600 + 15 * 60;

export type FeedState = "live" | "stale" | "closed" | "daily";
export interface FeedStatus {
  state: FeedState;
  text: string;
}

const hhmm = (epoch: number) => {
  const d = new Date(epoch * 1000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
};

/**
 * Is new data arriving? Bars are written once they close, so while the market is open the newest bar should
 * never be more than one bar length (plus a little slack for polling and ingest) behind the server clock.
 */
export function feedStatus(meta: AnalysisMeta): FeedStatus {
  const lastStart = Date.parse(meta.last.replace(" ", "T") + "Z") / 1000;
  const barSec = BAR_SEC[meta.timeframe];
  if (meta.timeframe === "1d") return { state: "daily", text: `last daily bar ${meta.last.slice(0, 10)}` };
  if (!meta.market_open) return { state: "closed", text: `market closed · last bar ${hhmm(lastStart)}` };
  const slack = barSec + 150;
  if (meta.now % 86400 - SESSION_OPEN_SEC < slack) return { state: "live", text: "session just opened · waiting for the first bar" };
  const behind = meta.now - (lastStart + barSec);
  if (behind <= slack) return { state: "live", text: `live · last bar ${hhmm(lastStart)}` };
  return { state: "stale", text: `no new bar for ${Math.round(behind / 60)} min · is "patterniq live" running?` };
}

let audio: AudioContext | null = null;

/** Create the audio context from a user gesture (browsers block it otherwise). */
export function primeAudio(): void {
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctor && !audio) audio = new Ctor();
    void audio?.resume();
  } catch {
    /* no audio available: alerts still show on screen */
  }
}

export function beep(): void {
  try {
    if (!audio) return;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.05;
    osc.connect(gain);
    gain.connect(audio.destination);
    osc.start();
    osc.stop(audio.currentTime + 0.18);
  } catch {
    /* ignore */
  }
}

export async function askNotifyPermission(): Promise<NotificationPermission | "unsupported"> {
  if (typeof Notification === "undefined") return "unsupported";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

export function systemNotify(title: string, body: string): void {
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") new Notification(title, { body });
  } catch {
    /* some browsers only allow notifications from a service worker */
  }
}
