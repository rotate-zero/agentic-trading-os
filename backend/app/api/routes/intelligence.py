"""
GET /intelligence/state — confirmed decision #47. Backs the Feature
Engine panel (frontend/src/components/intelligence/FeatureEnginePanel.tsx):
one merged read of FeatureEngine.get_snapshot() and
LevelInteractionEngine.get_snapshot(), shaped into the
Symbol -> Timeframe -> Unit -> Period -> Variables hierarchy discussed and
confirmed before any of this was built, so the frontend doesn't need to
stitch two responses together itself.

A Period node gained an optional `slope` sub-object in decision #85 —
`{slope, r2, slope_pct?, slope_angle?}` — for SMA/EMA entries once
sma_slope()/ema_slope() (decision #83) have warmed up. This is a fix to
a gap in #83's own delivery, not a new feature: those four values were
always being computed and published on `FeatureSet.features`, they just
weren't being GROUPED under their owning period here (see
`_parse_slope_key`'s own docstring) — each rendered as its own
standalone, confusingly-labeled unit instead. The `slope` sub-object
never carries its own `level_interaction` — those four keys are excluded
from Level Interaction tracking entirely (same decision,
`LevelInteractionEngine._process_one`), since none of them is a price.

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

from app.context_engine.engine import get_context_engine
from app.core.config import get_settings
from app.feature_engine.engine import get_feature_engine
from app.feature_engine.historical import compute_series
from app.market_state_engine.engine import get_market_state_engine
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


# Confirmed decision #85 (a fix to decision #83's own delivery gap, not a
# new feature) — the four keys sma_slope()/ema_slope()
# (indicators/sma.py, indicators/ema.py) publish alongside sma_{period}/
# ema_{period} itself: `_slope` and `_r2` (always), `_slope_pct` and
# `_slope_angle` (only once the current SMA/EMA value is nonzero — see
# sma_slope()'s own docstring). None of the four ends in a bare numeric
# suffix, so `_parse_level_key`'s digit-suffix rule never grouped them —
# each fell through to the flat "no period" path and rendered as its own
# standalone accordion row, literally labeled e.g. "sma_9_slope_angle",
# instead of nesting under the SMA-9 entry the way `sma_9` itself does.
#
# `_SMA_EMA_SLOPE_SUFFIXES` is checked with `str.endswith`, an exact
# suffix match — "_slope" is never a suffix of "..._slope_pct" or
# "..._slope_angle" (those end in "_pct"/"_angle"), so there's no
# ordering dependency between the four entries despite "slope" reading
# like a prefix of the other two.
_SMA_EMA_SLOPE_SUFFIXES = ("_slope", "_r2", "_slope_pct", "_slope_angle")


def _parse_slope_key(level_key: str) -> tuple[str, str, str] | None:
    """
    "sma_9_slope_angle" -> ("sma", "9", "slope_angle"); "ema_20_r2" ->
    ("ema", "20", "r2"); "sma_9" itself, Camarilla's own keys, and
    Regression/KAMA's own slope-shaped keys (`regression_9_slope`,
    `kama_9_slope`, ...) -> None.

    Deliberately its own function, not folded into `_parse_level_key`'s
    generic digit-suffix rule — that rule groups a key under (unit,
    period) using the value itself as the node; these four keys instead
    need to attach as SUB-values on the period node `sma_{period}`/
    `ema_{period}` already owns, which needs the caller to know which of
    the four slope fields a given key is, not just its unit/period.

    Scoped to `sma_`/`ema_` only, on direct instruction (decision #85).
    Regression/KAMA (decision #67) publish an analogous slope/r2/dist
    family with the identical grouping gap — flagged here, not fixed,
    same "don't make it worse, don't fix it either" boundary decision
    #85 draws around Camarilla's own already-flagged (decision #56),
    already-fixed (decision #66) quirk.
    """
    for prefix, unit in (("sma_", "sma"), ("ema_", "ema")):
        if not level_key.startswith(prefix):
            continue
        for suffix in _SMA_EMA_SLOPE_SUFFIXES:
            if level_key.endswith(suffix):
                period = level_key[len(prefix):-len(suffix)]
                if period.isdigit():
                    return unit, period, suffix.lstrip("_")
    return None


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

        # Two passes over the same dict, not one — deliberately, so a
        # slope-family key (decision #85) can never race the base
        # sma_{period}/ema_{period} key it attaches to, regardless of
        # which order `tf_data["features"]` happens to iterate in.
        # `raw_units[unit][period]` accumulates via setdefault rather
        # than being assigned wholesale, so a slope key seen before its
        # base key (or vice versa) can't clobber the other's fields —
        # the OLD code's `units.setdefault(unit, {})[period] = node`
        # was a full replace, which would have silently dropped whichever
        # of the two lost the race once slope keys started sharing a
        # period with their base key.
        raw_units: dict[str, dict[str | None, dict[str, Any]]] = {}

        for level_key, value in tf_data["features"].items():
            slope_component = _parse_slope_key(level_key)
            if slope_component is not None:
                unit, period, slope_field = slope_component
                node = raw_units.setdefault(unit, {}).setdefault(period, {"candle_ts": tf_data["candle_ts"]})
                # No `level_interaction` lookup here, on purpose: these
                # four keys are excluded from Level Interaction tracking
                # entirely as of this same decision (see
                # LevelInteractionEngine._process_one) — they're never in
                # `level_data_for_tf` to begin with, but the omission is
                # deliberate either way, not an oversight.
                node.setdefault("slope", {})[slope_field] = value
                continue

            unit, period = _parse_level_key(level_key)
            node = raw_units.setdefault(unit, {}).setdefault(period, {"candle_ts": tf_data["candle_ts"]})
            node["value"] = value
            node["candle_ts"] = tf_data["candle_ts"]
            level_interaction = level_data_for_tf.get(level_key)
            if level_interaction is not None:
                node["level_interaction"] = level_interaction

        for unit, periods in raw_units.items():
            if list(periods.keys()) == [None]:
                # No numeric period (e.g. "vwap" or "pdh") — the unit
                # bucket IS the node, no UnitValue level beneath it.
                units[unit] = periods[None]
            else:
                units[unit] = {period: node for period, node in periods.items() if period is not None}

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


@router.get("/market-state")
async def get_market_state_snapshot(symbol: str | None = Query(None)) -> dict[str, Any]:
    """
    Confirmed decision #98, M4 task 25 — live observability into
    MarketStateEngine, the same "point-in-time read, no need to
    replay MarketStateChanged history" purpose GET /state already
    serves for Feature Engine/Level Interaction Engine.

    A thin passthrough of `MarketStateEngine.get_snapshot()` — see that
    method's own docstring for the full shape. `symbol` is optional
    here (unlike GET /state above, which requires one): this route is
    also meant for "what does the engine currently know about
    anything," not only a single symbol's dashboard panel.
    """
    return get_market_state_engine().get_snapshot(symbol)


@router.get("/context")
async def get_context_snapshot(symbol: str | None = Query(None)) -> dict[str, Any]:
    """
    Confirmed decision #98, M4 task 25 — live observability into
    ContextEngine, same purpose as GET /market-state above, kept as a
    separate route rather than merged into one payload: Market State
    and Context are two independently-owned engines (system-design.md
    §4.8), and this route's whole job is to expose each one's own
    current belief honestly, not to pre-assemble a Strategy-shaped
    composite of the two — that composition is a future consumer's job
    (`app/trading_intelligence/state_snapshot.py`), not this route's.

    A thin passthrough of `ContextEngine.get_snapshot()` — see that
    method's own docstring for the full shape, including why
    `evaluated_at` is wall-clock rather than a domain-safe timestamp.
    """
    return get_context_engine().get_snapshot(symbol)


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
