import { useEffect, useMemo, useRef, useState } from "react";
import { useBacktestRun } from "../../hooks/useBacktestRun";
import { useIbkrBacktestRun, type IbkrBacktestRunError } from "../../hooks/useIbkrBacktestRun";
import { useWorkspace } from "../../state/WorkspaceContext";
import {
  BACKTEST_SCENARIOS,
  BACKTEST_STRATEGY_NAMES,
  type DiscardedSignalWireShape,
} from "../../services/api-client";
import { currentEtCalendarDate, etWallClockToUtc } from "./easternTime";

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 300;

// Deliberately local component state, not wired into WorkspaceContext.tsx
// the way ScannerPanel's own scannerCollapsed/scannerWidthPx are (that
// state is per-Main-Window, persisted to localStorage, and pushed across
// browser tabs via crossTabSync.ts). This panel's own contents — a form
// plus one run's in-flight/finished state — are meaningless to persist
// or sync: a run in progress belongs to the browser tab that started it,
// not to "whichever Main Window is active," and there's no server-side
// event for a backtest run to push updates to a second tab anyway (this
// route is a synchronous HTTP call, not something OpportunityCache-style
// WebSocket state could represent). Reusing ScannerPanel's exact
// MIN_WIDTH/MAX_WIDTH/COLLAPSED_WIDTH convention (same constants, same
// resize-handle behavior, same "starts collapsed" posture) keeps the
// visual/interaction shape consistent without extending
// WorkspaceContextValue, MainWindowState's localStorage schema, or
// crossTabSync's sync payload for state that has no reason to survive a
// reload. Flagged here explicitly rather than silently deviating from
// the pattern — worth reconsidering only if a future need (e.g. showing
// a run's progress from a second tab) actually shows up.
function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// Only used for the IBKR range's 24h-cap validation message, where a
// window can genuinely run into many hours — formatElapsed above stays
// exactly as it was (seconds-scale, used for live elapsed-time display),
// not widened for this different scale/purpose.
function formatHoursMinutes(totalMs: number): string {
  const totalMinutes = Math.round(totalMs / 60000);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

type BacktestMode = "fixture" | "ibkr";

// Decision #152. Maps every real failure
// shape POST /backtest/run/ibkr can return (confirmed directly against
// backend/app/api/routes/backtest.py + backend/app/backtest_runner/
// ibkr_historical.py's full IBKRHistoricalAcquisitionError hierarchy) to a
// short, honest heading. The backend's own message is always shown
// underneath verbatim — this never fabricates detail beyond what the
// backend actually said, and never falls through to a bare "request
// failed" for a code this route can genuinely produce. Unknown/absent
// code and unexpected shapes still get a real (if generic) heading, never
// a raw object or stack trace — parseIbkrErrorDetail (api-client.ts)
// already guarantees `message` is always a plain string before this ever
// runs.
function classifyIbkrBacktestError(err: IbkrBacktestRunError): { heading: string; message: string } {
  const { status, code, message } = err;
  if (status === 409) {
    return { heading: "Live data connection prevents backtesting", message };
  }
  switch (code) {
    case "invalid_backtest_request":
      return { heading: "Invalid date, symbol, or range", message };
    case "ibkr_backtest_not_configured":
      return { heading: "IBKR backtest connection is not configured", message };
    case "ibkr_contract_unresolved":
      return { heading: "Symbol could not be resolved", message };
    case "ibkr_no_data":
      return { heading: "No historical data available", message };
    case "ibkr_historical_permission_denied":
      return { heading: "Market-data permission denied", message };
    case "ibkr_historical_pacing_rejected":
      return { heading: "Rate limit / pacing rejected by IBKR", message };
    case "ibkr_historical_timeout":
      return { heading: "Request timed out", message };
    case "ibkr_malformed_response":
      return { heading: "Malformed data received from IBKR", message };
    case "ibkr_incomplete_response":
      return { heading: "Incomplete data received from IBKR", message };
    default:
      // Covers ibkr_historical_error's own generic code, any unrecognized/
      // future code, and the no-code case (network failure before a body
      // existed, or an unparseable response) — safe fallback, still the
      // backend's real message when one was recovered.
      return { heading: "Request failed", message: message || "The request failed for an unknown reason." };
  }
}

function DiscardedSignalRow({ signal }: { signal: DiscardedSignalWireShape }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-base-border px-2 py-1.5 last:border-b-0">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] font-medium text-text-primary">{signal.reason}</span>
        <span className="font-mono text-[9px] text-text-muted">{signal.signal_candle_ts}</span>
      </div>
      <span className="font-mono text-[10px] text-text-muted">{signal.detail}</span>
    </div>
  );
}

function ResultsView({
  runId,
  sweepId,
  outcomesRecorded,
  discardedSignals,
}: {
  runId: string;
  sweepId: string;
  outcomesRecorded: number;
  discardedSignals: DiscardedSignalWireShape[];
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-base-border p-2">
      <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 font-mono text-[10px]">
        <span className="text-text-muted">run_id</span>
        <span className="truncate text-text-primary" title={runId}>
          {runId}
        </span>
        <span className="text-text-muted">sweep_id</span>
        <span className="truncate text-text-primary" title={sweepId}>
          {sweepId}
        </span>
        <span className="text-text-muted">outcomes_recorded</span>
        {/* Neutral styling regardless of value — 0 is an honest,
            expected result for many (strategy, scenario) pairs per this
            route's own docstring (four of the seven strategies are
            structurally unreachable in any BacktestRunner replay today),
            never rendered as an error or a "nothing happened" warning. */}
        <span className="font-semibold text-text-primary">{outcomesRecorded}</span>
      </div>

      {/* Decision #134: this run_id is written to shared WorkspaceContext
          state (setLastBacktestRunId, see BacktestForm below) the moment
          this view renders — not a separate action here, just a plain
          note so the person knows where to find it. Not rendered as a
          clickable/actionable link: the Backtest Results panel's own
          collapsed state is deliberately local, not shared, per that
          panel's own header comment, so there's nothing this panel could
          reliably "jump to" without reversing that design for a
          convenience this task didn't ask for. If the Results panel is
          currently showing a different, manually-picked run_id, this
          new run won't silently override it there — see that panel's
          own "auto"/"manual" filter-mode comment for the full reasoning.
          Same treatment for a run started from either trigger mode below
          — the response shape is identical (confirmed directly against
          runner.py: both POST /backtest/run and POST /backtest/run/ibkr
          return dataclasses.asdict() of the same BacktestRunResult), so
          this view has no reason to know or care which mode produced it. */}
      <span className="font-mono text-[9px] text-text-muted">
        → prefilled as the run_id filter in the Backtest Results panel (unless it's currently pinned to a different run
        you picked manually there).
      </span>

      <div className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wide text-text-muted">
          discarded_signals {discardedSignals.length > 0 ? `(${discardedSignals.length})` : ""}
        </span>
        {discardedSignals.length === 0 ? (
          <p className="font-mono text-[10px] text-text-muted">None.</p>
        ) : (
          <div className="rounded border border-base-border">
            {discardedSignals.map((s, i) => (
              <DiscardedSignalRow key={`${s.signal_candle_ts}-${i}`} signal={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Result of validating the two Eastern-time datetime-local inputs against
// POST /backtest/run/ibkr's own real constraints (backtest.py's
// _validate_ibkr_range: both tz-aware, start < end, window <= 24 elapsed
// hours) — computed entirely client-side so a doomed request is never
// sent, but this is deliberately not treated as the last word: the
// backend's own _validate_ibkr_range stays the real authority (see the
// 422 branch in the error classifier above), and no market-calendar/
// session/holiday logic is added here — a client-side pass can confirm
// the window is well-formed and within the size cap, never whether a
// given date is an actual trading day.
interface IbkrRangeValidation {
  canSubmit: boolean;
  // null: nothing to say yet (inputs empty/incomplete) — not shown as an
  // error. Non-null: a real problem, shown inline, Run stays disabled.
  message: string | null;
  startIso: string | null;
  endIso: string | null;
}

function validateIbkrRange(startLocal: string, endLocal: string): IbkrRangeValidation {
  if (!startLocal || !endLocal) {
    return { canSubmit: false, message: null, startIso: null, endIso: null };
  }
  const start = etWallClockToUtc(startLocal);
  const end = etWallClockToUtc(endLocal);
  if (!start.ok || !end.ok) {
    // Distinguishes which side failed rather than a single blended
    // message — a person fixing this needs to know which field is wrong.
    const which = !start.ok && !end.ok ? "Start and end" : !start.ok ? "Start" : "End";
    return {
      canSubmit: false,
      message: `${which} time isn't a valid Eastern clock time (it may fall in a spring-forward gap that never occurred).`,
      startIso: null,
      endIso: null,
    };
  }
  if (start.utcMs >= end.utcMs) {
    return { canSubmit: false, message: "Start must be before end.", startIso: null, endIso: null };
  }
  const windowMs = end.utcMs - start.utcMs;
  const maxMs = 24 * 60 * 60 * 1000;
  if (windowMs > maxMs) {
    return {
      canSubmit: false,
      message: `Window is ${formatHoursMinutes(windowMs)} — exceeds the 24-hour cap. Narrow the range.`,
      startIso: null,
      endIso: null,
    };
  }
  return { canSubmit: true, message: null, startIso: start.iso, endIso: end.iso };
}

function BacktestForm() {
  const fixtureRun = useBacktestRun();
  const ibkrRun = useIbkrBacktestRun();
  const { setLastBacktestRunId } = useWorkspace();

  const [mode, setMode] = useState<BacktestMode>("fixture");
  const [strategyName, setStrategyName] = useState("");
  const [scenario, setScenario] = useState("");
  const [symbol, setSymbol] = useState("");
  const [startLocal, setStartLocal] = useState("");
  const [endLocal, setEndLocal] = useState("");
  const symbolInputRef = useRef<HTMLInputElement>(null);

  // Decision #134's shared run_id publish, unchanged in spirit but now
  // watching both hooks independently (one effect per hook) rather than
  // being folded into a single "active result" — a run finishing in
  // either mode should always publish its own run_id the moment it
  // finishes, regardless of which mode is currently selected in the UI,
  // matching this effect's own original "fires once per genuinely new
  // result object" behavior per hook.
  useEffect(() => {
    if (fixtureRun.status === "done" && fixtureRun.result?.run_id) {
      setLastBacktestRunId(fixtureRun.result.run_id);
    }
  }, [fixtureRun.status, fixtureRun.result, setLastBacktestRunId]);

  useEffect(() => {
    if (ibkrRun.status === "done" && ibkrRun.result?.run_id) {
      setLastBacktestRunId(ibkrRun.result.run_id);
    }
  }, [ibkrRun.status, ibkrRun.result, setLastBacktestRunId]);

  // Only one Backtest Runner execution happens at a time on the backend
  // regardless of which route started it (both share
  // engine_singleton_guard.py's _RUN_LOCK) — combining both hooks' status
  // here disables the mode toggle and both submit paths together, so a
  // person can't fire a second, wasted long-running request from this tab
  // while the first is still in flight, the same reasoning each hook's
  // own single-route guard already gives, extended across the two modes.
  const running = fixtureRun.status === "running" || ibkrRun.status === "running";

  const selectedScenario = useMemo(() => BACKTEST_SCENARIOS.find((sc) => sc.name === scenario), [scenario]);
  const rangeValidation = useMemo(() => validateIbkrRange(startLocal, endLocal), [startLocal, endLocal]);

  const canRunFixture = !running && strategyName !== "" && scenario !== "" && symbol.trim() !== "";
  const canRunIbkr = !running && strategyName !== "" && symbol.trim() !== "" && rangeValidation.canSubmit;
  const canRun = mode === "fixture" ? canRunFixture : canRunIbkr;

  const handleRun = () => {
    if (mode === "fixture") {
      if (!canRunFixture) return;
      fixtureRun.run(strategyName, symbol.trim(), scenario);
    } else {
      if (!canRunIbkr || !rangeValidation.startIso || !rangeValidation.endIso) return;
      ibkrRun.run(strategyName, symbol.trim(), rangeValidation.startIso, rangeValidation.endIso);
    }
  };

  // Fills both ET datetime-local inputs for a quick preset, anchored to
  // whatever date is already in `startLocal` (its first 10 chars,
  // "YYYY-MM-DD") or today's Eastern calendar date if nothing's been
  // picked yet — matches the approved design exactly. Deliberately makes
  // no claim this date is a real trading day (no weekend/holiday check
  // here) — an honest miss surfaces as this route's own ibkr_no_data 400,
  // not a client-side guess.
  const applyPreset = (startTime: string, endTime: string) => {
    const anchorDate = /^\d{4}-\d{2}-\d{2}$/.test(startLocal.slice(0, 10)) ? startLocal.slice(0, 10) : currentEtCalendarDate();
    setStartLocal(`${anchorDate}T${startTime}`);
    setEndLocal(`${anchorDate}T${endTime}`);
  };

  const ibkrError = ibkrRun.status === "error" && ibkrRun.error ? classifyIbkrBacktestError(ibkrRun.error) : null;

  return (
    <>
      <div className="flex flex-col gap-2 p-2">
        <div className="flex rounded border border-base-border font-mono text-[10px]" role="tablist" aria-label="Backtest data source">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "fixture"}
            onClick={() => setMode("fixture")}
            disabled={running}
            className={`flex-1 rounded-l px-2 py-1 ${
              mode === "fixture" ? "bg-signal/20 text-signal" : "text-text-muted hover:bg-base-bg"
            } disabled:opacity-50`}
          >
            Fixture scenario
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "ibkr"}
            onClick={() => setMode("ibkr")}
            disabled={running}
            className={`flex-1 rounded-r px-2 py-1 ${
              mode === "ibkr" ? "bg-signal/20 text-signal" : "text-text-muted hover:bg-base-bg"
            } disabled:opacity-50`}
          >
            Real IBKR data
          </button>
        </div>

        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Strategy</span>
          <select
            value={strategyName}
            onChange={(e) => setStrategyName(e.target.value)}
            disabled={running}
            className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
          >
            <option value="">Select…</option>
            {BACKTEST_STRATEGY_NAMES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>

        {mode === "fixture" && (
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Scenario</span>
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              disabled={running}
              className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
            >
              <option value="">Select…</option>
              {BACKTEST_SCENARIOS.map((sc) => (
                <option key={sc.name} value={sc.name}>
                  {sc.name} (~{sc.candleCount}s)
                </option>
              ))}
            </select>
            {selectedScenario && (
              <span className="font-mono text-[9px] leading-snug text-text-muted">{selectedScenario.description}</span>
            )}
          </label>
        )}

        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Symbol</span>
          <input
            ref={symbolInputRef}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleRun()}
            disabled={running}
            placeholder={mode === "fixture" ? "e.g. ZBTR1" : "e.g. AAPL"}
            maxLength={12}
            className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary placeholder:text-text-muted outline-none focus:border-signal disabled:opacity-50"
          />
          {/* Same underlying field/state in both modes (reused, not
              duplicated) — only the caption changes, since the real
              semantics genuinely differ: fixture mode's symbol is an
              arbitrary label on StrategyOutcome rows (route's own param
              description), IBKR mode's must resolve to a real US-listed
              contract via SMART/USD or the route 400s
              (ibkr_contract_unresolved). */}
          <span className="font-mono text-[9px] text-text-muted">
            {mode === "fixture"
              ? "Label only — not a real ticker lookup."
              : "Must resolve to a real US-listed symbol via IBKR SMART/USD."}
          </span>
        </label>

        {mode === "ibkr" && (
          <div className="flex flex-col gap-1.5 rounded border border-base-border p-1.5">
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => applyPreset("09:30", "16:00")}
                disabled={running}
                className="flex-1 rounded border border-base-border px-1.5 py-1 font-mono text-[9px] text-text-muted hover:bg-base-bg hover:text-text-primary disabled:opacity-50"
              >
                Regular session
                <br />
                9:30–16:00 ET
              </button>
              <button
                type="button"
                onClick={() => applyPreset("04:00", "20:00")}
                disabled={running}
                className="flex-1 rounded border border-base-border px-1.5 py-1 font-mono text-[9px] text-text-muted hover:bg-base-bg hover:text-text-primary disabled:opacity-50"
              >
                Extended session
                <br />
                4:00–20:00 ET
              </button>
            </div>
            <span className="font-mono text-[9px] leading-snug text-text-muted">
              Applies to the date already entered below (today's Eastern date if none yet). Not a guarantee this is a
              real trading day — weekends and exchange holidays aren't checked here; the backend/IBKR will report
              honestly if there's no data for the date picked.
            </span>

            <label className="flex flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Start (US Eastern Time)</span>
              <input
                type="datetime-local"
                value={startLocal}
                onChange={(e) => setStartLocal(e.target.value)}
                disabled={running}
                className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">End (US Eastern Time, exclusive)</span>
              <input
                type="datetime-local"
                value={endLocal}
                onChange={(e) => setEndLocal(e.target.value)}
                disabled={running}
                className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
              />
            </label>
            {/* Deliberately never shows a fixed "EST"/"EDT" label — the
                real UTC offset for these two fields depends on the date
                selected (standard vs. daylight time), which a static
                label would misstate on the wrong side of a DST
                transition. */}
            <span className="font-mono text-[9px] leading-snug text-text-muted">
              Both fields are US Eastern Time (ET) — the UTC offset is resolved automatically for the date entered.
              Capped at 24 elapsed hours; end is exclusive.
            </span>
            {rangeValidation.message && (
              <span className="font-mono text-[10px] leading-snug text-bear">{rangeValidation.message}</span>
            )}
            <span className="font-mono text-[9px] leading-snug text-text-muted">
              Roughly 1 real second per primary candle: about 6.5 minutes for a regular session, up to about 16
              minutes for a full extended session. This is genuinely slow, not a stall — see the running state below
              once started.
            </span>
          </div>
        )}

        <button
          onClick={handleRun}
          disabled={!canRun}
          className="rounded bg-signal/20 px-2 py-1 font-mono text-xs text-signal hover:bg-signal/30 disabled:opacity-40"
        >
          {running ? "Running…" : "Run Backtest"}
        </button>
        <span className="font-mono text-[9px] leading-snug text-text-muted">
          Runs serialize on the backend — only one Backtest Runner execution happens at a time in this process
          regardless of mode, so this panel disables itself rather than queue a second one.
        </span>
      </div>

      {/* Fixture mode's running/error/result states below are completely
          unchanged from before this delivery — same copy, same
          ~120-140s framing, gated on fixtureRun's own status only, never
          on ibkrRun's. IBKR mode gets its own parallel, explicitly
          separate blocks further down — this pairing (mode check +
          that mode's own hook status only) is what guarantees a result
          or error from one mode is never rendered while the other mode
          is selected. */}
      {mode === "fixture" && fixtureRun.status === "running" && (
        <div className="flex flex-col gap-1 border-t border-base-border px-2 py-2">
          <span className="font-mono text-xs font-semibold text-signal">
            Running… {formatElapsed(fixtureRun.elapsedSeconds)} elapsed
          </span>
          <span className="font-mono text-[9px] text-text-muted">
            Fully synchronous — roughly 1 real second per replayed candle
            {selectedScenario ? `, ~${selectedScenario.candleCount}s typical for this scenario` : ""}. This is
            expected, not a stall.
          </span>
        </div>
      )}

      {mode === "fixture" && fixtureRun.status === "error" && fixtureRun.error && (
        <div className="border-t border-base-border px-2 py-2">
          <p className="font-mono text-[11px] text-bear">{fixtureRun.error}</p>
        </div>
      )}

      {mode === "fixture" && fixtureRun.status === "done" && fixtureRun.result && (
        <ResultsView
          runId={fixtureRun.result.run_id}
          sweepId={fixtureRun.result.sweep_id}
          outcomesRecorded={fixtureRun.result.outcomes_recorded}
          discardedSignals={fixtureRun.result.discarded_signals}
        />
      )}

      {mode === "ibkr" && ibkrRun.status === "running" && (
        <div className="flex flex-col gap-1 border-t border-base-border px-2 py-2">
          <span className="font-mono text-xs font-semibold text-signal">
            Running… {formatElapsed(ibkrRun.elapsedSeconds)} elapsed
          </span>
          {/* No progress percentage or completion estimate — the backend
              gives this route no progress signal to show (a single
              synchronous call), so implying one here would be fabricated
              precision this task's own honesty principle rules out. */}
          <span className="font-mono text-[9px] text-text-muted">
            Still active — this is genuinely slow (roughly 1 second per primary candle) and can legitimately take up
            to approximately 16 minutes for a full extended session. No progress percentage is available; this is
            expected, not a stall.
          </span>
        </div>
      )}

      {mode === "ibkr" && ibkrRun.status === "error" && ibkrError && (
        <div className="border-t border-base-border px-2 py-2">
          <p className="font-mono text-[11px] font-semibold text-bear">{ibkrError.heading}</p>
          <p className="font-mono text-[10px] text-bear">{ibkrError.message}</p>
        </div>
      )}

      {mode === "ibkr" && ibkrRun.status === "done" && ibkrRun.result && (
        <ResultsView
          runId={ibkrRun.result.run_id}
          sweepId={ibkrRun.result.sweep_id}
          outcomesRecorded={ibkrRun.result.outcomes_recorded}
          discardedSignals={ibkrRun.result.discarded_signals}
        />
      )}
    </>
  );
}

export function BacktestPanel() {
  const [collapsed, setCollapsed] = useState(true); // starts collapsed, same reasoning ScannerPanel/FeatureEnginePanel already use — a fourth sidebar shouldn't grab space by default either
  const [widthPx, setWidthPx] = useState(DEFAULT_WIDTH);
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: widthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = collapsed ? COLLAPSED_WIDTH : widthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!collapsed && (
        <div
          onPointerDown={onResizeDown}
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={collapsed ? "Expand Backtest panel" : "Collapse Backtest panel"}
          >
            {collapsed ? "«" : "»"}
          </button>
          {!collapsed && <span className="font-mono text-xs font-semibold text-text-primary">Backtest</span>}
        </div>

        {!collapsed && (
          <div className="flex-1 overflow-y-auto">
            <BacktestForm />
          </div>
        )}
      </div>
    </div>
  );
}
