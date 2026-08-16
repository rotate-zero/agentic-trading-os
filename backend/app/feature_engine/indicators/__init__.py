"""
Feature Engine indicator math — pure functions only, one file per
indicator (sma.py, ema.py, vwap.py), not one growing module. Split out of
what used to be a single indicators.py once VWAP landed alongside SMA/EMA
made it clear more would keep arriving (PDH/PDL, Camarilla, VPOC — see
docs/architecture/feature-engine-chart-migration.md Stage 3) — the same
one-file-per-indicator shape frontend/src/indicators/ already uses
(sma.ts, ema.ts, vwap.ts), so this side of the migration doesn't stay the
odd one out.

No I/O, no Event Bus awareness, no database, in any file here — engine.py
is what wires these to CandleClosed/FeaturesUpdated for the live path,
historical.py for the batch/chart path; keeping this package pure makes
every function trivially unit-testable without a database, an event loop,
or a running app at all (see backend/tests/test_feature_engine.py's Tier 1
tests).

Re-exported here so `from app.feature_engine.indicators import sma, ema,
typical_price, vwap_from_accumulator` — every call site's actual import
line, in engine.py, historical.py, and the test suite — keeps working
unchanged. The split is an internal reorganization, not a public API
change; nothing outside this package needs to know indicators.py became a
package instead of staying one file.
"""
from __future__ import annotations

from app.feature_engine.indicators.ema import ema
from app.feature_engine.indicators.sma import sma
from app.feature_engine.indicators.vwap import typical_price, vwap_from_accumulator

__all__ = ["ema", "sma", "typical_price", "vwap_from_accumulator"]
