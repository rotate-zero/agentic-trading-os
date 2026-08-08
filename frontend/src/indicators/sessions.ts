// Shared session-boundary helper for every indicator that needs to know
// "which US trading day does this candle belong to" and "was it pre-market
// or regular hours" — previousDayLevels, premarketLevels, camarillaPivots,
// vpoc, and vwap all build on this rather than each doing their own
// UTC-to-Eastern math.
//
// Uses Intl.DateTimeFormat with an explicit IANA zone (America/New_York)
// rather than a fixed UTC offset, since that's the only way to get correct
// results across the EST/EDT daylight-saving transition without adding a
// timezone library dependency — a fixed offset would be off by an hour for
// roughly half the year.
import type { Candle } from "../types/market";

export interface EasternWallClock {
  dateKey: string; // "YYYY-MM-DD" in America/New_York — the trading-day bucket
  hour: number;
  minute: number;
}

const easternFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function toEasternWallClock(unixSeconds: number): EasternWallClock {
  const parts = easternFormatter.formatToParts(new Date(unixSeconds * 1000));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  const year = get("year");
  const month = get("month");
  const day = get("day");
  // Some environments format midnight as "24" instead of "00" under hour12:false — normalize.
  const hourRaw = get("hour");
  const hour = hourRaw === "24" ? 0 : Number(hourRaw);
  const minute = Number(get("minute"));
  return { dateKey: `${year}-${month}-${day}`, hour, minute };
}

// Regular NYSE/NASDAQ session: 9:30 AM - 4:00 PM ET.
export function isRegularSession(c: EasternWallClock): boolean {
  const minutesOfDay = c.hour * 60 + c.minute;
  return minutesOfDay >= 9 * 60 + 30 && minutesOfDay < 16 * 60;
}

// Standard pre-market window: 4:00 AM - 9:30 AM ET.
export function isPremarketSession(c: EasternWallClock): boolean {
  const minutesOfDay = c.hour * 60 + c.minute;
  return minutesOfDay >= 4 * 60 && minutesOfDay < 9 * 60 + 30;
}

// Every distinct America/New_York calendar date present in candles, in
// first-seen (chronological) order — candles are assumed already
// chronological, which every candle source in this app (mock, resampled
// live, future backend) guarantees.
function distinctTradingDays(candles: Candle[]): string[] {
  const seen = new Set<string>();
  const days: string[] = [];
  for (const c of candles) {
    const key = toEasternWallClock(c.time).dateKey;
    if (!seen.has(key)) {
      seen.add(key);
      days.push(key);
    }
  }
  return days;
}

// The most recent calendar day that isn't today's still-forming day — i.e.
// the last fully-elapsed session, which is what "previous day" means for
// PDC/PDH/PDL, Camarilla, and VPOC.
//
// IMPORTANT — currently returns [] against this app's real data: mock
// candles span ~4 hours and live candles are backfill-less per
// confirmed-decisions.md #39, so there is no confirmed source of prior-day
// history yet. Every consumer of this function already treats an empty
// result as "not enough data" and simply doesn't render, rather than
// guessing — these levels will start appearing automatically the moment
// real multi-day history exists (or a live session naturally runs long
// enough to cross a day boundary), with no code change needed here.
export function getPreviousTradingDayCandles(candles: Candle[]): Candle[] {
  const days = distinctTradingDays(candles);
  if (days.length < 2) return [];
  const previousDay = days[days.length - 2];
  return candles.filter((c) => toEasternWallClock(c.time).dateKey === previousDay);
}

// Today's (most recent calendar day's) pre-market candles so far — a
// developing range, not a fixed one, since pre-market for "today" is still
// forming until 9:30 ET.
export function getTodayPremarketCandles(candles: Candle[]): Candle[] {
  const days = distinctTradingDays(candles);
  if (days.length === 0) return [];
  const today = days[days.length - 1];
  return candles.filter((c) => {
    const wc = toEasternWallClock(c.time);
    return wc.dateKey === today && isPremarketSession(wc);
  });
}
