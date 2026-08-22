"""Payload schemas for market-data events. See system-design.md §10.3."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PriceUpdated(BaseModel):
    price: float
    size: int
    exchange_ts: datetime


class CandleClosed(BaseModel):
    timeframe: str  # e.g. "1m", "5m", "1d"
    open: float
    high: float
    low: float
    close: float
    volume: int
    candle_ts: datetime


class PriceSnapshot(BaseModel):
    """
    In-progress-bar snapshot for the currently-forming 1m candle on a
    small, actively-monitored symbol set (LiveTickRelay, confirmed
    decision #72) — NOT a closed candle. Deliberately mirrors
    CandleClosed field-for-field so the frontend's existing toCandle()
    (api-client.ts) and diffCandles' "update_last" path (ChartWidget.tsx)
    handle it with zero new type plumbing — only the EventType (and the
    fact that this bar isn't finished yet) distinguishes it from
    CandleClosed. Kept as its own model rather than reusing CandleClosed
    directly, same reasoning as decision #15's Tick vs PriceUpdated split:
    conflating "closed" and "in-progress" into one type would make every
    future CandleClosed consumer newly responsible for checking which
    kind it received.
    """
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    candle_ts: datetime
