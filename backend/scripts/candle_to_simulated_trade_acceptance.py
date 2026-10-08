#!/usr/bin/env python3
"""Command-line entry point for the candle-to-simulated-trade acceptance
(see app/acceptance/candle_to_simulated_trade.py).

    cd backend
    POSTGRES_DB=<disposable_db> ... python scripts/candle_to_simulated_trade_acceptance.py --database <disposable_db>

Exit code 0 = PASS, 1 = a milestone failed, 2 = a precondition failed, 3 = watchdog expired.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.acceptance.candle_to_simulated_trade import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
