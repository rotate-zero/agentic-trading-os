"""
engine_singleton_guard — installs a backtest run's own engine instances
as the process-wide singletons `state_snapshot.py`'s capture functions
(and any strategy calling `get_level_interaction_engine()` directly —
First Pullback/Reversal, per D9) resolve through, for the duration of
exactly one run, then restores whatever was there before.

**Why this is needed at all.** `FeatureEngine`/`LevelInteractionEngine`/
`MarketStateEngine`/`ContextEngine` are all process-wide singletons
(`get_feature_engine()` etc., each backed by a private module-level
`_feature_engine`-style global — confirmed by reading each engine's own
module, and matching the exact reset pattern `backend/tests/conftest.py`'s
own `_reset_app_singletons` fixture already uses for test isolation).
`state_snapshot.py`'s `capture_market_state_snapshot()`/
`capture_context_snapshot()` — the functions D17's own handling depends
on — read through `get_market_state_engine()`/`get_context_engine()`,
not through any reference the Backtest Runner controls directly. A
backtest run needs its own FRESH engine instances (a real historical
replay for AAPL from six months ago must never let the live process's
real, currently-streaming AAPL state leak in, and must never mutate that
live state either) — so those fresh instances have to become the
resolved singleton for the run's duration.

**Why this is a real, documented limitation, not a corner silently cut.**
This makes two backtest runs — or a backtest run and live trading — unsafe
to execute concurrently in one process: installing a second run's engines
while a first is still active would silently point the first run's
in-flight capture calls at the second run's state. `_RUN_LOCK` below
serializes runs in this process for exactly that reason. This module does
not attempt to redesign `state_snapshot.py`/the engines to take an
explicit engine-set parameter instead of resolving a singleton — that
would be a real, separate, larger change to code multiple other things
depend on, out of this task's scope (a future concurrent-backtest need is
the trigger to revisit it, not a hypothetical to build against now).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Process-wide: only one call to install_replay_engines() may be "inside
# the with block" at a time, anywhere in this process — not one lock per
# run, deliberately, since the thing being protected (the singletons
# themselves) is itself process-wide, not per-run.
_RUN_LOCK = asyncio.Lock()


@asynccontextmanager
async def install_replay_engines(
    *,
    feature_engine,
    level_interaction_engine,
    market_state_engine,
    context_engine,
) -> AsyncIterator[None]:
    """Install the four given engine instances as the process-wide
    singletons for the duration of the `async with` block; restore
    whatever was installed before (real live instances, another
    backtest's instances, or None) in `finally`, even on exception —
    a crashed backtest run must never leave the process's real
    singletons pointing at a torn-down backtest engine.

    Blocks (does not queue-and-proceed) if another run already holds
    this guard — see module docstring. Callers that want to reject
    rather than wait for a concurrent request should check
    `_RUN_LOCK.locked()` before calling, or wrap this in
    `asyncio.wait_for()` themselves; this module doesn't impose a
    timeout policy of its own.
    """
    import app.context_engine.engine as context_engine_module
    import app.feature_engine.engine as feature_engine_module
    import app.market_state_engine.engine as market_state_engine_module
    import app.trading_intelligence.level_interaction_engine as level_interaction_engine_module

    async with _RUN_LOCK:
        prev_feature_engine = feature_engine_module._feature_engine
        prev_level_interaction_engine = level_interaction_engine_module._level_interaction_engine
        prev_market_state_engine = market_state_engine_module._market_state_engine
        prev_context_engine = context_engine_module._context_engine

        feature_engine_module._feature_engine = feature_engine
        level_interaction_engine_module._level_interaction_engine = level_interaction_engine
        market_state_engine_module._market_state_engine = market_state_engine
        context_engine_module._context_engine = context_engine
        logger.info("engine_singleton_guard: installed backtest replay engines")
        try:
            yield
        finally:
            feature_engine_module._feature_engine = prev_feature_engine
            level_interaction_engine_module._level_interaction_engine = prev_level_interaction_engine
            market_state_engine_module._market_state_engine = prev_market_state_engine
            context_engine_module._context_engine = prev_context_engine
            logger.info("engine_singleton_guard: restored prior engine singletons")
