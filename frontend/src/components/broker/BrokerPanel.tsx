import { useRef, useState } from "react";
import { useBrokerStatus } from "../../hooks/useBrokerStatus";
import { useProtectedFeedStatus } from "../../hooks/useProtectedFeedStatus";
import { useSubscriptionStatus } from "../../hooks/useSubscriptionStatus";
import type { ProtectedFeedStatusWireShape, SubscriptionStatusWireShape } from "../../services/api-client";

// Same collapsible-width convention ScannerPanel.tsx established
// (MIN_WIDTH/MAX_WIDTH/COLLAPSED_WIDTH, drag-to-resize, "starts
// collapsed" posture) — reused verbatim for visual/interaction
// consistency with every other <main> sibling panel.
//
// Deliberately LOCAL component state (collapsed/widthPx), not threaded
// through WorkspaceContext.tsx — same reasoning BacktestPanel.tsx's own
// header comment already established, and BacktestResultsPanel.tsx
// reused after it: this task's own scope names exactly two shared files
// to touch (App.tsx, api-client.ts), and WorkspaceContext.tsx isn't one
// of them. The actual broker CONNECTION state this panel displays
// already lives on the backend (broker_registry) and is independently,
// correctly re-polled by every mounted instance of this panel — the
// full workspace's <main> AND a popped-out window's own <main> each get
// their own fresh useBrokerStatus() poll, so there's no correctness gap
// from keeping just the collapsed/widthPx CHROME local rather than
// synced. Flagged here explicitly, not a silent deviation — worth
// reconsidering only if Saqib wants this panel's chrome to persist
// across tabs the way Scanner's own does.
const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 300;

function StatusIndicator({ connected, loading }: { connected: boolean | null; loading: boolean }) {
  if (connected === null) {
    return <span className="font-mono text-[11px] text-text-muted">Checking…</span>;
  }
  return (
    <span className={`font-mono text-[11px] font-semibold ${connected ? "text-bull" : "text-text-muted"}`}>
      {connected ? "● Connected" : "○ Not connected"}
      {loading && <span className="ml-1 text-text-muted">…</span>}
    </span>
  );
}

function SubscribeForm({
  connected,
  busy,
  symbolActionError,
  onSubscribe,
}: {
  connected: boolean;
  /** Any broker action pending (the hook refuses a second one anyway). */
  busy: boolean;
  symbolActionError: string | null;
  onSubscribe: (symbol: string) => Promise<boolean>;
}) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  // Mirrors `input` synchronously (state is stale inside an async handler):
  // lets the post-await check see what the field holds NOW.
  const latestInputRef = useRef("");

  const handleSubscribe = async () => {
    if (busy) return; // UI-level guard; the hook's synchronous guard is the real one
    const submitted = input;
    const symbol = submitted.trim();
    if (!symbol) return;
    const ok = await onSubscribe(symbol);
    // Clear only if the field still holds what was submitted — text typed
    // while the request was pending belongs to the next subscribe.
    if (ok && latestInputRef.current === submitted) {
      latestInputRef.current = "";
      setInput("");
      inputRef.current?.focus();
    }
  };

  return (
    <div className="shrink-0 border-t border-base-border p-2">
      {symbolActionError && <div className="mb-1 font-mono text-[10px] text-bear">{symbolActionError}</div>}
      <div className="flex gap-1">
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => {
            const next = e.target.value.toUpperCase();
            latestInputRef.current = next;
            setInput(next);
          }}
          onKeyDown={(e) => e.key === "Enter" && handleSubscribe()}
          placeholder={connected ? "e.g. NVDA" : "Connect first"}
          maxLength={6}
          disabled={!connected}
          className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSubscribe}
          disabled={!connected || busy || !input.trim()}
          className="rounded border border-base-border px-2 py-1 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
        >
          Subscribe
        </button>
      </div>
    </div>
  );
}

// Plain-language text per backend `inventory.reason`. An unknown future
// reason falls back to showing the raw code rather than guessing.
function unavailableText(reason: string | null): string {
  switch (reason) {
    case "no_streaming_provider":
      return "No streaming provider is registered, so there is no inventory to show.";
    case "provider_not_connected":
      return "The provider is not connected. Any record it retained from before is not shown — it would not be an active inventory.";
    case "inventory_not_supported":
      return "This provider cannot report a subscription inventory.";
    case "snapshot_failed":
      return "The provider's inventory could not be read.";
    case "connection_state_unknown":
      return "The provider's connection state could not be read, so no inventory is shown.";
    default:
      return `Inventory unavailable${reason ? ` (${reason})` : ""}.`;
  }
}

function connectionText(connected: boolean | null): string {
  if (connected === null) return "unknown";
  return connected ? "connected" : "not connected";
}

function DiagnosticsReading({ data }: { data: SubscriptionStatusWireShape }) {
  const { inventory, provider } = data;
  return (
    <>
      {provider ? (
        <div className="px-2 py-1 font-mono text-[10px] text-text-muted">
          Provider: <span className="text-text-primary">{provider.id}</span> ({provider.class_name}) —{" "}
          {connectionText(data.connected)}
        </div>
      ) : (
        <div className="px-2 py-1 font-mono text-[10px] text-text-muted">Provider: none registered</div>
      )}

      {inventory.availability === "unavailable" && (
        <div className="px-2 py-1 font-mono text-[11px] text-text-muted">{unavailableText(inventory.reason)}</div>
      )}
      {inventory.availability === "available" && inventory.count === 0 && (
        <div className="px-2 py-1 font-mono text-[11px] text-text-muted">
          Connected; no subscribe requests are locally recorded.
        </div>
      )}
      {inventory.availability === "available" && inventory.symbols !== null && inventory.symbols.length > 0 && (
        <div className="px-2 py-1">
          <div className="font-mono text-[10px] text-text-muted">{inventory.count} locally tracked</div>
          <div className="mt-1 flex max-h-28 flex-wrap gap-1 overflow-y-auto">
            {inventory.symbols.map((s) => (
              <span key={s} className="rounded border border-base-border px-1 font-mono text-[10px] text-text-primary">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="px-2 py-1 font-mono text-[9px] text-text-muted">
        Capacity: {data.capacity.status} · Delivery: {data.delivery.status}
      </div>
      <div className="px-2 py-1 font-mono text-[9px] text-text-muted">
        Locally tracked requests only — not provider acknowledgement, proof of live delivery, ownership or capacity.
      </div>
    </>
  );
}

function SubscriptionDiagnosticsBody() {
  const { data, error, loadedAt, loading, refresh } = useSubscriptionStatus();

  return (
    <div className="max-h-64 overflow-y-auto pb-1">
      <div className="flex items-center justify-between px-2 py-1">
        <span className="font-mono text-[9px] text-text-muted">
          {loadedAt ? `Read at ${loadedAt.toLocaleTimeString()}` : ""}
        </span>
        {/* Never disabled: a newer Refresh supersedes a pending or hung one. */}
        <button
          onClick={refresh}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {loading && data === null && error === null && (
        <div className="px-2 py-2 font-mono text-[11px] text-text-muted">Loading subscription diagnostics…</div>
      )}
      {error !== null && data === null && (
        <div className="px-2 py-2 font-mono text-[11px] text-bear">Failed to load subscription diagnostics: {error}</div>
      )}
      {error !== null && data !== null && (
        <div className="px-2 py-1 font-mono text-[10px] text-bear">
          Refresh failed — showing the last successful reading: {error}
        </div>
      )}
      {data !== null && <DiagnosticsReading data={data} />}
    </div>
  );
}

function SubscriptionDiagnosticsSection() {
  const [open, setOpen] = useState(false);

  return (
    <div className="shrink-0 border-t border-base-border">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2 py-1 text-left font-mono text-[10px] font-semibold text-text-muted hover:text-text-primary"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Subscription diagnostics</span>
      </button>
      {/* Mounted only while expanded: collapsing unmounts the body, which
          invalidates any in-flight request. Re-expanding loads afresh. */}
      {open && <SubscriptionDiagnosticsBody />}
    </div>
  );
}

// ---- Protected feed (task `protected-feed-reconciliation-status`) ----------

function utcText(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${d.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

// Plain-language text per reconciler `state`. An unknown future state falls
// back to its raw code rather than guessing.
function protectedStateText(state: string): { text: string; failure: boolean } {
  switch (state) {
    case "never_attempted":
      return { text: "Installed, but no attempt has run yet.", failure: false };
    case "first_attempt_in_progress":
      return { text: "The first attempt is still running.", failure: false };
    case "completed":
      return { text: "Last attempt completed: every protected symbol was already recorded locally or had a request return.", failure: false };
    case "completed_with_failures":
      return { text: "Last attempt completed, but some subscription requests failed.", failure: true };
    case "protected_set_read_failed":
      return { text: "Last attempt could not read the protected set, so it requested nothing.", failure: true };
    case "no_streaming_provider":
      return { text: "Last attempt found no streaming provider, so it requested nothing.", failure: true };
    case "provider_disconnected":
      return { text: "Last attempt found the streaming provider disconnected.", failure: true };
    case "provider_check_failed":
      return { text: "Last attempt could not check the streaming provider's connection.", failure: true };
    case "interrupted":
      return { text: "Last attempt was interrupted (shutdown or provider change) before finishing.", failure: true };
    default:
      return { text: `Last attempt state: ${state}.`, failure: false };
  }
}

function requestOutcomeText(outcome: string, errorClass: string | null): string {
  switch (outcome) {
    case "locally_present":
      return "locally recorded (no request)";
    case "request_returned":
      return "request returned";
    case "request_failed":
      return `request failed${errorClass ? ` (${errorClass})` : ""}`;
    case "no_outcome":
      return "no outcome (cycle ended first)";
    default:
      return outcome;
  }
}

function ProtectedFeedReading({ data }: { data: ProtectedFeedStatusWireShape }) {
  if (data.status === "unavailable" || data.reconciler === null) {
    const reason =
      data.reason === "reconciler_not_installed"
        ? "No protected-feed reconciler is installed in this backend process (execution startup did not complete or is not running)."
        : data.reason === "snapshot_read_failed"
          ? "The reconciler's status could not be read."
          : `Protected-feed status unavailable${data.reason ? ` (${data.reason})` : ""}.`;
    return <div className="px-2 py-1 font-mono text-[11px] text-text-muted">{reason}</div>;
  }

  const { reconciler, protected_set: set, provider, requests } = data;
  const state = protectedStateText(reconciler.state);

  return (
    <>
      <div className={`px-2 py-1 font-mono text-[11px] ${state.failure ? "text-bear" : "text-text-muted"}`}>
        {state.text}
      </div>
      <div className="px-2 py-1 font-mono text-[10px] text-text-muted">
        {reconciler.running ? "Running" : "Not running"}
        {reconciler.cycle_in_progress ? " · cycle in progress" : ""} · {reconciler.attempts_started} attempt(s) started,{" "}
        {reconciler.attempts_completed} finished
        <br />
        Last attempt started: {utcText(reconciler.last_attempt_at)}
        <br />
        Last attempt finished: {utcText(reconciler.last_completed_at)}
      </div>

      {set !== null && (
        <div className="px-2 py-1">
          {set.availability === "never_read" ? (
            <div className="font-mono text-[11px] text-text-muted">
              The protected set has not been read successfully yet — this is not an empty set.
            </div>
          ) : set.count === 0 ? (
            <div className="font-mono text-[11px] text-text-muted">
              Protected set read OK at {utcText(set.read_at)}: no simulated positions or working orders need a feed.
            </div>
          ) : (
            <>
              <div className="font-mono text-[10px] text-text-muted">
                {set.count} protected symbol(s) · last successful read {utcText(set.read_at)}
              </div>
              <div className="mt-1 flex max-h-20 flex-wrap gap-1 overflow-y-auto">
                {(set.symbols ?? []).map((s) => (
                  <span key={s} className="rounded border border-base-border px-1 font-mono text-[10px] text-text-primary">
                    {s}
                  </span>
                ))}
              </div>
            </>
          )}
          {set.latest_read === "failed" && (
            <div className="mt-1 font-mono text-[10px] text-bear">
              The latest read ({utcText(set.latest_read_attempt_at)}) failed
              {set.latest_read_error ? ` (${set.latest_read_error})` : ""}.
              {set.availability === "read" ? ` Showing the symbols retained from the successful read at ${utcText(set.read_at)}.` : ""}
            </div>
          )}
        </div>
      )}

      <div className="px-2 py-1 font-mono text-[10px] text-text-muted">
        {provider
          ? `Provider at last attempt: ${provider.id} (${provider.class_name}) — ${connectionText(provider.connected)}`
          : "Provider at last attempt: none registered or not yet looked up"}
      </div>

      {requests !== null && (
        <div className="px-2 py-1">
          <div className="font-mono text-[10px] text-text-muted">
            Request outcomes from the attempt started {utcText(requests.recorded_at)} · {requests.provider.id} (
            {requests.provider.class_name}) · provider inventory {requests.inventory_available ? "readable" : "unavailable"}
            {reconciler.last_attempt_at && reconciler.last_attempt_at !== requests.recorded_at
              ? " — retained; a later attempt did not reach the request step"
              : ""}
          </div>
          {requests.entries.length === 0 ? (
            <div className="font-mono text-[10px] text-text-muted">No symbols needed a request in that attempt.</div>
          ) : (
            <ul className="mt-1 max-h-28 overflow-y-auto">
              {requests.entries.map((e) => (
                <li
                  key={e.symbol}
                  className={`font-mono text-[10px] ${e.outcome === "request_failed" ? "text-bear" : "text-text-primary"}`}
                >
                  {e.symbol}: {requestOutcomeText(e.outcome, e.error_class)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="px-2 py-1 font-mono text-[9px] text-text-muted">
        Committed simulated exposure and provider changes prompt a re-check. A {reconciler.interval_seconds}s periodic
        check recovers missed signals or failures; requests can still be delayed by a cycle or outage. These are request
        records only — not provider acknowledgement, proof that ticks are arriving, or confirmed protection.
      </div>
    </>
  );
}

function ProtectedFeedBody() {
  const { data, error, loadedAt, loading, refresh } = useProtectedFeedStatus();

  return (
    <div className="max-h-72 overflow-y-auto pb-1">
      <div className="flex items-center justify-between px-2 py-1">
        <span className="font-mono text-[9px] text-text-muted">
          {data?.read_at ? `Server read ${utcText(data.read_at)}` : loadedAt ? `Read at ${loadedAt.toLocaleTimeString()}` : ""}
        </span>
        {/* Never disabled: a newer Refresh supersedes a pending or hung one. */}
        <button
          onClick={refresh}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {loading && data === null && error === null && (
        <div className="px-2 py-2 font-mono text-[11px] text-text-muted">Loading protected-feed status…</div>
      )}
      {error !== null && data === null && (
        <div className="px-2 py-2 font-mono text-[11px] text-bear">Failed to load protected-feed status: {error}</div>
      )}
      {error !== null && data !== null && (
        <div className="px-2 py-1 font-mono text-[10px] text-bear">
          Refresh failed — showing the last successful reading{data.read_at ? ` (server read ${utcText(data.read_at)})` : ""}: {error}
        </div>
      )}
      {data !== null && <ProtectedFeedReading data={data} />}
    </div>
  );
}

function ProtectedFeedSection() {
  const [open, setOpen] = useState(false);

  return (
    <div className="shrink-0 border-t border-base-border">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2 py-1 text-left font-mono text-[10px] font-semibold text-text-muted hover:text-text-primary"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Protected feed</span>
      </button>
      {/* Mounted only while expanded: collapsing unmounts the body, which
          invalidates any in-flight request. Re-expanding loads afresh. */}
      {open && <ProtectedFeedBody />}
    </div>
  );
}

export function BrokerPanel() {
  const [collapsed, setCollapsed] = useState(true); // third sidebar in a row shouldn't grab space by default either — same posture Scanner/FeatureEngine/Backtest all start with
  const [widthPx, setWidthPx] = useState(DEFAULT_WIDTH);
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const {
    connected,
    statusLoading,
    statusError,
    connecting,
    connectError,
    connect,
    disconnecting,
    disconnectError,
    disconnect,
    subscribedSymbols,
    mutating,
    symbolActionError,
    subscribe,
    unsubscribe,
  } = useBrokerStatus();

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
          // Same widened (10px), always-visible handle as ScannerPanel's
          // own resizer.
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={collapsed ? "Expand Broker panel" : "Collapse Broker panel"}
          >
            {collapsed ? "«" : "»"}
          </button>
          {!collapsed && <span className="font-mono text-xs font-semibold text-text-primary">Broker</span>}
        </div>

        {!collapsed && (
          <>
            <div className="flex shrink-0 items-center justify-between border-b border-base-border px-2 py-1.5">
              <StatusIndicator connected={connected} loading={statusLoading} />
              <div className="flex gap-1">
                <button
                  onClick={() => void connect()}
                  disabled={connecting || disconnecting || connected === true}
                  className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
                >
                  {connecting ? "Connecting…" : "Connect"}
                </button>
                <button
                  onClick={() => void disconnect()}
                  disabled={connecting || disconnecting || connected !== true}
                  className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
                >
                  {disconnecting ? "…" : "Disconnect"}
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {statusError && (
                <div className="px-2 py-1.5 font-mono text-[10px] text-text-muted">
                  Status check failed — showing the last known reading: {statusError}
                </div>
              )}
              {connectError && (
                // Shown close to verbatim, honestly — this is the real
                // 502 detail backend/app/api/routes/broker.py raises
                // when IB Gateway/TWS isn't running/logged in, not
                // collapsed into a generic "connection failed."
                <div className="px-2 py-1.5 font-mono text-[10px] text-bear">{connectError}</div>
              )}
              {disconnectError && <div className="px-2 py-1.5 font-mono text-[10px] text-bear">{disconnectError}</div>}

              {connected === false && !connectError && (
                <div className="px-2 py-3 font-mono text-[11px] text-text-muted">
                  Not connected — this is IBKR's normal resting state outside a live session, not an error. IB
                  Gateway or TWS has to be running and logged in on your machine first; then click Connect.
                </div>
              )}

              {connected === true && subscribedSymbols.length === 0 && (
                <div className="px-2 py-3 font-mono text-[11px] text-text-muted">
                  Connected. No symbols subscribed yet — add one below.
                </div>
              )}

              {subscribedSymbols.map((s) => (
                <div key={s} className="flex items-center justify-between border-b border-base-border px-2 py-1.5">
                  <span className="font-mono text-xs text-text-primary">{s}</span>
                  <button
                    onClick={() => void unsubscribe(s)}
                    title={`Unsubscribe ${s}`}
                    disabled={mutating}
                    className="rounded px-1 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-bear disabled:opacity-50"
                  >
                    ×
                  </button>
                </div>
              ))}

              {subscribedSymbols.length > 0 && (
                <div className="px-2 py-1 font-mono text-[9px] text-text-muted">
                  Subscribed this session, from this panel only — not a read of everything IBKR is actually
                  streaming (no backend route exposes that yet).
                </div>
              )}
            </div>

            <SubscribeForm
              connected={connected === true}
              busy={mutating}
              symbolActionError={symbolActionError}
              onSubscribe={subscribe}
            />

            <SubscriptionDiagnosticsSection />
            <ProtectedFeedSection />
          </>
        )}
      </div>
    </div>
  );
}
