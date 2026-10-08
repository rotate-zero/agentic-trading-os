"""Contract check: the streaming-coverage command against the REAL gateway code.

Runs the production `/ws` router, `WebSocketGateway`, `EventBus`, event models (`make_envelope`, `PriceUpdated`,
`CandleClosed`, `FeatureSet`) and the real `GET /market/subscription-status` and `GET /scanner/universe` routes in a
minimal FastAPI app under uvicorn. Only the streaming provider (a controlled double) and the universe DB read are
substituted; no market-data provider, database or Finnhub key is involved. This proves the measurement parses the
payloads production code actually emits; it is still synthetic and says nothing about a real feed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import uvicorn
from fastapi import FastAPI

from app.api.routes import market, scanner
from app.api.websocket import channels
from app.api.websocket.channels import WebSocketGateway
from app.api.websocket.manager import get_connection_manager
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.measurement import streaming_coverage as sc
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_data import CandleClosed, PriceSnapshot, PriceUpdated
from app.services import broker_registry

T0 = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


class RecordingProvider:
    """Controlled streaming-provider double exposing the real optional inventory capability."""

    provider_id = "contract-double"

    def __init__(self, symbols):
        self._symbols = tuple(sorted(symbols))

    def is_connected(self):
        return True

    def get_subscription_snapshot(self):
        return self._symbols


@pytest.fixture
async def real_gateway_server(monkeypatch):
    monkeypatch.setattr(broker_registry, "get_streaming_provider", lambda: RecordingProvider(["AAPL", "MSFT"]))
    monkeypatch.setattr(scanner, "list_universe_symbols", lambda session_factory: ["AAPL", "MSFT", "NVDA"])

    bus = EventBus()
    await bus.start()
    WebSocketGateway(bus, get_connection_manager()).attach()

    app = FastAPI()
    app.include_router(channels.router)
    app.include_router(market.router)
    app.include_router(scanner.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield bus, port
    finally:
        server.should_exit = True
        await task
        await bus.stop()


async def publish_real_traffic(bus: EventBus) -> None:
    manager = get_connection_manager()
    for _ in range(300):  # wait until the measurement socket holds all three subscriptions
        counts = manager.subscriber_counts()
        if all(counts.get(ch, 0) >= 1 for ch in sc.CHANNELS):
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)  # the last ack is sent after its subscription registers; let the window open

    def candle(tf, ts):
        return CandleClosed(timeframe=tf, open=1, high=2, low=1, close=2, volume=10, candle_ts=ts)

    def features(tf, ts):
        return FeatureSet(timeframe=tf, candle_ts=ts, close=2.0, features={"sma_9": 1.5})

    for i, symbol in enumerate(["AAPL", "MSFT", "AAPL"]):
        await bus.publish(make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=10 + i, size=100, exchange_ts=T0 + timedelta(seconds=i)),
                                        symbol=symbol))
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, candle("1m", T0), symbol="AAPL"))
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, candle("5m", T0), symbol="AAPL"))
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, features("1m", T0), symbol="AAPL"))
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, features("15m", T0), symbol="MSFT"))
    # not on a subscribed channel: the gateway never delivers it to this measurement socket
    await bus.publish(make_envelope(EventType.PRICE_SNAPSHOT,
                                    PriceSnapshot(timeframe="1m", open=1, high=2, low=1, close=2, volume=1, candle_ts=T0), symbol="NVDA"))
    await bus.publish(make_envelope(EventType.PRICE_UPDATED, PriceUpdated(price=1, size=1, exchange_ts=T0), symbol="ZZZ"))


async def test_measurement_parses_what_the_real_gateway_emits(real_gateway_server):
    bus, port = real_gateway_server
    config = sc.build_config(f"http://127.0.0.1:{port}", symbols=None, scanner_universe=True, duration_s=1.5,
                             http_timeout_s=5.0, ack_timeout_s=5.0)
    m = sc.Measurement(config)
    run = asyncio.create_task(m.run())
    await publish_real_traffic(bus)
    assert await asyncio.wait_for(run, 20) == sc.EXIT_OK

    r = m.report()
    assert r["status"] == "completed" and r["monitored"]["symbols"] == ["AAPL", "MSFT", "NVDA"]
    assert set(r["channels"]["acknowledged"]) == set(sc.CHANNELS)  # real `_meta` acknowledgement shape accepted
    rows = {row["symbol"]: row for row in r["symbols"]}
    assert (rows["AAPL"]["ticks"]["count"], rows["MSFT"]["ticks"]["count"], rows["NVDA"]["ticks"]["count"]) == (2, 1, 0)
    assert rows["AAPL"]["candles_1m"]["count"] == 1 and rows["AAPL"]["features_1m"]["count"] == 1
    assert rows["MSFT"]["features_1m"]["count"] == 0
    assert rows["AAPL"]["ticks"]["first_source_ts"] == "2026-01-05T14:30:00.000+00:00"
    assert r["no_events"]["all_categories"] == ["NVDA"]
    a = r["anomalies"]
    assert a["malformed_messages"] == 0 and a["unexpected_channels"] == {} and a["meta_errors"] == 0
    assert a["other_timeframe_filtered"] == {"features.updated": {"15m": 1}, "market.candle": {"5m": 1}}
    assert a["unmonitored_symbol_events"]["market.tick"] == 1 and a["unmonitored_symbol_sample"] == ["ZZZ"]
    assert a["pre_window_events"] == {"features.updated": 0, "market.candle": 0, "market.tick": 0}

    # real GET /market/subscription-status body reduced without capacity/delivery claims
    d = r["diagnostics"]
    assert d["start"]["provider"] == {"id": "contract-double", "class_name": "RecordingProvider"} and d["start"]["connected"] is True
    assert d["start"]["inventory"]["availability"] == "available" and d["start"]["monitored_locally_listed"] == 2
    assert d["identity"]["changed"] is False
