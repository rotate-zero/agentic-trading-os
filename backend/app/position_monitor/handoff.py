"""
Pending/acknowledged observation handoff — Position Monitor's side of the
approved simulated-EOD contract (decision #185, design doc §6.6).

The monitor keeps up to two observation *slots* per position: one
`"protective"` (stop/target) and one `"eod"`. A slot is created `PENDING`
the moment the monitor decides; it becomes `ACKNOWLEDGED` only when the
consumer calls `PositionMonitor.acknowledge_observation()` **after** its own
durable commit. Invoking a callback, or putting work on Execution's queue, is
never proof of a database commit, so it never advances a slot by itself: a
pending slot is retained (and stays readable through
`PositionMonitor.pending_observations()`) until the consumer says otherwise.

This module holds only the plain data types. It imports nothing from
Execution, the exit ledger or the database, and nothing here places an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # annotation only — engine.py imports this module
    from app.position_monitor.engine import ExitIntent

ObservationKind = Literal["protective", "eod"]


class ObservationState(str, Enum):
    PENDING = "pending"  # decided by the monitor; not yet proven durable
    ACKNOWLEDGED = "acknowledged"  # consumer confirmed a committed state
    EXPIRED = "expired"  # EOD only: placement window closed; terminal


class ReleaseReason(str, Enum):
    """Why a consumer hands a pending slot back without a commit."""

    WINDOW_CLOSED = "window_closed"  # EOD only: window ended before commit
    POSITION_CLOSED = "position_closed"  # position is flat; nothing left to observe
    INVALID = "invalid"  # consumer/ledger rejected the observation's data


@dataclass(frozen=True)
class Observation:
    """A point-in-time copy of one observation slot.

    `sequence` is the monitor's own strictly increasing creation order across
    both kinds and all positions, so a consumer can process observations in
    the order the monitor decided them and never lets a later one overwrite
    an earlier uncommitted one.
    """

    sequence: int
    kind: ObservationKind
    state: ObservationState
    intent: ExitIntent
