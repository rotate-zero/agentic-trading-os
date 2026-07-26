from datetime import datetime, timedelta, timezone

import pytest

from app.broker_adapters.base import BrokerAdapter
from app.broker_adapters.ibkr_adapter import IBKRAdapter, _bar_size_for, _duration_str


def test_duration_str_under_a_day_uses_seconds():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    assert _duration_str(start, end) == "7200 S"


def test_duration_str_over_a_day_uses_days():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=3)
    assert _duration_str(start, end) == "3 D"


def test_bar_size_for_known_timeframes():
    assert _bar_size_for("1m") == "1 min"
    assert _bar_size_for("1d") == "1 day"


def test_bar_size_for_unknown_timeframe_raises():
    with pytest.raises(ValueError):
        _bar_size_for("not-a-real-timeframe")


def test_ibkr_adapter_satisfies_broker_adapter_interface():
    """
    Construction only — this deliberately never calls connect(), since
    that requires a real running IB Gateway this sandbox doesn't have.
    What IS verified: the class actually implements every abstract method
    (isinstance would fail at construction time otherwise, since Python
    refuses to instantiate an ABC with unimplemented abstract methods),
    and none of the constructor path touches the network.
    """
    adapter = IBKRAdapter(host="127.0.0.1", port=4002, client_id=99)
    assert isinstance(adapter, BrokerAdapter)
    assert adapter.is_connected() is False
