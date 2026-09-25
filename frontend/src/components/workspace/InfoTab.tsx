import { useMemo, useRef, useState } from "react";
import { LINK_CONNECTOR_IDS, type InfoConnectorMode } from "../../types/workspace";
import { MOCK_TICKERS } from "../../mocks/tickers";
import { useLatestPrices } from "../../hooks/useLatestPrices";
import { useOpportunities } from "../../hooks/useOpportunities";
import { useOpportunityConflicts } from "../../hooks/useOpportunityConflicts";
import { useStrategyOutcomes } from "../../hooks/useStrategyOutcomes";
import { usePerformanceAnalytics } from "../../hooks/usePerformanceAnalytics";
import { useContextSnapshot } from "../../hooks/useContextSnapshot";
import { useMarketState } from "../../hooks/useMarketState";
import { useWorldView } from "../../hooks/useWorldView";
import { AIAnalysisPanel } from "../ai-panel/AIAnalysisPanel";
import { useWorkspace } from "../../state/WorkspaceContext";
import {
  BACKTEST_STRATEGY_NAMES,
  type HourlyWinRateWireShape,
  type SessionTypeExpectancyWireShape,
} from "../../services/api-client";

const CONNECTOR_COLORS: Record<number, string> = {
  0: "#F85149",
  1: "#E3B341",
  2: "#3FB950",
  3: "#58A6FF",
  4: "#BC8CFF",
  5: "#F778BA",
  6: "#79C0FF",
  7: "#FFA657",
  8: "#7EE787",
  9: "#D2A8FF",
};

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;

// exit_filled_at formatted the same "time only" way
// AIAnalysisPanel.tsx's own formatDetectedAt does for setup_detected_at —
// this list is a recent-activity feed, not a full trade-history view, so
// same "just the clock time" treatment fits.
function formatExitTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Small additional section (decision #123) surfacing GET /intelligence/
// strategy-outcomes — global, not tied to any one connector's symbol, so
// it lives here in GeneralContent (the market-wide view) rather than
// inside ConnectorContent/AIAnalysisPanel below, which are both scoped to
// whichever single symbol a connector currently holds. Always rendered,
// including the empty case — hiding it entirely would make this
// capability harder to notice once real rows start flowing in (no
// Execution Engine/Position Monitor writes here yet — decision #120).
function RecentClosedTrades() {
  const { outcomes, loading } = useStrategyOutcomes(10);

  return (
    <div className="flex flex-col gap-1">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Recent Closed Trades</div>
      {loading && outcomes.length === 0 ? (
        <p className="p-1 text-[11px] text-text-muted">Loading…</p>
      ) : outcomes.length === 0 ? (
        <p className="p-1 text-[11px] text-text-muted">No closed trades recorded yet.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {outcomes.map((o) => (
            <div
              key={o.outcomeId}
              className="flex items-center justify-between rounded border border-base-border px-2 py-1.5"
            >
              <div>
                <div className="font-mono text-xs font-medium text-text-primary">
                  {o.symbol} <span className="text-text-muted">{o.strategyName}</span>
                </div>
                <div className="text-[10px] text-text-muted">
                  {o.direction} · {o.exitReason} · {formatExitTime(o.exitFilledAt)}
                </div>
              </div>
              <div className="text-right">
                <div className={`font-mono text-xs ${o.realizedPnl >= 0 ? "text-bull" : "text-bear"}`}>
                  {o.realizedPnl >= 0 ? "+" : ""}
                  {o.realizedPnl.toFixed(2)}
                </div>
                <div className={`font-mono text-[10px] ${o.realizedR >= 0 ? "text-bull" : "text-bear"}`}>
                  {o.realizedR >= 0 ? "+" : ""}
                  {o.realizedR.toFixed(2)}R
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Small additional section (decision #127) surfacing GET /intelligence/
// win-rate-by-hour + GET /intelligence/expectancy-by-session-type — same
// "global, not tied to any one connector's symbol" placement reasoning
// RecentClosedTrades above already establishes for this file (both read
// the same strategy_outcomes table), so it sits directly below it.
// Always rendered, including the empty case, same "don't hide it, don't
// fabricate it" posture as the rest of this file — but unlike
// RecentClosedTrades, a request failure renders as its own distinct
// error state rather than folding into "no data yet": see
// usePerformanceAnalytics's own docstring for why that distinction
// matters here specifically.
//
// Decision #137 — Live/Backtest toggle. Until this decision,
// usePerformanceAnalytics() was always called with no arguments, so
// isBacktest was always undefined and the backend's own default
// (false, live-only) was the only thing this section could ever show —
// structurally guaranteed to read "no data yet" forever, since no
// Execution Engine exists, even though real backtest-derived
// StrategyOutcome rows exist today (decision #128 onward). Local
// component state (`view`), not WorkspaceContext — this selection
// isn't shared across panels the way featureEnginePanelSymbol/
// lastBacktestRunId are; StrategyPerformanceSummary is the only
// consumer of isBacktest filtering anywhere in the UI. Both hook calls
// now pass an explicit isBacktest (true/false), never undefined — a
// strict either/or selector, matching useStrategyOutcomes.ts's own
// precedent of pinning isBacktest explicitly rather than relying on
// the backend's implicit default.
//
// Decision #162 — strategyName filter added; #137's deferral of it is
// CLOSED here, and the reason #137 gave for it was wrong. #137 wrote
// "There is no existing source of selectable strategy names anywhere
// in this codebase (checked directly)." In fact BACKTEST_STRATEGY_NAMES
// (api-client.ts) — the 7 real v1 strategy names, each equal to that
// Strategy's own `.name` in default_registry() and so to the
// strategy_name every backtest outcome row is written under — already
// existed, and was already rendered as BacktestPanel.tsx's Strategy
// <select>, when #137 was written. It arrived with BacktestPanel's own
// frontend delivery (unnumbered, self-cited as "decision #130";
// decision #134 later calls it "decision #131's frontend"), ahead of
// #133 and #137. The claim was inaccurate when written, not overtaken
// since. #138 then restated the deferral as "choosing a strategy is a
// separate UI decision" — this is that decision:
//   - Control: a native <select> ("All strategies" + the 7 names), not
//     a button row like the Live/Backtest toggle. Eight options, some
//     as long as "FirstPullback", don't fit one row in this narrow,
//     resizable panel, and BacktestPanel already uses a <select> for
//     the same list. The toggle stays a button row (two states); the
//     <select> reuses its font-mono / text-[10px] / base-border look.
//   - "All strategies" is the default and means strategyName =
//     undefined — today's exact request, no strategy_name param —
//     never a strategy picked on the person's behalf. undefined, not
//     "": _performanceAnalyticsQuery only omits the param for
//     undefined, so "" would send `strategy_name=` and match nothing.
//   - The selection is independent of Live/Backtest and survives
//     switching between them. Local state, same reason `view` is.
//   - usePerformanceAnalytics.ts, api-client.ts and the backend are
//     unchanged: the hook's own [strategyName, strategyVersion,
//     isBacktest] dependency array (#127) already refetches on a name
//     change, through the same load()/`loading` gate #137 built.
//   - The header names the strategy when one is selected, and the
//     Backtest empty message names it too: a strategy filter can now
//     itself be why nothing matches, and "run a backtest" has to say
//     which strategy. The Live message is unchanged — its stated reason
//     (no Execution Engine) is strategy-independent.
// strategyVersion is STILL deliberately not exposed. Unlike names, no
// list of selectable versions exists (strategy_version is a per-
// strategy string like "orb_v1", minted in each strategy's
// default_config(); the UI only ever shows it as a read-only field on
// a single result), and the backend rejects a version without a name
// (400, #127), so a version control would also have to depend on the
// name selection. That is its own design/data-source question —
// deferred, not improvised here.
//
// Found while wiring the toggle, not part of the original ask: with a
// static filters argument (the only way this hook was ever called
// before), usePerformanceAnalytics's own load() — which sets
// loading=true but does NOT clear winRateByHour/sessionExpectancy
// until the new fetch actually resolves — could never visibly show
// stale data, since nothing ever re-triggered it after mount. A
// toggle-driven isBacktest change makes that a real, visible gap:
// flipping Live -> Backtest would keep rendering the previous Live
// numbers, under a header that now reads "Backtest", for the duration
// of the new in-flight request — exactly the "label says one thing,
// data says another" state this project's honest-state discipline
// forbids, however briefly. Fixed locally, without touching
// usePerformanceAnalytics.ts: the loading branch below now gates on
// `loading` alone (previously `loading && isEmpty`), so ANY in-flight
// fetch — including a toggle switch — shows "Loading…" instead of a
// mismatched render. `usePerformanceAnalytics.ts`/`api-client.ts`/the
// backend are otherwise completely untouched by this decision.
function formatHourEt(hourEt: number): string {
  const period = hourEt < 12 ? "AM" : "PM";
  const hour12 = hourEt % 12 === 0 ? 12 : hourEt % 12;
  return `${hour12} ${period} ET`;
}

type StrategyPerformanceView = "live" | "backtest";

// One selectable strategy name; `undefined` is "All strategies".
type StrategyPerformanceStrategy = (typeof BACKTEST_STRATEGY_NAMES)[number] | undefined;

function StrategyPerformanceSummary() {
  // Decision #138: backtests currently produce the only persisted outcomes.
  const [view, setView] = useState<StrategyPerformanceView>("backtest");
  // Decision #162: undefined = "All strategies" (no strategy_name param).
  const [strategyName, setStrategyName] = useState<StrategyPerformanceStrategy>(undefined);
  const isBacktest = view === "backtest";
  const { winRateByHour, sessionExpectancy, loading, error } = usePerformanceAnalytics({ isBacktest, strategyName });
  const isEmpty = winRateByHour.length === 0 && sessionExpectancy.length === 0;

  // Honest, view-specific absence — "no live data" and "no backtest
  // data" are different facts, not one collapsed message: the former
  // because no Execution Engine exists to write live rows at all; the
  // latter because no backtest run happens to have produced a matching
  // row (a fixable, per-run fact, not a structural one). With a
  // strategy selected, the Backtest message names it — the filter can
  // itself be the reason nothing matches, and a run of THAT strategy is
  // what would change it (a run can also legitimately record none:
  // decision #131). The Live message stays as-is: no Execution Engine
  // means no live rows for any strategy, so naming one would imply a
  // strategy-specific absence that isn't the real reason.
  const emptyMessage =
    view === "live"
      ? "No live performance data is available yet — no Execution Engine exists to write it."
      : strategyName === undefined
        ? "No matching backtest performance data is available yet — run a backtest to populate this."
        : `No backtest performance data for ${strategyName} is available yet — run a backtest with this strategy (a run can legitimately record none).`;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-wide text-text-muted">
          Strategy Performance{" "}
          <span className="text-text-primary">
            — {view === "live" ? "Live" : "Backtest"}
            {strategyName !== undefined && ` · ${strategyName}`}
          </span>
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => setView("live")}
            className={`rounded px-2 py-0.5 font-mono text-[10px] ${
              view === "live"
                ? "bg-base-bg text-text-primary ring-1 ring-text-muted"
                : "text-text-muted hover:bg-base-bg"
            }`}
          >
            Live
          </button>
          <button
            onClick={() => setView("backtest")}
            className={`rounded px-2 py-0.5 font-mono text-[10px] ${
              view === "backtest"
                ? "bg-base-bg text-text-primary ring-1 ring-text-muted"
                : "text-text-muted hover:bg-base-bg"
            }`}
          >
            Backtest
          </button>
        </div>
      </div>
      <label className="flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Strategy</span>
        <select
          value={strategyName ?? ""}
          onChange={(e) => setStrategyName(BACKTEST_STRATEGY_NAMES.find((name) => name === e.target.value))}
          className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-0.5 font-mono text-[10px] text-text-primary outline-none focus:border-signal"
        >
          <option value="">All strategies</option>
          {BACKTEST_STRATEGY_NAMES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <div className="text-[10px] text-text-muted">
        {view === "backtest"
          ? "Backtest-derived performance from simulated StrategyOutcome data."
          : "Live-trading-derived performance from live StrategyOutcome data."}
      </div>
      {loading ? (
        <p className="p-1 text-[11px] text-text-muted">Loading…</p>
      ) : error ? (
        <p className="p-1 text-[11px] text-bear">Couldn't load performance data — {error}</p>
      ) : isEmpty ? (
        <p className="p-1 text-[11px] text-text-muted">{emptyMessage}</p>
      ) : (
        <div className="flex flex-col gap-2">
          {winRateByHour.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] text-text-muted">Win rate by hour</div>
              <div className="flex flex-col gap-1">
                {winRateByHour.map((row) => (
                  <div
                    key={row.hour_et}
                    className="flex items-center justify-between rounded border border-base-border px-2 py-1 font-mono text-[11px]"
                  >
                    <span className="text-text-primary">{formatHourEt(row.hour_et)}</span>
                    <span className="text-text-muted">
                      {row.win_count}/{row.total_trades}
                    </span>
                    <span className={row.win_rate >= 0.5 ? "text-bull" : "text-bear"}>
                      {(row.win_rate * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {sessionExpectancy.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] text-text-muted">Expectancy by session</div>
              <div className="flex flex-col gap-1">
                {sessionExpectancy.map((row) => (
                  <div
                    key={row.session_type ?? "__none__"}
                    className="flex items-center justify-between rounded border border-base-border px-2 py-1 font-mono text-[11px]"
                  >
                    <span className="text-text-primary">
                      {row.session_type ? row.session_type.replace("_", " ") : "Unknown session"}
                    </span>
                    <span className="text-text-muted">{row.trade_count} trades</span>
                    <span className={row.expectancy_r >= 0 ? "text-bull" : "text-bear"}>
                      {row.expectancy_r >= 0 ? "+" : ""}
                      {row.expectancy_r.toFixed(2)}R
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Calendar (Context Engine, decision #90/#92/#96) is market-wide, not
// symbol-specific — same "global" scope GeneralContent below already
// has, so it surfaces here rather than per-connector in
// AIAnalysisPanel.tsx (that's where Fundamentals/News go instead — see
// AIAnalysisPanel.tsx's own SymbolContextSummary comment). Always
// rendered, including the not-yet-evaluated case, same "don't hide it,
// don't fabricate it" posture RecentClosedTrades above already
// establishes for this file.
//
// Note on aggregate/global scoring: `ContextEngine.get_snapshot()`
// exposes exactly `{"providers": {...}, "evaluated_at": ...}` for its
// "global" section (confirmed directly against context_engine/
// engine.py's own get_snapshot() docstring and implementation) — no
// aggregate context score, assessment, or other derived/summary field
// exists anywhere in the real snapshot today. Nothing below recomputes
// or invents one; each Calendar field is shown as-is.
function MarketSessionSummary() {
  const { calendar, loading } = useContextSnapshot();

  return (
    <div className="flex flex-col gap-1">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Market Session</div>
      {loading && !calendar ? (
        <p className="p-1 text-[11px] text-text-muted">Loading…</p>
      ) : !calendar ? (
        <p className="p-1 text-[11px] text-text-muted">No calendar data yet.</p>
      ) : (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-base-border px-2 py-1.5 font-mono text-[11px]">
          <div>
            <div className="text-text-muted">Session</div>
            <div className="text-text-primary">{calendar.session.replace("_", " ")}</div>
          </div>
          <div>
            <div className="text-text-muted">Market</div>
            <div className={calendar.isMarketOpen ? "text-bull" : "text-text-muted"}>
              {calendar.isMarketOpen ? "Open" : "Closed"}
            </div>
          </div>
          {calendar.fedDay && (
            <div className="col-span-2">
              <span className="rounded border border-signal/40 px-1 py-0.5 text-signal">Fed Day</span>
            </div>
          )}
          {calendar.isHalfDay && <div className="col-span-2 text-text-muted">Half day session</div>}
        </div>
      )}
    </div>
  );
}

// Market State Engine's cross-symbol composite (this task) — the core
// "interprets" stage of the pipeline (Feature Engine measures, Market
// State interprets, Context Engine describes, Strategy decides), fully
// built and reachable via GET /intelligence/market-state (decision #98)
// but invisible anywhere in the frontend until now. Same market-wide-
// vs-per-symbol split decision #125 already used for Context: SPY/QQQ/
// IWM's synthesized composite (spy/qqq/iwm direction, trend alignment,
// risk-on, qqq-leadership, iwm-confirmation) describes the whole market,
// not any one connector's symbol, so it sits here in GeneralContent
// alongside MarketSessionSummary rather than in AIAnalysisPanel.tsx (see
// that file's own SymbolMarketStateSummary for the per-symbol half of
// this same split — trend/volatility/volume/vwap-relationship/
// acceleration). Always rendered, including the not-yet-synthesized
// case (`market` stays null until SPY/QQQ/IWM have each reported a
// trend_score at least once) — same "don't hide it, don't fabricate it"
// posture MarketSessionSummary/RecentClosedTrades already establish in
// this file.
function MarketStateSummary() {
  const { market, loading } = useMarketState();

  return (
    <div className="flex flex-col gap-1">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Market State</div>
      {loading && !market ? (
        <p className="p-1 text-[11px] text-text-muted">Loading…</p>
      ) : !market ? (
        <p className="p-1 text-[11px] text-text-muted">
          Composite not yet available — waiting for SPY/QQQ/IWM to each report at least once.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-base-border px-2 py-1.5 font-mono text-[11px]">
          <div>
            <div className="text-text-muted">SPY direction</div>
            <div className="text-text-primary">{market.spyDirectionScore.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-text-muted">QQQ direction</div>
            <div className="text-text-primary">{market.qqqDirectionScore.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-text-muted">IWM direction</div>
            <div className="text-text-primary">{market.iwmDirectionScore.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-text-muted">Trend alignment</div>
            <div className="text-text-primary">{market.trendAlignmentScore.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-text-muted">Risk-on</div>
            <div className="text-text-primary">{market.riskOnScore.toFixed(0)}</div>
          </div>
          <div>
            <div className="text-text-muted">QQQ leadership</div>
            <div className="text-text-primary">{market.qqqLeadershipScore.toFixed(0)}</div>
          </div>
          <div className="col-span-2">
            <div className="text-text-muted">IWM confirmation</div>
            <div className="text-text-primary">{market.iwmConfirmationScore.toFixed(0)}</div>
          </div>
        </div>
      )}
    </div>
  );
}

// World View surfaced in the UI for the first time (this task) — GET
// /intelligence/world-view (decision #150) had zero frontend
// representation until now, confirmed by grep before starting.
//
// Scope decision, stated explicitly per this task's own instruction:
// World View's own architectural purpose (trading-intelligence-
// architecture.md §15) is "one summary instead of several separate
// queries" for a debug view or a future automated-reasoning layer — not
// a replacement for the detailed, filterable panels already built for
// each domain individually. A full re-rendering of every field
// GET /intelligence/world-view returns would mostly duplicate
// MarketSessionSummary/MarketStateSummary (Calendar/Market State, both
// already live-updating via WebSocket, decisions #125/#126/#147/#151)
// and StrategyPerformanceSummary (decisions #127/#137/#138) — busywork,
// not new value. So `market_state`/`context` are deliberately NOT
// re-rendered here at all (useWorldView.ts doesn't even normalize them —
// see that hook's own docstring); the two genuinely new things World
// View adds, and the only two this section renders, are:
// (a) `performance`'s all-time, both-populations-side-by-side shape —
//     StrategyPerformanceSummary's own toggle immediately above shows
//     exactly ONE of live/backtest at a time, filtered by hour/session;
//     nothing else in this codebase shows both at once, unfiltered; and
// (b) being the one place that honestly demonstrates Portfolio State's
//     current absence as part of a complete four-domain picture.
//
// Placement: directly below StrategyPerformanceSummary, same section
// (GeneralContent) — same "global, not tied to any one connector's
// symbol" placement rule #123/#125/#127 already established for this
// file, and adjacent to the toggle it's most at risk of being confused
// with, which is exactly why its own subtitle below states the
// distinction plainly rather than relying on proximity alone.
// StrategyPerformanceSummary's own internals are completely untouched —
// this is a new, separate function, added alongside it, not a
// restructuring of it.
//
// aggregateWinRate/aggregateExpectancy below sum/weight-average fields
// that are already real per-row numbers from the backend
// (total_trades/win_count per hour, trade_count/expectancy_r per
// session) — the same fields StrategyPerformanceSummary's own toggle
// already renders unaggregated. This is NOT the kind of invented
// composite/aggregate score decisions #125/#127 explicitly avoided
// adding (no new interpretation, weighting scheme, or ranking is
// introduced) — it's the minimal arithmetic needed to show "how many
// trades, what fraction won, what expectancy" as a single side-by-side
// glance rather than duplicating the full hour-by-hour/session-by-
// session breakdown a second time in a different section.
function aggregateWinRate(rows: HourlyWinRateWireShape[]): { trades: number; winRate: number | null } {
  const trades = rows.reduce((sum, row) => sum + row.total_trades, 0);
  const wins = rows.reduce((sum, row) => sum + row.win_count, 0);
  return { trades, winRate: trades > 0 ? wins / trades : null };
}

function aggregateExpectancy(rows: SessionTypeExpectancyWireShape[]): {
  trades: number;
  expectancyR: number | null;
} {
  const trades = rows.reduce((sum, row) => sum + row.trade_count, 0);
  const weighted = rows.reduce((sum, row) => sum + row.expectancy_r * row.trade_count, 0);
  return { trades, expectancyR: trades > 0 ? weighted / trades : null };
}

function WorldViewSummary() {
  const { performance, portfolio, loading, error, refetch } = useWorldView();

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-[11px] uppercase tracking-wide text-text-muted">
        <span>World View <span className="text-text-primary">— All-Time</span></span>
        <button type="button" onClick={refetch} disabled={loading} className="text-text-primary disabled:opacity-50">
          Refresh
        </button>
      </div>
      <div className="text-[10px] text-text-muted">
        Live and backtest together, all matching history — distinct from the hour/session toggle above.
      </div>
      {loading ? (
        <p className="p-1 text-[11px] text-text-muted">Loading…</p>
      ) : error ? (
        <p className="p-1 text-[11px] text-bear">Couldn't load World View — {error}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            {(["live", "backtest"] as const).map((population) => {
              const pop = performance?.[population];
              const winRate = aggregateWinRate(pop?.hourly_win_rates ?? []);
              const expectancy = aggregateExpectancy(pop?.session_expectancy ?? []);
              const isEmpty = winRate.trades === 0 && expectancy.trades === 0;
              return (
                <div
                  key={population}
                  className="rounded border border-base-border px-2 py-1.5 font-mono text-[11px]"
                >
                  <div className="mb-1 text-text-muted">{population === "live" ? "Live" : "Backtest"}</div>
                  {isEmpty ? (
                    <div className="text-text-muted">
                      {population === "live"
                        ? "No live trades yet."
                        : "No backtest trades yet."}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-0.5">
                      <div className="flex items-center justify-between">
                        <span className="text-text-muted">Win rate</span>
                        <span className={winRate.winRate !== null && winRate.winRate >= 0.5 ? "text-bull" : "text-bear"}>
                          {winRate.winRate !== null ? `${(winRate.winRate * 100).toFixed(0)}%` : "—"}
                        </span>
                      </div>
                      <div className="text-[10px] text-text-muted">{winRate.trades} trades</div>
                      <div className="flex items-center justify-between">
                        <span className="text-text-muted">Expectancy</span>
                        <span
                          className={
                            expectancy.expectancyR !== null && expectancy.expectancyR >= 0 ? "text-bull" : "text-bear"
                          }
                        >
                          {expectancy.expectancyR !== null
                            ? `${expectancy.expectancyR >= 0 ? "+" : ""}${expectancy.expectancyR.toFixed(2)}R`
                            : "—"}
                        </span>
                      </div>
                      <div className="text-[10px] text-text-muted">{expectancy.trades} trades</div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-1 rounded border border-base-border px-2 py-1 font-mono text-[11px]">
            <div className="flex items-center justify-between">
              <span className="text-text-muted">Portfolio</span>
              <span className="text-text-muted">
                {portfolio === null ? "Unavailable (execution pipeline or snapshot)" : `${portfolio.positions.length} open positions`}
              </span>
            </div>
            {portfolio !== null && (
              <div className="mt-1 flex flex-col gap-1">
                <div className="text-[10px] text-text-muted">
                  {portfolio.execution_mode} · {portfolio.in_flight_order_count} in-flight orders · Snapshot {portfolio.snapshot_time}
                </div>
                {portfolio.positions.map((position) => (
                  <div key={position.position_id} title={`Position ${position.position_id}`} className="flex flex-wrap gap-x-2 border-t border-base-border pt-1">
                    <span className="text-text-primary">{position.symbol} {position.side} × {position.remaining_quantity}</span>
                    <span>Avg {position.average_entry}</span>
                    <span>Stop {position.stop ?? "—"}</span>
                    <span>Target {position.target ?? "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function GeneralContent() {
  const symbols = useMemo(() => MOCK_TICKERS.map((t) => t.symbol), []);
  const prices = useLatestPrices(symbols);

  const rows = MOCK_TICKERS.map((t) => {
    const last = prices[t.symbol] ?? t.basePrice;
    const changePct = ((last - t.basePrice) / t.basePrice) * 100;
    return { ...t, last, changePct };
  });

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Market Overview</div>
      <div className="flex flex-col gap-1">
        {rows.map((r) => (
          <div
            key={r.symbol}
            className="flex items-center justify-between rounded border border-base-border px-2 py-1.5"
          >
            <div>
              <div className="font-mono text-xs font-medium text-text-primary">{r.symbol}</div>
              <div className="text-[10px] text-text-muted">{r.name}</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-xs text-text-primary">{r.last.toFixed(2)}</div>
              <div className={`font-mono text-[10px] ${r.changePct >= 0 ? "text-bull" : "text-bear"}`}>
                {r.changePct >= 0 ? "+" : ""}
                {r.changePct.toFixed(2)}%
              </div>
            </div>
          </div>
        ))}
      </div>
      <MarketSessionSummary />
      <MarketStateSummary />
      <RecentClosedTrades />
      <StrategyPerformanceSummary />
      <WorldViewSummary />
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Notes</div>
      <p className="text-xs leading-relaxed text-text-muted">
        General mode isn't tied to any single connector — it's the scrollable, market-wide view. Select a
        connector above to see AI opportunity data for whatever symbol that link group currently holds.
      </p>
    </div>
  );
}

function ConnectorContent({ symbol }: { symbol: string }) {
  // Real Strategy Engine wiring (decision #114/D10) replaces
  // generateMockOpportunities — candles are no longer needed here at all,
  // that mock was their only real consumer in this file.
  const { opportunities, loading } = useOpportunities(symbol);
  // Decision #123 — same symbol, same underlying OpportunityCache;
  // ConnectorContent owns all data-fetching for this connector and passes
  // clean props down, same split useOpportunities/AIAnalysisPanel above
  // already establish (AIAnalysisPanel itself fetches nothing).
  const { agreement, conflict } = useOpportunityConflicts(symbol);
  // Same symbol, Fundamentals/News per-symbol Context Engine data
  // (decision #96) — Calendar itself is out of scope here, it's
  // market-wide and surfaces separately in GeneralContent's own
  // MarketSessionSummary above. ConnectorContent owns all data-fetching
  // for this connector and passes clean props down, same split
  // useOpportunities/useOpportunityConflicts and AIAnalysisPanel already
  // establish (AIAnalysisPanel itself fetches nothing).
  const { fundamentals, news } = useContextSnapshot(symbol);
  // Per-symbol Market State (this task) — same symbol, same "parent owns
  // fetching, child stays presentational" split useOpportunities/
  // useOpportunityConflicts/useContextSnapshot above already establish
  // for this component (AIAnalysisPanel itself fetches nothing). The
  // cross-symbol composite this same hook can also return is out of
  // scope here — that's market-wide, already surfaced separately in
  // GeneralContent's own MarketStateSummary above, so only `symbolState`
  // is passed down.
  const { symbolState: marketState } = useMarketState(symbol);
  return (
    <AIAnalysisPanel
      symbol={symbol}
      opportunities={opportunities}
      loading={loading}
      agreement={agreement}
      conflict={conflict}
      fundamentals={fundamentals}
      news={news}
      marketState={marketState}
    />
  );
}

export function InfoTab() {
  const { infoCollapsed, infoWidthPx, setInfoCollapsed, setInfoWidthPx, connectorSymbols } = useWorkspace();
  const [mode, setMode] = useState<InfoConnectorMode>("general");
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: infoWidthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setInfoWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = infoCollapsed ? COLLAPSED_WIDTH : infoWidthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!infoCollapsed && (
        <div
          onPointerDown={onResizeDown}
          className="absolute left-0 top-0 z-10 h-full w-[6px] -translate-x-1/2 cursor-col-resize hover:bg-signal/30"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setInfoCollapsed(!infoCollapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={infoCollapsed ? "Expand info tab" : "Collapse info tab"}
          >
            {infoCollapsed ? "«" : "»"}
          </button>
          {!infoCollapsed && <span className="font-mono text-xs font-semibold text-text-primary">Info</span>}
        </div>

        {!infoCollapsed && (
          <>
            <div className="flex flex-wrap gap-1 border-b border-base-border p-2">
              <button
                onClick={() => setMode("general")}
                className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                  mode === "general"
                    ? "bg-base-bg text-text-primary ring-1 ring-text-muted"
                    : "text-text-muted hover:bg-base-bg"
                }`}
              >
                General
              </button>
              {LINK_CONNECTOR_IDS.map((id) => (
                <button
                  key={id}
                  onClick={() => setMode(id)}
                  className="flex h-5 w-5 items-center justify-center rounded font-mono text-[11px]"
                  style={{
                    backgroundColor: mode === id ? CONNECTOR_COLORS[id] : "transparent",
                    color: mode === id ? "#0B0E14" : CONNECTOR_COLORS[id],
                    border: `1px solid ${CONNECTOR_COLORS[id]}`,
                  }}
                >
                  {id}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1">
              {mode === "general" ? <GeneralContent /> : <ConnectorContent symbol={connectorSymbols[mode]} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
