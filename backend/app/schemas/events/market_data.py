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
