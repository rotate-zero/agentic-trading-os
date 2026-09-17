import { useDataFeedStatus } from "../../hooks/useDataFeedStatus";

// A small dot, not a pill/badge with its own background — this sits inside
// an already-compact header next to LayoutsMenu/GridPicker, so it follows
// the same restrained visual weight as the existing "Phase 1 — static mock
// data" tag in App.tsx rather than introducing a new, heavier UI pattern for
// what's meant to be a glanceable status light, not a dashboard widget.
//
// Colors deliberately avoid `bear` (red) for the ordinary disconnected
// case — that token means "bearish/error" everywhere else in this app
// (ScannerPanel, InfoTab, etc.), and a routine "not connected right now" is
// not an error state, per this task's own explicit framing. `text-muted`
// reads as a calm, off status light; `bull` (green) reads as calm and
// positive for on. `signal` (amber) is reserved for the one genuine
// attention-worthy case: a reconnect attempt that just failed.
function StatusDot({ connected }: { connected: boolean | null }) {
  const color = connected === true ? "bg-bull" : connected === false ? "bg-text-muted" : "bg-text-muted opacity-40";
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${color}`} />;
}

/**
 * Small, persistent header indicator for the two auto-connect-on-startup
 * market data providers (Finnhub streaming, Polygon historical) — real,
 * working `GET /finnhub/status` / `GET /market-data/status` routes that had
 * zero frontend representation before this task. Mounted in App.tsx's
 * `<header>` in both shells (full workspace + popped-out window) — NOT a
 * new `<main>` panel, deliberately: this is lower-stakes, read-only, and
 * doesn't need dedicated screen real estate the way a full IBKR connection
 * panel does.
 *
 * Read-only for Polygon; Finnhub gets one small "Reconnect" affordance —
 * see connectFinnhub()'s doc comment in api-client.ts for the real,
 * confirmed gap that justifies that one exception (no auto-reconnect on an
 * unexpected WebSocket close, deferred to a future Phase 4
 * ConnectionManager; confirmed directly with Saqib before adding it).
 * Polygon has no equivalent button since it has no equivalent gap.
 */
export function DataFeedStatus() {
  const {
    finnhubConnected,
    polygonConnected,
    polygonRole,
    loading,
    reconnecting,
    reconnectError,
    reconnectFinnhub,
  } = useDataFeedStatus();

  if (loading) {
    return <span className="font-mono text-[11px] text-text-muted">Data feeds…</span>;
  }

  const polygonLabel = polygonRole === "historical+streaming" ? "Polygon · hist+stream" : "Polygon";

  return (
    <div className="flex items-center gap-3 font-mono text-[11px] text-text-muted">
      <div
        className="flex items-center gap-1.5"
        title={
          finnhubConnected
            ? "Finnhub — real-time streaming, connected"
            : "Finnhub — disconnected (no live WebSocket)"
        }
      >
        <StatusDot connected={finnhubConnected} />
        <span>Finnhub</span>
        {finnhubConnected === false && (
          <button
            onClick={reconnectFinnhub}
            disabled={reconnecting}
            className="rounded border border-base-border px-1 py-0.5 text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-40"
          >
            {reconnecting ? "Reconnecting…" : "Reconnect"}
          </button>
        )}
        {reconnectError && (
          <span className="text-signal" title={`Reconnect failed: ${reconnectError}`}>
            !
          </span>
        )}
      </div>

      <div
        className="flex items-center gap-1.5"
        title={
          polygonConnected
            ? `Polygon — ${polygonRole === "historical+streaming" ? "historical + streaming fallback (15-min delayed)" : "historical, connected"}`
            : "Polygon — disconnected"
        }
      >
        <StatusDot connected={polygonConnected} />
        <span>{polygonLabel}</span>
      </div>
    </div>
  );
}
