import pytest

from app.services import broker_registry


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True

    def __repr__(self) -> str:
        return f"_FakeProvider({self.name!r})"


@pytest.fixture(autouse=True)
def _reset():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


@pytest.mark.asyncio
async def test_take_over_streaming_sets_the_provider():
    provider = _FakeProvider("a")
    await broker_registry.take_over_streaming(provider)
    assert broker_registry.get_streaming_provider() is provider


@pytest.mark.asyncio
async def test_take_over_streaming_disconnects_the_previous_provider_by_default():
    old = _FakeProvider("old")
    new = _FakeProvider("new")
    await broker_registry.take_over_streaming(old)
    await broker_registry.take_over_streaming(new)

    assert old.disconnected is True
    assert broker_registry.get_streaming_provider() is new


@pytest.mark.asyncio
async def test_take_over_streaming_does_not_disconnect_a_provider_still_needed_for_historical():
    """
    The whole point of confirmed decision #33: Polygon might be serving
    as both streaming-fallback AND historical. When Finnhub takes over
    streaming, Polygon must stay connected — it's still needed for
    GET /market/candles.
    """
    polygon = _FakeProvider("polygon")
    broker_registry.set_historical_provider(polygon)
    await broker_registry.take_over_streaming(polygon)  # Polygon fills both roles

    finnhub = _FakeProvider("finnhub")
    await broker_registry.take_over_streaming(finnhub)  # Finnhub connects later

    assert polygon.disconnected is False  # still serving historical — must not be torn down
    assert broker_registry.get_streaming_provider() is finnhub
    assert broker_registry.get_historical_provider() is polygon  # unaffected


@pytest.mark.asyncio
async def test_take_over_streaming_with_same_provider_is_a_noop_disconnect():
    provider = _FakeProvider("a")
    await broker_registry.take_over_streaming(provider)
    await broker_registry.take_over_streaming(provider)  # "taking over" from itself
    assert provider.disconnected is False


def test_get_all_active_providers_deduplicates_by_identity():
    shared = _FakeProvider("shared")
    broker_registry.set_historical_provider(shared)
    # Bypass take_over_streaming's async signature for this sync test —
    # direct module-level assignment is fine here since we're only
    # testing get_all_active_providers()'s dedup, not the takeover logic.
    import app.services.broker_registry as mod
    mod._streaming_provider = shared

    assert broker_registry.get_all_active_providers() == [shared]


def test_get_all_active_providers_returns_both_when_distinct():
    streaming = _FakeProvider("streaming")
    historical = _FakeProvider("historical")
    broker_registry.set_historical_provider(historical)

    import app.services.broker_registry as mod
    mod._streaming_provider = streaming

    result = broker_registry.get_all_active_providers()
    assert len(result) == 2
    assert streaming in result and historical in result


def test_clear_all_resets_both_roles():
    provider = _FakeProvider("a")
    broker_registry.set_historical_provider(provider)
    broker_registry.clear_all()
    assert broker_registry.get_historical_provider() is None
    assert broker_registry.get_streaming_provider() is None
