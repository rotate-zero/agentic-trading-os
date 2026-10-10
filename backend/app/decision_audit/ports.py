"""Narrow persistence port for the append-only selection journal (D2, §19.4).

The port exposes ``append`` and ``get`` only. There is no update or delete of a
historical attempt. A persistence failure is always an explicit
:class:`SelectionJournalError`: a caller that must stop an entry cycle on a
durable-write failure (or report an unavailable shadow audit) can tell success
from failure, and nothing is swallowed or retried here.

Candidate acceptance has no port of its own: the claim is written only by the
Governor ledger adapter, inside the approval transaction
(``governor.ports.CandidateAcceptanceContext``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.decision_audit.records import SelectionAttemptRecord


class SelectionJournalError(Exception):
    """The journal write or read could not be completed durably. Never swallowed."""


class SelectionJournalConflict(SelectionJournalError):
    """The same ``selection_id`` already holds a DIFFERENT record. The stored record is unchanged."""


@dataclass(frozen=True)
class AppendResult:
    selection_id: UUID
    inserted: bool  # False = an identical record was already stored (replay no-op)


class SelectionJournalPort(Protocol):
    def append(self, record: SelectionAttemptRecord) -> AppendResult:
        """Durably append ``record``. Identical same-ID replay is a no-op; a different
        record under the same ID raises :class:`SelectionJournalConflict`."""
        ...

    def get(self, selection_id: UUID) -> SelectionAttemptRecord | None:
        """The stored record as a detached value, or None when absent."""
        ...
