import { useState } from "react";
import { usePortfolioState } from "../../hooks/usePortfolioState";
import type {
  PortfolioStateExposureWireShape,
  PortfolioStateMarkWireShape,
  PortfolioStateWireShape,
} from "../../services/api-client";

// "Portfolio details" (`live-portfolio-details`) — the expandable detail view
// beneath the compact WorldViewSummary. Reads GET /intelligence/portfolio-state,
// a read-only projection of the running simulated Portfolio State. Amounts are
// shown exactly as the server sends them (decimal strings, never parsed into
// floats); an unavailable value (`null`) is "—" with an explanation, never 0.
// Nothing here implies a connected real-money account: the only supported mode
// is simulated and buying power/cash have no source.

const DASH = "—";

function amount(value: string | null): string {
  return value ?? DASH;
}

function modeLabel(mode: string): string {
  return mode === "simulated" ? "Simulated" : `${mode} (unrecognised mode)`;
}

function pnlTone(value: string | null): string {
  if (value === null) return "text-text-muted";
  if (value.startsWith("-")) return "text-bear";
  return Number(value) > 0 ? "text-bull" : "text-text-primary";
}

function SummaryRow({ label, value, note, tone }: { label: string; value: string | null; note?: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-text-muted">{label}</span>
      <span className="text-right">
        <span className={tone ?? "text-text-primary"}>{amount(value)}</span>
        {note && <span className="ml-1 text-[10px] text-text-muted">{note}</span>}
      </span>
    </div>
  );
}

function ExposureRows({
  title,
  hint,
  rows,
  marks,
}: {
  title: string;
  hint: string;
  rows: PortfolioStateExposureWireShape[];
  marks: Map<string, PortfolioStateMarkWireShape>;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="text-[10px] uppercase tracking-wide text-text-muted">
        {title} <span className="normal-case">— {hint}</span>
      </div>
      {rows.length === 0 ? (
        <div className="text-[10px] text-text-muted">None.</div>
      ) : (
        rows.map((row, index) => {
          const mark = marks.get(row.symbol);
          return (
            <div key={`${row.symbol}-${row.is_in_flight ? "p" : "h"}-${index}`} className="border-t border-base-border pt-1">
              <div className="flex flex-wrap gap-x-2">
                <span className="text-text-primary">{row.symbol} {row.direction} × {row.qty}</span>
                <span>{row.is_in_flight ? "Ref" : "Avg"} {amount(row.avg_entry_price)}</span>
                <span>Stop {amount(row.stop)}</span>
              </div>
              <div className="flex flex-wrap gap-x-2 text-[10px] text-text-muted">
                <span>Mark {amount(row.mark)}{row.mark !== null && mark ? ` @ ${mark.as_of}` : ""}</span>
                <span>
                  Unrealized <span className={pnlTone(row.unrealized_pnl)}>{amount(row.unrealized_pnl)}</span>
                </span>
                {row.mark === null && <span>no mark yet</span>}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

function PortfolioBody({ portfolio }: { portfolio: PortfolioStateWireShape }) {
  const marks = new Map(portfolio.marks.map((m) => [m.symbol, m]));
  const held = portfolio.exposures.filter((e) => !e.is_in_flight);
  const pending = portfolio.exposures.filter((e) => e.is_in_flight);
  const isFlat = portfolio.open_position_count === 0 && portfolio.exposures.length === 0;
  const feesNote =
    portfolio.unknown_fee_count_today === null
      ? "history incomplete"
      : portfolio.unknown_fee_count_today > 0
        ? `incomplete — ${portfolio.unknown_fee_count_today} unknown`
        : "complete";

  return (
    <div className="flex flex-col gap-2">
      <div className="text-[10px] text-text-muted">
        {modeLabel(portfolio.execution_mode)} portfolio · trading day {portfolio.trading_day} · snapshot{" "}
        {portfolio.snapshot_time} · {portfolio.open_position_count} open positions · {portfolio.in_flight_order_count}{" "}
        in-flight orders
      </div>
      <div className="flex flex-col gap-0.5">
        <SummaryRow label="Unrealized P&L" value={portfolio.unrealized_pnl} tone={pnlTone(portfolio.unrealized_pnl)}
          note={portfolio.unrealized_pnl === null ? "needs every exposure marked" : undefined} />
        <SummaryRow label="Open risk" value={portfolio.open_risk}
          note={portfolio.open_risk === null ? "needs mark and stop on every exposure" : undefined} />
        <SummaryRow label="Realized today" value={portfolio.realized_pnl_today} tone={pnlTone(portfolio.realized_pnl_today)}
          note={portfolio.realized_pnl_today === null ? "history incomplete" : undefined} />
        <SummaryRow label="  profit" value={portfolio.realized_profit_today} />
        <SummaryRow label="  loss" value={portfolio.realized_loss_today} />
        <SummaryRow label="Reported fees today" value={portfolio.reported_fees_today} note={feesNote} />
        <SummaryRow label="Total fees today" value={portfolio.fees_today}
          note={portfolio.fees_today === null ? "unavailable until every fee is known" : undefined} />
        <SummaryRow label="Buying power" value={portfolio.buying_power}
          note={portfolio.buying_power === null ? "no cash source" : undefined} />
      </div>
      {isFlat ? (
        <div className="rounded border border-base-border px-2 py-1 text-text-muted">
          Flat — no open positions or pending entries.
        </div>
      ) : (
        <>
          <ExposureRows title="Held exposure" hint="filled positions" rows={held} marks={marks} />
          <ExposureRows title="Pending-entry exposure" hint="remaining quantity of unfilled entry orders" rows={pending} marks={marks} />
        </>
      )}
    </div>
  );
}

export function PortfolioStateSummary() {
  const [expanded, setExpanded] = useState(false);
  const { portfolio, loaded, loading, error, refresh } = usePortfolioState(expanded);

  return (
    <div className="mt-1 flex flex-col gap-1 rounded border border-base-border px-2 py-1 font-mono text-[11px]">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="text-left text-text-primary"
        >
          {expanded ? "▾" : "▸"} Portfolio details
        </button>
        {expanded && (
          <button type="button" onClick={refresh} disabled={loading} className="text-text-primary disabled:opacity-50">
            Refresh
          </button>
        )}
      </div>
      {expanded && (
        <>
          <div className="text-[10px] text-text-muted">
            Simulated portfolio from the running Portfolio State — not a connected real-money account. Loads on
            expansion and Refresh; not a live feed.
          </div>
          {loading && !loaded ? (
            <p className="text-text-muted">Loading…</p>
          ) : error ? (
            <p className="text-bear">Couldn't load portfolio details — {error}</p>
          ) : !loaded ? null : portfolio === null ? (
            <p className="text-text-muted">
              Portfolio unavailable — the execution pipeline is not running or its snapshot is not ready.
            </p>
          ) : (
            <div className={loading ? "opacity-60" : undefined}>
              <PortfolioBody portfolio={portfolio} />
              {loading && <div className="text-[10px] text-text-muted">Refreshing…</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
