"""PostgreSQL TradeLedgerPort: decision and entry reservation commit together."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.db.ledger_transaction import ledger_transaction
from app.models.execution_ledger import Trade, TradeReservation
from .ports import LedgerCommitError, TradeDecisionCommitResult


def _record_data(record):
    data = asdict(record)
    for name in ("setup_detected_at", "decided_at"):
        ts = data[name]
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise LedgerCommitError("decision timestamps must be timezone-aware")
        data[name] = ts.astimezone(timezone.utc).isoformat()
    # Validate finite JSON values and detach mutable caller-owned dictionaries.
    return json.loads(json.dumps(data, allow_nan=False))


class PostgresTradeLedger:
    def __init__(self, session_factory):
        self._sessions = session_factory

    def commit_decision(self, record):
        with ledger_transaction(self._sessions, LedgerCommitError) as session:
            data = _record_data(record)
            if record.decision not in {"approved", "rejected"} or record.direction not in {"BUY", "SELL"}:
                raise LedgerCommitError("invalid decision or direction")
            if record.execution_mode is not None and not isinstance(record.execution_mode, str):
                raise LedgerCommitError("requested execution mode must be a string or absent")
            if record.decision == "approved":
                if record.execution_mode != "simulated":
                    raise LedgerCommitError("only simulated decisions may be approved")
                if not isinstance(record.opportunity_id, str):
                    raise LedgerCommitError("approval requires an accepted opportunity UUID")
                trade_id = UUID(record.opportunity_id)
                if record.client_order_id != f"{trade_id}:entry":
                    raise LedgerCommitError("approval requires its deterministic entry ID")
                if type(record.qty) is not int or record.qty <= 0 or record.reference_price is None:
                    raise LedgerCommitError("approval requires positive quantity and reference price")
                price = Decimal(str(record.reference_price))
                if not price.is_finite() or price <= 0:
                    raise LedgerCommitError("approval requires positive finite reference price")
                existing = session.get(Trade, trade_id)
                if existing is not None:
                    reservation = session.get(TradeReservation, trade_id)
                    if (existing.decision, existing.execution_mode, existing.execution_venue,
                        existing.symbol, existing.direction) != (
                        "approved", "simulated", "simulated", record.symbol, record.direction
                    ) or existing.decision_record != data or reservation is None or (
                        reservation.client_order_id, reservation.qty, reservation.reference_price
                    ) != (record.client_order_id, record.qty, price):
                        raise LedgerCommitError("conflicting or incomplete committed approval")
                    return TradeDecisionCommitResult(existing.created_at, str(trade_id))
            else:
                if any(value is not None for value in (record.opportunity_id, record.client_order_id, record.qty, record.reference_price)):
                    raise LedgerCommitError("rejected decision must not carry an accepted identity or reservation")
                trade_id = uuid4()  # audit row identity; never an accepted opportunity
            trade = Trade(trade_id=trade_id, execution_mode=record.execution_mode,
                execution_venue="simulated" if record.decision == "approved" else None,
                strategy_name=record.strategy, strategy_version=record.strategy_version,
                symbol=record.symbol, direction=record.direction, decision=record.decision,
                reasons=record.reasons, limits_snapshot=record.limits_snapshot, decision_record=data,
                thesis={"structural_invalidation": record.structural_invalidation,
                        "structural_target": record.structural_target,
                        "final_stop": record.structural_invalidation, "final_target": record.structural_target,
                        "confidence": record.confidence_at_signal,
                        "setup_detected_at": data["setup_detected_at"]},
                status="open" if record.decision == "approved" else None)
            session.add(trade)
            session.flush()
            if record.decision == "approved":
                session.add(TradeReservation(trade_id=trade_id, client_order_id=record.client_order_id,
                                             qty=record.qty, reference_price=price))
        return TradeDecisionCommitResult(datetime.now(timezone.utc), record.opportunity_id)
