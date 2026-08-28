"""
Runs the Core Tier Scanner's v1 ActivityScorer (unusual volume +
volatility-relative move — decided over rvol/gap/session-change scanning
in scanner-design.md §2/§9) against whatever Feature Engine is actually
computing right now, for a small test universe — NOT the real Core-100
(app/scanner/universe.py's TEST_UNIVERSE is a placeholder; see its
docstring). This is deliberately a read against a REAL running server
(same "test against real processes, not mocks" posture
verify_roundtrip.py already uses), not a unit test — the point is to see
what actual live rankings look like, not just that the math runs.

Usage:
    # in one terminal, with live Finnhub ticks flowing for the test
    # universe's symbols (market hours, or at least SOME 1m candles
    # already recorded for them):
    uvicorn app.main:app --reload

    # in another terminal (same venv):
    python scripts/test_scanner_pipeline.py
    python scripts/test_scanner_pipeline.py AAPL TSLA NVDA   # custom universe

Prints a ranked table and exits 0. Doesn't assert anything — there's no
"correct" ranking to check this against yet, that's the whole reason
Saqib wants to look at real output before committing further (§9's
build-now-vs-wait question, and the weight-tuning question in §10).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

# Running this as `python scripts/test_scanner_pipeline.py` (this
# docstring's own documented usage) does NOT put backend/ on sys.path —
# Python adds the script's OWN directory (scripts/), not the invoking
# cwd, so `from app...` below fails with ModuleNotFoundError regardless
# of which directory you ran it from without this. Bug in the original
# delivery of this script — fixed here, not caught before shipping.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.scanner.scorer import ActivityScore, score_symbol
from app.scanner.universe import TEST_UNIVERSE
from app.schemas.events.features import FeatureSet

BASE_URL = "http://127.0.0.1:8000"

# The 4 flat feature keys this scan needs, read back out of
# /intelligence/state's per-unit reshaping (intelligence.py's
# _parse_level_key only splits on a trailing NUMERIC period — none of
# these four qualify, so each survives as a flat top-level unit key
# rather than being grouped under a period). Confirmed against
# intelligence.py directly, not assumed.
_NEEDED_KEYS = ["rvol", "gap_pct", "session_pct_change", "atr_14_pct"]


def _extract_1m_features(state: dict) -> tuple[dict[str, float], str | None, float | None]:
    """Pulls the flat feature values this scan needs back out of
    GET /intelligence/state's reshaped {unit: {...}} response for the
    "1m" timeframe — the only timeframe rvol/gap/session-change/ATR are
    ever computed under. Returns (features, candle_ts, close); features
    is missing whatever hasn't been computed yet for this symbol (cold
    start), same honest-gap meaning as everywhere else in this system —
    never backfilled with a guess.
    """
    tf_data = state.get("timeframes", {}).get("1m")
    if tf_data is None:
        return {}, None, None

    features: dict[str, float] = {}
    candle_ts: str | None = None
    for key in _NEEDED_KEYS:
        node = tf_data["units"].get(key)
        if node is not None:
            features[key] = node["value"]
            candle_ts = node["candle_ts"]  # any of our flat nodes carries the same candle_ts as of this read

    return features, candle_ts, tf_data.get("close")


async def _score_one(client: httpx.AsyncClient, symbol: str) -> ActivityScore | None:
    r = await client.get(f"{BASE_URL}/intelligence/state", params={"symbol": symbol})
    r.raise_for_status()
    features, candle_ts, close = _extract_1m_features(r.json())

    if not features or candle_ts is None or close is None:
        print(f"[{symbol}] no 1m FeatureSet yet (cold start — needs at least one recorded candle)")
        return None

    feature_set = FeatureSet(timeframe="1m", candle_ts=candle_ts, close=close, features=features)
    settings = get_settings()
    return score_symbol(
        symbol,
        feature_set,
        weight_rvol=settings.scanner_weight_rvol,
        weight_gap=settings.scanner_weight_gap,
        weight_session_change=settings.scanner_weight_session_change,
    )


async def main() -> None:
    universe = sys.argv[1:] or TEST_UNIVERSE
    print(f"[scanner test] universe: {universe}\n")

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_score_one(client, symbol) for symbol in universe))

    scored = sorted((r for r in results if r is not None), key=lambda r: r.score, reverse=True)
    skipped = [s for s, r in zip(universe, results) if r is None]

    print(f"{'RANK':<5}{'SYMBOL':<8}{'SCORE':<12}{'INPUTS':<8}")
    for i, r in enumerate(scored, start=1):
        flag = "  (low confidence — few inputs)" if r.inputs_available < 2 else ""
        print(f"{i:<5}{r.symbol:<8}{r.score:<12}{r.inputs_available}/3{flag}")

    if skipped:
        print(f"\nSkipped (no data yet): {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
