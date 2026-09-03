"""
NewsFlagProvider tests. `_summarize()` is pure (takes a plain article
list, no I/O) so the scoring logic is tested directly without a live
Finnhub key — same posture as market_state_engine's scoring.py tests.
The ETF guard and the "no API key configured" soft-fail are tested via
evaluate() itself, confirming _fetch() is never even attempted.
"""
from __future__ import annotations

import time

from app.context_engine.providers.news import ETF_SYMBOLS, NewsFlagProvider


def _article(minutes_ago: float, headline: str = "Some routine update") -> dict:
    return {"datetime": time.time() - minutes_ago * 60, "headline": headline}


def test_no_articles_returns_empty_result():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([])
    assert result == {"present": False, "count_15m": 0, "recency_seconds": None, "importance": "none"}


def test_present_true_with_article_outside_15m_window_is_low_importance():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([_article(minutes_ago=60)])
    assert result["present"] is True
    assert result["count_15m"] == 0
    assert result["recency_seconds"] == 3600
    assert result["importance"] == "low"


def test_one_article_within_15m_is_medium_importance():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([_article(minutes_ago=5)])
    assert result["count_15m"] == 1
    assert result["importance"] == "medium"


def test_three_articles_within_15m_is_high_importance_by_volume():
    provider = NewsFlagProvider(api_key="unused")
    articles = [_article(minutes_ago=1), _article(minutes_ago=5), _article(minutes_ago=10)]
    result = provider._summarize(articles)
    assert result["count_15m"] == 3
    assert result["importance"] == "high"


def test_keyword_triggers_high_importance_even_with_one_article():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([_article(minutes_ago=2, headline="Company issues earnings guidance cut")])
    assert result["count_15m"] == 1
    assert result["importance"] == "high"


def test_recency_seconds_uses_most_recent_article():
    provider = NewsFlagProvider(api_key="unused")
    articles = [_article(minutes_ago=120), _article(minutes_ago=10), _article(minutes_ago=200)]
    result = provider._summarize(articles)
    assert result["recency_seconds"] == 600  # the 10-minutes-ago one


def test_articles_outside_24h_fetch_window_are_ignored():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([_article(minutes_ago=25 * 60)])  # 25 hours ago
    assert result == {"present": False, "count_15m": 0, "recency_seconds": None, "importance": "none"}


def test_malformed_datetime_field_is_skipped_not_crashed_on():
    provider = NewsFlagProvider(api_key="unused")
    result = provider._summarize([{"datetime": "not-a-number", "headline": "x"}, _article(minutes_ago=1)])
    assert result["present"] is True
    assert result["count_15m"] == 1  # only the well-formed article counted


async def test_evaluate_never_fetches_for_etf_symbols():
    provider = NewsFlagProvider(api_key="fake-key-would-fail-if-actually-used")

    def _boom(symbol):
        raise AssertionError(f"_fetch should never be called for an ETF symbol, got {symbol}")

    provider._fetch = _boom  # type: ignore[method-assign]

    for etf in ETF_SYMBOLS:
        result = await provider.evaluate(etf)
        assert result["present"] is False


async def test_evaluate_soft_fails_with_no_api_key_configured():
    provider = NewsFlagProvider(api_key=None)
    result = await provider.evaluate("AAPL")
    assert result["present"] is False
