"""Payload schema for LevelInteractionChanged. See system-design.md §10.3
and trading-intelligence-architecture.md §4/§7 (confirmed decision #46).
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class LevelInteractionChanged(BaseModel):
    timeframe: str  # "1m" only in this pass — matches Feature Engine's own current scope
    level_key: str  # whatever key FeatureEngine published, e.g. "sma_9" — not a hardcoded enum
    trading_day: date

    status: str  # "holding" | "rejected" | "conquered" | "unclassified"
    zone: str  # below | inside_aura | above — the zone AFTER this transition
    touch_count_today: int
    seconds_in_zone: int  # 0 at touch start; final dwell time at resolution
    distance_pct: float  # signed, relative to touch_anchor_price; 0.0 at touch start
    anchor_price: float | None  # the level's value when this touch began

    # Only meaningful at resolution (status in rejected/conquered/unclassified):
    # dwell = normal case; gap = price closed on the opposite side with no
    # candle ever closing inside the Aura in between; cold_start_unknown_origin
    # = this process's first-ever observation of this level was already
    # inside the Aura, so there's no known entry side to classify against.
    observed_via: str | None

    candle_ts: datetime
