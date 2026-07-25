"""
Dev-only payload for proving the Event Bus round-trip end to end
(Phase 2 exit criterion, docs/roadmap/phase-roadmap.md). Not part of the
real event vocabulary in system-design.md §10.3.
"""
from __future__ import annotations

from pydantic import BaseModel


class DevPing(BaseModel):
    message: str
    lane: str  # "critical" or "normal" — echoed back so the caller can verify routing
