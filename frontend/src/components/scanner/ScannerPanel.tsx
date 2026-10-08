import { useRef, useState } from "react";
import { useWorkspace } from "../../state/WorkspaceContext";
import { useScannerObservation } from "../../hooks/useScannerObservation";
import { useScannerState } from "../../hooks/useScannerState";
import { useScannerUniverse } from "../../hooks/useScannerUniverse";
import type {
  ScannerObservationDetailWireShape,
  ScannerObservationRowWireShape,
  ScannerResultWireShape,
} from "../../services/api-client";

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;

// v1 score = rvol (regular session) or premarket_volume_ratio
// (pre-market) only — the two share one "activity" slot, mutually
// exclusive by session (Saqib's call, 2026-08-27; premarket_volume_ratio
// wired in 2026-08-28). gap/session-change terms exist in scorer.py and
// still run, but their weights are 0.0 in Settings, so they don't
// currently move the ranking; premarket_volume_ratio's own weight is
// ALSO 0.0 for now — wired in and tested, but inert until Saqib has
// actually looked at real pre-market values (missed 2026-08-28's
// session before this landed; next chance is Monday). The chip row
// below still shows every available reading regardless of weight —
// informative context even for the currently-inert ones. Flip weights
// back on server-side to bring any of them into the ranking; nothing
// here would need to change to reflect that.
const SCORE_BASIS_LABEL = "Ranked by RVOL / PM Vol (v1)";

function formatFeatureChip(key: string, value: number): { label: string; primary: boolean } {
  if (key === "rvol") return { label: `RVOL ${value.toFixed(2)}x`, primary: true };
  if (key === "premarket_volume_ratio") return { label: `PM Vol ${value.toFixed(2)}x`, primary: true };
  if (key === "gap_pct") return { label: `Gap ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`, primary: false };
  if (key === "session_pct_change") return { label: `Day ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`, primary: false };
  if (key === "atr_14_pct") return { label: `ATR ${value.toFixed(2)}%`, primary: false };
  return { label: `${key} ${value}`, primary: false };
}

const FEATURE_DISPLAY_ORDER = ["rvol", "premarket_volume_ratio", "gap_pct", "session_pct_change", "atr_14_pct"];

function ResultRow({ result, rank }: { result: ScannerResultWireShape; rank: number }) {
  const lowConfidence = result.inputs_available < 2;
  const orderedFeatures = FEATURE_DISPLAY_ORDER.filter((k) => k in result.features);

  return (
    <div className="flex flex-col gap-1 border-b border-base-border px-2 py-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-4 font-mono text-[10px] text-text-muted">{rank + 1}</span>
          <span className="font-mono text-xs font-medium text-text-primary">{result.symbol}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {lowConfidence && (
            <span
              title={`Only ${result.inputs_available}/3 inputs available — thin reading, not a confident score`}
              className="rounded bg-base-bg px-1 font-mono text-[9px] text-text-muted"
            >
              {result.inputs_available}/3
            </span>
          )}
          <span className={`font-mono text-xs font-semibold ${rank === 0 ? "text-signal" : "text-text-primary"}`}>
            {result.score.toFixed(2)}
          </span>
        </div>
      </div>
      {orderedFeatures.length > 0 && (
        <div className="flex flex-wrap gap-x-2 pl-6 font-mono text-[10px]">
          {orderedFeatures.map((key) => {
            const chip = formatFeatureChip(key, result.features[key]);
            return (
              <span key={key} className={chip.primary ? "font-semibold text-text-primary" : "text-text-muted"}>
                {chip.label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ResultsTab() {
  const { results, skipped, loading, error, lastUpdated, refresh } = useScannerState();

  return (
    <>
      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="px-2 py-3 font-mono text-[11px] text-bear">
            Failed to load: {error}
            {lastUpdated && results.length > 0 && " — showing last successful result"}
          </div>
        )}
        {!error && results.length === 0 && !loading && (
          <div className="px-2 py-3 font-mono text-[11px] text-text-muted">
            No symbols scored yet — waiting on at least one recorded 1m candle.
          </div>
        )}
        {results.map((r, i) => (
          <ResultRow key={r.symbol} result={r} rank={i} />
        ))}
      </div>
      {skipped.length > 0 && (
        <div className="shrink-0 border-t border-base-border px-2 py-1 font-mono text-[9px] text-text-muted">
          No data yet: {skipped.join(", ")}
        </div>
      )}
      <div className="flex shrink-0 items-center justify-between border-t border-base-border px-2 py-1">
        {/* Never disabled: pressing it while a request is pending starts a
            newer one that supersedes it (recovery from a hung request). */}
        <button
          onClick={refresh}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
        >
          Refresh
        </button>
        {loading && <span className="font-mono text-[9px] text-text-muted">loading…</span>}
        {lastUpdated && <span className="font-mono text-[9px] text-text-muted">Updated {lastUpdated.toLocaleTimeString()}</span>}
      </div>
    </>
  );
}

function formatUtc(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function parseUtcMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** Whole-second duration as its two largest units (e.g. "2d 17h", "5m 3s"). */
function formatDuration(totalSeconds: number): string {
  const s = Math.floor(totalSeconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

/** The row's source-candle line. Unknown stays unknown; a source time after
 * the server read time is stated as such (no age, no clamping to 0); the age
 * is always labelled as relative to the server read time of this response. */
function formatSourceTime(sourceIso: string | null | undefined, readAtIso: string | null | undefined): string {
  if (!sourceIso) return "Source candle time unknown";
  const sourceMs = parseUtcMs(sourceIso);
  if (sourceMs === null) return `Source candle ${sourceIso} (unreadable timestamp)`;
  const label = `Source candle ${formatUtc(sourceIso)}`;
  const readMs = parseUtcMs(readAtIso);
  if (readMs === null) return `${label} · age unavailable (no server read time)`;
  if (sourceMs > readMs) return `${label} · later than the server read time — age not shown`;
  return `${label} · age ${formatDuration((readMs - sourceMs) / 1000)} at server read`;
}

function ObservationRow({ row, readAt }: { row: ScannerObservationRowWireShape; readAt: string | null | undefined }) {
  const lowConfidence = row.inputs_available < 2;
  const features = FEATURE_DISPLAY_ORDER.filter((k) => typeof row.features[k] === "number");

  return (
    <div className="flex flex-col gap-1 border-b border-base-border px-2 py-1.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs font-medium text-text-primary">{row.symbol}</span>
        <div className="flex items-center gap-1.5">
          {lowConfidence && (
            <span
              title={`Only ${row.inputs_available}/3 inputs available — thin reading, not a confident score`}
              className="rounded bg-base-bg px-1 font-mono text-[9px] text-text-muted"
            >
              {row.inputs_available}/3
            </span>
          )}
          <span className="font-mono text-xs font-semibold text-text-primary">
            {row.score === null ? "—" : row.score.toFixed(2)}
          </span>
        </div>
      </div>
      {features.length > 0 && (
        <div className="flex flex-wrap gap-x-2 font-mono text-[10px]">
          {features.map((key) => {
            const chip = formatFeatureChip(key, row.features[key] as number);
            return (
              <span key={key} className={chip.primary ? "font-semibold text-text-primary" : "text-text-muted"}>
                {chip.label}
              </span>
            );
          })}
        </div>
      )}
      <div data-testid="observation-source-time" className="font-mono text-[9px] text-text-muted">
        {formatSourceTime(row.source_candle_ts, readAt)}
      </div>
    </div>
  );
}

/** Earliest/latest known source candle across the retained rows, plus how
 * many rows have no usable source time. Descriptive only — no threshold. */
function summarizeSourceTimes(rows: ScannerObservationRowWireShape[]): { earliest: string; latest: string; unknown: number } | null {
  let earliest: { ms: number; iso: string } | null = null;
  let latest: { ms: number; iso: string } | null = null;
  let unknown = 0;
  for (const row of rows) {
    const ms = parseUtcMs(row.source_candle_ts);
    if (ms === null || !row.source_candle_ts) {
      unknown += 1;
      continue;
    }
    if (earliest === null || ms < earliest.ms) earliest = { ms, iso: row.source_candle_ts };
    if (latest === null || ms > latest.ms) latest = { ms, iso: row.source_candle_ts };
  }
  if (earliest === null || latest === null) return unknown > 0 ? { earliest: "", latest: "", unknown } : null;
  return { earliest: earliest.iso, latest: latest.iso, unknown };
}

function ObservationSourceTimes({
  observation,
  readAt,
}: {
  observation: ScannerObservationDetailWireShape;
  readAt: string | null | undefined;
}) {
  const summary = summarizeSourceTimes(observation.results);
  if (observation.results.length === 0 || summary === null) return null;
  return (
    <div data-testid="observation-source-summary" className="px-2 py-1 font-mono text-[10px] text-text-muted">
      <div>
        Scan completed {formatUtc(observation.last_success_at)}
        {readAt ? ` · server read ${formatUtc(readAt)}` : ""}
      </div>
      <div>
        {summary.earliest
          ? `Source candles ${formatUtc(summary.earliest)}${summary.latest !== summary.earliest ? ` – ${formatUtc(summary.latest)}` : ""}`
          : "Source candle times unknown"}
        {summary.earliest && summary.unknown > 0 ? ` · ${summary.unknown} unknown` : ""}
      </div>
      <div className="text-[9px]">
        Scan completion time and data time differ: a scan finishing just now can still score older feature candles. Ages
        are measured from the server read time shown, not a live counter, and are not a freshness verdict.
      </div>
    </div>
  );
}

function ObservationStatus({ observation, running }: { observation: ScannerObservationDetailWireShape; running: boolean }) {
  const { retained, latest_attempt: attempt } = observation;
  const retainedNote =
    retained === "none"
      ? "No successful scan has been retained."
      : `Showing results retained from the last success at ${formatUtc(observation.last_success_at)}.`;

  return (
    <>
      {attempt === "failed" && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-bear">
          Latest attempt{observation.last_attempt_at ? ` at ${formatUtc(observation.last_attempt_at)}` : ""} failed
          {observation.last_error ? `: ${observation.last_error}` : ""}. {retainedNote}
        </div>
      )}
      {attempt === "interrupted" && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
          The latest attempt at {formatUtc(observation.last_attempt_at)} was interrupted before it completed. {retainedNote}
        </div>
      )}
      {!running && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
          Worker stopped.{" "}
          {retained === "none" ? "No successful scan was retained." : `Showing results retained from the last success at ${formatUtc(observation.last_success_at)}.`}
        </div>
      )}
      {retained === "none" && attempt === "in_progress" && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">First scan in progress — no successful scan yet.</div>
      )}
      {retained === "none" && attempt === "none" && running && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">No successful scan yet — none has been attempted.</div>
      )}
      {retained === "none" && attempt === "none" && !running && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">No scan was ever attempted.</div>
      )}
      {retained === "none" && (attempt === "failed" || attempt === "interrupted") && running && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">No successful scan yet.</div>
      )}
      {retained === "empty" && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
          Last successful scan at {formatUtc(observation.last_success_at)} scored no symbols (successful, empty
          result).
        </div>
      )}
      {retained === "populated" && attempt !== "failed" && running && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
          Last successful scan at {formatUtc(observation.last_success_at)}.
        </div>
      )}
    </>
  );
}

function ObservationBody() {
  const { data, error, loadedAt, loading, refresh } = useScannerObservation();
  const observation = data?.observation ?? null;
  const worker = data?.worker ?? null;

  return (
    <div className="flex max-h-80 flex-col overflow-y-auto">
      <div className="flex shrink-0 items-center justify-between px-2 py-1">
        {/* Manual only, never disabled: pressing it while a request is pending
            starts a newer one that supersedes it. */}
        <button
          onClick={refresh}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
        >
          Refresh
        </button>
        <span className="font-mono text-[9px] text-text-muted">
          {loading ? "loading…" : loadedAt ? `Read ${loadedAt.toLocaleTimeString()}` : ""}
        </span>
      </div>

      {error && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-bear">
          {data ? `Refresh failed: ${error} — showing the last loaded read.` : `Failed to load: ${error}`}
        </div>
      )}
      {!data && !error && loading && <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">Loading…</div>}

      {data?.status === "unavailable" && (
        <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
          Unavailable — {data.reason ?? "no scheduled observation is reported by this backend"}. Nothing is being
          scanned on a schedule here; use the Results tab for an on-demand scan.
        </div>
      )}

      {data?.status === "available" && observation && worker && (
        <>
          <div className="px-2 py-1 font-mono text-[10px] text-text-muted">
            Worker {worker.running ? "running" : "stopped"} · {worker.cycle_running ? "scan in progress" : "no scan in progress"}
          </div>
          <ObservationStatus observation={observation} running={worker.running} />
          {(observation.retained !== "none" || observation.universe.length > 0) && (
            <div
              className="px-2 py-1 font-mono text-[10px] text-text-muted"
              title={observation.skipped.length > 0 ? `Skipped: ${observation.skipped.join(", ")}` : undefined}
            >
              Universe {observation.universe.length} · Scored {observation.results.length} · Skipped{" "}
              {observation.skipped.length}
            </div>
          )}
          <ObservationSourceTimes observation={observation} readAt={data.read_at} />
          {observation.results.length > 0 && (
            <>
              <div className="px-2 py-1 font-mono text-[9px] text-text-muted">
                Activity observations — not execution recommendations.
              </div>
              {observation.results.map((r) => (
                <ObservationRow key={r.symbol} row={r} readAt={data.read_at} />
              ))}
            </>
          )}
          <div className="px-2 py-1.5 font-mono text-[9px] text-text-muted">
            Worker availability does not establish healthy feed delivery or complete coverage.
          </div>
        </>
      )}
    </div>
  );
}

function ScheduledObservationSection() {
  const [open, setOpen] = useState(false);

  return (
    <div className="shrink-0 border-t border-base-border">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2 py-1 text-left font-mono text-[10px] font-semibold text-text-muted hover:text-text-primary"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Scheduled observation</span>
      </button>
      {/* Mounted only while expanded: collapsing unmounts the body, which
          invalidates any in-flight request. Re-expanding loads afresh. */}
      {open && <ObservationBody />}
    </div>
  );
}

function UniverseTab() {
  const {
    symbols,
    hasLoaded,
    loading,
    loadError,
    staleAfterWrite,
    mutationError,
    pendingAdd,
    pendingRemove,
    mutating,
    addSymbol,
    removeSymbol,
    refresh,
  } = useScannerUniverse();
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleAdd = async () => {
    // `mutating` is render-late; the hook's own synchronous guard is what
    // really stops a second Enter / click in the same tick.
    if (mutating) return;
    const submitted = input;
    const symbol = submitted.trim();
    if (!symbol) return;
    const ok = await addSymbol(symbol);
    if (ok) {
      // Only clear what was actually submitted — text typed while the add
      // was pending is kept.
      setInput((current) => (current === submitted ? "" : current));
      inputRef.current?.focus();
    }
  };

  const initialLoading = !hasLoaded && !loadError;
  const initialFailure = !hasLoaded && loadError !== null;

  return (
    <>
      <div className="flex-1 overflow-y-auto">
        {initialLoading && <div className="px-2 py-3 font-mono text-[11px] text-text-muted">Loading universe…</div>}
        {initialFailure && (
          <div className="px-2 py-3 font-mono text-[11px] text-bear">Failed to load the universe: {loadError}</div>
        )}
        {hasLoaded && symbols.length === 0 && !loadError && !loading && (
          <div className="px-2 py-3 font-mono text-[11px] text-text-muted">Universe is empty — add a symbol below.</div>
        )}
        {hasLoaded && symbols.length === 0 && loadError && (
          <div className="px-2 py-3 font-mono text-[11px] text-text-muted">The last loaded universe was empty.</div>
        )}
        {symbols.map((s) => (
          <div key={s.symbol} className="flex items-center justify-between border-b border-base-border px-2 py-1.5">
            <span className="font-mono text-xs text-text-primary">{s.symbol}</span>
            <button
              onClick={() => removeSymbol(s.symbol)}
              disabled={mutating}
              title={`Remove ${s.symbol} from the universe`}
              className="rounded px-1 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-bear disabled:opacity-50"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div className="shrink-0 border-t border-base-border p-2">
        {mutationError && <div className="mb-1 font-mono text-[10px] text-bear">{mutationError}</div>}
        {loadError && hasLoaded && (
          <div className="mb-1 font-mono text-[10px] text-bear">
            {staleAfterWrite ? "Change saved, but reloading the universe failed" : "Failed to reload the universe"}:{" "}
            {loadError} — showing the last loaded list{staleAfterWrite ? ", which may be out of date" : ""}.
          </div>
        )}
        {(pendingAdd || pendingRemove !== null || (loading && hasLoaded)) && (
          <div className="mb-1 font-mono text-[9px] text-text-muted">
            {pendingAdd ? "Adding…" : pendingRemove !== null ? `Removing ${pendingRemove}…` : "Reloading…"}
          </div>
        )}
        <div className="flex gap-1">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="e.g. NVDA"
            maxLength={6}
            className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
          />
          <button
            onClick={handleAdd}
            disabled={mutating || !input.trim()}
            className="rounded border border-base-border px-2 py-1 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
          >
            {pendingAdd ? "Adding…" : "Add"}
          </button>
          {loadError && (
            <button
              onClick={refresh}
              disabled={mutating}
              className="rounded border border-base-border px-2 py-1 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </>
  );
}

export function ScannerPanel() {
  const { scannerCollapsed, scannerWidthPx, setScannerCollapsed, setScannerWidthPx } = useWorkspace();
  const [tab, setTab] = useState<"results" | "universe">("results");
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: scannerWidthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setScannerWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = scannerCollapsed ? COLLAPSED_WIDTH : scannerWidthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!scannerCollapsed && (
        <div
          onPointerDown={onResizeDown}
          // Same widened (10px), always-visible handle as FeatureEnginePanel's
          // own resizer — see that component for why 10px over InfoTab's
          // original 6px (decision #49).
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setScannerCollapsed(!scannerCollapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={scannerCollapsed ? "Expand Scanner panel" : "Collapse Scanner panel"}
          >
            {scannerCollapsed ? "«" : "»"}
          </button>
          {!scannerCollapsed && <span className="font-mono text-xs font-semibold text-text-primary">Scanner</span>}
        </div>

        {!scannerCollapsed && (
          <>
            <div className="flex shrink-0 items-center justify-between border-b border-base-border px-2 py-1">
              <div className="flex gap-1">
                <button
                  onClick={() => setTab("results")}
                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                    tab === "results" ? "bg-base-bg text-text-primary" : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  Results
                </button>
                <button
                  onClick={() => setTab("universe")}
                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                    tab === "universe" ? "bg-base-bg text-text-primary" : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  Universe
                </button>
              </div>
              {tab === "results" && <span className="font-mono text-[9px] text-text-muted">{SCORE_BASIS_LABEL}</span>}
            </div>

            <div className="flex min-h-0 flex-1 flex-col">{tab === "results" ? <ResultsTab /> : <UniverseTab />}</div>
            <ScheduledObservationSection />
          </>
        )}
      </div>
    </div>
  );
}
