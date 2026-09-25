"""
GET /scanner/state route tests — focused on the `?symbols=` override's
normalization/validation (this task), not on scoring math (test_scanner.py)
or run_scan's own orchestration (test_scanner_runner.py, which explicitly
defers "route-specific" correctness here — see that file's own module
docstring, corrected alongside this file).

Direct ASGI transport against the real, unstarted `app` — same technique
test_scanner_route_concurrency.py/test_execution_startup_status_route.py
already use (pytest.ini's asyncio_mode=auto picks up these `async def`
tests with no marker needed, same as that file) — because this route
needs neither FeatureEngine startup nor any other lifespan-installed
state: the override path never touches FeatureEngine.get_snapshot() with
a real streamed symbol (nothing in this process has streamed anything),
so every override resolves through run_scan() as fully skipped, which is
exactly what these tests assert alongside the normalized `universe` list.

The omitted-parameter test is the one exception — it reads the real
`scanner_universe_symbols` table (migration 0004) via DbUniverseProvider,
same "always real Postgres, never mocks" baseline test_scanner_universe.py
already establishes. It asserts only the documented contract (a non-empty
list, never a 400) rather than specific tickers, since the persisted
universe's actual contents are environment state this file doesn't own or
need to seed.
"""
from __future__ import annotations

import httpx

from app.main import app as fastapi_app


async def _get(params: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/scanner/state", params=params)


async def test_valid_override_is_trimmed_and_uppercased():
    response = await _get({"symbols": " aapl , tsla ,nvda"})

    assert response.status_code == 200
    body = response.json()
    assert body["universe"] == ["AAPL", "TSLA", "NVDA"]
    # Nothing in this process has streamed any of these — every symbol is
    # skipped (no 1m FeatureSet yet), same honest "cold start" behavior
    # run_scan already documents; this is what proves the override string
    # actually reached run_scan as real symbols, not just that the JSON
    # 200'd.
    assert set(body["skipped"]) == {"AAPL", "TSLA", "NVDA"}
    assert body["total_scored"] == 0


async def test_duplicate_entries_are_deduplicated_preserving_first_seen_order():
    response = await _get({"symbols": "TSLA,AAPL,tsla,AAPL,MSFT"})

    assert response.status_code == 200
    assert response.json()["universe"] == ["TSLA", "AAPL", "MSFT"]


async def test_share_class_suffix_ticker_is_accepted():
    response = await _get({"symbols": "brk.b"})

    assert response.status_code == 200
    assert response.json()["universe"] == ["BRK.B"]


async def test_lowercase_only_input_is_not_rejected_for_case():
    # Sanity check the OTHER direction of the format rule: not rejected
    # for being lowercase (normalized first) — only genuinely malformed
    # input should 400.
    response = await _get({"symbols": "aapl"})
    assert response.status_code == 200


async def test_invalid_ticker_format_returns_400():
    response = await _get({"symbols": "AAPL,123,TSLA"})

    assert response.status_code == 400
    assert "123" in response.json()["detail"]


async def test_too_many_letters_returns_400():
    response = await _get({"symbols": "TOOLONG1"})

    assert response.status_code == 400
    assert "TOOLONG1" in response.json()["detail"]


async def test_empty_list_item_from_stray_comma_returns_400():
    response = await _get({"symbols": "AAPL,,TSLA"})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


async def test_trailing_comma_returns_400():
    response = await _get({"symbols": "AAPL,"})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


async def test_empty_string_override_returns_400():
    # ?symbols= present but empty — an explicit override of nothing,
    # distinct from omitting the parameter entirely (next test).
    response = await _get({"symbols": ""})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


async def test_whitespace_only_override_returns_400():
    response = await _get({"symbols": "   "})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


async def test_omitted_symbols_parameter_uses_persisted_universe_not_400():
    """Genuinely omitted (no `symbols` key in the query string at all) —
    must still hit the persisted-universe/TEST_UNIVERSE-fallback path,
    never the override parser, and never 400."""
    response = await _get({})  # no `symbols` key at all

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["universe"], list)
    assert len(body["universe"]) > 0
