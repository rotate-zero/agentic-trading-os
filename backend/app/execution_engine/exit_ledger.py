"""Durable, position-bound reduce-only reservations for simulated exits.

Two layers share one table and one serialization barrier:

* The legacy surface (`observe`, `pending_position_ids`, `prepare`,
  `confirm_recovery_exit`, `set_status`) is what the unchanged Execution worker
  calls. It serves stop/target rows exactly as decision #184 built them and
  never surfaces an EOD-lifecycle row, so the worker cannot place an EOD order.
* The explicit surface (`observe_exit`, `prepare_exit`, `claim_dispatch`,
  `advance_eod_expiry`, `pending_exit_position_ids`, `slot_state`) is the EOD
  request / fallback / reservation / expiry / dispatch-claim state machine of
  decision #185 and design §6.6. It returns typed dispositions instead of
  booleans so a later integration can tell a normal expiry from an unsafe
  ledger.

Nothing here reads the market, the venue or a timer. Every wall-clock
comparison uses the injected `clock`; every EOD window is derived from the
committed `positions.opened_at` through `core.session_window`, never from the
monitor's supplied bounds.

Session guard (`simulated-protective-session-retry`). The simulated venue only
accepts orders during regular hours, so a stop/target close (original request
or the fallback captured on an expired EOD row) is neither reserved nor
dispatched while `MarketClock.is_regular_session(now)` is false. The durable
request is retained, `positions.exit_attempt` and rejection history are left
untouched, and the existing worker resumes the request when regular hours
return, through the same committed-position / entry / pending-fill / retry
checks. An active or dispatch-uncertain close is checked first and stays
exclusive. EOD's own `[flatten_at, close_at)` placement rule is not touched.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.market_clock import MarketClock
from app.core.session_window import UnsupportedEodCalendarError, eod_session_window
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, PositionFillReceipt, Trade

ACTIVE = ("approved", "submitted", "partially_filled", "unknown")
TERMINAL = ("filled", "cancelled", "rejected", "expired")
PROTECTIVE = ("stop", "target")
EOD_FLATTEN = "eod_flatten"
# Ledger-owned proof-of-unsent cancellation. The database refuses it on any
# order that ever carried a dispatch marker; set_status() refuses it as input.
EOD_WINDOW_CLOSED = "eod_window_closed"
RETRY_DELAY = timedelta(seconds=5)


class ExitLedgerError(Exception):
    """The observation or reservation was not committed safely."""


@dataclass(frozen=True)
class ExitAction:
    kind: str  # cancel_entry | submit
    client_order_id: str
    symbol: str = ""
    side: str = ""
    qty: int = 0
    exit_reason: str = ""  # stop | target | eod_flatten for a submit


# --------------------------------------------------------------------------
# Result types the integration task wires
# --------------------------------------------------------------------------

class ObserveDisposition(str, Enum):
    STORED = "stored"                                # new original request committed
    ALREADY_STORED = "already_stored"                # replay; the first request stands
    FALLBACK_STORED = "fallback_stored"              # first stop/target captured on an EOD row
    FALLBACK_ALREADY_STORED = "fallback_already_stored"  # first fallback wins; this one ignored
    SUPERSEDED = "superseded"                        # EOD offered after a protective request exists
    WINDOW_NOT_OPEN = "window_not_open"              # before flatten_at: retain and retry
    WINDOW_CLOSED = "window_closed"                  # at/after close_at: drop the EOD slot only
    POSITION_CLOSED = "position_closed"              # flat: nothing to protect
    INVALID = "invalid"                              # permanent defect; `reason` says which


@dataclass(frozen=True)
class ExitSlotState:
    """Committed request/slot state, as recovery and the monitor hydrate it."""

    position_id: UUID
    original_reason: str
    eod_flatten_at: datetime | None
    eod_close_at: datetime | None
    eod_expired_at: datetime | None
    fallback_reason: str | None
    fallback_trigger_price: Decimal | None
    fallback_trigger_ts: datetime | None
    retry_after: datetime | None

    @property
    def is_eod(self) -> bool:
        return self.original_reason == EOD_FLATTEN

    @property
    def eod_expired(self) -> bool:
        return self.eod_expired_at is not None

    @property
    def dormant(self) -> bool:
        """Expired EOD with no fallback: inert until a later protective observation."""
        return self.is_eod and self.eod_expired and self.fallback_reason is None


@dataclass(frozen=True)
class ObserveResult:
    disposition: ObserveDisposition
    position_id: UUID
    reason: str | None = None
    slot: ExitSlotState | None = None

    @property
    def acknowledged(self) -> bool:
        """True when the durable state already covers this observation."""
        return self.disposition in {
            ObserveDisposition.STORED, ObserveDisposition.ALREADY_STORED,
            ObserveDisposition.FALLBACK_STORED, ObserveDisposition.FALLBACK_ALREADY_STORED,
            ObserveDisposition.SUPERSEDED,
        }

    @property
    def retry(self) -> bool:
        """True only when the caller should keep the pending observation."""
        return self.disposition is ObserveDisposition.WINDOW_NOT_OPEN


class PrepareDisposition(str, Enum):
    SUBMIT = "submit"                                # action: reserved or reused unsent close
    CANCEL_ENTRY = "cancel_entry"                    # action: cancel a working entry first
    NO_REQUEST = "no_request"
    POSITION_CLOSED = "position_closed"
    WAIT_PENDING_FILL = "wait_pending_fill"          # committed fill lacks its position receipt
    WAIT_ACTIVE_ORDER = "wait_active_order"          # submitted/partial/unknown close is exclusive
    WAIT_UNCERTAIN_DISPATCH = "wait_uncertain_dispatch"  # approved close carries a dispatch marker
    WAIT_RETRY_DELAY = "wait_retry_delay"
    WAIT_WINDOW_NOT_OPEN = "wait_window_not_open"    # EOD row, wall clock before flatten_at
    WAIT_OUTSIDE_REGULAR_SESSION = "wait_outside_regular_session"  # stop/target/fallback: retained, no new attempt
    DORMANT = "dormant"                              # expired EOD, no fallback


@dataclass(frozen=True)
class PrepareResult:
    disposition: PrepareDisposition
    position_id: UUID
    action: ExitAction | None = None
    eod_expired: bool = False             # the request is durably EOD-expired after this call
    expired_now: bool = False             # this call recorded the expiry
    cancelled_order_id: str | None = None  # proven-unsent EOD reservation cancelled by this call
    reason: str | None = None


class ClaimDisposition(str, Enum):
    CLAIMED = "claimed"                          # marker committed; the ONLY value that permits a venue call
    ALREADY_CLAIMED = "already_claimed"          # someone claimed it: uncertain, never resend
    STALE = "stale"                              # order not sendable (terminal/progressed/position flat)
    WINDOW_EXPIRED = "window_expired"            # EOD placement ended; unsent order cancelled (normal skip)
    WAIT_WINDOW_NOT_OPEN = "wait_window_not_open"
    WAIT_OUTSIDE_REGULAR_SESSION = "wait_outside_regular_session"  # stop/target/fallback: no marker, no venue call
    WAIT_ENTRY_ACTIVITY = "wait_entry_activity"  # an entry can still change the position
    WAIT_PENDING_FILL = "wait_pending_fill"
    UNSAFE = "unsafe"                            # identity/quantity/reason mismatch: an error, not an expiry


@dataclass(frozen=True)
class ClaimResult:
    disposition: ClaimDisposition
    client_order_id: str
    action: ExitAction | None = None
    reason: str | None = None

    @property
    def send(self) -> bool:
        return self.disposition is ClaimDisposition.CLAIMED


class EodExpiryDisposition(str, Enum):
    NO_REQUEST = "no_request"
    NOT_EOD = "not_eod"
    POSITION_CLOSED = "position_closed"
    NOT_DUE = "not_due"
    EXPIRED = "expired"
    ALREADY_EXPIRED = "already_expired"


@dataclass(frozen=True)
class EodExpiryResult:
    disposition: EodExpiryDisposition
    position_id: UUID
    cancelled_order_id: str | None = None


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def _slot(request: ExitRequest) -> ExitSlotState:
    return ExitSlotState(
        request.position_id, request.exit_reason, request.eod_flatten_at, request.eod_close_at,
        request.eod_expired_at, request.fallback_reason, request.fallback_trigger_price,
        request.fallback_trigger_ts, request.retry_after,
    )


class PostgresExitLedger:
    def __init__(
        self,
        session_factory,
        *,
        clock: Callable[[], datetime] | None = None,
        market_clock: MarketClock | None = None,
        eod_lead_seconds: int | None = None,
    ):
        self._sessions = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._market_clock = market_clock or MarketClock()
        if eod_lead_seconds is None:
            from app.core.config import get_settings

            eod_lead_seconds = get_settings().execution_eod_flatten_lead_seconds
        self._eod_lead_seconds = eod_lead_seconds

    def _now(self) -> datetime:
        now = self._clock()
        if not _aware(now):
            raise ExitLedgerError("ledger clock must be timezone-aware")
        return now

    def _transaction(self):
        # All execution writers acquire trades/orders first. This barrier
        # serializes reservations with fill and position commits.
        @contextmanager
        def owned():
            try:
                with self._sessions() as session:
                    with session.begin():
                        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                        session.execute(text("SET LOCAL synchronous_commit = on"))
                        session.execute(text(
                            "LOCK TABLE trades, orders, trade_reservations, fills, positions, "
                            "portfolio_state_cursor, position_fill_receipts, exit_requests "
                            "IN SHARE ROW EXCLUSIVE MODE"
                        ))
                        yield session
            except ExitLedgerError:
                raise
            except (SQLAlchemyError, ValueError, TypeError, ArithmeticError) as exc:
                raise ExitLedgerError(f"exit ledger transaction failed: {exc}") from exc

        return owned()

    # ------------------------------------------------------------------
    # Shared guards
    # ------------------------------------------------------------------

    @staticmethod
    def _require_simulated(position: Position) -> None:
        if (position.execution_mode, position.execution_venue) != ("simulated", "simulated"):
            raise ExitLedgerError("position mode or venue cannot use simulated exits")

    @staticmethod
    def _matching_trade(session, position: Position) -> Trade:
        trade = session.get(Trade, position.trade_id)
        if trade is None or trade.decision != "approved" or (
            trade.execution_mode, trade.execution_venue, trade.symbol, trade.direction
        ) != ("simulated", "simulated", position.symbol, position.side):
            raise ExitLedgerError("position has no matching approved trade")
        return trade

    @staticmethod
    def _pending_fill(session, trade_id) -> int | None:
        return session.scalar(
            select(Fill.ledger_seq).join(Order, Order.client_order_id == Fill.client_order_id)
            .outerjoin(PositionFillReceipt, PositionFillReceipt.ledger_seq == Fill.ledger_seq)
            .where(Order.trade_id == trade_id, PositionFillReceipt.ledger_seq.is_(None)).limit(1)
        )

    def _advance_expiry(self, session, request: ExitRequest, now: datetime) -> tuple[bool, str | None]:
        """Persist a due EOD expiry; cancel an EOD reservation that is PROVEN unsent.

        Independent of entry cancellation, receipts and retry delay. A working,
        partially filled, unknown or dispatch-marked close is left exactly as it
        is: expiry ends placement eligibility, it never cancels or resolves an
        order that may have reached the venue.
        """
        if request.exit_reason != EOD_FLATTEN or request.eod_expired_at is not None:
            return False, None
        if now < request.eod_close_at:
            return False, None
        cancelled = None
        close = session.scalar(
            select(Order).where(Order.position_id == request.position_id, Order.position_effect == "close",
                                Order.status.in_(ACTIVE)).order_by(Order.id.desc()).limit(1)
        )
        if (close is not None and close.status == "approved" and close.exit_dispatch_started_at is None
                and close.exit_reason == EOD_FLATTEN):
            close.status = "cancelled"
            close.reject_reason = EOD_WINDOW_CLOSED
            cancelled = close.client_order_id
        request.eod_expired_at = now
        session.flush()
        return True, cancelled

    @staticmethod
    def _effective(request: ExitRequest, now: datetime) -> tuple[str | None, str]:
        """(reason, state) with state in protective|eod|fallback|dormant|not_open."""
        if request.exit_reason != EOD_FLATTEN:
            return request.exit_reason, "protective"
        if request.eod_expired_at is None:
            if now < request.eod_flatten_at:
                return None, "not_open"
            if now < request.eod_close_at:
                return EOD_FLATTEN, "eod"
            # Only reachable if a caller skipped _advance_expiry; never actionable as EOD.
            return None, "not_open"
        if request.fallback_reason is not None:
            return request.fallback_reason, "fallback"
        return None, "dormant"

    def _protective_outside_session(self, reason: str | None, now: datetime) -> bool:
        """True when a stop/target close must wait for regular hours.

        Only a protective reason (an original stop/target or an EOD row's
        fallback) is held; `eod_flatten` keeps its own `[flatten_at, close_at)`
        rule. Uses the ledger's injected clock, not the venue's, so boundaries
        are deterministic in tests.
        """
        return reason in PROTECTIVE and not self._market_clock.is_regular_session(now)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe_exit(self, intent) -> ObserveResult:
        """Commit a monitor observation and say exactly what state now holds.

        Quantity on the intent is never read. EOD bounds supplied on the intent
        (`eod_flatten_at` / `eod_close_at`, optional) must equal the window the
        ledger derives from the committed position's `opened_at`; absent bounds
        are derived, mismatched bounds are INVALID. Identity that differs from the
        committed position raises: that is an unsafe ledger, not a disposition.
        """
        position_id = intent.position_id
        reason = intent.exit_reason
        if reason not in PROTECTIVE + (EOD_FLATTEN,):
            return ObserveResult(ObserveDisposition.INVALID, position_id, "unsupported_exit_reason")
        try:
            price = Decimal(str(intent.trigger_price))
        except (InvalidOperation, ValueError):
            return ObserveResult(ObserveDisposition.INVALID, position_id, "invalid_trigger_price")
        if not price.is_finite() or price <= 0:
            return ObserveResult(ObserveDisposition.INVALID, position_id, "invalid_trigger_price")
        if not _aware(intent.trigger_ts):
            return ObserveResult(ObserveDisposition.INVALID, position_id, "naive_trigger_ts")

        with self._transaction() as session:
            now = self._now()
            position = session.get(Position, position_id)
            if position is None:
                return ObserveResult(ObserveDisposition.INVALID, position_id, "unknown_position")
            if position.status == "closed" or position.qty <= 0:
                return ObserveResult(ObserveDisposition.POSITION_CLOSED, position_id)
            if (position.execution_mode, position.execution_venue, position.symbol, position.side) != (
                "simulated", "simulated", intent.symbol, intent.side
            ):
                raise ExitLedgerError("exit observation differs from committed position")

            existing = session.get(ExitRequest, position_id)
            if existing is not None:
                self._advance_expiry(session, existing, now)
                return self._observe_existing(existing, intent, price)

            if reason == EOD_FLATTEN:
                early, window = self._validate_eod(intent, position, now)
                if early is not None:
                    return early
                request = ExitRequest(
                    position_id=position_id, exit_reason=EOD_FLATTEN, trigger_price=price,
                    trigger_ts=intent.trigger_ts, eod_flatten_at=window.flatten_at,
                    eod_close_at=window.close_at,
                )
            else:
                request = ExitRequest(position_id=position_id, exit_reason=reason, trigger_price=price,
                                      trigger_ts=intent.trigger_ts)
            session.add(request)
            session.flush()
            return ObserveResult(ObserveDisposition.STORED, position_id, slot=_slot(request))

    @staticmethod
    def _observe_existing(existing: ExitRequest, intent, price: Decimal) -> ObserveResult:
        position_id = existing.position_id
        if intent.exit_reason == EOD_FLATTEN:
            disposition = (ObserveDisposition.ALREADY_STORED if existing.exit_reason == EOD_FLATTEN
                           else ObserveDisposition.SUPERSEDED)
            return ObserveResult(disposition, position_id, slot=_slot(existing))
        if existing.exit_reason != EOD_FLATTEN:
            # #184 semantics: the first protective request stands, whatever its reason.
            return ObserveResult(ObserveDisposition.ALREADY_STORED, position_id, slot=_slot(existing))
        if existing.fallback_reason is not None:
            return ObserveResult(ObserveDisposition.FALLBACK_ALREADY_STORED, position_id, slot=_slot(existing))
        existing.fallback_reason = intent.exit_reason
        existing.fallback_trigger_price = price
        existing.fallback_trigger_ts = intent.trigger_ts
        return ObserveResult(ObserveDisposition.FALLBACK_STORED, position_id, slot=_slot(existing))

    def _validate_eod(self, intent, position: Position, now: datetime):
        """Return (early ObserveResult | None, EodSessionWindow | None)."""
        pid = position.position_id

        def invalid(why):
            return ObserveResult(ObserveDisposition.INVALID, pid, why), None

        opened = position.opened_at
        if not _aware(opened):
            raise ExitLedgerError("committed position opened_at is not timezone-aware")
        ts = intent.trigger_ts
        if ts < opened:
            return invalid("label_before_position_opened")
        if ts > now:
            return invalid("label_in_future")
        if self._market_clock.trading_day(ts) != self._market_clock.trading_day(opened):
            return invalid("label_not_entry_day")
        try:
            window = eod_session_window(self._market_clock, opened, self._eod_lead_seconds)
        except UnsupportedEodCalendarError:
            return invalid("unsupported_eod_calendar")
        if window is None:
            return invalid("no_eod_session_on_entry_day")

        supplied = (getattr(intent, "eod_flatten_at", None), getattr(intent, "eod_close_at", None))
        if any(v is not None for v in supplied):
            if any(v is None for v in supplied):
                return invalid("eod_bounds_incomplete")
            if not all(_aware(v) for v in supplied):
                return invalid("eod_bounds_naive")
            if supplied != (window.flatten_at, window.close_at):
                return invalid("eod_bounds_mismatch")
        if now < window.flatten_at:
            return ObserveResult(ObserveDisposition.WINDOW_NOT_OPEN, pid, "before_flatten_at"), None
        if now >= window.close_at:
            return ObserveResult(ObserveDisposition.WINDOW_CLOSED, pid, "at_or_after_close"), None
        return None, window

    # ------------------------------------------------------------------
    # Read side
    # ------------------------------------------------------------------

    def slot_state(self, position_id: UUID) -> ExitSlotState | None:
        """Committed slot state for recovery/monitor hydration; None when no row."""
        with self._sessions() as session:
            request = session.get(ExitRequest, position_id)
            return _slot(request) if request is not None else None

    def pending_exit_position_ids(self) -> tuple[UUID, ...]:
        """Open simulated positions with serviceable requests, EOD lifecycle included.

        A dormant EOD row (expired, no fallback) is omitted: nothing can be done
        for it until a protective observation stores a fallback.
        """
        with self._sessions() as session:
            return tuple(session.scalars(
                select(ExitRequest.position_id).join(Position)
                .where(Position.execution_mode == "simulated", Position.qty > 0, Position.status != "closed")
                .where(~((ExitRequest.exit_reason == EOD_FLATTEN) & ExitRequest.eod_expired_at.is_not(None)
                         & ExitRequest.fallback_reason.is_(None)))
                .order_by(ExitRequest.created_at)
            ).all())

    def advance_eod_expiry(self, position_id: UUID) -> EodExpiryResult:
        """Recover or record a due expiry from the stored bounds (restart, timer)."""
        with self._transaction() as session:
            now = self._now()
            request = session.get(ExitRequest, position_id)
            position = session.get(Position, position_id)
            if request is None or position is None:
                return EodExpiryResult(EodExpiryDisposition.NO_REQUEST, position_id)
            if request.exit_reason != EOD_FLATTEN:
                return EodExpiryResult(EodExpiryDisposition.NOT_EOD, position_id)
            if position.status == "closed" or position.qty <= 0:
                return EodExpiryResult(EodExpiryDisposition.POSITION_CLOSED, position_id)
            if request.eod_expired_at is not None:
                return EodExpiryResult(EodExpiryDisposition.ALREADY_EXPIRED, position_id)
            expired, cancelled = self._advance_expiry(session, request, now)
            if not expired:
                return EodExpiryResult(EodExpiryDisposition.NOT_DUE, position_id)
            return EodExpiryResult(EodExpiryDisposition.EXPIRED, position_id, cancelled)

    # ------------------------------------------------------------------
    # Reservation
    # ------------------------------------------------------------------

    def prepare_exit(self, position_id: UUID) -> PrepareResult:
        """Cancel working entries first, or commit exactly one close attempt."""
        with self._transaction() as session:
            now = self._now()
            request = session.get(ExitRequest, position_id)
            position = session.get(Position, position_id)
            if request is None or position is None:
                return PrepareResult(PrepareDisposition.NO_REQUEST, position_id)
            if position.status == "closed" or position.qty <= 0:
                return PrepareResult(PrepareDisposition.POSITION_CLOSED, position_id)
            self._require_simulated(position)
            trade = self._matching_trade(session, position)

            # Expiry first: it must not wait on entry cancellation or receipts.
            expired_now, cancelled = self._advance_expiry(session, request, now)
            reason, state = self._effective(request, now)
            common = dict(eod_expired=request.eod_expired_at is not None, expired_now=expired_now,
                          cancelled_order_id=cancelled)
            if state == "dormant":
                return PrepareResult(PrepareDisposition.DORMANT, position_id, **common)
            if state == "not_open":
                return PrepareResult(PrepareDisposition.WAIT_WINDOW_NOT_OPEN, position_id, **common)
            lifecycle = request.exit_reason == EOD_FLATTEN
            outside_session = self._protective_outside_session(reason, now)

            orders = session.scalars(select(Order).where(Order.trade_id == trade.trade_id).order_by(Order.id)).all()
            for order in orders:
                if order.position_effect != "open" or order.status not in ACTIVE:
                    continue
                filled = session.scalar(select(func.coalesce(func.sum(Fill.qty), 0))
                    .where(Fill.client_order_id == order.client_order_id)) or 0
                if filled < order.qty:
                    return PrepareResult(PrepareDisposition.CANCEL_ENTRY, position_id,
                                         ExitAction("cancel_entry", order.client_order_id), **common)

            # No close can be sized against stale accounting or a fill still
            # waiting for its durable position receipt.
            if self._pending_fill(session, trade.trade_id) is not None:
                return PrepareResult(PrepareDisposition.WAIT_PENDING_FILL, position_id, **common)

            for order in orders:
                if order.position_effect == "close" and order.status in ACTIVE:
                    if order.position_id != position_id:
                        raise ExitLedgerError("active close is not linked to the observed position")
                    if order.status != "approved":
                        return PrepareResult(PrepareDisposition.WAIT_ACTIVE_ORDER, position_id, **common)
                    if lifecycle and order.exit_dispatch_started_at is not None:
                        # Approved + marker: the venue may already hold it. Never
                        # cancel as unsent, never replace, never blindly resend.
                        return PrepareResult(PrepareDisposition.WAIT_UNCERTAIN_DISPATCH, position_id, **common)
                    if lifecycle and order.exit_reason != reason:
                        raise ExitLedgerError("unsent reservation reason differs from the effective exit reason")
                    if outside_session:
                        # Retained unsent reservation: reused, same ID, when regular hours return.
                        return PrepareResult(PrepareDisposition.WAIT_OUTSIDE_REGULAR_SESSION, position_id, **common)
                    return PrepareResult(PrepareDisposition.SUBMIT, position_id, ExitAction(
                        "submit", order.client_order_id, order.symbol, order.side, order.qty, order.exit_reason or ""
                    ), **common)

            if request.retry_after is not None and request.retry_after > now:
                return PrepareResult(PrepareDisposition.WAIT_RETRY_DELAY, position_id, **common)
            if outside_session:
                # No new attempt: exit_attempt, rejection history and the durable request stay as they are.
                # Checked last so this wait only ever replaces what would have been a new reservation.
                return PrepareResult(PrepareDisposition.WAIT_OUTSIDE_REGULAR_SESSION, position_id, **common)
            attempt = position.exit_attempt + 1
            order_id = f"{trade.trade_id}:exit:{attempt}"
            side = "SELL" if position.side == "BUY" else "BUY"
            session.add(Order(
                client_order_id=order_id, trade_id=trade.trade_id, position_id=position_id,
                execution_mode="simulated", execution_venue="simulated", symbol=position.symbol, side=side,
                position_effect="close", qty=position.qty, order_type="market", status="approved",
                exit_reason=reason,
            ))
            position.exit_attempt = attempt
            session.flush()
            return PrepareResult(PrepareDisposition.SUBMIT, position_id,
                                 ExitAction("submit", order_id, position.symbol, side, position.qty, reason), **common)

    # ------------------------------------------------------------------
    # Final guard and dispatch claim
    # ------------------------------------------------------------------

    def claim_dispatch(self, client_order_id: str) -> ClaimResult:
        """Final guard. CLAIMED is the only result that permits a venue call.

        For an EOD-lifecycle close (including a fallback attempt) the dispatch
        marker is committed before this method returns, so a crash after it and
        before the venue call is conservatively ambiguous, and a second or stale
        caller can never claim the same order. A legacy stop/target reservation
        keeps decision #184 semantics: the same checks, no marker.
        """
        with self._transaction() as session:
            now = self._now()

            def result(disposition, why=None, action=None):
                return ClaimResult(disposition, client_order_id, action, why)

            order = session.scalar(select(Order).where(Order.client_order_id == client_order_id))
            if order is None or order.position_effect != "close" or order.position_id is None:
                return result(ClaimDisposition.UNSAFE, "not_a_position_close_order")
            position = session.get(Position, order.position_id)
            request = session.get(ExitRequest, order.position_id)
            if position is None or request is None:
                return result(ClaimDisposition.UNSAFE, "close_without_position_or_request")
            if position.status == "closed":
                return result(ClaimDisposition.STALE, "position_closed")
            lifecycle = request.exit_reason == EOD_FLATTEN
            if order.status == "approved" and lifecycle and order.exit_dispatch_started_at is not None:
                return result(ClaimDisposition.ALREADY_CLAIMED, "dispatch_marker_present")
            if order.status != "approved":
                return result(ClaimDisposition.STALE, "order_not_approved")
            if not (position.trade_id == order.trade_id and position.symbol == order.symbol
                    and order.side == ("SELL" if position.side == "BUY" else "BUY")
                    and position.execution_mode == order.execution_mode == "simulated"
                    and position.execution_venue == order.execution_venue == "simulated"):
                return result(ClaimDisposition.UNSAFE, "reservation_differs_from_position")

            if lifecycle:
                self._advance_expiry(session, request, now)
                if order.status == "cancelled":  # cancelled just now as proven-unsent
                    return result(ClaimDisposition.WINDOW_EXPIRED, "eod_window_closed")
                reason, state = self._effective(request, now)
                if order.exit_reason == EOD_FLATTEN:
                    if request.eod_expired_at is not None:
                        # An unsent EOD order surviving an earlier expiry is still proven unsent.
                        order.status = "cancelled"
                        order.reject_reason = EOD_WINDOW_CLOSED
                        session.flush()
                        return result(ClaimDisposition.WINDOW_EXPIRED, "eod_window_closed")
                    if state == "not_open":
                        return result(ClaimDisposition.WAIT_WINDOW_NOT_OPEN, "before_flatten_at")
                elif not (state == "fallback" and order.exit_reason == reason):
                    return result(ClaimDisposition.UNSAFE, "fallback_not_actionable")

            if self._protective_outside_session(order.exit_reason, now):
                # The boundary passed between prepare() and here: no marker, no venue call.
                return result(ClaimDisposition.WAIT_OUTSIDE_REGULAR_SESSION, "outside_regular_session")

            # A fill can arrive after prepare() but before placement. Never send a
            # close while an entry can still change the position or while
            # accounting has not applied every committed fill.
            if session.scalar(select(Order.id).where(
                Order.trade_id == order.trade_id, Order.position_effect == "open", Order.status.in_(ACTIVE),
            ).limit(1)) is not None:
                return result(ClaimDisposition.WAIT_ENTRY_ACTIVITY, "working_entry")
            if self._pending_fill(session, order.trade_id) is not None:
                return result(ClaimDisposition.WAIT_PENDING_FILL, "fill_without_receipt")
            if position.qty != order.qty:
                return result(ClaimDisposition.UNSAFE, "reservation_quantity_differs")

            if lifecycle:
                order.exit_dispatch_started_at = now
                session.flush()
            return result(ClaimDisposition.CLAIMED, None, ExitAction(
                "submit", order.client_order_id, order.symbol, order.side, order.qty, order.exit_reason or ""))

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def set_status(self, client_order_id: str, status: str, *, reason: str | None = None, venue_order_id: str | None = None) -> bool:
        if status not in {"submitted", "cancelled", "rejected"}:
            raise ExitLedgerError("unsupported status transition")
        if reason == EOD_WINDOW_CLOSED:
            raise ExitLedgerError("eod_window_closed is reserved for the ledger's proven-unsent cancellation")
        with self._transaction() as session:
            now = self._now()
            order = session.scalar(select(Order).where(Order.client_order_id == client_order_id))
            if order is None:
                raise ExitLedgerError("status for unknown order")
            allowed = (order.status == "approved" and status in {"submitted", "cancelled", "rejected"}) or (
                order.status in {"submitted", "partially_filled"} and status in {"cancelled", "rejected"}
            )
            if not allowed:
                return False
            request = (session.get(ExitRequest, order.position_id)
                       if order.position_effect == "close" and order.position_id else None)
            lifecycle = request is not None and request.exit_reason == EOD_FLATTEN
            if lifecycle and order.status == "approved" and order.exit_dispatch_started_at is None:
                raise ExitLedgerError("EOD-lifecycle close has no durable dispatch claim")
            order.status = status
            if reason is not None:
                order.reject_reason = reason
            if venue_order_id is not None:
                order.venue_order_id = venue_order_id
            if order.position_effect == "close" and status in {"cancelled", "rejected"} and request is not None:
                request.retry_after = now + RETRY_DELAY
            if lifecycle:
                session.flush()
                self._advance_expiry(session, request, now)
        return True

    # ------------------------------------------------------------------
    # Legacy surface — what the unchanged Execution worker calls
    # ------------------------------------------------------------------

    def observe(self, intent) -> bool:
        """Persist a stop/target observation once; never use its quantity as authority.

        EOD intents are not accepted here (False, as before): the unchanged
        worker cannot create an EOD request. Use `observe_exit`.
        """
        if intent.exit_reason not in PROTECTIVE:
            return False
        result = self.observe_exit(intent)
        if result.disposition is ObserveDisposition.INVALID:
            if result.reason == "unknown_position":
                return False
            raise ExitLedgerError("invalid exit observation")
        if result.disposition is ObserveDisposition.POSITION_CLOSED:
            return False
        return True

    def pending_position_ids(self) -> tuple[UUID, ...]:
        """Legacy stop/target rows only; EOD-lifecycle rows use `pending_exit_position_ids`."""
        with self._sessions() as session:
            return tuple(session.scalars(
                select(ExitRequest.position_id).join(Position)
                .where(Position.execution_mode == "simulated", Position.qty > 0,
                       Position.status != "closed", ExitRequest.exit_reason != EOD_FLATTEN)
                .order_by(ExitRequest.created_at)
            ).all())

    def prepare(self, position_id: UUID) -> ExitAction | None:
        """Legacy: cancel working entries first, or commit exactly one close attempt."""
        with self._sessions() as session:
            reason = session.scalar(select(ExitRequest.exit_reason).where(ExitRequest.position_id == position_id))
        if reason is None or reason == EOD_FLATTEN:
            return None
        result = self.prepare_exit(position_id)
        return result.action

    def confirm_recovery_exit(self, client_order_id: str) -> bool:
        """Legacy final guard: True only when a venue call is now permitted."""
        return self.claim_dispatch(client_order_id).send
