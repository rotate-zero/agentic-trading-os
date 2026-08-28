# TESTING — Extended VWAP (vwap_ext) + pre-market data availability check

Unzip at project root. No migration needed — this is pure Feature Engine
logic, no new tables. Overwrites `backend/app/feature_engine/engine.py`
(adds `_update_vwap_ext` — `_update_vwap`/`vwap`/`session_volume`
completely untouched) and `backend/tests/test_feature_engine.py` (one
exact-equality assertion updated to include the two new keys — expected,
not a regression, same as when `session_volume` was added). Adds
`backend/tests/test_vwap_ext.py`, `backend/scripts/check_premarket_data_availability.py`,
`docs/architecture/premarket-accumulator-design.md`. Also fixes a real
bug in the **already-delivered** `backend/scripts/test_scanner_pipeline.py`
(see "Bug fixed in a prior delivery" below) — that file is overwritten
too.

## 1. Backend tests

```bash
cd backend
pytest tests/test_feature_engine.py tests/test_vwap_ext.py -v
```

Already run clean against a real Postgres 16 instance before this was
sent: **245/245 backend tests passing**, zero regressions. The one test
worth looking at directly if you want to see the actual fix demonstrated:

```bash
pytest tests/test_vwap_ext.py::test_vwap_ext_continues_across_the_930_boundary_without_resetting -v
```

Publishes a pre-market bar at price 50, then a regular-open bar at price
100. `vwap` reads 100 (unchanged — resets at 9:30 same as always).
`vwap_ext` reads 75 (pre-market's bar is still in the running average).
That divergence, at the exact moment `vwap` resets, is the discrepancy
you saw against other platforms.

## 2. Pre-market data availability check (needs your real Polygon key)

This is the actual open question blocking `premarket_volume_ratio`
(the ORB-enabling feature) — not a formality:

```bash
cd backend
python scripts/check_premarket_data_availability.py
python scripts/check_premarket_data_availability.py TSLA 10   # different symbol/lookback
```

Needs `POLYGON_API_KEY` set (same one your existing `PolygonAdapter`
already uses). Prints a small table of pre-market 1-minute bar counts
and volume for the last few weekdays, then a verdict: either Polygon
genuinely has this data (in which case `premarket_volume_ratio` is
buildable against it) or it doesn't (in which case that feature is
gated on the IBKR subscription instead, same as spread tightness
already was).

## 3. Bug fixed in a prior delivery

Both this new script and the earlier `scripts/test_scanner_pipeline.py`
failed with `ModuleNotFoundError: No module named 'app'` when run
exactly as documented (`cd backend && python scripts/foo.py`) — Python
adds the script's own directory to `sys.path`, not the directory you ran
it from, so anything importing `app.*` directly needs an explicit fix.
`verify_roundtrip.py` never hit this because it only talks to a running
server over HTTP. Caught while verifying the new script; fixed in both.
If you'd already tried running `test_scanner_pipeline.py` and hit this,
that's why — not something wrong on your end.

## What's still NOT built

`premarket_volume_ratio` itself — correctly still gated on step 2 above.
Nothing in this delivery lets you rank symbols on pre-market activity
yet; that's the next piece, once the data-availability question has a
real answer.
