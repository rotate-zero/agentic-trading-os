"""
Tests for candle_aggregator._bucket_and_aggregate() and
aggregate_from_recorded(). No DB involved — aggregate_from_recorded's own
candle_store.get_recorded_candles call is monkeypatched, same "Postgres-down
still testable" split test_market_routes.py/test_candle_recorder.py already
use elsewhere in this suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.schemas.events.market_data import CandleClosed as Candle
from app.services import candle_aggregator, candle_store

_ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def _one_min(ts: datetime, o: float, h: float, l: float, c: float, v: int) -> Candle:  # noqa: E741 — l=low reads clearly here
    return Candle(timeframe="1m", open=o, high=h, low=l, close=c, volume=v, candle_ts=ts)


def test_ohlcv_uses_full_range_not_just_first_and_last_minute():
    """The actual correction from the design discussion: high/low/volume
    must come from EVERY member, not just the first and last minute."""
    candles = [
        _one_min(_et(2026, 8, 11, 9, 30), o=100, h=100.5, l=99.5, c=100.2, v=1000),
        _one_min(_et(2026, 8, 11, 9, 31), o=100.2, h=105.0, l=100.0, c=101.0, v=2000),  # the spike
        _one_min(_et(2026, 8, 11, 9, 32), o=101.0, h=101.2, l=98.0, c=100.8, v=1500),  # the dip
        _one_min(_et(2026, 8, 11, 9, 33), o=100.8, h=101.0, l=100.5, c=100.9, v=500),
        _one_min(_et(2026, 8, 11, 9, 34), o=100.9, h=101.1, l=100.7, c=101.05, v=800),
    ]
    result = candle_aggregator._bucket_and_aggregate(candles, 5)
    assert len(result) == 1
    bar = result[0]
    assert bar.open == 100  # first member's open
    assert bar.close == 101.05  # last member's close
    assert bar.high == 105.0  # the spike, from the MIDDLE candle — not just first/last
    assert bar.low == 98.0  # the dip, from the MIDDLE candle
    assert bar.volume == 1000 + 2000 + 1500 + 500 + 800


def test_buckets_are_market_open_anchored_not_midnight_anchored():
    """5m buckets during regular session must land on 09:30, 09:35, ...
    not on wall-clock 09:00-anchored boundaries."""
    candles = [
        _one_min(_et(2026, 8, 11, 9, 30), 10, 10, 10, 10, 100),
        _one_min(_et(2026, 8, 11, 9, 34), 10, 10, 10, 10, 100),
        _one_min(_et(2026, 8, 11, 9, 35), 10, 10, 10, 10, 100),
    ]
    result = candle_aggregator._bucket_and_aggregate(candles, 5)
    assert len(result) == 2
    assert (result[0].candle_ts.hour, result[0].candle_ts.minute) == (9, 30)
    assert (result[1].candle_ts.hour, result[1].candle_ts.minute) == (9, 35)


def test_regular_session_1h_bucket_has_short_stub_at_close():
    """6.5h regular session / 1h -> six full hours + a 30-minute stub
    (15:30-16:00), anchored at market open, per the confirmed design."""
    candles = [_one_min(_et(2026, 8, 11, 15, 45), 10, 10, 10, 10, 100)]
    result = candle_aggregator._bucket_and_aggregate(candles, 60)
    assert len(result) == 1
    assert (result[0].candle_ts.hour, result[0].candle_ts.minute) == (15, 30)


def test_bar_never_straddles_a_session_boundary():
    """The core reason session-local (not continuous-clock) aggregation
    was requested: a candle at 15:58 (regular) and one at 16:02
    (after-hours) must NOT be folded into the same 1h bucket, even though
    naive clock-aligned bucketing would put both in a "16:00" bucket."""
    candles = [
        _one_min(_et(2026, 8, 11, 15, 58), 10, 10, 10, 10, 100),  # regular session
        _one_min(_et(2026, 8, 11, 16, 2), 20, 20, 20, 20, 200),  # after-hours
    ]
    result = candle_aggregator._bucket_and_aggregate(candles, 60)
    assert len(result) == 2
    starts = sorted((r.candle_ts.hour, r.candle_ts.minute) for r in result)
    assert starts == [(15, 30), (16, 0)]  # regular's stub bucket, and after-hours' own first bucket


def test_after_hours_1h_buckets_are_clean_no_stub():
    """After-hours (4h) is the only one of the three sessions that divides
    evenly by 1h — confirming that, not just asserting it."""
    candles = [_one_min(_et(2026, 8, 11, hh, 15), 10, 10, 10, 10, 100) for hh in (16, 17, 18, 19)]
    result = candle_aggregator._bucket_and_aggregate(candles, 60)
    assert len(result) == 4
    assert [(r.candle_ts.hour, r.candle_ts.minute) for r in result] == [(16, 0), (17, 0), (18, 0), (19, 0)]


def test_15m_and_1h_are_consistent_whether_built_from_1m_directly_or_hierarchically():
    """Building 1h via the 1m->5m->15m->1h chain (what aggregate_from_recorded
    actually does) must give the identical bar to aggregating straight from
    1m at width=60 — proving the hierarchy doesn't silently change results."""
    base = _et(2026, 8, 11, 9, 30)
    candles = [
        _one_min(base + timedelta(minutes=i), 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i + 0.1, 100 * (i + 1))
        for i in range(60)  # 09:30 through 10:29 — a full hour
    ]
    direct = candle_aggregator._bucket_and_aggregate(candles, 60)
    five = candle_aggregator._bucket_and_aggregate(candles, 5)
    fifteen = candle_aggregator._bucket_and_aggregate(five, 15)
    hierarchical = candle_aggregator._bucket_and_aggregate(fifteen, 60)
    assert direct == hierarchical


def test_trailing_bucket_is_included_even_if_partial():
    """A bucket that hasn't fully elapsed yet (only 2 of 5 minutes recorded)
    is still returned — same as the "current candle" on any live chart."""
    candles = [
        _one_min(_et(2026, 8, 11, 9, 30), 10, 10, 10, 10, 100),
        _one_min(_et(2026, 8, 11, 9, 31), 10, 10, 10, 10, 100),
    ]
    result = candle_aggregator._bucket_and_aggregate(candles, 5)
    assert len(result) == 1
    assert result[0].volume == 200


def test_aggregate_from_recorded_returns_empty_not_error_when_nothing_recorded(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(candle_store, "get_recorded_candles", lambda *a, **k: [])
    result = candle_aggregator.aggregate_from_recorded(
        "ZZNOHIST", "5m", datetime.now(_ET) - timedelta(hours=1), datetime.now(_ET)
    )
    assert result == []


def test_aggregate_from_recorded_rejects_non_aggregatable_timeframe():
    with pytest.raises(ValueError):
        candle_aggregator.aggregate_from_recorded("NVDA", "1d", datetime.now(_ET), datetime.now(_ET))


def test_aggregate_from_recorded_builds_1h_end_to_end(monkeypatch: pytest.MonkeyPatch):
    base = _et(2026, 8, 11, 9, 30)
    candles = [
        _one_min(base + timedelta(minutes=i), 100 + i, 100 + i + 1, 100 + i - 1, 100 + i, 10)
        for i in range(60)
    ]
    monkeypatch.setattr(candle_store, "get_recorded_candles", lambda *a, **k: candles)
    result = candle_aggregator.aggregate_from_recorded(
        "ZZTEST", "1h", datetime.now(_ET) - timedelta(hours=2), datetime.now(_ET)
    )
    assert len(result) == 1
    assert result[0].timeframe == "1h"
    assert result[0].open == 100
    assert result[0].close == 159
    assert result[0].volume == 600
