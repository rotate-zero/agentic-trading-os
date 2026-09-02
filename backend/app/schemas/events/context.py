"""Payload schema for ContextChanged. See system-design.md §10.3 and
trading-intelligence-architecture.md §5 (decision #90 for the provider
boundary; decision #92 for this aggregator's shape and v1 provider list).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ContextChanged(BaseModel):
    # One key per registered ContextProvider (its `.name`); value is
    # whatever that provider's evaluate() returned. Merged, not
    # flattened, so a naming collision between two providers' own output
    # keys can never silently overwrite one field with another's. v1 has
    # one entry: "calendar". "fundamentals"/"news" join once M0's Finnhub
    # spike results unblock FundamentalsProvider/NewsFlagProvider
    # (M0-SPIKE-NOTES.md).
    providers: dict[str, dict] = Field(default_factory=dict)
