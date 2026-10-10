"""PostgreSQL TradeLedgerPort: decision and entry reservation commit together."""
import json
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.db.ledger_transaction import ledger_transaction
from app.models.execution_ledger import Trade, TradeReservation
from app.trade_planning.proposal import ProposalError, validate_proposal, proposals_equal
from .evidence import EvidenceError, detach_evidence, evidence_equal
from .ports import LedgerCommitError, TradeDecisionCommitResult


def _record_data(record):
    # Snapshot fields live only in thesis. Do not traverse the caller's
    # originals here: they have already been validated and detached.
    data = {f.name: getattr(record, f.name) for f in fields(record)
            if f.name not in {"evidence", "proposal"}}
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

    @staticmethod
    def _validated_evidence(record):
        """Detached JSON-safe copy for an approval; None when none was carried."""
        if record.evidence is None:
            return None
        if record.decision != "approved":
            raise LedgerCommitError("only an approved decision may carry evidence")
        try:
            return detach_evidence(record.evidence)
        except EvidenceError as exc:
            raise LedgerCommitError(f"approval evidence is not plain finite JSON: {exc}") from exc

    def commit_decision(self, record):
        # Validate BEFORE opening the transaction: a bad payload is refused
        # explicitly (never repaired) and takes no table lock, leaving no trade,
        # no reservation and -- because the engine publishes only after a
        # successful commit -- no approval event.
        evidence = self._validated_evidence(record)
        proposal = self._validated_proposal(record)
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
                    stored = existing.thesis.get("evidence") if isinstance(existing.thesis, dict) else None
                    has_stored = isinstance(existing.thesis, dict) and "evidence" in existing.thesis
                    if has_stored != (evidence is not None) or (
                        has_stored and not evidence_equal(stored, evidence)
                    ):
                        raise LedgerCommitError("conflicting evidence for committed approval")
                    has_proposal = isinstance(existing.thesis, dict) and "proposal" in existing.thesis
                    try:
                        if has_proposal != (proposal is not None) or (
                            has_proposal and not proposals_equal(existing.thesis["proposal"], proposal)
                        ):
                            raise LedgerCommitError("conflicting proposal for committed approval")
                    except ProposalError as exc:
                        raise LedgerCommitError(f"invalid committed proposal: {exc}") from exc
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
                        "setup_detected_at": data["setup_detected_at"],
                        **({"proposal": proposal} if proposal is not None else {}),
                        **({"evidence": evidence} if evidence is not None else {})},
                status="open" if record.decision == "approved" else None)
            session.add(trade)
            session.flush()
            if record.decision == "approved":
                session.add(TradeReservation(trade_id=trade_id, client_order_id=record.client_order_id,
                                             qty=record.qty, reference_price=price))
        return TradeDecisionCommitResult(datetime.now(timezone.utc), record.opportunity_id)

    @staticmethod
    def _validated_proposal(record):
        if record.proposal is None:
            return None  # Legacy callers/rows retain honest absence.
        try:
            proposal = validate_proposal(record.proposal)
        except ProposalError as exc:
            raise LedgerCommitError(f"invalid planning proposal: {exc}") from exc
        if record.decision == "rejected":
            if record.reasons not in (
                ["loss_exposure_unknown"], ["daily_loss_cap_reached"],
                ["projected_loss_exceeds_daily_cap"],
            ):
                raise LedgerCommitError("only a rule-6 rejection may carry a proposal")
        elif record.decision == "approved":
            if (proposal["size"] != record.qty or record.reference_price is None
                    or Decimal(proposal["entry"]) != Decimal(str(record.reference_price))):
                raise LedgerCommitError("proposal differs from authorized reservation terms")
        else:
            raise LedgerCommitError("invalid decision for planning proposal")
        if (proposal["direction"] != ("long" if record.direction == "BUY" else "short")
                or Decimal(proposal["stop"]) != Decimal(str(record.structural_invalidation))
                or proposal["target"] is None
                or Decimal(proposal["target"]) != Decimal(str(record.structural_target))):
            raise LedgerCommitError("proposal differs from decision geometry")
        return proposal
