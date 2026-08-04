from datetime import datetime, timedelta, timezone

import pytest

from app.broker_adapters.base import HistoricalDataUnavailableError, MarketDataProvider
from app.broker_adapters.finnhub_provider import FinnhubAdapter


def test_missing_api_key_raises_immediately():
    with pytest.raises(ValueError):
        FinnhubAdapter(api_key=None)


def test_adapter_satisfies_market_data_provider_interface():
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    assert isinstance(adapter, MarketDataProvider)
    assert adapter.is_connected() is False


@pytest.mark.asyncio
async def test_get_historical_raises_historical_data_unavailable():
    """
    The whole point of confirmed decision #32: Finnhub's free tier can't
    serve historical stock candles at all (confirmed 403, not assumed),
    so get_historical() must fail loudly and specifically — not return
    empty data, not silently succeed with nothing.
    """
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    with pytest.raises(HistoricalDataUnavailableError) as exc_info:
        await adapter.get_historical(
            "AAPL", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
        )
    assert exc_info.value.provider == "Finnhub"


def test_handle_message_ignores_ping():
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    adapter._handle_message({"type": "ping"})
    assert received == []


def test_handle_message_ignores_unknown_type():
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    adapter._handle_message({"type": "news", "data": []})
    assert received == []


def test_handle_message_parses_single_trade():
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    adapter._handle_message(
        {"type": "trade", "data": [{"s": "AAPL", "p": 234.56, "t": 1735689600000, "v": 100, "c": []}]}
    )
    assert len(received) == 1
    assert received[0].symbol == "AAPL"
    assert received[0].price == 234.56
    assert received[0].size == 100


def test_handle_message_parses_multiple_trades_in_one_message():
    """Finnhub docs are explicit: "A message can contain multiple
    trades" — worth its own test since it's easy to only handle the
    single-trade case and silently drop the rest of the batch."""
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    adapter._handle_message({
        "type": "trade",
        "data": [
            {"s": "AAPL", "p": 234.56, "t": 1735689600000, "v": 100, "c": []},
            {"s": "MSFT", "p": 410.0, "t": 1735689600500, "v": 50, "c": []},
        ],
    })
    assert len(received) == 2
    assert {t.symbol for t in received} == {"AAPL", "MSFT"}


def test_handle_message_skips_malformed_trade_without_crashing():
    """Forex/crypto trades sometimes arrive with volume=0 per Finnhub's
    docs, which is fine — but a trade missing symbol/price/timestamp
    entirely should be skipped, not crash the whole batch."""
    adapter = FinnhubAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    adapter._handle_message({
        "type": "trade",
        "data": [
            {"s": "AAPL"},  # missing price/timestamp — malformed
            {"s": "MSFT", "p": 410.0, "t": 1735689600500, "v": 0, "c": []},  # valid, zero volume
        ],
    })
    assert len(received) == 1
    assert received[0].symbol == "MSFT"
    assert received[0].size == 0
