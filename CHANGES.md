# CHANGES — IBKR historical Backtest Runner provider

## Outcome

Backtest Runner now has a sibling real-price-history path:

```text
POST /backtest/run/ibkr
```

It downloads real IBKR OHLCV before replay, disconnects the isolated read-only IBKR
client, and gives `BacktestRunner` a disconnected run-scoped provider containing those
validated candles. The existing named-fixture `POST /backtest/run` route and frontend
caller are unchanged.

This closes the synthetic candle/OHLCV gap only. `FixtureBacktestContextProvider` still
provides replay-safe calendar context; point-in-time historical fundamentals and news
remain unavailable and are not fabricated.

## Components and lifecycle

- `IBKRAdapter` gained historical-only primitives for contract qualification, request/error listeners, and one bounded `TRADES` request. Live streaming defaults, read-only connection behavior, and execution stubs are unchanged.
- `acquire_ibkr_replay_data()` creates one isolated adapter with the explicitly configured `IBKR_BACKTEST_CLIENT_ID`, enables request errors, observes error/disconnect events, qualifies once, issues serial chunks, normalizes/validates/merges them, and disconnects in `finally`.
- `PreloadedHistoricalCandleProvider` holds real downloaded `1m` and `1d` candles. It permanently reports disconnected and cannot stream, subscribe, or publish ticks. BacktestRunner installs this—not the network adapter—during replay.
- Acquisition includes the exact primary `1m` interval, Feature Engine's configured prior-session `1m` premarket span, and its configured `1d` Daily Levels/ATR/RVOL span.
- Primary/auxiliary minute bars use `TRADES`, `useRTH=False`; daily bars use `TRADES`, `useRTH=True`.
- The user interval is exact `[start,end)`, fixed at one-minute replay, and limited to 24 elapsed hours. Auxiliary lookbacks are not capped. The cap can be reconsidered when replay performance improves or background jobs exist.
- One-minute history is acquired in serial one-day chunks with no blind retries. Timestamps become aware UTC; results are sorted, exact-filtered, overlap-deduplicated, and conflict-checked.
- A failed acquisition happens before `BacktestRunner.run()` and therefore before any `BacktestRunRecord` write.

## Safety and configuration

- Added optional raw setting `IBKR_BACKTEST_CLIENT_ID` with no default. Blank or malformed values do not break general backend startup; the sibling route validates it on demand and rejects missing, negative, or live-ID-colliding values before connection.
- The live-provider guard now includes registry-owned IBKR adapters, closing `future-ideas.md` #25 without registering the historical-only acquisition client.
- The guard runs before acquisition and again after disconnect, before replay touches process-wide engines.
- No order, execution route, streaming subscription, TickIngestBridge, or frontend behavior was added or changed.

## Failure contract and provenance

Stable application errors cover connection/Gateway failure, unresolved contract,
permission/subscription denial, pacing rejection, timeout, disconnect, zero primary
bars, malformed timestamps, and conflicting/incomplete responses. They map to explicit
`400`, `409`, `422`, `502`, `503`, or `504` responses rather than successful empty
runs.

Persisted provenance is `ibkr:TRADES:1m-ext:1d-rth`. It identifies the vendor and
request semantics without claiming an immutable IBKR dataset version.

## Documentation

Updated:

- `backend/README.md`
- `backend/.env.example`
- `backend/app/api/routes/backtest.py`
- `backend/app/backtest_runner/fixture_provider.py`
- `backend/app/backtest_runner/historical_provider_guard.py`
- `backend/app/backtest_runner/runner.py`
- `docs/architecture/backtest-runner-design.md`
- `docs/decisions/future-ideas.md` (#17 comparison note; #25 resolved)
- `docs/decisions/INDEX.md` and `confirmed-decisions.md`
- root `TESTING.md`, recreated from scratch

The architecture document includes the request → live guard → isolated acquisition →
normalization/preload → disconnect → replay → persistence lifecycle diagram.

## Validation

- Untouched UTC/PostgreSQL baseline: 741 collected; 740 passed; one timing-sensitive existing test failed and passed immediately in isolation.
- Final collection: 766, exactly +25.
- Focused adapter/acquisition/route set: 33 passed.
- Complete final suite against real PostgreSQL: **766 passed, 0 failed, 0 skipped**.
- No live IBKR connection was verified or claimed; `TESTING.md` contains Saqib's exact paper Gateway/TWS verification procedure and expected HTTP examples.
