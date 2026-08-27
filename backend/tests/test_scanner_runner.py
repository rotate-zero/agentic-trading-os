"""
run_scan unit tests — a fake FeatureEngine (no DB, no real snapshot
machinery), verifying the orchestration logic on top of the already-
tested score_symbol (test_scanner.py). GET /scanner/state's own
correctness is downstream of this — not re-tested separately here, since
the route is a thin pass-through (query param parsing + this call +
JSON shaping), nothing route-specific to get wrong beyond what manual
verification already checked.
"""
from __future__ import annotations

from unittest.mock import patch

import app.scanner.runner as runner_module
from app.scanner.runner import run_scan


class _FakeFeatureEngine:
    def __init__(self, snapshots: dict[str, dict]) -> None:
        self._snapshots = snapshots

    def get_snapshot(self, symbol: str | None = None) -> dict:
        if symbol is None:
            return self._snapshots
        return {symbol: self._snapshots[symbol]} if symbol in self._snapshots else {}


def _tf_row(features: dict[str, float], close: float = 100.0) -> dict:
    return {"candle_ts": "2026-08-27T14:30:00+00:00", "close": close, "features": features, "daily_levels": []}


def test_run_scan_ranks_descending_and_skips_symbols_with_no_snapshot():
    fake = _FakeFeatureEngine(
        {
            "AAPL": {"1m": _tf_row({"rvol": 2.3, "gap_pct": 1.8, "session_pct_change": -0.9, "atr_14_pct": 2.1})},
            "TSLA": {"1m": _tf_row({"rvol": 5.1})},
        }
    )
    with patch.object(runner_module, "get_feature_engine", return_value=fake):
        results, skipped = run_scan(["AAPL", "TSLA", "SPY"], weight_rvol=1.0, weight_gap=1.0, weight_session_change=1.0)

    assert skipped == ["SPY"]  # never streamed — no entry in the fake snapshot at all
    assert [r.symbol for r in results] == ["TSLA", "AAPL"]  # TSLA's rvol=5.1 alone beats AAPL's ~3.59 combined
    assert results[1].inputs_available == 3
    assert results[0].inputs_available == 1


def test_run_scan_display_features_exclude_non_scan_keys():
    fake = _FakeFeatureEngine({"AAPL": {"1m": _tf_row({"rvol": 1.5, "sma_9": 230.1, "ema_20": 229.0})}})
    with patch.object(runner_module, "get_feature_engine", return_value=fake):
        results, skipped = run_scan(["AAPL"], weight_rvol=1.0, weight_gap=1.0, weight_session_change=1.0)

    assert skipped == []
    assert results[0].features == {"rvol": 1.5}  # sma_9/ema_20 not part of this scan — filtered out of the display payload


def test_run_scan_symbol_with_no_1m_row_at_all_is_skipped_not_zero_scored():
    fake = _FakeFeatureEngine({"AAPL": {}})  # symbol exists but no "1m" timeframe computed yet
    with patch.object(runner_module, "get_feature_engine", return_value=fake):
        results, skipped = run_scan(["AAPL"], weight_rvol=1.0, weight_gap=1.0, weight_session_change=1.0)

    assert results == []
    assert skipped == ["AAPL"]
