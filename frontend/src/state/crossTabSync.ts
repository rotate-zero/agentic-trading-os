/**
 * Cross-tab live sync for the workspace session — the piece behind the
 * multi-monitor pop-out (App.tsx's `/window/:id` route, MainWindowTabs.tsx's
 * pop-out button). localStorage alone only tells OTHER tabs something
 * changed via the native `storage` event, and only fires in tabs that
 * DIDN'T make the write — a tab never gets its own `storage` event for its
 * own write. That's backwards for "every open tab of this app stays live"
 * without extra plumbing. BroadcastChannel is the more direct fit:
 * same-origin pub/sub, built for exactly this, no polling.
 *
 * Deliberately NOT used to transmit the actual state payload. Every tab
 * already writes the full session/saved-layouts state to localStorage
 * synchronously (see WorkspaceContext.tsx's persistence effects) — a
 * broadcast here is just "hey, localStorage changed, go re-read it." The
 * receiving tab calls the exact same loadSession()/loadSavedLayouts()
 * parsing WorkspaceContext already uses on initial mount, so there's only
 * ever one code path that turns localStorage into state, whether that's
 * "first load" or "another tab just changed something."
 *
 * Gracefully degrades to today's single-tab-only behavior (no error, no
 * cross-tab sync, silent no-op) if BroadcastChannel isn't available.
 */

export type SyncKind = "session" | "saved-layouts";
export interface SyncMessage {
  kind: SyncKind;
  originId: string;
}

const CHANNEL_NAME = "trading-workspace:sync";

// One id per browser tab — module-scoped (not per-component), so it's
// stable for the lifetime of this tab regardless of how many
// WorkspaceProvider instances mount/unmount within it (there's normally
// just one, but nothing should depend on that).
export const TAB_ID = `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

let channel: BroadcastChannel | null = null;
try {
  if (typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel(CHANNEL_NAME);
  }
} catch {
  channel = null; // same graceful degrade — some embedded/older environments throw on construction rather than lacking the global
}

export function broadcastSync(kind: SyncKind): void {
  channel?.postMessage({ kind, originId: TAB_ID } satisfies SyncMessage);
}

export function subscribeSync(onMessage: (msg: SyncMessage) => void): () => void {
  if (!channel) return () => {};
  const activeChannel = channel;
  const handler = (event: MessageEvent<SyncMessage>) => {
    if (event.data?.originId === TAB_ID) return; // ignore our own broadcast
    onMessage(event.data);
  };
  activeChannel.addEventListener("message", handler);
  return () => activeChannel.removeEventListener("message", handler);
}
