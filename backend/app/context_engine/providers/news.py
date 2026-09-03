"""
NewsFlagProvider — decision #90's provider, decision #96's build.
Presence/count/recency for a symbol's recent headlines, deliberately not
sentiment scoring (§5; NLP sentiment stays deferred, `future-ideas.md`
#13). Nothing behind this is ever persisted (§5) — computed fresh from a
short rolling fetch on every evaluate(), discarded immediately after.

**ETF exclusion (decision #94, load-bearing now, not hypothetical):**
`/company-news` for an ETF ticker (SPY/QQQ/IWM) returns generic
broad-market news mislabeled as related, not fund-specific — confirmed
by M0's spike. SPY is already in the seeded Scanner Universe
(`app/scanner/universe.py`'s `TEST_UNIVERSE`), so this guard is checked
on every real evaluate() call, not just a documented future concern.
Guarded symbols get `present: False` and nulled-out fields unconditionally
— never even makes the API call — rather than calling it and discarding
misleading results, which would burn rate-limit budget for nothing.

**`importance`'s keyword list (below) is a first-pass heuristic, flagged
as exactly that** — §5 is explicit this is meant to be "a keyword/volume
heuristic, explicitly not language understanding," so there's no
"correct" list to look up, only a reasonable starting set worth
iterating on with real data. Not independently re-verified against a
live `/company-news` response for exact field names (`headline`,
`datetime`, `related`) — this session has no live Finnhub key; built
from Finnhub's documented schema, same caveat as fundamentals_refresh.py
carries for its own three unverified fields.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import finnhub

from app.context_engine.provider import SymbolContextProvider
from app.core.config import get_settings

ETF_SYMBOLS = {"SPY", "QQQ", "IWM"}  # decision #94 — small hardcoded set, same posture as MarketClock's own holiday set

_FETCH_WINDOW = timedelta(hours=24)  # broad enough for a meaningful recency_seconds without over-fetching
_COUNT_WINDOW = timedelta(minutes=15)  # matches count_15m's own name and the doc's example output

# First-pass keyword heuristic (module docstring) — lowercase, matched as
# a substring against the headline. Grouped loosely by how likely a term
# is to actually move the stock, not a rigorous taxonomy.
_HIGH_IMPACT_KEYWORDS = {
    "earnings", "guidance", "downgrade", "upgrade", "lawsuit", "investigation",
    "recall", "bankruptcy", "merger", "acquisition", "acquires", "fda",
    "sec probe", "resigns", "resignation", "buyback", "halted", "halt",
    "delisted", "restatement", "fraud", "recession",
}

_EMPTY_RESULT = {"present": False, "count_15m": 0, "recency_seconds": None, "importance": "none"}


class NewsFlagProvider(SymbolContextProvider):
    name = "news"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_settings().finnhub_api_key

    async def evaluate(self, symbol: str) -> dict:
        if symbol in ETF_SYMBOLS:
            return dict(_EMPTY_RESULT)
        if not self._api_key:
            return dict(_EMPTY_RESULT)
        articles = await asyncio.to_thread(self._fetch, symbol)
        return self._summarize(articles)

    def _fetch(self, symbol: str) -> list[dict]:
        client = finnhub.Client(api_key=self._api_key)
        today = date.today()
        window_start = today - timedelta(days=1)  # company_news takes date-granularity from/to, not datetime
        try:
            return client.company_news(symbol=symbol, _from=window_start.isoformat(), to=today.isoformat()) or []
        except finnhub.exceptions.FinnhubAPIException:
            return []  # soft-fail — a missed news check is not worth crashing Context Engine over

    def _summarize(self, articles: list[dict]) -> dict:
        now = datetime.now(timezone.utc)
        cutoff = now - _FETCH_WINDOW

        recent: list[dict] = []
        for article in articles:
            dt_val = article.get("datetime")
            if not isinstance(dt_val, (int, float)):
                continue
            published = datetime.fromtimestamp(dt_val, tz=timezone.utc)
            if published >= cutoff:
                recent.append({"published": published, "headline": (article.get("headline") or "").lower()})

        if not recent:
            return dict(_EMPTY_RESULT)

        recent.sort(key=lambda a: a["published"], reverse=True)
        most_recent = recent[0]["published"]
        recency_seconds = int((now - most_recent).total_seconds())
        count_15m = sum(1 for a in recent if a["published"] >= now - _COUNT_WINDOW)

        has_keyword = any(
            keyword in a["headline"] for a in recent if a["published"] >= now - _COUNT_WINDOW for keyword in _HIGH_IMPACT_KEYWORDS
        )
        if has_keyword or count_15m >= 3:
            importance = "high"
        elif count_15m >= 1:
            importance = "medium"
        else:
            importance = "low"  # present in the broader 24h window, nothing in the last 15 minutes

        return {
            "present": True,
            "count_15m": count_15m,
            "recency_seconds": recency_seconds,
            "importance": importance,
        }
