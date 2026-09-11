"""
FixtureCandleProvider — a synthetic, explicitly-labeled `MarketDataProvider`
(`app/broker_adapters/base.py`) used ONLY to validate that the Backtest
Runner's replay/persistence plumbing works end-to-end.

**What this is, and is not (read before using this for anything else).**
This is NOT a historical data source. `PolygonAdapter.get_historical()`
raises `HistoricalDataUnavailableError` for 1m/5m/15m timeframes on the
free tier (a real, confirmed `NOT_AUTHORIZED` response — see
`polygon_provider.py`'s own handling) and no other real historical
minute-level provider is wired into this codebase today. A run against
this provider proves the harness can replay candles through the real
Feature/Level-Interaction/Market-State/Context/Strategy pipeline and
persist a real `StrategyOutcomeRecord` — it proves NOTHING about whether
any strategy is actually profitable, because the candles are hand-built
or synthetically generated, not real market history. Sourcing real
minute-level historical data (paid Polygon tier, Databento, or another
vendor — `docs/decisions/future-ideas.md` #17) remains a separate, real
prerequisite this module does not resolve.

**Why this implements `MarketDataProvider`, not something bespoke.**
`docs/decisions/future-ideas.md` #5 already establishes that a Replay
Engine is just another `MarketDataProvider` implementation, consumed
without a real-time throttle (`strategy-engine-design.md` §7). This class
is that same idea, deliberately narrow: it doesn't replay, throttle, or
even talk to a broker — it just returns a pre-built, in-memory candle
sequence for `get_historical()`. It satisfies the full ABC (rather than a
narrower ad-hoc protocol) specifically so the Backtest Runner's replay
loop is written against `MarketDataProvider` generically, per the task's
own requirement, and could point at a real historical provider later with
zero change to the runner.

**Construction, not vendor semantics.** Candles are supplied directly
(a Python list) or loaded from a small CSV — whichever is easiest for a
reviewer to read and hand-verify. No attempt is made to look "market
realistic" (no randomness, no noise model) — synthetic OHLCV chosen
deliberately to exercise specific strategy MATCH conditions is more
useful for proving the plumbing than realistic-looking noise would be,
and is honestly labeled as such either way.
"""
from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.broker_adapters.base import (
    Candle,
    HistoricalDataUnavailableError,
    MarketDataProvider,
    SymbolNotFoundError,
    Tick,
)

__all__ = ["FixtureCandleProvider", "load_fixture_candles_csv"]


def load_fixture_candles_csv(path: str | Path, timeframe: str = "1m") -> list[Candle]:
    """Load a small hand-built candle sequence from CSV. Expected columns
    (header row required): `candle_ts,open,high,low,close,volume`.
    `candle_ts` must be ISO-8601 (`Z` or `+00:00` offset accepted); values
    that omit a UTC offset are rejected rather than silently assumed to be
    UTC — matching this project's "no fabricated state" posture applied to
    time, not just data. Chronological order is the caller's
    responsibility (same contract `feature_engine/historical.py`'s
    `compute_series()` already documents) — this loader does not sort.
    """
    candles: list[Candle] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["candle_ts"])
            if ts.tzinfo is None:
                raise ValueError(
                    f"Fixture candle_ts {row['candle_ts']!r} has no UTC offset — "
                    "refusing to guess a timezone for historical replay data."
                )
            candles.append(
                Candle(
                    timeframe=timeframe,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    candle_ts=ts.astimezone(timezone.utc),
                )
            )
    return candles


class FixtureCandleProvider(MarketDataProvider):
    """Synthetic `MarketDataProvider` — see module docstring. Holds one
    candle sequence per (symbol, timeframe); `get_historical()` returns
    the slice within `[start, end)`, raising `SymbolNotFoundError` for a
    symbol/timeframe combination it wasn't built with (never an empty
    list masquerading as "we checked and there's nothing there" — that
    would be indistinguishable from a real symbol that genuinely has no
    data in range, a different, honest condition this class doesn't
    fabricate either).

    `connect()`/`disconnect()`/`subscribe()`/`unsubscribe()`/`on_tick()`
    are all real, trivial implementations (satisfying the ABC in full,
    per the module docstring's reasoning) — this class is a historical
    ("what happened") provider only; nothing in the Backtest Runner calls
    the streaming half.
    """

    def __init__(self, candles: dict[tuple[str, str], list[Candle]]) -> None:
        """`candles` keyed by (symbol, timeframe) -> chronological list.
        Use `FixtureCandleProvider.single(symbol, timeframe, candles)` for
        the common one-symbol case."""
        self._candles = candles
        self._connected = False
        self._subscribed: set[str] = set()
        self._tick_callbacks: list[Callable[[Tick], None]] = []

    @classmethod
    def single(cls, symbol: str, timeframe: str, candles: list[Candle]) -> "FixtureCandleProvider":
        return cls({(symbol, timeframe): candles})

    # --- MarketDataProvider interface --------------------------------------

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def subscribe(self, symbols: list[str]) -> None:
        self._subscribed.update(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self._subscribed.discard(symbol)

    async def get_historical(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        # Timeframe checked BEFORE symbol existence, deliberately: a
        # request for a timeframe this provider structurally can't ever
        # serve (v1 fixture data is 1m-only, matching every v1 strategy's
        # real requirement — they all read 1m FeatureSets, decision #99)
        # is a capability gap independent of which symbol was asked for —
        # same "can never work, for any symbol" category
        # HistoricalDataUnavailableError's own docstring defines, and
        # PolygonAdapter hits this exact ordering question for the same
        # reason (a bad timeframe on an unresolvable ticker is still a
        # timeframe problem, not evidence the ticker doesn't exist).
        if timeframe != "1m":
            raise HistoricalDataUnavailableError(
                provider="FixtureCandleProvider",
                reason=f"fixture data only covers 1m candles, not {timeframe!r}",
            )
        key = (symbol, timeframe)
        if key not in self._candles:
            # Same "provider structurally doesn't have this" signal
            # PolygonAdapter's own get_historical() uses for an
            # unresolvable ticker (see its module docstring) — a
            # FixtureCandleProvider not built with this symbol/timeframe
            # is exactly that case, not "zero rows in range."
            raise SymbolNotFoundError(symbol, provider="FixtureCandleProvider")
        return [c for c in self._candles[key] if start <= c.candle_ts < end]

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)
