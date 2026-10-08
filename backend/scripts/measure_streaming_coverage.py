"""Streaming-coverage measurement command (task `streaming-coverage-measurement`).

Observes a RUNNING backend's /ws boundary for an explicit window and reports
which monitored symbols produced ticks, closed 1m candles and 1m feature
updates. Read-only: it never connects a market-data provider, requests feeds,
edits the scanner universe or starts trading. Operators request feeds first
with the existing manual action (POST /scanner/request-universe-feeds, or the
Universe tab's request button), then run this command.

USAGE (from backend/):
    python scripts/measure_streaming_coverage.py --backend-url http://127.0.0.1:8000 \\
        --symbols AAPL,MSFT,NVDA --duration 300
    python scripts/measure_streaming_coverage.py --backend-url http://127.0.0.1:8000 \\
        --scanner-universe --duration 600 --json-report ./coverage-report.json

Exit codes: 0 completed window (zero events included); 2 invalid input;
3 failed setup / precondition; 4 interrupted or incomplete; 5 report not written.

The measurement logic lives in app/measurement/streaming_coverage.py; see
docs/architecture/scanner-design.md §18.17 and TESTING.md for interpretation limits.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `python scripts/foo.py` puts scripts/ on sys.path, not backend/ (same fix the other scripts use).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.measurement.streaming_coverage import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
