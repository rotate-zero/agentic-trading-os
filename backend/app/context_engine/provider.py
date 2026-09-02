"""
ContextProvider — the one-question-per-provider interface Context Engine
aggregates over. See trading-intelligence-architecture.md §5 (decision
#90) for the boundary rule and full v1 provider list.

Signature note (decision #92): the architecture doc's
`evaluate(self, market_state: MarketState) -> dict` takes a MarketState
that doesn't exist yet — Market State Engine (M2) hasn't been built.
Dropped for this slice rather than guessed at; `market_state` goes back
on once M2 lands and there's a real type to put there, not a stand-in
shape that would likely need to change anyway. ContextEngine always calls
`provider.evaluate()` with no arguments — a subclass MAY add its own
optional keyword args on top of that (CalendarProvider does, for
deterministic tests) as long as the zero-arg call stays valid.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ContextProvider(ABC):
    """One provider, one question. `name` is the key this provider's
    output is merged under in ContextChanged.providers."""

    name: str

    @abstractmethod
    async def evaluate(self) -> dict:
        """Return this provider's current output as a flat dict. Called
        by ContextEngine.evaluate_all() — never invoked directly by
        anything downstream of Context Engine."""
        raise NotImplementedError
