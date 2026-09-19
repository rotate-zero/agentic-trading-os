/**
 * DST-aware America/New_York wall-clock <-> UTC conversion for the IBKR
 * backtest date-range inputs (decision #152).
 *
 * No date/timezone library is a dependency anywhere in this codebase
 * (confirmed directly against frontend/package.json before writing this) —
 * this file is a small, self-contained utility scoped to BacktestPanel.tsx's
 * one real need (interpreting two `datetime-local` values as US/Eastern
 * wall-clock times, since the backend's own POST /backtest/run/ibkr
 * docstring frames session timing entirely in ET, "a regular session" /
 * "04:00-20:00 extended session"), not a general-purpose module promoted
 * ahead of a second real caller.
 *
 * Approach: the standard "double conversion" trick. `Intl.DateTimeFormat`
 * with `timeZone: "America/New_York"` already knows every real DST
 * transition (including future ones, since it reads the browser's own tz
 * database) — no hardcoded EST/EDT offset or transition dates are baked in
 * here, which is also why callers must never display a fixed "EST" or
 * "EDT" label: the correct offset depends on the selected date.
 */

const MARKET_TIME_ZONE = "America/New_York";

const ET_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: MARKET_TIME_ZONE,
  hourCycle: "h23",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

interface WallClock {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function formatAsEtWallClock(utcMs: number): WallClock {
  const parts = ET_FORMATTER.formatToParts(new Date(utcMs));
  const map: Record<string, string> = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = p.value;
  }
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour),
    minute: Number(map.minute),
    second: Number(map.second),
  };
}

/** Parses a `datetime-local` input value ("YYYY-MM-DDTHH:mm[:ss]"). Rejects
 * anything that doesn't match the shape outright — deliberately not lenient,
 * since a silently-misparsed value would defeat the whole point of the
 * round-trip check below. */
function parseDateTimeLocal(value: string): WallClock | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!m) return null;
  const [, yStr, moStr, dStr, hStr, miStr, sStr] = m;
  const wc: WallClock = {
    year: Number(yStr),
    month: Number(moStr),
    day: Number(dStr),
    hour: Number(hStr),
    minute: Number(miStr),
    second: sStr ? Number(sStr) : 0,
  };
  if (wc.month < 1 || wc.month > 12) return null;
  if (wc.day < 1 || wc.day > 31) return null;
  if (wc.hour < 0 || wc.hour > 23) return null;
  if (wc.minute < 0 || wc.minute > 59) return null;
  if (wc.second < 0 || wc.second > 59) return null;
  return wc;
}

export type EtConversionResult =
  | { ok: true; utcMs: number; iso: string }
  | { ok: false; reason: "invalid_format" | "nonexistent_or_ambiguous" };

/**
 * Interprets a `datetime-local` value as US/Eastern wall-clock and returns
 * the equivalent UTC instant, DST-aware.
 *
 * **Round-trip validated, not just computed.** After deriving a candidate
 * UTC instant, this re-formats that instant back into an ET wall clock and
 * requires it to match the original input exactly. This is what actually
 * catches a wall-clock time that doesn't exist (the spring-forward gap,
 * e.g. 2:30 AM on the "spring forward" date) — the naive double-conversion
 * alone would otherwise silently return some nearby instant instead of
 * refusing. Known, accepted limitation: a wall-clock time that occurs
 * TWICE (the one hour repeated every "fall back" date) round-trips
 * successfully and resolves to one of the two valid instants without
 * signaling the ambiguity — genuinely indistinguishable from a bare
 * wall-clock string with no library here, and out of scope for what this
 * task needs (backtest ranges targeting market hours never land on that
 * one ambiguous hour in practice).
 */
/** offset(instant) := (ET wall-clock reading at `instant`, read back as if
 * those numbers were themselves a UTC timestamp) minus `instant` itself.
 * Negative for a zone behind UTC (-5h for EST, -4h for EDT) — the standard
 * sign convention. This is the one primitive the fixed-point solve below
 * is built on. */
function offsetMsAt(instantMs: number): number {
  const wc = formatAsEtWallClock(instantMs);
  const wallAsUtcMs = Date.UTC(wc.year, wc.month - 1, wc.day, wc.hour, wc.minute, wc.second);
  return wallAsUtcMs - instantMs;
}

export function etWallClockToUtc(value: string): EtConversionResult {
  const wc = parseDateTimeLocal(value);
  if (!wc) return { ok: false, reason: "invalid_format" };

  // Solving `actualUtcMs = naiveUtcMs - offset(actualUtcMs)` — a
  // fixed-point equation, not a one-shot guess. A single-pass "guess the
  // offset from the naive instant, apply once" (the textbook-simple
  // version of this trick) is WRONG within a couple of hours of a DST
  // transition: the offset that applies to the naive guess can differ
  // from the offset that actually applies at the resolved instant,
  // producing a false "nonexistent" rejection for a wall-clock time that
  // genuinely exists (caught by this file's own round-trip check during
  // testing — e.g. 03:00 on the US spring-forward date, the first valid
  // minute right after the 2:00am gap, was being rejected by the naive
  // version). Two iterations converge for every real case: the offset is
  // piecewise-constant and only ever changes at the (at most one) DST
  // transition, so refining once against the first candidate's own
  // offset is enough.
  const naiveUtcMs = Date.UTC(wc.year, wc.month - 1, wc.day, wc.hour, wc.minute, wc.second);
  let candidateUtcMs = naiveUtcMs - offsetMsAt(naiveUtcMs);
  candidateUtcMs = naiveUtcMs - offsetMsAt(candidateUtcMs);

  const verify = formatAsEtWallClock(candidateUtcMs);
  const roundTripMatches =
    verify.year === wc.year &&
    verify.month === wc.month &&
    verify.day === wc.day &&
    verify.hour === wc.hour &&
    verify.minute === wc.minute &&
    verify.second === wc.second;

  if (!roundTripMatches) {
    return { ok: false, reason: "nonexistent_or_ambiguous" };
  }

  return { ok: true, utcMs: candidateUtcMs, iso: new Date(candidateUtcMs).toISOString() };
}

/** Today's calendar date in America/New_York, as "YYYY-MM-DD" — used to
 * initialize the date-range inputs when the person hasn't picked a date
 * yet (preset buttons, first render of IBKR mode). Deliberately just the
 * ET calendar date with no notion of whether it's a real trading day —
 * weekends/exchange holidays are explicitly not validated here (backend
 * and IBKR remain authoritative for data availability; a non-trading date
 * surfaces as this route's own honest `ibkr_no_data` 400, not a client-side
 * guess). */
export function currentEtCalendarDate(): string {
  const wc = formatAsEtWallClock(Date.now());
  const mm = String(wc.month).padStart(2, "0");
  const dd = String(wc.day).padStart(2, "0");
  return `${wc.year}-${mm}-${dd}`;
}
