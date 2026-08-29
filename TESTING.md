# TESTING — Scanner integration for premarket_volume_ratio

Unzip at project root. No migration needed. Overwrites
`backend/app/scanner/{scorer.py,runner.py}`, `backend/app/api/routes/scanner.py`,
`backend/app/core/config.py` (adds `scanner_weight_premarket_volume_ratio`,
default 0.0), `backend/scripts/test_scanner_pipeline.py`,
`backend/tests/test_scanner.py`, `frontend/src/components/scanner/ScannerPanel.tsx`.

## What changed

`premarket_volume_ratio` now flows all the way through the Scanner — it
shares `rvol`'s "activity" slot in the composite score (the two never
coexist for the same symbol, since one needs regular session and the
other needs pre-market) rather than becoming a bolt-on 4th input. Its
weight (`scanner_weight_premarket_volume_ratio`) defaults to **0.0** —
deliberately inert. You missed today's pre-market and the weekend has no
market at all, so wiring the code in now (rather than waiting) was the
right call — but trusting its ranking output is a separate decision that
still waits on you actually looking at real values, which is why the
weight stays at 0 until you flip it.

## 1. Tests

```bash
cd backend
pytest tests/test_scanner.py -v
pytest   # full suite
```

9 scanner tests (3 new — mutual exclusivity, weight defaults, weighted-on
behavior). **268 passed, 1 deselected** — see "A separate, pre-existing
bug" below for what that one is and why it's not part of this delivery.

## 2. What you'll see in the panel

Once Monday's pre-market arrives, the Scanner panel's chip row will show
a `PM Vol X.XXx` chip (bolded, same treatment as `RVOL`) for any symbol
in pre-market — informative immediately, even though it won't move the
ranking yet. The header caption now reads "Ranked by RVOL / PM Vol (v1)"
to reflect that.

## 3. Turning it on, once you've actually looked

```python
# app/core/config.py
scanner_weight_premarket_volume_ratio: float = 1.0  # or whatever feels right after watching real values
```

No other code changes needed — same pattern as flipping
`scanner_weight_gap`/`scanner_weight_session_change` back on.

## 4. A separate, pre-existing bug found during verification — NOT fixed here

`tests/test_intelligence_routes.py::test_intelligence_series_reflects_real_persisted_candles`
fails intermittently with a foreign-key violation during its own
teardown — a race between the app's shutdown sequence and
`LevelInteractionEngine`'s background worker, unrelated to anything in
this delivery. **Confirmed directly**: this same test fails identically
on the code as it stood *before* today's Scanner work, proving it
predates this change entirely. Deliberately left unfixed rather than
speculatively patched — it needs its own investigation into async
shutdown ordering. Worth a look separately, not urgent.
