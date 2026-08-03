from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.broker_adapters.base import MarketDataProvider, SymbolNotFoundError
from app.broker_adapters.polygon_provider import PolygonAdapter, _polygon_params_for


def _fake_agg(close: float, volume: int, ts_ms: int) -> SimpleNamespace:
    """Stands in for polygon.rest.models.aggs.Agg — same field names
    (open/high/low/close/volume/timestamp), just a plain object so tests
    don't need a real API key or network access to construct one."""
    return SimpleNamespace(open=close, high=close, low=close, close=close, volume=volume, timestamp=ts_ms)


def test_polygon_params_for_known_timeframes():
    assert _polygon_params_for("1m") == (1, "minute")
    assert _polygon_params_for("1d") == (1, "day")


def test_polygon_params_for_unknown_timeframe_raises():
    with pytest.raises(ValueError):
        _polygon_params_for("not-a-real-timeframe")


def test_missing_api_key_raises_immediately():
    with pytest.raises(ValueError):
        PolygonAdapter(api_key=None)


def test_adapter_satisfies_market_data_provider_interface():
    adapter = PolygonAdapter(api_key="test-key-not-real")
    assert isinstance(adapter, MarketDataProvider)
    assert adapter.is_connected() is False


@pytest.mark.asyncio
async def test_get_historical_maps_aggs_to_candles():
    adapter = PolygonAdapter(api_key="test-key-not-real")

    now_ms = int(datetime(2026, 8, 2, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    fake_aggs = [_fake_agg(100.0, 1000, now_ms), _fake_agg(101.5, 1200, now_ms + 60_000)]

    def fake_get_aggs(**kwargs):
        return fake_aggs

    adapter._client.get_aggs = fake_get_aggs

    candles = await adapter.get_historical(
        "NVDA", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
    )

    assert len(candles) == 2
    assert candles[0].close == 100.0
    assert candles[1].close == 101.5
    assert candles[0].timeframe == "1m"


@pytest.mark.asyncio
async def test_get_historical_raises_symbol_not_found_on_empty_result():
    """
    get_aggs returns an empty list for a bad ticker rather than raising —
    same "silently succeeds" trap ib_async's qualifyContractsAsync had
    (confirmed decision #16). Checked explicitly here, same fix pattern.
    """
    adapter = PolygonAdapter(api_key="test-key-not-real")
    adapter._client.get_aggs = lambda **kwargs: []

    with pytest.raises(SymbolNotFoundError):
        await adapter.get_historical(
            "NOTREAL", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_poll_symbol_fires_on_tick_for_new_bar():
    adapter = PolygonAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    ts_ms = int(datetime(2026, 8, 2, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    adapter._client.get_aggs = lambda **kwargs: [_fake_agg(150.0, 500, ts_ms)]

    await adapter._poll_symbol("NVDA")

    assert len(received) == 1
    assert received[0].symbol == "NVDA"
    assert received[0].price == 150.0


@pytest.mark.asyncio
async def test_poll_symbol_does_not_refire_on_same_bar():
    """The whole point of tracking _last_bar_ts — polling every 60s
    against 15-min-delayed data means most polls will see the same bar
    repeatedly; only a genuinely new bar should fire on_tick."""
    adapter = PolygonAdapter(api_key="test-key-not-real")
    received = []
    adapter.on_tick(lambda tick: received.append(tick))

    ts_ms = int(datetime(2026, 8, 2, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    adapter._client.get_aggs = lambda **kwargs: [_fake_agg(150.0, 500, ts_ms)]

    await adapter._poll_symbol("NVDA")
    await adapter._poll_symbol("NVDA")
    await adapter._poll_symbol("NVDA")

    assert len(received) == 1  # not 3 — same bar, no new data


@pytest.mark.asyncio
async def test_poll_symbol_handles_empty_result_without_raising():
    adapter = PolygonAdapter(api_key="test-key-not-real")
    adapter._client.get_aggs = lambda **kwargs: []
    await adapter._poll_symbol("NVDA")  # should not raise


@pytest.mark.asyncio
async def test_poll_symbol_survives_client_exception():
    """A single bad poll (network blip, transient 5xx) must not kill the
    polling loop for every other symbol."""
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_error(**kwargs):
        raise RuntimeError("simulated network error")

    adapter._client.get_aggs = raise_error
    await adapter._poll_symbol("NVDA")  # should not raise
