"""
`PositionMonitor`'s own narrow, frozen-dataclass-based read Protocol.

Why a NEW Protocol rather than reusing something that already exists
(this task's own §1.6 instruction — read as a pattern reference, not a
dependency):

  - `portfolio_state.snapshot.PortfolioSnapshot`/`accounting.PositionState`
    (read-only reference, this task's own §1.5) carry `avg_price`, P&L
    fields, exit bookkeeping (`exit_qty`, `exit_attempt`, ...) and a
    `status` field this module has no business reading or exposing —
    importing them directly would hand this module a much wider surface
    than it actually uses, and would couple a live decision module to
    Portfolio State's own internal accounting shape evolving underneath
    it. This module's own `PositionView` below carries only the seven
    fields `_evaluate()` (engine.py) actually reads.
  - `governor.ports.PortfolioStateReader`/`OpenExposure` (read as a
    PATTERN reference per this task's own instruction, not reused) is
    deliberately the wrong shape for this task: it has no `target` field
    (the daily-loss gate never needed one), and its `OpenExposure` mixes
    already-open positions with in-flight ENTRY orders (both count as
    "exposure" for governor's own purpose) — this module must only ever
    see genuinely open, filled positions, never an order still working
    its way to a fill. Mirroring governor's *pattern* (a narrow, frozen,
    read-only Protocol) does not mean
    mirroring its *classes*.

`portfolio_state_reader.py` supplies the concrete read-only adapter over
the real Portfolio State snapshot. Wiring it into `main.py` remains a
separate integration task.

Type choice, stated plainly: `stop`/`target` are typed `float | None`
here, not `Decimal | None` as `accounting.PositionState` stores them.
This module never does money arithmetic — it only ever compares a stop/
target against a tick price or a candle's high/low, both of which arrive
as `float` on `PriceUpdated`/`CandleClosed` (schemas/events/market_data.py)
— so `float` keeps every comparison in `engine.py` a same-type comparison
with `fill_simulator.py`'s own identical `float`-based convention (§1.8),
rather than every evaluation needing a `Decimal`/`float` coercion for no
precision benefit an in-process exit *decision* (as opposed to the
eventual order's actual fill accounting) needs. Converting a real
`Decimal` position field to `float` is the adapter's job, one honest,
visible `float(...)` call at the integration boundary, not hidden inside
this module's comparisons.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class PositionView:
    """The subset of a real position this module actually reads — no
    `avg_price`, no P&L, no `execution_mode`/`execution_venue`, nothing
    `_evaluate()` (engine.py) doesn't use. `stop`/`target` are `None`
    when a position genuinely has neither configured yet (an honest
    absence — see accounting.PositionState, this task's own §1.5 read —
    not something this module fabricates a value for): a position with
    `stop is None` simply can never produce a `"stop"` `ExitIntent`,
    same for `target`."""

    position_id: UUID
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    stop: float | None
    target: float | None
    opened_at: datetime


class PositionReader(Protocol):
    def get_open_positions(self) -> tuple[PositionView, ...]:
        """Synchronous, point-in-time read of every currently-open
        position this module should be monitoring — already filtered to
        genuinely open (filled, not yet closed) positions by whatever
        adapts the real Portfolio State to this Protocol; this module
        does not filter by status itself, since it has no `status` field
        to filter on. In-memory-speed, no I/O, no network/DB call — the
        same "no I/O surprises" contract every other `get_snapshot()`-
        style read in this codebase already makes (PortfolioStateReader,
        governor's own port, ...). Ordering is not meaningful; callers
        that need one symbol filter the returned tuple themselves."""
        ...
