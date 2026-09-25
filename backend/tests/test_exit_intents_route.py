"""Diagnostic response ordering and symbol delegation without pipeline setup."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.intelligence import router
from app.position_monitor.engine import ExitIntent


def test_exit_intents_route_sorts_observations_and_delegates_symbol_filter():
    earlier = datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 25, 13, 1, tzinfo=timezone.utc)
    intents = (
        ExitIntent(UUID(int=3), "ZZZ", "BUY", 1, "target", 120.0, earlier),
        ExitIntent(UUID(int=2), "AAA", "SELL", 2, "stop", 90.0, later),
        ExitIntent(UUID(int=1), "AAA", "BUY", 3, "stop", 90.0, earlier),
    )

    class Monitor:
        def __init__(self):
            self.filters = []

        def get_exit_intents(self, symbol=None):
            self.filters.append(symbol)
            return tuple(intent for intent in intents if symbol is None or intent.symbol == symbol)

    app = FastAPI()
    app.include_router(router)
    app.state.position_monitor = monitor = Monitor()
    with TestClient(app) as client:
        all_response = client.get("/intelligence/exit-intents")
        filtered_response = client.get("/intelligence/exit-intents?symbol=AAA")

    assert all_response.status_code == filtered_response.status_code == 200
    assert [item["position_id"] for item in all_response.json()["exit_intents"]] == [
        str(UUID(int=1)), str(UUID(int=2)), str(UUID(int=3)),
    ]
    assert [item["position_id"] for item in filtered_response.json()["exit_intents"]] == [
        str(UUID(int=1)), str(UUID(int=2)),
    ]
    assert monitor.filters == [None, "AAA"]
    assert all_response.json()["intent_status"] == "observed_only"
