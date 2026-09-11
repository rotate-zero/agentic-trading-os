"""
BacktestContextProvider — the seam between the Backtest Runner and
whatever supplies Context Engine's provider set for a replay run.

**Why this seam exists at all, rather than just wiring a fixture
Calendar provider directly into the runner.** Tracing `Strategy.evaluate()`
(`base_strategy.py`) back to its inputs: it needs a real `ContextChanged`,
which only ever comes from `ContextEngine`. But `ContextEngine` (see its
own module docstring) is NOT candle-driven — `_symbol_loop` fires
`evaluate_for_symbol()` off a real 15-minute `asyncio.sleep` wall-clock
timer, and its two real per-symbol providers (`FundamentalsProvider`,
`NewsFlagProvider`) call live Finnhub endpoints that only ever answer
"what is this right now" — there is no point-in-time historical mode for
either, at any subscription tier. That is a structural gap, not a
missing-credentials one, and it's separate from (and arguably deeper
than) the Polygon minute-candle gap `fixture_provider.py` documents.

A naive fix — hardcode "backtest context = calendar only" straight into
the runner — would teach the architecture the wrong lesson: that
Context, for backtest purposes, IS the calendar. It isn't; it's just the
only piece of Context Engine that happens to be network-free and
`MarketClock`-derivable, so it's the only piece a fixture run can
honestly produce today. `BacktestContextProvider` names that as a
pluggable choice, not a fact about backtesting in general, so a real
future historical-context source (see `HistoricalContextProvider` below)
has somewhere to plug in without the Runner or this module changing.

**What a `BacktestContextProvider` actually supplies.** `ContextEngine`
is constructed with two provider lists (`providers: list[ContextProvider]`,
market-wide; `symbol_providers: list[SymbolContextProvider]`, per-symbol
— `context_engine/provider.py`'s own two-ABC split, decision #96). A
`BacktestContextProvider` hands the Runner exactly those two lists via
`build_engine_providers()`, and — since a real historical provider would
need to report facts for the REPLAYED instant, not wall-clock now, and
`ContextProvider.evaluate()` is always called with zero arguments (see
`context_engine/provider.py`'s own signature note) — exposes
`advance_to(candle_ts)` so the Runner can tell a stateful provider set
"the replay clock is here" immediately before each
`evaluate_for_symbol()`/`evaluate_all()` call. A provider set with
nothing time-sensitive to track (none exist yet) can make this a no-op.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.core.market_clock import MarketClock

# 2026 FOMC decision dates — the SAME set `context_engine/providers/
# calendar.py`'s real CalendarProvider hardcodes (that module's own
# TODO(Phase 5+) note applies equally here). Duplicated rather than
# imported as a private module-level constant, same call this project
# already made for `scheduler.py`'s `_CROSS_SYMBOL_SENTINEL` (see that
# module's own docstring for the reasoning) — promoting it to a shared
# location is a trivial follow-up if it drifts, not something this task
# should do as a side effect.
from app.context_engine.providers.calendar import _FOMC_DATES_2026

__all__ = ["BacktestContextProvider", "FixtureBacktestContextProvider", "HistoricalContextProvider"]


class BacktestContextProvider(ABC):
    """See module docstring. One instance is constructed per backtest
    run and handed to whichever component wires the real `ContextEngine`
    (`replay_state_producer.py`)."""

    @abstractmethod
    def build_engine_providers(self) -> tuple[list[ContextProvider], list[SymbolContextProvider]]:
        """Returns (market-wide providers, per-symbol providers) — the
        exact two lists `ContextEngine.__init__` takes."""
        raise NotImplementedError

    @abstractmethod
    def advance_to(self, candle_ts: datetime) -> None:
        """Called before each context evaluation during replay so a
        stateful provider can report facts for `candle_ts`, not wall-clock
        `datetime.now()` — the same backtest-safety invariant
        `strategy-engine-design.md` §7 already requires of every
        strategy, applied here to Context instead."""
        raise NotImplementedError


class _ReplayClockCalendarProvider(ContextProvider):
    """Market-wide `ContextProvider` backed by the REAL `CalendarProvider`
    logic (`MarketClock.trading_day()`/`current_session()`/etc. — not
    reimplemented, not approximated), but reporting facts for the
    replay's current instant instead of wall-clock now.

    Why not just reuse `context_engine/providers/calendar.py`'s
    `CalendarProvider` directly: its `evaluate(ts=...)` optional argument
    is explicitly documented as "test-only — ContextEngine always calls
    this with no arguments in production." `ContextEngine.evaluate_all()`/
    `evaluate_for_symbol()` never pass `ts` — they can't, the zero-arg
    contract is fixed at the ABC level (`context_engine/provider.py`).
    So a real `CalendarProvider` instance driven by the real `ContextEngine`
    would silently read `datetime.now()` for every replayed candle,
    leaking wall-clock time into simulated history. This class holds a
    small mutable cursor (`advance_to()`) instead, updated by the Runner
    immediately before each evaluation, and its own zero-arg `evaluate()`
    reads that cursor — same MarketClock methods, same FOMC-date set,
    different (replay-safe) source of "now."
    """

    name = "calendar"

    def __init__(self, clock: MarketClock | None = None) -> None:
        from app.core.market_clock import get_market_clock

        self._clock = clock or get_market_clock()
        self._current_ts: datetime | None = None

    def advance_to(self, candle_ts: datetime) -> None:
        self._current_ts = candle_ts

    async def evaluate(self) -> dict:
        if self._current_ts is None:
            raise RuntimeError(
                "_ReplayClockCalendarProvider.evaluate() called before advance_to() — "
                "the Runner must set the replay instant before the first context evaluation."
            )
        ts = self._current_ts
        today = self._clock.trading_day(ts)
        session = self._clock.current_session(ts)
        return {
            "session": session.value,
            "is_market_open": self._clock.is_market_open(ts),
            "is_half_day": self._clock.is_half_day(today),
            "minutes_since_open": self._clock.minutes_since_open(ts),
            "fed_day": today in _FOMC_DATES_2026,
            "trading_day": today.isoformat(),
        }


class FixtureBacktestContextProvider(BacktestContextProvider):
    """v1's only real implementation. Market-wide: a replay-clock-safe
    calendar provider (real `MarketClock` logic — see
    `_ReplayClockCalendarProvider` above). Per-symbol: deliberately NONE
    — `FundamentalsProvider`/`NewsFlagProvider` need live Finnhub data
    with no historical/point-in-time mode (see this module's own
    docstring); fabricating fundamentals/news values would violate
    "honest absence over fabricated state" (`strategy-engine-design.md`
    §11) at exactly the layer that principle exists to protect.

    Concretely: `ContextEngine(bus, providers=[calendar], symbol_providers=[])`.
    Calling `evaluate_for_symbol(symbol)` against an EMPTY
    `symbol_providers` list still records the symbol as evaluated
    (`ContextEngine._latest_by_symbol[symbol] = {}`, confirmed by reading
    `context_engine/engine.py` directly) — so `capture_context_snapshot()`
    (`state_snapshot.py`) returns a REAL, non-`None` dict once the Runner
    has called `evaluate_for_symbol()` at least once for this run's
    symbol, containing only calendar facts. That dict honestly has no
    `\"fundamentals\"`/`\"news\"` key — never a fabricated placeholder for
    either — which is what \"absence, not fabrication\" means at this
    layer specifically. Same construction shape
    `test_strategy_scheduler.py`'s own `_FakeCalendarProvider` +
    `symbol_providers=[]` precedent already established for exactly this
    problem, extended here to be replay-clock-safe rather than wall-clock
    (that existing test never replays historical timestamps, so it never
    needed to be).
    """

    def __init__(self, clock: MarketClock | None = None) -> None:
        self._calendar = _ReplayClockCalendarProvider(clock)

    def build_engine_providers(self) -> tuple[list[ContextProvider], list[SymbolContextProvider]]:
        return [self._calendar], []

    def advance_to(self, candle_ts: datetime) -> None:
        self._calendar.advance_to(candle_ts)


class HistoricalContextProvider(BacktestContextProvider):
    """NOT BUILT — a documented extension point, not a stub pretending to
    work. This is where a genuinely point-in-time-capable context source
    would plug in once one exists: something that can answer "what were
    AAPL's TTM fundamentals, and what news had broken, as of 2024-03-14
    09:47 ET" — which today's `FundamentalsProvider`/`NewsFlagProvider`
    structurally cannot do (live-only Finnhub endpoints, decision #94/#96
    scope). Sourcing that data is a real, separate prerequisite, same
    category as `fixture_provider.py`'s documented minute-candle gap —
    not resolved by this task, not evaluated here (per this task's own
    explicit scope: no vendor evaluation).

    Trigger to build, matching `future-ideas.md`'s own convention for
    deferred-with-a-condition entries: a real point-in-time
    fundamentals/news source is identified and wired. Until then this
    class exists only so `BacktestContextProvider` has a visible second
    implementation to point at in this docstring — it is not imported or
    constructed anywhere in `backtest_runner/`.
    """

    def build_engine_providers(self) -> tuple[list[ContextProvider], list[SymbolContextProvider]]:
        raise NotImplementedError(
            "HistoricalContextProvider is a documented extension point, not a working "
            "implementation — see this class's own docstring. No point-in-time historical "
            "fundamentals/news source is wired into this codebase yet."
        )

    def advance_to(self, candle_ts: datetime) -> None:
        raise NotImplementedError(
            "HistoricalContextProvider is a documented extension point, not a working "
            "implementation — see this class's own docstring."
        )
