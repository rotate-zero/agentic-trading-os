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

Two base classes, not one (decision #96): `CalendarProvider` is genuinely
market-wide — one evaluate(), one ContextChanged(symbol=None). But
`FundamentalsProvider`/`NewsFlagProvider` are inherently per-symbol (the
architecture doc itself says "a symbol's recent headlines"), and §5 never
actually resolved how "one ContextChanged event" was supposed to work
once some providers are global and others aren't — it just wasn't
relevant until Fundamentals/News existed to expose the gap. Rather than
force both shapes through one ambiguous interface, `SymbolContextProvider`
below is the explicit second shape; `ContextEngine.evaluate_for_symbol()`
is what calls it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ContextProvider(ABC):
    """One provider, one question, market-wide. `name` is the key this
    provider's output is merged under in ContextChanged.providers."""

    name: str

    @abstractmethod
    async def evaluate(self) -> dict:
        """Return this provider's current output as a flat dict. Called
        by ContextEngine.evaluate_all() — never invoked directly by
        anything downstream of Context Engine."""
        raise NotImplementedError


class SymbolContextProvider(ABC):
    """One provider, one question, per-symbol (decision #96). `name` is
    the key this provider's output is merged under in ContextChanged.providers,
    same convention as ContextProvider — the only difference is evaluate()
    takes the symbol being evaluated."""

    name: str

    @abstractmethod
    async def evaluate(self, symbol: str) -> dict:
        """Return this provider's current output for `symbol` as a flat
        dict. Called by ContextEngine.evaluate_for_symbol() — never
        invoked directly by anything downstream of Context Engine."""
        raise NotImplementedError

