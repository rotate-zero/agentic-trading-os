"""
historical_provider_guard — installs a backtest run's own
`MarketDataProvider` as `broker_registry`'s process-wide "historical"
role for the duration of exactly one run, then restores whatever was
there before. Decision #135.

**Why this is needed at all.** `FeatureEngine`'s Daily Levels/ATR/RVOL
refresh (`_maybe_refresh_daily_levels`/`_update_atr`/`_update_rvol`,
`feature_engine/engine.py`) all read `self._daily_candle_cache`, which
is populated exclusively through `broker_registry.get_historical_provider()`
— a process-wide global, re-resolved fresh on every call (confirmed by
reading `feature_engine/engine.py` directly), never something a
`FeatureEngine` instance is handed at construction — there is no such
constructor parameter (confirmed by reading `FeatureEngine.__init__`).
`EngineBackedReplayStateProducer` (`replay_state_producer.py`) builds a
brand-new `FeatureEngine()` for every backtest run with nothing wired
into that global, which is exactly why `scenarios.py`'s own module
docstring could state, before this: "`volume_regime_score`/
`volatility_regime_score` are structurally always `0.0` for ANY
BacktestRunner replay today, for any symbol." Closing that means a
backtest run's own `MarketDataProvider` has to temporarily BECOME the
process's historical role for the run's duration — this module is that
seam, deliberately shaped to match `engine_singleton_guard.py`'s own
already-accepted save/install/restore pattern (own `asyncio.Lock`, same
try/finally restore-even-on-exception shape) rather than invent a new
one for a structurally identical problem.

**Why this is a real, documented limitation, not a corner silently
cut — same disclosure `engine_singleton_guard.py` already gives its own
four engine singletons, extended here to one more process-wide global,
checked directly rather than assumed.** `broker_registry.get_historical_provider()`
has exactly three real call sites in this codebase outside
`feature_engine/engine.py` itself (confirmed by grep, not guessed):

  - `GET /market/candles` (`api/routes/market.py`) — the one real risk
    the fork this decision resolved was worried about: a concurrent
    caller of this live-facing route getting fixture data back instead
    of an honest error during a backtest's ~1-3 minute run. Checked
    directly: that route gates on `adapter.is_connected()` BEFORE ever
    calling `get_historical()`. This module never calls `.connect()` on
    whatever it installs (see `install_replay_historical_provider()`
    below) — `FeatureEngine` itself never checks `is_connected()`, only
    `is None`, so nothing on the FeatureEngine side needs the installed
    provider to report itself connected. Net effect: `GET
    /market/candles` keeps returning its existing honest 400
    ("no historical provider connected") for the entire duration of a
    backtest run, exactly as it would with nothing installed at all — no
    silent fixture-data leak into a live-facing route, by construction,
    not by luck.
  - `main.py`/`market_data.py` — two identity-comparison reads
    (`is X the same object as Y`), never a `get_historical()` call.
    Unaffected regardless of what's installed here.

`POST /backtest/run` (decision #132) already refuses to run at all while
Finnhub or Polygon is connected — so in the only precondition under
which a backtest is allowed to run today, `broker_registry`'s historical
role was already unclaimed by either of those two going into this
module's swap. One real, PRE-EXISTING gap, not created by this module:
decision #132's guard has no public `is_connected()` accessor for IBKR
the way it does for Finnhub/Polygon (`broker.py` never got one), so an
IBKR-connected live session isn't blocked by that 409 today. This module
doesn't close that gap either — `engine_singleton_guard.py`'s own
process-wide swap of all four engine singletons is already unsafe
against a concurrent IBKR session for reasons that have nothing to do
with the historical-provider role specifically, so this module's own
swap doesn't change that exposure one way or the other. Flagged in
decision #135's own text as a separate, pre-existing follow-up, not
attempted here.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.broker_adapters.base import MarketDataProvider
from app.services import broker_registry

logger = logging.getLogger(__name__)

# Process-wide, mirroring engine_singleton_guard.py's own _RUN_LOCK
# exactly: only one call to install_replay_historical_provider() may be
# "inside the with block" at a time, anywhere in this process — not one
# lock per run, since the thing being protected (broker_registry's
# single historical-role slot) is itself process-wide, not per-run. In
# `BacktestRunner.run()`'s real call path this is always entered nested
# inside `install_replay_engines()`'s own lock, which already serializes
# concurrent runs — this module keeps its own lock anyway so it's
# correctly self-contained and independently testable, same reasoning
# `engine_singleton_guard.py` itself doesn't rely on any OTHER module's
# locking to be correct.
_RUN_LOCK = asyncio.Lock()


@asynccontextmanager
async def install_replay_historical_provider(provider: MarketDataProvider) -> AsyncIterator[None]:
    """Install `provider` as `broker_registry`'s historical role for the
    duration of the `async with` block; restore whatever was installed
    before (a real live provider, another backtest's provider, or None)
    in `finally`, even on exception — a crashed backtest run must never
    leave the process's real historical role pointing at a torn-down
    backtest provider.

    Deliberately never calls `provider.connect()`/`.disconnect()` —
    `broker_registry` only tracks WHICH provider fills a role, not its
    connection state, and `FeatureEngine` never checks `is_connected()`
    on whatever it reads from `get_historical_provider()`. Leaving the
    installed provider's own `is_connected()` at whatever it already
    reports (`FixtureCandleProvider`'s own default: `False`, since
    nothing in a replay ever calls `connect()` on it either) is exactly
    what keeps `GET /market/candles` answering honestly during a
    backtest run — see this module's own docstring.

    Blocks (does not queue-and-proceed) if another run already holds
    this guard — see module docstring's reasoning on `_RUN_LOCK`. In the
    real call path (`BacktestRunner.run()`, nested inside
    `install_replay_engines()`) this is never actually contended, since
    the outer guard already serializes runs — a caller using this module
    standalone that wants to reject rather than wait should check
    `_RUN_LOCK.locked()` first or wrap this in `asyncio.wait_for()`
    itself, the same guidance `engine_singleton_guard.py` gives for its
    own lock.
    """
    async with _RUN_LOCK:
        prev = broker_registry.get_historical_provider()
        broker_registry.set_historical_provider(provider)
        logger.info("historical_provider_guard: installed backtest replay historical provider")
        try:
            yield
        finally:
            if prev is None:
                broker_registry.clear_historical_provider()
            else:
                broker_registry.set_historical_provider(prev)
            logger.info("historical_provider_guard: restored prior historical provider")
