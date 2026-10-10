"""PostgreSQL adapter for the append-only selection journal (D2, §19.4).

``append`` validates nothing new (the record is already validated and detached
by :class:`SelectionAttemptRecord`) and runs ONE owned transaction:

1. ``INSERT ... ON CONFLICT (selection_id) DO NOTHING RETURNING`` -- the primary
   key arbitrates concurrent writers of the same ID, so no table lock is needed
   and the append never touches ``trades``/``orders``/``trade_reservations``.
2. If nothing was inserted, the stored row is read and compared field-for-field
   (evidence by value, ``True`` never equal to ``1``): identical is a no-op,
   anything else raises :class:`SelectionJournalConflict` and changes nothing.

There is no update or delete, and the database refuses UPDATE as well (migration
0018). Any database failure is an explicit :class:`SelectionJournalError`; the
caller decides whether that stops an entry cycle or marks the shadow audit
unavailable. The adapter never retries and never swallows an error.

Lock order: this adapter takes no table lock. The claim table is locked only by
the Governor ledger adapter, always AFTER the established
``trades -> orders -> trade_reservations`` prefix.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.decision_audit.ports import AppendResult, SelectionJournalConflict, SelectionJournalError
from app.decision_audit.records import SelectionAttemptRecord, SelectionAuditError
from app.models.decision_audit import SelectionAttempt


def record_from_row(row: SelectionAttempt) -> SelectionAttemptRecord:
    """A detached record from a stored row, or ``SelectionAuditError`` if the row is invalid."""
    return SelectionAttemptRecord.create(
        selection_id=row.selection_id, created_at=row.created_at, execution_mode=row.execution_mode,
        shadow=row.shadow, policy_version=row.policy_version, result=row.result,
        selected_candidate_id=row.selected_candidate_id, evidence=row.evidence,
        schema_version=row.schema_version,
    )


class PostgresSelectionJournal:
    def __init__(self, session_factory):
        self._sessions = session_factory

    def append(self, record: SelectionAttemptRecord) -> AppendResult:
        if not isinstance(record, SelectionAttemptRecord):
            raise SelectionJournalError("append requires a validated SelectionAttemptRecord")
        try:
            with self._sessions() as session:
                if session.in_transaction():
                    raise SelectionJournalError("journal adapter requires a fresh owned transaction")
                with session.begin():
                    inserted = session.execute(
                        insert(SelectionAttempt)
                        .values(
                            selection_id=record.selection_id, created_at=record.created_at,
                            execution_mode=record.execution_mode, shadow=record.shadow,
                            policy_version=record.policy_version, result=record.result,
                            selected_candidate_id=record.selected_candidate_id,
                            schema_version=record.schema_version, evidence=record.evidence,
                        )
                        .on_conflict_do_nothing(index_elements=[SelectionAttempt.selection_id])
                        .returning(SelectionAttempt.selection_id)
                    ).first()
                    if inserted is not None:
                        return AppendResult(record.selection_id, True)
                    stored = session.get(SelectionAttempt, record.selection_id)
                    if stored is None:  # cannot happen under PK semantics; never guess
                        raise SelectionJournalError("journal conflict row vanished during append")
                    if not record_from_row(stored).equivalent(record):
                        raise SelectionJournalConflict(
                            f"selection {record.selection_id} already holds a different record; "
                            "historical attempts are never changed"
                        )
                    return AppendResult(record.selection_id, False)
        except SelectionJournalError:
            raise
        except (SQLAlchemyError, SelectionAuditError, ValueError, TypeError) as exc:
            raise SelectionJournalError(f"selection journal append failed: {exc}") from exc

    def get(self, selection_id: UUID) -> SelectionAttemptRecord | None:
        try:
            with self._sessions() as session:
                row = session.scalars(
                    select(SelectionAttempt).where(SelectionAttempt.selection_id == selection_id)
                ).first()
                return None if row is None else record_from_row(row)
        except (SQLAlchemyError, SelectionAuditError, ValueError, TypeError) as exc:
            raise SelectionJournalError(f"selection journal read failed: {exc}") from exc
