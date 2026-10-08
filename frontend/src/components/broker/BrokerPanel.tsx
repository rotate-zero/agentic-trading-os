import { useRef, useState } from "react";
import { useBrokerStatus } from "../../hooks/useBrokerStatus";
import { useSubscriptionStatus } from "../../hooks/useSubscriptionStatus";
import type { SubscriptionStatusWireShape } from "../../services/api-client";

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
          </>
        )}
      </div>
    </div>
  );
}
