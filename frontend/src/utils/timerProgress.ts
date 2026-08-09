import type { Timeframe } from "../types/workspace";

const SECONDS_PER_TIMEFRAME: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 5 * 60,
  "15m": 15 * 60,
  "1h": 60 * 60,
  "4h": 4 * 60 * 60,
  "1d": 24 * 60 * 60,
};

// Phase 1 has no live Market Clock yet (that lands in Phase 2 as
// `core/market_clock.py` on the backend), so there's no authoritative
// "current bar's open timestamp" to measure real elapsed time against.
// This approximates bar progress from wall-clock time modulo the timeframe's
// duration instead — good enough to demonstrate the radar sweep now, and
// meant to be swapped for the real current bar's actual elapsed/remaining
// time once live candles exist. Nothing that consumes this function's output
// (TimerBadge) needs to change when that swap happens.
export function currentBarProgressPct(timeframe: Timeframe, nowMs: number = Date.now()): number {
  const durationSec = SECONDS_PER_TIMEFRAME[timeframe];
  const elapsedSec = (nowMs / 1000) % durationSec;
  return (elapsedSec / durationSec) * 100;
}
