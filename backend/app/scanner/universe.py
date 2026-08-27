"""
UniverseProvider — which symbols are even eligible to be scored.
docs/architecture/scanner-design.md §3. One implementation today
(`StaticUniverseProvider`), backed by a plain in-memory list. A real
`scanner_universe` config table is genuine follow-up work for once Saqib
has actually curated the Core-100 list (§10 open question #1 — still his
call, not resolved by this module).

`TEST_UNIVERSE` below is NOT that curated list. It's a small, well-known,
liquid placeholder that exists for exactly one reason: exercising the
scan pipeline end to end (Saqib: "we will test the pipeline based on
these scan results"). Swap it for the real Core-100 the moment that list
exists — nothing about `UniverseProvider`'s interface, or
`MarketActivityScanner` once it's built (§5), needs to change when that
happens. That's the whole point of this being an interface rather than a
symbol list baked directly into the scorer or the test script.
"""
from __future__ import annotations

from typing import Protocol


class UniverseProvider(Protocol):
    def get_core_universe(self) -> list[str]: ...


class StaticUniverseProvider:
    """The only implementation today. Takes whatever list it's given —
    doesn't know or care whether that's the real Core-100 or a
    throwaway test set."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)

    def get_core_universe(self) -> list[str]:
        return list(self._symbols)


# Placeholder ONLY — see module docstring. Six liquid, well-known names,
# not Saqib's curated Core-100 (which doesn't exist yet). Good enough to
# tell whether unusual-volume / volatility-relative-move scoring produces
# sane rankings; not good enough to represent what the real universe's
# spread of liquidity/ATR will actually look like.
TEST_UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA", "SPY"]
