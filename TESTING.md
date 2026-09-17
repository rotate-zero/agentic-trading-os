# TESTING — `MarketStateChanged` → WebSocket channel (temp id: `market-state-changed-websocket-channel`)

## What changed

One missing routing entry closed, same shape as decision #126's `ContextChanged` fix:

```python
EventType.MARKET_STATE_CHANGED: "intelligence.market-state"
```

added to `EVENT_TO_CHANNEL` in `backend/app/api/websocket/channels.py`. `MarketStateEngine`
already publishes both real shapes (per-symbol and cross-symbol/`"__MARKET__"`, decisions
#93/#97) — no engine, publish-side, or payload change was made anywhere.

**Correction versus this task's own initial framing:** the cross-symbol shape's
`envelope.symbol` is **never** null/absent — it's always the literal sentinel
`"__MARKET__"` (`engine.py`'s own `_CROSS_SYMBOL_SENTINEL`). A subscriber must compare
`envelope.symbol == "__MARKET__"`, not check for `None`. This differs from
`ContextChanged`'s own market-wide shape, where `symbol` really is unset.

## How to verify

Real PostgreSQL 16, no mocks.

```bash
cd backend
python -m alembic upgrade head   # no new migration in this delivery
python -m pytest -q tests/test_websocket_channels.py -v
```

Expect **8 passed** — decision #126's original 4 `ContextChanged` tests plus this
delivery's 4 new `MarketStateChanged` tests:

- `test_market_state_changed_mapping_present` — regression guard on the routing entry itself
- `test_per_symbol_market_state_changed_reaches_intelligence_market_state_with_real_symbol`
- `test_cross_symbol_market_state_changed_reaches_intelligence_market_state_with_sentinel_symbol`
  — explicitly asserts `msg["symbol"] is not None`, not just the right value
- `test_unrelated_event_does_not_reach_intelligence_market_state_channel`

All four use the same real `TestClient`/`.portal`-based delivery pattern #126 already
established — nothing mocked, nothing patched.

## Full-suite results (real Postgres, chunked — see note below)

Against a fresh clone of `main` taken *after* the parallel `ibkr-historical-backtest-provider`
delivery merged:

| | Collected | Passed | Failed |
|---|---|---|---|
| Fresh `main` (before) | 766 | 766 | 0 |
| This delivery's tree (after) | 770 | 770 | 0 |

Exactly **+4**, zero regressions. No member of the long-documented decision #119 flaky
cluster surfaced on either run (not claimed fixed — consistent with that cluster's own
documented intermittency).

**Why chunked, not one unbroken run:** this codebase's own documented ~1s/candle
Backtest Runner replay cost (decision #131) — `test_backtest_runner_regression.py` alone
takes ~8 minutes — makes one unbroken `pytest` invocation impractical in this sandbox. Ran
in groups instead (all non-backtest/IBKR files together, ~50s; each backtest/IBKR file
individually or in small groups) and summed. Every group matched its counterpart exactly
except the group containing `test_websocket_channels.py` (204 vs. 200 baseline — the
expected +4).

## `diff -rq` footprint

Against the same fresh post-merge clone, confirmed the only files this delivery touches are:

- `backend/app/api/websocket/channels.py`
- `backend/tests/test_websocket_channels.py`
- `docs/architecture/system-design.md`
- `docs/decisions/confirmed-decisions.md`
- `docs/decisions/INDEX.md`
- `TESTING.md` (this file)

Nothing under `backend/app/backtest_runner/`, `routes/backtest.py`, `routes/broker.py`,
`finnhub_data.py`, `market_data.py`, or any frontend file was touched.

## Parallel-track re-check

Re-pulled a fresh tarball mid-task (Saqib: "git is updated") and found the
`ibkr-historical-backtest-provider` delivery had landed since this task's first pull.
Confirmed file-disjoint both ways by `diff -rq` — that delivery's own stated footprint
(`backend/app/backtest_runner/`, `routes/backtest.py`, `routes/broker.py`,
`broker_adapters/ibkr_adapter.py`, `core/config.py`, plus its own docs/decisions files)
has zero overlap with this delivery's footprint above. Rebased this delivery's two changed
files cleanly onto that new `main` tip before running the full-suite comparison.

A separate Claude session was also reported to be building a first-ever frontend surface
for Market State Engine (new hook, `InfoTab.tsx`). No matching entry has landed on `main`
as of this delivery's own packaging-time re-check, so there was nothing to reconcile
against — that surface can subscribe to `intelligence.market-state` immediately once this
delivery merges.

## Decision number

Left unassigned, per Saqib's standing rule against minting real decision numbers during
parallel work. `INDEX.md`/`confirmed-decisions.md` carry this delivery under temp id
`market-state-changed-websocket-channel`. Three-source re-check at packaging time shows a
**four-way collision** on next-available-**143**, alongside `data-feed-status-indicator`,
`broker-connection-panel`, and `ibkr-historical-backtest-provider`. Whoever merges first
should re-run the three-source check, assign the real number, and update: this delivery's
own decision-log entry header, its `INDEX.md` row, and the temp-id reference in the
`EVENT_TO_CHANNEL` code comment in `channels.py`.

## Not covered / manual merge notes

- No frontend hook subscribes to `intelligence.market-state` yet — out of scope per this
  task's explicit file boundaries (frontend is a parallel session's job). The channel is
  live and ready the moment a consumer subscribes to it.
- If the parallel Market State frontend session's delivery lands with its own
  `EVENT_TO_CHANNEL`-adjacent doc edits to `system-design.md` §10.3, merge both prose
  additions to the `MarketStateChanged` row by hand rather than letting one overwrite
  the other — same category of manual-merge point decision #114/#115 already documents
  for this repo's parallel-session history.
