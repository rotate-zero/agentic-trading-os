from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from polygon.exceptions import AuthError, BadResponse

from app.broker_adapters.base import HistoricalDataUnavailableError, MarketDataProvider, SymbolNotFoundError
from app.broker_adapters.polygon_provider import PolygonAdapter, _is_plan_limitation, _polygon_params_for


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


# The exact real response body captured from a live account hitting
# minute-level aggregates on the free/Basic tier — not a guessed shape.
_REAL_NOT_AUTHORIZED_BODY = (
    '{"status":"NOT_AUTHORIZED","request_id":"f73a9e39364524f66ccb08499ea385b3",'
    '"message":"Your plan doesn\'t include this data timeframe. '
    'Please upgrade your plan at https://polygon.io/pricing"}'
)


def test_is_plan_limitation_detects_the_real_not_authorized_response():
    exc = BadResponse(_REAL_NOT_AUTHORIZED_BODY)
    assert _is_plan_limitation(exc) is True


def test_is_plan_limitation_false_for_other_bad_responses():
    assert _is_plan_limitation(BadResponse('{"status":"ERROR","message":"something else"}')) is False


def test_is_plan_limitation_false_for_unparseable_body():
    # get_aggs's actual behavior on a genuinely malformed/non-JSON body —
    # must not itself raise while trying to classify the original error.
    assert _is_plan_limitation(BadResponse("not json at all")) is False


@pytest.mark.asyncio
async def test_get_historical_raises_historical_data_unavailable_for_plan_limitation():
    """
    The actual reported bug: querying timespan="minute" on the free/Basic
    tier returns this exact NOT_AUTHORIZED response (confirmed against a
    real account, not assumed) — it was propagating as a raw 500 with
    Polygon's JSON error text before this fix categorized it properly.
    """
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_bad_response(**kwargs):
        raise BadResponse(_REAL_NOT_AUTHORIZED_BODY)

    adapter._client.get_aggs = raise_bad_response

    with pytest.raises(HistoricalDataUnavailableError) as exc_info:
        await adapter.get_historical(
            "AAPL", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
        )
    assert exc_info.value.provider == "Polygon"
    assert "1m" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_historical_reraises_other_bad_responses_unmodified():
    """A BadResponse that ISN'T the plan-limitation pattern must not be
    silently swallowed or mis-categorized — it should reach
    UnhandledExceptionMiddleware (confirmed decision #37) as a genuine
    unexpected error, same as before this fix existed."""
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_other_bad_response(**kwargs):
        raise BadResponse('{"status":"SOMETHING_ELSE","message":"a different problem"}')

    adapter._client.get_aggs = raise_other_bad_response

    with pytest.raises(BadResponse):
        await adapter.get_historical(
            "AAPL", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_get_historical_reraises_auth_error_unmodified():
    """A real auth failure (bad/expired key) is a different problem with
    a different fix than a plan limitation — must not look the same to
    whoever's debugging it."""
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_auth_error(**kwargs):
        raise AuthError("invalid API key")

    adapter._client.get_aggs = raise_auth_error

    with pytest.raises(AuthError):
        await adapter.get_historical(
            "AAPL", "1m", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc)
        )


@pytest.mark.asyncio
async def test_poll_symbol_handles_plan_limitation_without_full_traceback_spam():
    """Same NOT_AUTHORIZED condition, hit via the polling path (used when
    Polygon is the streaming fallback) instead of get_historical() — must
    not raise, and must not log a full traceback every cycle for a
    permanent condition that will never resolve itself."""
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_bad_response(**kwargs):
        raise BadResponse(_REAL_NOT_AUTHORIZED_BODY)

    adapter._client.get_aggs = raise_bad_response
    await adapter._poll_symbol("AAPL")  # should not raise
