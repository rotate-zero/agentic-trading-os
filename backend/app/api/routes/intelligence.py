"""
GET /intelligence/state — confirmed decision #47. Backs the Feature
Engine panel (frontend/src/components/intelligence/FeatureEnginePanel.tsx):
one merged read of FeatureEngine.get_snapshot() and
LevelInteractionEngine.get_snapshot(), shaped into the
Symbol -> Timeframe -> Unit -> Period -> Variables hierarchy discussed and
confirmed before any of this was built, so the frontend doesn't need to
stitch two responses together itself.

Also carries a top-level `daily_levels` array (confirmed decision #61,
Stage 4) — symbol-scoped, not nested under any one timeframe, since the
same list is attached to every timeframe's FeatureSet on a given close
(see FeatureEngine.get_snapshot()'s own docstring). No `level_interaction`
data attached to these yet — Stage 3 (LevelInteractionEngine reading
daily_levels, not just `features`) isn't built; this is deliberately just
the raw price/strength/level_id shape for now.

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
    "sma_9" -> ("sma", "9"); "vwap" -> ("vwap", None); "cam_r1" ->
    ("camarilla", "r1"). Split only on the LAST underscore, and only when
    what follows it is purely numeric — a future unit name that itself
    contains an underscore (unlikely, but not this function's job to
    assume against) wouldn't be misread as having a numeric period it
    doesn't have.

    Camarilla (decision #56) is special-cased ahead of that rule, not
    folded into it: its nine keys (`cam_pp`, `cam_r1`-`cam_r4`,
    `cam_s1`-`cam_s4`, from indicators/camarilla.py) already share the
    `cam_` prefix the generic rule was built for, but their suffixes
    (`pp`, `r1`, ...) aren't numeric, so the digit check silently sent
    each one down the flat "no period" path instead — nine standalone
    accordion rows in the panel where every other multi-value family
    (SMA, EMA) gets exactly one, grouped by period. Flagged as a known,
    non-blocking display quirk in decision #56 and left for a follow-up
    rather than fixed inline there, since nothing about Level Interaction
    tracking depended on it (that engine walks FeatureSet.features by key
    regardless of how this route chooses to group them for display).
    """
    if level_key.startswith("cam_"):
        return "camarilla", level_key[len("cam_"):]
    if "_" in level_key:
        unit, _, suffix = level_key.rpartition("_")
        if suffix.isdigit():
            return unit, suffix
    return level_key, None


@router.get("/state")
async def get_intelligence_state(
    symbol: str = Query(...),
    daily_levels_lookback_days: int | None = Query(
        None,
        description=(
            "Confirmed decision #62. Omit for the server-configured default "
            "(the pre-computed, cached snapshot — zero extra work). When "
            "provided, re-clusters on the fly from already-cached raw candles "
            "(no new provider fetch) — e.g. 30 for 'past 30 days only'. Larger "
            "than what's actually cached is silently clamped, not an error."
        ),
    ),
) -> dict[str, Any]:
    feature_snapshot = get_feature_engine().get_snapshot(symbol)
    level_snapshot = get_level_interaction_engine().get_snapshot(symbol)

    timeframes: dict[str, Any] = {}
    daily_levels: list[dict[str, Any]] = []

    for timeframe, tf_data in feature_snapshot.get(symbol, {}).items():
        units: dict[str, Any] = {}
        level_data_for_tf = level_snapshot.get(symbol, {}).get(timeframe, {})

        # Daily Levels (confirmed decision #59/#60/#62) is symbol-scoped,
        # not per-timeframe — the same list is attached to every
        # timeframe's FeatureSet on a given close (see
        # FeatureEngine.get_snapshot()'s own docstring). Read once from
        # whichever timeframe happens to be iterated first rather than
        # duplicated per timeframe below; all of them carry an identical
        # snapshot as of this same read, since _maybe_refresh_daily_levels
        # runs once per (symbol, ET day) before any of a tick's
        # timeframes are computed.
        if not daily_levels and tf_data.get("daily_levels"):
            daily_levels = tf_data["daily_levels"]

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

    if daily_levels_lookback_days is not None:
        # Re-cluster from cached raw candles at the caller's chosen
        # lookback (decision #62) — overrides whatever the loop above
        # picked up from the pre-computed default snapshot. Cheap: no new
        # provider fetch, see get_daily_levels()'s own docstring.
        daily_levels = [level.model_dump() for level in get_feature_engine().get_daily_levels(symbol, daily_levels_lookback_days)]

    # Daily Levels x Level Interaction (Stage 3, confirmed decision #64) —
    # closes the gap decision #61 explicitly left open ("No level_interaction
    # data attached to these yet — Stage 3 isn't built"). Deliberately
    # AFTER the lookback override above, not before: it must apply to
    # whichever `daily_levels` list is actually being returned, not just
    # the pre-computed default that a custom lookback request replaces.
    # Unlike the `units` loop earlier, `daily_levels` itself stays a flat,
    # symbol-scoped list (decision #61's own deliberate shape — not
    # nested per timeframe), so interaction state — which genuinely DOES
    # differ per timeframe, same as every other level type — is attached
    # as a `{timeframe: {...}}` dict on each entry rather than picking
    # just one timeframe to represent all of them. A level with no
    # interaction history yet for any timeframe (nothing published
    # through it so far, or an ad-hoc preview level from a custom
    # lookback, which never gets a REAL, persisted level_id and so can
    # never match anything here) simply gets an empty dict — same "empty
    # means not-yet, not zero" convention as everywhere else in this route.
    for level in daily_levels:
        level["level_interaction"] = {
            tf: tf_levels[level["level_id"]]
            for tf, tf_levels in level_snapshot.get(symbol, {}).items()
            if level["level_id"] in tf_levels
        }

    return {"symbol": symbol, "timeframes": timeframes, "daily_levels": daily_levels}


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
