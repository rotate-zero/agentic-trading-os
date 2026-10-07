import { useExecutionTradeDetail } from "../../hooks/useExecutionTradeDetail";
import type { ExecutionTradeDetailWireShape } from "../../services/api-client";

function utc(value: string | null): string {
  if (value === null) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toISOString().replace("T", " ").replace(/(?:\.000)?Z$/, " UTC");
}

function Group({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div className="border-t border-base-border px-2 py-1" data-testid={`lifecycle-${title.toLowerCase().replace(/ /g, "-")}`}>
      <div className="font-semibold text-text-primary">{title} ({count})</div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-text-muted">{children}</div>;
}

function outcomeLabel(outcome: ExecutionTradeDetailWireShape["outcome"]): string {
  if (outcome.outcome_status === null && outcome.outcome_id === null) return "No outcome recorded.";
  const status = outcome.outcome_status ?? "not recorded";
  return outcome.outcome_id === null
    ? `Outcome status: ${status}. No outcome row is linked; the ledger stores no failure reason.`
    : `Outcome status: ${status}.`;
}

function Lifecycle({ data }: { data: ExecutionTradeDetailWireShape }) {
  const { trade, orders, fills, positions, exit_requests: exits, outcome } = data;
  const rejected = trade.decision === "rejected";
  return (
    <div data-testid="trade-lifecycle">
      <div className="px-2 py-1 text-text-muted">
        <div>
          Decision: <span className={rejected ? "text-bear" : "text-bull"}>{trade.decision}</span> · Trade status: {trade.status ?? "none"} · {trade.direction}
        </div>
        <div>Requested mode: {trade.execution_mode ?? "not recorded"} · Venue: {trade.execution_venue ?? "not recorded"}</div>
        <div className="break-words">Reasons: {trade.reasons?.length ? trade.reasons.join(", ") : "none recorded"}</div>
        <div>Recorded <time dateTime={trade.created_at}>{utc(trade.created_at)}</time></div>
      </div>

      <Group title="Orders" count={orders.length}>
        {orders.length === 0 && (
          <Empty>
            {rejected
              ? "Rejected attempt: no downstream records are expected."
              : "No order recorded yet. Approval alone does not establish an order or fill."}
          </Empty>
        )}
        {orders.map((order) => (
          <div key={order.id} className="break-all text-text-muted">
            #{order.id} {order.position_effect} {order.side} {order.qty} {order.order_type}
            {order.limit_price !== null && ` @ ${order.limit_price}`} · <span className="text-text-primary">{order.status}</span>
            {order.exit_reason && ` · exit ${order.exit_reason}`}
            {order.reject_reason && <span className="text-bear"> · rejected: {order.reject_reason}</span>}
            <div>{order.client_order_id}</div>
          </div>
        ))}
      </Group>

      <Group title="Fills" count={fills.length}>
        {fills.length === 0 && <Empty>{orders.length === 0 ? "No fills recorded." : "No fills recorded yet for these orders."}</Empty>}
        {fills.map((fill) => (
          <div key={fill.ledger_seq} className="break-all text-text-muted">
            seq {fill.ledger_seq} · {fill.qty} @ {fill.price} · commission {fill.commission ?? "not reported"} · {utc(fill.venue_ts)}
            {fill.anomaly && <span className="text-bear"> · {fill.anomaly}</span>}
            <div>{fill.client_order_id}</div>
          </div>
        ))}
      </Group>

      <Group title="Positions" count={positions.length}>
        {positions.length === 0 && <Empty>No position recorded.</Empty>}
        {positions.map((position) => (
          <div key={position.position_id} className="break-all text-text-muted">
            <span className="text-text-primary">{position.status}</span> {position.side} {position.qty} @ {position.avg_price} ·
            stop {position.stop ?? "—"} · target {position.target ?? "—"} · exit attempts {position.exit_attempt}
            <div>Opened {utc(position.opened_at)} · Closed {utc(position.closed_at)} · Realized P&amp;L {position.realized_pnl ?? "—"}</div>
          </div>
        ))}
      </Group>

      <Group title="Exit requests" count={exits.length}>
        {exits.length === 0 && <Empty>No exit request recorded.</Empty>}
        {exits.map((request) => (
          <div key={request.position_id} className="break-all text-text-muted">
            <span className="text-text-primary">{request.exit_reason}</span> trigger {request.trigger_price} at {utc(request.trigger_ts)} ·
            position {request.position_status} ({request.remaining_qty} remaining)
            {request.retry_after && ` · retry after ${utc(request.retry_after)}`}
            {request.eod_flatten_at && (
              <div>EOD window {utc(request.eod_flatten_at)} → {utc(request.eod_close_at)}{request.eod_expired_at && ` · placement expired ${utc(request.eod_expired_at)}`}</div>
            )}
            {request.fallback_reason && (
              <div>First fallback observation: {request.fallback_reason} {request.fallback_trigger_price} at {utc(request.fallback_trigger_ts)}</div>
            )}
          </div>
        ))}
      </Group>

      <div className="border-t border-base-border px-2 py-1" data-testid="lifecycle-outcome">
        <div className="font-semibold text-text-primary">Recorded outcome</div>
        <div className="text-text-muted">{outcomeLabel(outcome)}</div>
        {outcome.summary && (
          <div className="break-all text-text-muted">
            {outcome.summary.exit_reason} · {outcome.summary.entry_qty} @ {outcome.summary.entry_price} → {outcome.summary.exit_qty} @ {outcome.summary.exit_price} ·
            P&amp;L {outcome.summary.realized_pnl} · R {outcome.summary.realized_r} · commission {outcome.summary.commission_total ?? "not reported"} ·
            held {outcome.summary.holding_seconds}s
            <div>{outcome.outcome_id}</div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Read-only lifecycle detail for one recorded authorization. Mounted only while
 * its trade is selected, so collapsing it unmounts the hook and any in-flight
 * response is ignored. No trading, retry or re-arm controls.
 */
export function TradeLifecycleDetail({ tradeId, onClose }: { tradeId: string; onClose: () => void }) {
  const { detail, loading, error, notFound, refresh } = useExecutionTradeDetail(tradeId);
  return (
    <div className="mt-1 rounded border border-base-border font-mono text-[10px]" data-testid="trade-lifecycle-detail">
      <div className="flex items-center justify-between gap-1 px-2 py-1">
        <span className="text-text-primary">Lifecycle</span>
        <span>
          <button onClick={refresh} className="rounded px-1 py-0.5 text-signal hover:bg-base-bg">Refresh</button>
          <button onClick={onClose} className="rounded px-1 py-0.5 text-text-muted hover:bg-base-bg">Hide</button>
        </span>
      </div>
      {loading && <p className="px-2 pb-1 text-text-muted">Loading lifecycle…</p>}
      {notFound && <p className="px-2 pb-1 text-bear">This trade was not found. It may have been removed.</p>}
      {error && (
        <p className="px-2 pb-1 text-bear">
          Could not fetch lifecycle: {error}{detail !== null && " Showing last loaded detail."}
        </p>
      )}
      {detail !== null && <Lifecycle data={detail} />}
    </div>
  );
}
