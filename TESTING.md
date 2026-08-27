# TESTING — Scanner v1 (ActivityScorer + pipeline test script)

Unzip at project root — this overwrites `backend/app/core/config.py` (adds
3 new settings, nothing else in that file changed) and adds 5 new files:
`backend/app/scanner/{__init__.py,scorer.py,universe.py}`,
`backend/scripts/test_scanner_pipeline.py`, `backend/tests/test_scanner.py`.

## What this is

Per `docs/architecture/scanner-design.md` §2/§9: the v1 composite
`ActivityScorer` (unusual volume + volatility-relative move — the two
scan types chosen to test the pipeline on current APIs, ahead of the
IBKR subscription), plus a script to run it against whatever Feature
Engine is actually computing live right now for a small placeholder
universe (`app/scanner/universe.py`'s `TEST_UNIVERSE` — NOT the real
Core-100, which is still your call).

No `MarketActivityScanner` orchestrator, `ScanCadenceSchedule`, or
`LiveTickRelay` wiring yet (§1/§4/§5 of the design doc) — deliberately
scoped to just the scorer + a way to look at real output, since that's
what "test the pipeline" needs right now, not the full promotion
machinery.

## 1. Unit tests (no server, no market data needed)

```bash
cd backend
pytest tests/test_scanner.py -v
```

6 tests, already run against the real `FeatureSet` schema before this
was sent to you — all passing. Covers: ATR normalization when
`atr_14_pct` is available, fallback to raw values when it isn't, missing
`rvol` handled as "skip this term" rather than a fabricated zero, the
all-missing cold-start case, per-input weighting, and that gap/session
change use absolute value (a large down move should score the same as
a large up move).

## 2. Live pipeline test (the actual point of this delivery)

Needs a couple of the `TEST_UNIVERSE` symbols (`AAPL MSFT NVDA AMD TSLA
SPY`) to have at least one recorded 1m candle already — ideally during
market hours with live Finnhub ticks flowing, so `rvol`/`gap_pct`/
`session_pct_change` have actually computed at least once.

```bash
# terminal 1
cd backend
uvicorn app.main:app --reload

# terminal 2, same venv
cd backend
python scripts/test_scanner_pipeline.py
```

Prints a ranked table (`RANK / SYMBOL / SCORE / INPUTS`) plus a `Skipped`
line for any symbol Feature Engine hasn't computed anything for yet — a
skip means genuinely no data yet, not a bug. Symbols scored from fewer
than 2 of the 3 possible inputs get a `(low confidence — few inputs)`
flag next to them so a thin reading doesn't look as trustworthy as a
full one.

Optional: `python scripts/test_scanner_pipeline.py AAPL TSLA NVDA` to
try a different symbol set without editing `TEST_UNIVERSE`.

## What to actually look at

The point of this delivery isn't "does it run" (already verified) — it's
"do these rankings look right to you." Specifically worth checking:

- Does the symbol you'd subjectively call "most active right now" land
  near the top?
- Do the equal 1.0/1.0/1.0 weights (§8 of the design doc, still an open
  question in §10) feel right, or does one input dominate/get drowned
  out in practice?
- Any symbol stuck at `0/3` or `1/3` inputs for longer than expected —
  worth checking whether that's genuinely cold-start or something else
  not computing.

Nothing here is asserted as "correct" — that's explicitly your call to
make from watching real output, per the design doc's own "ship
equal-weighted, tune from observed behavior" stance.
