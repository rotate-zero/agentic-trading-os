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
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.feature_engine.engine import get_feature_engine
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


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
