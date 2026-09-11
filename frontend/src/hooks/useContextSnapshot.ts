import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchContextSnapshot,
  type CalendarProviderWireShape,
  type FundamentalsProviderWireShape,
  type NewsProviderWireShape,
} from "../services/api-client";

// Normalized, display-ready shapes — same camelCase-flattening split
// useOpportunities.ts's own Opportunity type establishes for its wire
// shape.
export interface CalendarContext {
  session: "pre_market" | "open" | "lunch" | "power_hour" | "after_hours" | "closed";
  isMarketOpen: boolean;
  isHalfDay: boolean;
  minutesSinceOpen: number;
  fedDay: boolean;
  tradingDay: string;
}

export interface FundamentalsContext {
  sector: string | null;
  industry: string | null;
  profileUpdatedAt: string | null;
  marketCap: number | null;
  marketCapUpdatedAt: string | null;
  revenueTtm: number | null;
  netIncomeTtm: number | null;
  operatingCashFlowTtm: number | null;
  financialsPeriod: string | null;
  financialsUpdatedAt: string | null;
  nextEarningsDate: string | null;
  earningsUpdatedAt: string | null;
}

export interface NewsContext {
  present: boolean;
  count15m: number;
  recencySeconds: number | null;
  importance: "high" | "medium" | "low" | "none";
}

function normalizeCalendar(wire: CalendarProviderWireShape): CalendarContext {
  return {
    session: wire.session,
    isMarketOpen: wire.is_market_open,
    isHalfDay: wire.is_half_day,
    minutesSinceOpen: wire.minutes_since_open,
    fedDay: wire.fed_day,
    tradingDay: wire.trading_day,
  };
}

function normalizeFundamentals(wire: FundamentalsProviderWireShape): FundamentalsContext {
  return {
    sector: wire.sector,
    industry: wire.industry,
    profileUpdatedAt: wire.profile_updated_at,
    marketCap: wire.market_cap,
    marketCapUpdatedAt: wire.market_cap_updated_at,
    revenueTtm: wire.revenue_ttm,
    netIncomeTtm: wire.net_income_ttm,
    operatingCashFlowTtm: wire.operating_cash_flow_ttm,
    financialsPeriod: wire.financials_period,
    financialsUpdatedAt: wire.financials_updated_at,
    nextEarningsDate: wire.next_earnings_date,
    earningsUpdatedAt: wire.earnings_updated_at,
  };
}

function normalizeNews(wire: NewsProviderWireShape): NewsContext {
  return {
    present: wire.present,
    count15m: wire.count_15m,
    recencySeconds: wire.recency_seconds,
    importance: wire.importance,
  };
}

// Poll interval reasoning, stated explicitly (same discipline
// useStrategyOutcomes.ts's own docstring used for its no-WS choice):
// there is no `EventType.CONTEXT_CHANGED` entry in `EVENT_TO_CHANNEL`
// (backend/app/api/websocket/channels.py, confirmed against the live
// file) — no WebSocket channel relays Context updates to the frontend
// today, so a push-based refresh isn't available, unlike
// useOpportunities.ts/useOpportunityConflicts.ts.
//
// Unlike `strategy_outcomes` (useStrategyOutcomes.ts's own case — no
// live writer exists AT ALL yet, so a plain fetch-on-mount is honest),
// Context genuinely changes on its own while a connector panel stays
// open: session boundaries several times a trading day
// (pre_market -> open -> lunch -> power_hour -> after_hours -> closed)
// plus a 15-minute Fundamentals/News timer per symbol (both cadences
// straight from context_engine/engine.py's own module docstring,
// decisions #92/#96). `ContextEngine.get_snapshot()` is also a
// synchronous, in-memory, zero-I/O read by its own docstring — cheap
// enough that a light poll costs effectively nothing server-side.
// 60 seconds is tighter than the 15-minute provider cadence (so most
// ticks return byte-identical data — fine, since the read is free) but
// loose enough to not hammer anything, and it catches a session-boundary
// transition within a minute of it happening without needing to compute
// the client's own guess at exactly when the next boundary lands.
const POLL_INTERVAL_MS = 60_000;

export interface UseContextSnapshotResult {
  calendar: CalendarContext | null;
  // Global path's own evaluated_at (session-boundary loop) — kept
  // separate from symbolEvaluatedAt below on purpose: the two paths
  // refresh on genuinely different cadences (engine.py's decision #96
  // split), so merging them into one timestamp would imply a shared
  // freshness that doesn't exist.
  calendarEvaluatedAt: string | null;
  fundamentals: FundamentalsContext | null;
  news: NewsContext | null;
  symbolEvaluatedAt: string | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * Backend read side for Context Engine — GET /intelligence/context
 * (confirmed decision #98), unsurfaced anywhere in the UI until this
 * task. `symbol` is optional: omit it for Calendar (market-wide) only,
 * same scope InfoTab.tsx's GeneralContent has; pass it to also get that
 * ticker's Fundamentals/News (per-symbol providers, decision #96).
 *
 * Named `useContextSnapshot`, deliberately not `useContext` — the
 * latter would collide with React's own built-in hook of that name.
 *
 * Fetch-based + lightly polled, NOT WebSocket-push — see
 * POLL_INTERVAL_MS's own comment above for the full reasoning on both
 * halves of that choice.
 *
 * Uses a `mountedRef` guard rather than the single per-effect
 * `cancelled` closure useOpportunities.ts/useStrategyOutcomes.ts use:
 * those hooks' `load()` only ever runs once per effect invocation (on
 * mount/symbol-change, or once per manual refetch), so one closed-over
 * flag covers it. This hook's `load()` also fires repeatedly from
 * `setInterval` within a single effect run, so a flag scoped to just
 * the initial call wouldn't cover every later poll tick — `mountedRef`
 * covers all of them uniformly for as long as the component stays
 * mounted.
 */
export function useContextSnapshot(symbol?: string): UseContextSnapshotResult {
  const [calendar, setCalendar] = useState<CalendarContext | null>(null);
  const [calendarEvaluatedAt, setCalendarEvaluatedAt] = useState<string | null>(null);
  const [fundamentals, setFundamentals] = useState<FundamentalsContext | null>(null);
  const [news, setNews] = useState<NewsContext | null>(null);
  const [symbolEvaluatedAt, setSymbolEvaluatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(() => {
    fetchContextSnapshot(symbolRef.current)
      .then((wire) => {
        if (!mountedRef.current) return;

        const calendarWire = wire.global.providers.calendar;
        setCalendar(calendarWire ? normalizeCalendar(calendarWire) : null);
        setCalendarEvaluatedAt(wire.global.evaluated_at);

        const sym = symbolRef.current;
        const symbolEntry = sym ? wire.symbols[sym] : undefined;
        setFundamentals(
          symbolEntry?.providers.fundamentals ? normalizeFundamentals(symbolEntry.providers.fundamentals) : null,
        );
        setNews(symbolEntry?.providers.news ? normalizeNews(symbolEntry.providers.news) : null);
        setSymbolEvaluatedAt(symbolEntry?.evaluated_at ?? null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useContextSnapshot(${symbolRef.current ?? "global"}): fetch failed — ${detail}`);
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    // Symbol change clears the previous ticker's Fundamentals/News
    // immediately, same "don't show stale data mid-switch" convention
    // useOpportunities.ts/useOpportunityConflicts.ts use. Calendar isn't
    // symbol-scoped, so it's deliberately NOT cleared here.
    setFundamentals(null);
    setNews(null);
    setSymbolEvaluatedAt(null);

    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [symbol, load]);

  return { calendar, calendarEvaluatedAt, fundamentals, news, symbolEvaluatedAt, loading, refetch: load };
}
