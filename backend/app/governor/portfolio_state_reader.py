"""
Concrete `PortfolioStateReader` (governor/ports.py) — the last of the
three concrete adapters entry-lifecycle-wiring's scope item 3 called
for (`OrderLedgerPort`/`DecisionAuthorizationPort` and `TradeLedgerPort`
were already built and merged to `main` undocumented before this task
began — see this delivery's decision entry).

A thin translation over the SAME `app.portfolio_state.engine.
PortfolioState` event-worker instance `main.py`'s `lifespan()` wires
for real accounting (decision #173) — this adapter owns no state of
its own; it reads that instance's `get_snapshot()` and reshapes it
into governor's own narrower `PortfolioSnapshot`/`OpenExposure` shape
(`governor/ports.py`'s own docstring: a thin adapter satisfying both
Protocols once the real implementations land).

**I14's "halt NEW entries" clause, resolved here.** The design doc
requires that an unresolved fill anomaly (overfill — see
`execution_engine/fill_ledger.py`) halt new entry approvals. There is
no separate halt flag/table in this schema (adding one would touch
`models/execution_ledger.py` and a migration, both outside this task's
file boundary). Instead: `get_snapshot()` checks for any unresolved
anomalous fill in this `execution_mode` FIRST, before reading Portfolio
State at all, and raises if one exists. `AuthorizerStub._process_one()`
(governor/engine.py, unmodified) already treats any exception from
`portfolio_state.get_snapshot()` as fatal-for-this-Opportunity-only:
logged, no decision committed, worker continues — the exact same
fail-closed path an unready/unrestored Portfolio State snapshot
already takes. No new machinery, no edit to engine.py needed; this
adapter's own raise IS the halt.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.governor.ports import OpenExposure, PortfolioSnapshot
from app.models.execution_ledger import Fill, Order
from app.portfolio_state.engine import PortfolioState


class PortfolioStateUnavailable(Exception):
    """No safe PortfolioSnapshot could be produced right now — treated
    by AuthorizerStub exactly like any other processing fault (fail
    closed, I6): the current OpportunityCreated is dropped and logged,
    no decision is committed, and the worker moves on to the next
    item."""


class PortfolioStateAdapter:
    """`execution_mode` is fixed at construction (the one live
    `PortfolioState` instance this process wires, per §6.2: "this slice
    only ever runs execution_mode == 'simulated'") — a request for any
    other mode is refused rather than silently answered from the wrong
    instance."""

    def __init__(self, portfolio_state: PortfolioState, session_factory) -> None:
        self._portfolio_state = portfolio_state
        self._sessions = session_factory

    def get_snapshot(self, execution_mode: str, trading_day: date) -> PortfolioSnapshot:
        if execution_mode != self._portfolio_state.execution_mode:
            raise PortfolioStateUnavailable(
                f"no Portfolio State wired for execution_mode={execution_mode!r} "
                f"(this process only tracks {self._portfolio_state.execution_mode!r})"
            )
        self._raise_if_unresolved_anomaly(execution_mode)

        snapshot = self._portfolio_state.get_snapshot(trading_day=trading_day)
        if snapshot is None:
            raise PortfolioStateUnavailable("Portfolio State not ready (unrestored, stale, or blocked)")
        if snapshot.realized_pnl_today is None:
            raise PortfolioStateUnavailable("Portfolio State realized P&L today is unknown (incomplete daily history)")

        exposures = tuple(
            OpenExposure(
                symbol=e.symbol,
                direction=e.direction,
                qty=e.qty,
                avg_entry_price=None if e.avg_entry_price is None else float(e.avg_entry_price),
                stop=None if e.stop is None else float(e.stop),
                mark=None if e.mark is None else float(e.mark),
                unrealized_pnl=None if e.unrealized_pnl is None else float(e.unrealized_pnl),
                is_in_flight=e.is_in_flight,
            )
            for e in snapshot.exposures
        )
        return PortfolioSnapshot(
            execution_mode=snapshot.execution_mode,
            trading_day=snapshot.trading_day,
            as_of=snapshot.as_of,
            realized_pnl_today=float(snapshot.realized_pnl_today),
            exposures=exposures,
        )

    def _raise_if_unresolved_anomaly(self, execution_mode: str) -> None:
        with self._sessions() as session:
            anomalous = session.scalar(
                select(Fill.ledger_seq)
                .join(Order, Order.client_order_id == Fill.client_order_id)
                .where(Order.execution_mode == execution_mode, Fill.anomaly.isnot(None))
                .limit(1)
            )
        if anomalous is not None:
            raise PortfolioStateUnavailable(
                f"unresolved anomalous fill (ledger_seq={anomalous}) in execution_mode={execution_mode!r} "
                "— entry acceptance halted per I14 until reviewed"
            )
