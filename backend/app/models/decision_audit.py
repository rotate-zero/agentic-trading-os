"""
Decision audit persistence (D2, `decision-selection-audit-contract`,
trading-intelligence-architecture.md §19.4): the append-only selection
journal and the candidate acceptance claim.

* `selection_attempts` — one immutable row per selection attempt, shadow or
  not. A selection is advisory evidence; it is never an approved/rejected
  trade, a reservation or a `StrategyOutcome`.
* `candidate_acceptances` — the durable claim that a candidate was consumed by
  one approved trade. Its composite primary key `(execution_mode,
  candidate_id)` is the whole uniqueness contract: a candidate is never
  accepted twice, including after the trade closed or its unsent entry was
  cancelled. Rows are written only by the Governor ledger adapter, in the SAME
  transaction as the approved trade, proposal and reservation.

`candidate_acceptances.selection_shadow` is a constant-false column. It exists
only so the composite foreign key to
`selection_attempts(selection_id, execution_mode, selected_candidate_id,
shadow)` can be satisfied exclusively by a NON-shadow, SELECTED attempt of the
same mode that selected exactly this candidate: a shadow, abstained, missing,
wrong-mode or wrong-candidate attempt cannot support a claim, even for a second
writer. (`selected_candidate_id` is NULL on an abstained attempt, and a NULL
never matches a foreign key.) The trade-side association (the trade is approved,
shares the claim's mode and matches the selected candidate) is verified by the
Governor adapter inside the transaction, not by a constraint.

Both tables refuse UPDATE with a trigger (migration 0018). Application code
never deletes either; there is no backfill from legacy trades.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_MODES = "('backtest', 'simulated', 'paper', 'live')"


class SelectionAttempt(Base):
    """One immutable selection attempt (§19.4). `selection_id` is supplied by the
    caller (it is the D1 `audit_id`), never generated here."""

    __tablename__ = "selection_attempts"

    selection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # selected | abstained
    selected_candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(f"execution_mode IN {_MODES}", name="ck_selection_attempts_mode"),
        CheckConstraint("result IN ('selected', 'abstained')", name="ck_selection_attempts_result"),
        CheckConstraint(
            "(result = 'selected') = (selected_candidate_id IS NOT NULL)",
            name="ck_selection_attempts_selected_candidate",
        ),
        CheckConstraint(
            "selected_candidate_id IS NULL OR length(selected_candidate_id) > 0",
            name="ck_selection_attempts_candidate_id",
        ),
        CheckConstraint("length(policy_version) > 0", name="ck_selection_attempts_policy_version"),
        CheckConstraint("schema_version >= 1", name="ck_selection_attempts_schema_version"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_selection_attempts_evidence_object"),
        # Foreign-key target for candidate_acceptances (see module docstring).
        UniqueConstraint(
            "selection_id", "execution_mode", "selected_candidate_id", "shadow",
            name="uq_selection_attempts_acceptance_target",
        ),
    )


class CandidateAcceptance(Base):
    """The durable claim that `candidate_id` was consumed by approved trade `trade_id`."""

    __tablename__ = "candidate_acceptances"

    execution_mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trade_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.trade_id"), nullable=False)
    selection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    selection_shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_id", name="uq_candidate_acceptances_trade_id"),
        ForeignKeyConstraint(
            ["selection_id", "execution_mode", "candidate_id", "selection_shadow"],
            [
                "selection_attempts.selection_id",
                "selection_attempts.execution_mode",
                "selection_attempts.selected_candidate_id",
                "selection_attempts.shadow",
            ],
            name="fk_candidate_acceptances_selected_attempt",
        ),
        CheckConstraint(f"execution_mode IN {_MODES}", name="ck_candidate_acceptances_mode"),
        CheckConstraint("NOT selection_shadow", name="ck_candidate_acceptances_non_shadow"),
        CheckConstraint("length(candidate_id) > 0", name="ck_candidate_acceptances_candidate_id"),
    )
