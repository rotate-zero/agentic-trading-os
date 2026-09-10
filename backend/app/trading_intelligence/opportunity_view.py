"""
opportunity_view.py — a narrow, read-only downstream view over
OpportunityCache.get_snapshot() (decision #114/#115), answering exactly
one observability question that currently requires manually inspecting
the raw per-(symbol, strategy) cache structure: for a given symbol, do
the strategies that currently have a cached opportunity agree on
direction, or conflict?

**This is NOT the Opportunity Engine.** `opportunity_cache.py`'s own
module docstring already reserves that name for the real, cross-strategy
RANKING engine described in strategy-engine-design.md §9 — the one that
would answer "which opportunity should win." This module answers a
strictly smaller, D4-independent question: "is there more than one
opportunity here, and do they point the same way." It computes no score,
weight, priority, or winner, and must never be extended to do so without
first resolving D4 (strategy-engine-design.md §10) — which remains
explicitly OPEN. `confidence`/`setup_detected_at` below are passthrough
observability fields only, copied verbatim from each cached Opportunity,
never averaged, compared, or otherwise used to break a tie.

**Placement.** Same "reads another engine's get_snapshot(), owns
nothing itself" shape `state_snapshot.py` already established one layer
above the engines it depends on — this module sits one layer above
`opportunity_cache.py` the same way. It holds no state of its own (no
singleton, nothing for main.py's lifespan or conftest.py's singleton
reset to manage) and does not import from, or modify, `OpportunityCache`
itself beyond calling its already-public `get_snapshot()`.

**Terminology: "currently cached," not "live."** `OpportunityCache` is
status-blind (it never reads `Opportunity.status` — "potential" /
"waiting" / "actionable" / "expired" are all cached identically) and has
no TTL or purge (its own module docstring: "the LAST Opportunity per
(symbol, strategy) pair," retained indefinitely, no rolling window).
Concretely: an ORB BUY from 9:35am and a Reversal SELL from 2:15pm both
still read as "currently cached" at 3:00pm if neither strategy has fired
again since. This module inherits that behavior exactly — it does not
invent status filtering or staleness/TTL semantics `OpportunityCache`
itself doesn't have. "Live," used loosely in earlier discussion of this
task, is deliberately avoided in this module's own vocabulary because it
implies a recency guarantee that doesn't actually exist yet. If a
TTL/status-aware notion of "live" is ever wanted, that's a change to
`OpportunityCache`'s own contract, not something to paper over here.

**Classification, per symbol — mutually exclusive, never both:**
  - 0 or 1 strategy currently cached for a symbol -> the symbol is
    absent from BOTH `agreements` and `conflicts`. Not a false "no
    conflict" entry — genuinely absent, same "honest state over
    fabricated state" discipline every get_snapshot() in this codebase
    already follows.
  - 2+ strategies, every one's `direction` the same -> one `agreements`
    entry.
  - 2+ strategies, both "BUY" and "SELL" present -> one `conflicts`
    entry covering ALL strategies for that symbol (not just the
    disagreeing pair) — a 2-BUY-1-SELL symbol is a single conflict
    entry with three strategies represented under their own directions,
    never an agreement entry for the two BUYs plus a separate anomaly.
  A cached entry whose `direction` is missing or not one of "BUY"/"SELL"
  (malformed at the publisher — OpportunityCache trusts raw payloads
  without re-validating, per its own docstring) is excluded from
  consideration for that symbol rather than crashing this computation;
  logged at debug, same "don't let one bad entry take down the rest"
  posture the Scheduler's own per-strategy exception isolation already
  uses elsewhere in this codebase.

**Determinism.** Symbols and, within a symbol, strategy names are
processed in sorted order, and conflicting directions are always
reported "BUY" before "SELL" when both are present — presentation/test
determinism only, not a ranking signal of any kind.
"""
from __future__ import annotations

import logging
from typing import Any

from app.trading_intelligence.opportunity_cache import get_opportunity_cache

logger = logging.getLogger(__name__)

_DIRECTIONS_IN_ORDER = ("BUY", "SELL")


def _passthrough(strategy_name: str, opportunity: dict[str, Any]) -> dict[str, Any]:
    """Copies exactly the fields this view is allowed to surface —
    `strategy` (the dict key, not part of the cached payload itself),
    plus `confidence`/`setup_detected_at` straight off the cached
    Opportunity. Nothing here is computed from more than one
    Opportunity at a time."""
    return {
        "strategy": strategy_name,
        "confidence": opportunity.get("confidence"),
        "setup_detected_at": opportunity.get("setup_detected_at"),
    }


def compute_opportunity_conflicts(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pure function — the primary target for this module's own tests.
    Takes exactly the shape `OpportunityCache.get_snapshot()` returns
    (`{"symbols": {ticker: {strategy_name: {...Opportunity fields,
    "received_at": ...}}}}`) and returns:

        {"agreements": {symbol: {...}}, "conflicts": {symbol: {...}}}

    `agreements[symbol]` = {"direction": "BUY"|"SELL", "count": int,
    "strategies": [{"strategy", "confidence", "setup_detected_at"}, ...]}
    sorted by strategy name.

    `conflicts[symbol]` = {"count": int, "by_direction": {"BUY": [...],
    "SELL": [...]}} — only the directions actually present appear as
    keys, "BUY" always ordered before "SELL" when both do, each list
    sorted by strategy name.

    Never mutates the input snapshot. No I/O, no engine access — see
    `get_opportunity_conflicts()` below for the version that actually
    reads the live cache.
    """
    agreements: dict[str, Any] = {}
    conflicts: dict[str, Any] = {}

    symbols = snapshot.get("symbols", {})
    for symbol in sorted(symbols):
        strategies_for_symbol = symbols[symbol]

        entries: list[tuple[str, str, dict[str, Any]]] = []
        for strategy_name in sorted(strategies_for_symbol):
            opportunity = strategies_for_symbol[strategy_name]
            direction = opportunity.get("direction")
            if direction not in _DIRECTIONS_IN_ORDER:
                logger.debug(
                    "opportunity_view: %s/%s has no usable direction (%r) — excluded from conflict view",
                    symbol,
                    strategy_name,
                    direction,
                )
                continue
            entries.append((strategy_name, direction, opportunity))

        if len(entries) < 2:
            # Honest absence: 0 or 1 usable cached opportunity for this
            # symbol is neither an agreement nor a conflict. Do not
            # appear in either collection.
            continue

        directions_present = {direction for _, direction, _ in entries}

        if len(directions_present) == 1:
            (direction,) = directions_present
            agreements[symbol] = {
                "direction": direction,
                "count": len(entries),
                "strategies": [_passthrough(name, opp) for name, _, opp in entries],
            }
        else:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for name, direction, opp in entries:
                grouped.setdefault(direction, []).append(_passthrough(name, opp))
            conflicts[symbol] = {
                "count": len(entries),
                "by_direction": {
                    d: grouped[d] for d in _DIRECTIONS_IN_ORDER if d in grouped
                },
            }

    return {"agreements": agreements, "conflicts": conflicts}


def get_opportunity_conflicts(symbol: str | None = None) -> dict[str, Any]:
    """Thin wrapper — the one function anything outside this module
    (a future route, a script, a REPL check) should actually call.
    Reads OpportunityCache's real, current snapshot (optionally
    filtered to a single symbol, same convention as
    OpportunityCache.get_snapshot() itself) and delegates to the pure
    function above. No optional route was added in this delivery
    (decision entry has the reasoning) — this function is what such a
    route would call, unchanged, whenever one is added later.
    """
    snapshot = get_opportunity_cache().get_snapshot(symbol)
    return compute_opportunity_conflicts(snapshot)
