# CHANGES — pending decision (temp id: `real-market-data-backtest-attempt`) — first attempted real-market-data BacktestRunner execution, blocked by environment

**Decision number intentionally not assigned** — see this delivery's entry in `docs/decisions/confirmed-decisions.md` for why, and for the number to assign at merge time (143, per a three-source check at packaging time, unless another parallel session lands first — a **seven-way** collision, the widest yet in this log).

**No parallel deliveries landed on `main` during this task's own session** — re-pulled and diffed twice (task start and immediately before packaging); zero change either time. `main` stayed at `4f5f32b8444fbd5e384a604f8ea8add5740b60a9` throughout.

Saqib asked for a real, complete `BacktestRunner` execution against real market data — not fixtures, not the test suite — because D4's 2026-09-16 readiness check found zero rows in `strategy_outcomes`/`backtests`. **This attempt also produced zero rows, but for an environmental reason, not a code gap:** the only route that replays real, non-fixture market data (`POST /backtest/run/ibkr`) requires a reachable IB Gateway/TWS process that does not and cannot exist in this sandboxed session. The call was made for real, against a genuinely fresh, real local Postgres, and failed with a real `503`. No workaround was applied to manufacture rows — the goal was an honest account of what happened, not a forced result, per this task's own explicit framing.

## What changed

- **`docs/architecture/backtest-runner-design.md`** — new as-built note recording the exact attempt: environment provisioned, request made, real `503 ibkr_connection_unavailable` response, Finnhub/Polygon ruled out independently, and three concrete paths forward for Saqib to choose from. Includes one ASCII flow diagram (this doc's existing convention).
- **`docs/architecture/strategy-engine-open-decisions.md`** — D4's row gained a second, 2026-09-18 readiness-check note alongside the existing 2026-09-16 one, recording that this attempt also found zero rows and why (environment, not code).
- **`docs/decisions/confirmed-decisions.md`** / **`INDEX.md`** — this delivery's own PENDING entry.

## What did not change

- **No `backend/` files touched at all** — no code changed, because there was nothing safe or in-scope to fix. The blocker is a missing reachable IB Gateway process and a network-egress allowlist with no market-data-provider domains in it, neither of which this delivery can alter unilaterally.
- **The five quarantined paths named in this task's own hard boundary** (`backend/app/feature_engine/`, `backend/app/trading_intelligence/level_interaction_engine.py`, `backend/app/market_state_engine/`, and their matching test files) — confirmed untouched, not merely avoided by intent. `diff -rq` against a freshly re-pulled clone shows zero changes anywhere under `backend/`.
- `strategy_outcomes`/`backtests` tables — still zero rows, in the real `trading_workspace` database this session provisioned. Nothing was fabricated or substituted to make this delivery look more finished than it is.
- `App.tsx`, `BacktestPanel.tsx`, `BacktestResultsPanel.tsx`, and every other frontend file — untouched; this delivery never reached the point of needing frontend changes.
