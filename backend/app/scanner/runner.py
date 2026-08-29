"""
run_scan — orchestration-lite for the on-demand GET /scanner/state route.
docs/architecture/scanner-design.md §5 describes a continuously-running
MarketActivityScanner keyed to ScanCadenceSchedule and wired to
LiveTickRelay — none of that exists yet (still open, §10). This is
deliberately smaller: score whatever Feature Engine has computed RIGHT
NOW for a given universe, on demand, triggered by a frontend request
rather than a background schedule. Every line of scoring logic this
calls (score_symbol, from scorer.py) is the same function the eventual
continuous orchestrator would also use — this isn't throwaway, it's the
one piece of §5 that doesn't depend on resolving the cadence/promotion
questions first.

Runs IN-PROCESS (this route lives in the same Python process as
FeatureEngine), so unlike scripts/test_scanner_pipeline.py — an external
HTTP client, deliberately kept that way per that script's own docstring
— this reads FeatureEngine.get_snapshot() directly. No need to reverse
GET /intelligence/state's per-unit reshaping the way that script does;
that reshaping exists for that route's own display purposes, not as a
second source of truth to route around.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.feature_engine.engine import get_feature_engine
from app.scanner.scorer import score_symbol
from app.schemas.events.features import FeatureSet

_SUPPORTED_TIMEFRAME = "1m"  # only timeframe rvol/gap/session-change/ATR are ever computed under (engine.py's SUPPORTED_TIMEFRAME)

# The 4 keys this v1 scan actually uses (scorer.py) — kept alongside the
# score itself in ScanResult so a caller (the frontend) can show WHY a
# symbol scored the way it did, not just a bare number. Honest-state: a
# key genuinely absent from a symbol's FeatureSet (cold start) is simply
# missing from `features` below, never backfilled with a guess.
_DISPLAY_KEYS = ["rvol", "premarket_volume_ratio", "gap_pct", "session_pct_change", "atr_14_pct"]


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    score: float
    inputs_available: int
    features: dict[str, float]  # whichever of _DISPLAY_KEYS this symbol actually has right now


def run_scan(
    universe: list[str],
    *,
    weight_rvol: float,
    weight_gap: float,
    weight_session_change: float,
    weight_premarket_volume_ratio: float = 0.0,
) -> tuple[list[ScanResult], list[str]]:
    """Returns (ranked results descending by score, symbols skipped this
    cycle). A symbol with no 1m FeatureSet computed yet at all (never
    streamed, or streaming but not one full candle closed yet) is
    skipped entirely — not scored as 0.0, same convention score_symbol
    itself already applies one level down for a partially-missing
    FeatureSet."""
    engine = get_feature_engine()
    results: list[ScanResult] = []
    skipped: list[str] = []

    for symbol in universe:
        snapshot = engine.get_snapshot(symbol)
        tf_data = snapshot.get(symbol, {}).get(_SUPPORTED_TIMEFRAME)
        if tf_data is None:
            skipped.append(symbol)
            continue

        feature_set = FeatureSet(
            timeframe=_SUPPORTED_TIMEFRAME,
            candle_ts=tf_data["candle_ts"],
            close=tf_data["close"],
            features=tf_data["features"],
        )
        activity = score_symbol(
            symbol,
            feature_set,
            weight_rvol=weight_rvol,
            weight_gap=weight_gap,
            weight_session_change=weight_session_change,
            weight_premarket_volume_ratio=weight_premarket_volume_ratio,
        )
        display_features = {k: v for k, v in tf_data["features"].items() if k in _DISPLAY_KEYS}
        results.append(
            ScanResult(
                symbol=symbol,
                score=activity.score,
                inputs_available=activity.inputs_available,
                features=display_features,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results, skipped
