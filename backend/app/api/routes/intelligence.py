"""
GET /intelligence/state — confirmed decision #47. Backs the Feature
Engine panel (frontend/src/components/intelligence/FeatureEnginePanel.tsx):
one merged read of FeatureEngine.get_snapshot() and
LevelInteractionEngine.get_snapshot(), shaped into the
Symbol -> Timeframe -> Unit -> Period -> Variables hierarchy discussed and
confirmed before any of this was built, so the frontend doesn't need to
stitch two responses together itself.

Deliberately NOT pre-populated for the whole configured symbol universe —
same posture as both engines' own get_snapshot() docstrings: a symbol/
timeframe/unit that's never been computed simply isn't in the response, so
the panel naturally renders only what's real right now (today: one
timeframe, "1m"; one unit, "sma") rather than showing empty placeholder
branches for EMA/VWAP/PDH/PDL or 5m/15m/1h before Feature Engine actually
computes any of them.

GET /intelligence/series — confirmed decision #54, Stage 1 of the chart
migration. A DIFFERENT need from /state above: /state answers "what does
Feature Engine currently believe right now" (one point); this answers
"what would this indicator's line look like across a range of candles"
(many points, for the Chart to actually draw). Backed by
feature_engine/historical.py's batch walk, not FeatureEngine.get_snapshot()
— see that module's own docstring for why the live engine's snapshot
can't serve this on its own.

Scoped to exactly the timeframes Feature Engine's live engine itself ever
computes for — "1m", "5m", "15m", "1h" (decisions #45, #51) — not the
full set /market/candles accepts. There is no "1d SMA" computed anywhere
in this system (Feature Engine never aggregates to 1d), and "1d VWAP"
doesn't mean the same thing as intraday VWAP (which resets every regular
session) — so both are rejected here with the same clean-400 posture
/market/candles already uses for "4h", rather than silently returning an
empty series that looks like "not warmed up yet" instead of "not
supported at all."
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import get_settings
from app.feature_engine.engine import get_feature_engine
from app.feature_engine.historical import compute_series
from app.services import candle_aggregator, candle_store
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

# Wall-clock lookback per requested candle, by timeframe — same values
# market.py's own _MINUTES_PER_UNIT uses for the same "count -> how far
# back to query" purpose, duplicated rather than imported: this route
# deliberately supports a NARROWER timeframe set (no "1d", no external-
# provider fallback — see module docstring), so importing market.py's
# full mapping would pull in timeframes this route explicitly rejects.
_MINUTES_PER_UNIT = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


def _parse_level_key(level_key: str) -> tuple[str, str | None]:
    """
    "sma_9" -> ("sma", "9"); "vwap" -> ("vwap", None). Split only on the
    LAST underscore, and only when what follows it is purely numeric — a
    future unit name that itself contains an underscore (unlikely, but
    not this function's job to assume against) wouldn't be misread as
    having a numeric period it doesn't have.
    """
    if "_" in level_key:
        unit, _, suffix = level_key.rpartition("_")
        if suffix.isdigit():
            return unit, suffix
    return level_key, None


@router.get("/state")
async def get_intelligence_state(symbol: str = Query(...)) -> dict[str, Any]:
    feature_snapshot = get_feature_engine().get_snapshot(symbol)
    level_snapshot = get_level_interaction_engine().get_snapshot(symbol)

    timeframes: dict[str, Any] = {}

    for timeframe, tf_data in feature_snapshot.get(symbol, {}).items():
        units: dict[str, Any] = {}
        level_data_for_tf = level_snapshot.get(symbol, {}).get(timeframe, {})

        for level_key, value in tf_data["features"].items():
            unit, period = _parse_level_key(level_key)
            node: dict[str, Any] = {"value": value, "candle_ts": tf_data["candle_ts"]}
            level_interaction = level_data_for_tf.get(level_key)
            if level_interaction is not None:
                node["level_interaction"] = level_interaction

            if period is not None:
                units.setdefault(unit, {})[period] = node
            else:
                # No numeric period (e.g. a future "vwap" or "pdh") — the
                # unit bucket IS the node, no UnitValue level beneath it.
                units[unit] = node

        timeframes[timeframe] = {"close": tf_data["close"], "units": units}

    return {"symbol": symbol, "timeframes": timeframes}


@router.get("/series")
async def get_intelligence_series(
    symbol: str = Query(...),
    timeframe: str = Query("1m"),
    count: int = Query(240, le=1000),
) -> dict[str, Any]:
    if timeframe not in _MINUTES_PER_UNIT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported timeframe {timeframe!r} for indicator series (supported: {sorted(_MINUTES_PER_UNIT)})",
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=_MINUTES_PER_UNIT[timeframe] * count)

    # Same self-recorded-first, aggregated-second retrieval FeatureEngine's
    # own live engine relies on (decisions #45, #51) — deliberately NOT
    # market.py's full three-tier chain: no external-provider fallback,
    # since the live engine never reaches one either, and this series
    # exists specifically to describe what FEATURE ENGINE would have
    # computed, not "whatever candle history exists from any source."
    if timeframe == "1m":
        candles = await asyncio.to_thread(candle_store.get_recorded_candles, symbol, "1m", start, end)
    else:
        candles = await asyncio.to_thread(candle_aggregator.aggregate_from_recorded, symbol, timeframe, start, end)

    settings = get_settings()
    series = compute_series(
        candles[-count:],
        sma_periods=settings.feature_engine_sma_periods,
        ema_periods=settings.feature_engine_ema_periods,
        ema_seed_multiplier=settings.feature_engine_ema_seed_multiplier,
    )
    return {"symbol": symbol, "timeframe": timeframe, "series": series}
