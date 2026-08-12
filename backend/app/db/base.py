"""
Declarative base. alembic/env.py imports Base.metadata as target_metadata
for autogenerate — every model module must be imported somewhere reachable
from here (see the bottom of this file) or Alembic won't see it.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import model modules so their tables register on Base.metadata.
# Add new model modules here as they're created (models/market_data.py is
# the only one that exists as of Phase 2 — symbols + candles only;
# everything else in system-design.md §4.13 is a later phase).
from app.models import market_data  # noqa: E402,F401
from app.models import trading_intelligence  # noqa: E402,F401 — level_interaction_state/events (confirmed decision #46)
