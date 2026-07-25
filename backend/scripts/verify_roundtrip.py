"""
Verifies the Phase 2 exit criterion end to end against a REAL running
server (not TestClient) — HTTP -> Event Bus -> WebSocket Gateway -> client,
for both the normal and critical dispatch lanes.

Usage:
    # in one terminal:
    uvicorn app.main:app --reload

    # in another terminal (same venv):
    python scripts/verify_roundtrip.py

Exits non-zero with a clear message on any failure, so it's safe to use
as a quick smoke test whenever something upstream changes.
"""
from __future__ import annotations

import asyncio
import sys

import httpx
import websockets

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"


async def check_health() -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{BASE_URL}/health")
        r.raise_for_status()
        data = r.json()
        print(f"[health] {data}")
        assert data["status"] == "ok"


async def check_normal_lane() -> None:
    async with websockets.connect(WS_URL) as ws:
        await ws.send('{"action": "subscribe", "channel": "dev.ping"}')
        ack = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"[normal] subscribe ack: {ack}")

        async with httpx.AsyncClient() as client:
            r = await client.post(f"{BASE_URL}/dev/dummy-event", json={"message": "round trip works"})
            r.raise_for_status()
            print(f"[normal] POST /dev/dummy-event -> {r.json()}")

        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"[normal] received over WS: {msg}")
        assert '"channel":"dev.ping"' in msg or '"channel": "dev.ping"' in msg
        assert "round trip works" in msg


async def check_critical_lane() -> None:
    async with websockets.connect(WS_URL) as ws:
        await ws.send('{"action": "subscribe", "channel": "orders.status"}')
        ack = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"[critical] subscribe ack: {ack}")

        async with httpx.AsyncClient() as client:
            r = await client.post(f"{BASE_URL}/dev/critical-event")
            r.raise_for_status()
            print(f"[critical] POST /dev/critical-event -> {r.json()}")

        msg = await asyncio.wait_for(ws.recv(), timeout=5)
        print(f"[critical] received over WS: {msg}")
        assert "rejected" in msg


async def main() -> None:
    try:
        await check_health()
        await check_normal_lane()
        await check_critical_lane()
    except Exception as exc:  # noqa: BLE001 — this is a CLI script, not a library
        print(f"\nFAILED: {exc!r}")
        print("Is `uvicorn app.main:app --reload` running in another terminal?")
        sys.exit(1)

    print("\nALL ROUND-TRIP CHECKS PASSED (normal lane + critical lane, against a live server)")


if __name__ == "__main__":
    asyncio.run(main())
