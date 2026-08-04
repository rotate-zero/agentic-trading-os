import { WS_BASE_URL } from "../config";

// Matches the envelope shape the backend's WebSocket Gateway sends —
// see backend/app/api/websocket/channels.py's broadcast() call.
export interface WireMessage {
  channel: string;
  symbol?: string | null;
  event_type?: string;
  payload?: Record<string, unknown>;
  timestamp?: string;
  [key: string]: unknown; // "_meta" acks (subscribed/unsubscribed/error) don't match the shape above
}

type Handler = (message: WireMessage) => void;

/**
 * One shared WebSocket connection for the whole app, matching the
 * backend's design (system-design.md §4.12: "a single multiplexed
 * connection, topic-tagged envelopes"). Multiple hooks/components can
 * subscribe to the same channel — each handler fires independently, and
 * the underlying WS "subscribe" message is only sent once per channel
 * (reference-counted), only "unsubscribe" once nothing needs it anymore.
 */
class WorkspaceSocket {
  private ws: WebSocket | null = null;
  private connecting = false;
  private reconnectDelayMs = 1000;
  private readonly maxReconnectDelayMs = 15000;
  private handlers = new Map<string, Set<Handler>>();
  private subscribedChannels = new Set<string>();

  private ensureConnected(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    if (this.connecting) return;
    this.connecting = true;

    const ws = new WebSocket(WS_BASE_URL);

    ws.onopen = () => {
      this.connecting = false;
      this.reconnectDelayMs = 1000; // reset backoff on a successful connect
      for (const channel of this.subscribedChannels) {
        ws.send(JSON.stringify({ action: "subscribe", channel }));
      }
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      let message: WireMessage;
      try {
        message = JSON.parse(event.data) as WireMessage;
      } catch {
        console.warn("WorkspaceSocket: received non-JSON message, ignoring");
        return;
      }
      const set = this.handlers.get(message.channel);
      if (set) {
        for (const handler of set) handler(message);
      }
    };

    ws.onclose = () => {
      this.ws = null;
      this.connecting = false;
      // Reconnect with exponential backoff — the backend may be
      // restarting (e.g. picking up a code change), not gone for good.
      setTimeout(() => this.ensureConnected(), this.reconnectDelayMs);
      this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, this.maxReconnectDelayMs);
    };

    ws.onerror = () => {
      ws.close();
    };

    this.ws = ws;
  }

  /**
   * Subscribe to a channel. Returns an unsubscribe function — call it on
   * cleanup (e.g. a React effect's return value) rather than leaving the
   * subscription dangling.
   */
  subscribe(channel: string, handler: Handler): () => void {
    this.ensureConnected();

    if (!this.handlers.has(channel)) this.handlers.set(channel, new Set());
    this.handlers.get(channel)!.add(handler);

    const alreadySubscribed = this.subscribedChannels.has(channel);
    this.subscribedChannels.add(channel);
    if (!alreadySubscribed && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: "subscribe", channel }));
    }

    return () => {
      const set = this.handlers.get(channel);
      set?.delete(handler);
      if (set && set.size === 0) {
        this.handlers.delete(channel);
        this.subscribedChannels.delete(channel);
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ action: "unsubscribe", channel }));
        }
      }
    };
  }
}

// Module-level singleton — ES modules are only evaluated once, so every
// importer shares this one instance, matching the backend's "one
// multiplexed connection" design rather than opening a socket per hook.
export const workspaceSocket = new WorkspaceSocket();
