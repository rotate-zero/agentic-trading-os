# TESTING — decision #169: Phase 4 scale/load investigation (`phase4-scale-load-measurement`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled as a tarball
(`codeload.github.com/.../tar.gz/refs/heads/main`; no `.git` metadata in this
sandbox). Highest decision at task start: **#167** (three-source check
agreed: `INDEX.md` last row #167, `confirmed-decisions.md` tail #167, archive
files `001-060`…`134-160` — open log held #161–#167). A second, fresh pull
taken immediately before assigning a real decision number found the parallel
`execution-engine-design` sibling had already landed **#168** — confirmed by
the same three-source check against the fresh pull (`INDEX.md` last row
#168, `confirmed-decisions.md` tail #168, archive files unchanged). That
sibling's own `TESTING.md` record (retained below) named this task by its
exact slug in a "Manual merge notes" section and anticipated exactly this
ordering, including exactly which four files are shared and how to merge
them. Diffed the fresh pull against this session's working tree for every
file outside the four shared ones this task might touch
(`docs/roadmap/phase-roadmap.md`, `docs/architecture/scanner-design.md`,
`backend/scripts/`): identical — zero collision on this task's own file
boundary. **#169 assigned only after that re-check.**

## What changed (exact five-file footprint)

| File | Change |
|---|---|
| `backend/scripts/measure_live_pipeline_scale.py` | **new** — opt-in harness, not pytest-collected (matches no glob in `pytest.ini`) |
| `docs/roadmap/phase-roadmap.md` | one sentence — Phase 4 exit-criterion status, now measured-on-synthetic-input |
| `docs/decisions/confirmed-decisions.md` | decision #169 appended at the true end (`### 169. …`) |
| `docs/decisions/INDEX.md` | row #169 appended after #168 |
| `CHANGES.md`, `TESTING.md` | this delivery's records, prepended above the #168 record (kept intact below) |

**Not touched:** `backend/app/**`, `frontend/**`, `backend/tests/**`,
`docs/architecture/system-design.md`, `docs/architecture/scanner-design.md`
(see the decision entry for why — a different, still-open question), any
existing decision text.

## Environment

1 vCPU / 3.9 GB sandbox (`nproc`=1, `os.cpu_count()`=1), Ubuntu 24.04, Python
3.12.3, PostgreSQL 16.15 (apt, freshly installed this session — not present
at container start). Scratch database only:

```bash
# one-time setup
service postgresql start
su postgres -c "psql -c \"CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;\""
su postgres -c "psql -c \"CREATE DATABASE trading_scale_scratch OWNER trading;\""
su postgres -c "psql -c \"ALTER SYSTEM SET fsync = off;\""   # matches decision #155's own precedent
su postgres -c "psql -c \"SELECT pg_reload_conf();\""

cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python -m alembic upgrade head   # -> head 0011, clean
```

SQLAlchemy `create_engine()` (`app/db/session.py`) takes no explicit
`pool_size`/`max_overflow` — confirmed by reading the call site — so both are
library defaults (`pool_size=5`, `max_overflow=10`). Default
`asyncio.to_thread` executor size on this box: `min(32, cpu+4) == 5` threads
(process-wide, shared by every stage).

## Reproduction

```bash
cd backend
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python scripts/measure_live_pipeline_scale.py \
  --ramp 1,10,25,50,100 --bursts 16 --tick-minutes 3 --ticks-per-minute 3 \
  --output full_ramp_results.json

# supplementary stress point (N=100, 60-candle burst — beyond the requested ramp)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=trading_scale_scratch \
  POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/python scripts/measure_live_pipeline_scale.py \
  --ramp 100 --bursts 60 --tick-minutes 3 --ticks-per-minute 3 \
  --output stress_n100_k60.json
```

The script refuses to run unless `POSTGRES_DB` contains `"scratch"`
(`_require_scratch_db()`) — a hard guard, not a convention, since it
`TRUNCATE`s application tables between ramp steps.

## Results

Full numeric results are in the decision #169 entry (`confirmed-decisions.md`)
— tables reproduced from this run's own JSON output, not hand-transcribed.
Summary:

| N | FeaturesUpdated | drain | LevelInteraction (queue-verified) | drain | MarketStateChanged | 1m coverage |
|---|---|---|---|---|---|---|
| 1 | 21 | 0.031s | queue fully drained (3 events) | 0.058s | 1 | 1/1 |
| 10 | 210 | 0.236s | queue fully drained (17 events) | 0.427s | 20 | 10/10 |
| 25 | 525 | 0.673s | queue fully drained (36 events) | 1.203s | 50 | 25/25 |
| 50 | 1050 | 1.157s | queue fully drained (57 events) | 2.205s | 124 | 50/50 |
| 100 | 2100 | 2.744s | queue fully drained (125 events) | 4.194s | 400 | **100/100** |

Stress point N=100/K=60: 7,700 FeaturesUpdated in 9.11s; LevelInteraction
queue fully drained (`queue.join()`) in 14.03s (1,038 events); 1m coverage
100/100. `MarketStateChanged` settle timed out at its fixed 2.0s window
(`"timed out after 2.0s at count=1051"`) — a documented lower bound for that
one number at K=60 only (the settle window is sized for the K=16 primary
ramp); does not affect the FeatureEngine/LevelInteractionEngine coverage or
drain-time numbers.

Stage A (tick ingestion), every N: `PriceUpdated` observed == expected and
`CandleClosed` observed == expected (10/10 … 1000/1000 ticks; 3/3 … 300/300
candles); zero `LiveTickRelay` active-symbol gating violations at any N.

Full per-burst queue-depth telemetry, per-symbol coverage arrays, and raw
timing are in `full_ramp_results.json` / `stress_n100_k60.json` (not
committed — regenerate via the commands above; each run TRUNCATEs and
reseeds the scratch database itself, so results are exactly reproducible
modulo this sandbox's own timing noise).

## Checks and results

- **Footprint:** `diff -rq` of a fresh untouched `main` pull (post-#168)
  against the working tree lists exactly the five files above and nothing
  else.
- **No production code changed:** confirmed no `backend/app/**` file's
  content differs from the fresh pull (`diff -rq`); this harness only reads
  private `_queue` attributes and calls existing public methods
  (`bus.subscribe_all`, `queue.join()`, `evaluate_for_symbol` indirectly via
  `ContextEngine.start()`'s own bootstrap) — nothing under
  `backend/app/**` was edited to make any of this observable.
- **No suite-time impact:** `backend/scripts/measure_live_pipeline_scale.py`
  matches no glob `pytest.ini` collects (`grep -n "python_files\|testpaths"
  backend/pytest.ini` — scripts/ is not a testpath; the file also has no
  `test_` prefix). Not run as part of the backend test suite; full suite was
  not re-run for this delivery since no application code changed (same
  posture as decision #155/#158/#168's own precedent for docs/investigation-
  only deliveries).
- **Harness self-verification (smoke test, N=1):** cross-checked
  `LevelInteractionChanged`'s low count directly against
  `level_interaction_state` — 6 rows (vwap/vwap_ext/regular_open × 1m/5m),
  `updated_at` spanning the whole run, confirming the engine really
  processed every item even though it published only 1 transition event
  (see decision entry Finding 1).
- **Coverage is per-symbol, not just aggregate:** every ramp step asserts
  `min == max == burst_count` across all N symbols' individual
  `FeaturesUpdated(1m)` counts, not just that the total matches.

## Not covered / limitations

- No real broker/feed exercised — everything upstream of `CandleClosed`
  publication in Stage B, and the tick source in Stage A, is synthetic.
- Real tick burstiness, reconnects, and partial/duplicate provider delivery
  are not represented.
- Production hardware was not used; this sandbox's `fsync=off` is a real
  advantage a production database likely won't have, and its 1 vCPU is a
  real disadvantage a production host likely won't have — both stated, not
  netted against each other.
- `ContextEngine`, `StrategyScheduler`, and `OpportunityCache` ran (so
  `StrategyScheduler` genuinely evaluated all 7 strategies per
  `MarketStateChanged`, via seeded `scanner_universe_symbols` rows) but
  their own throughput was not separately measured or reported.
- N was not pushed past 100 (K was, at fixed N=100, to 60). The
  `MarketStateChanged` settle-wait limitation at K=60 is noted above and in
  the decision entry.

## Manual merge notes (parallel session)

This delivery landed **second**, after the sibling `execution-engine-design`
task's #168. Per that sibling's own anticipated merge notes (retained
below): `confirmed-decisions.md`/`INDEX.md` keep both entries in numerical
order (#168 then #169 — already true, appended at the true end); `CHANGES.md`
keeps both records (this one prepended on top); `TESTING.md` keeps this
record on top and the sibling's retained directly below, unedited.

## Baseline SHA-256

The five pre-existing files this delivery edits were clean at baseline
(matching the fresh post-#168 pull) with these hashes (the new file has
none until after this delivery):

```text
docs/decisions/confirmed-decisions.md 79a37d3b51d782c627ff6988e8ea2781709128e59f0857eef39ce70726abd152
docs/decisions/INDEX.md               15b84c8293501d5558ab012d2e1cae0877e525e3ab2ebae116cc06cfd7839d88
CHANGES.md                            61faa01cf81ca90eac4b29fcf82e69312edbb5a7879f437214de6b609437284d
TESTING.md                            41723b3f0ffd301495e45f806968b3c1cb61037241e6d601ecade799473ce83e
docs/roadmap/phase-roadmap.md         473239117a5c5f08ffa2a0930a981a27963c3e252445d9d5a842933bb8380651
```

<!-- Previous delivery record retained below. -->

# TESTING — decision #168: Execution Engine & Portfolio State design doc (`execution-engine-design`)

## Baseline and evidence

Repository: `rotate-zero/agentic-trading-os`, `main`, pulled as a tarball
(`codeload.github.com/.../tar.gz/refs/heads/main`; no `.git` metadata in the
sandbox, and a GitHub API commit-SHA lookup returned no data, so no remote SHA
was retrieved). Latest-`main` equality was established a second way: a second,
fresh tarball pull taken immediately before packaging was compared to the pull
taken at task start with `diff -rq` — **identical**, i.e. nothing landed in
between. Highest decision before this delivery: **#167**; the three-source
check agreed — `INDEX.md` last row #167, `confirmed-decisions.md` tail #167,
archive files `001-060` … `134-160` (open log holds #161–#167). #168 was
assigned only after that re-check.

This delivery is **documentation only**. No file under `backend/` or
`frontend/` changed, and no application, migration, schema, or event-model
change was made. No application tests were run for that reason.

## What changed (exact six-file footprint)

| File | Change |
|---|---|
| `docs/architecture/execution-engine-design.md` | **new** — DRAFT design pass (§0 summary, §1 verified inventory, §2 thirteen findings, §3 invariants, §4 `StrategyOutcome` source map, §5 slice analysis, §6 component design + four diagrams, §7 fourteen open forks, §8 deferred prerequisites, §9 proposed acceptance criteria, §10 out-of-boundary findings) |
| `docs/architecture/system-design.md` | pointers only: one companion-doc entry, one paragraph under §4.6, one under §4.9; no existing text rewritten, version/status header untouched |
| `docs/decisions/confirmed-decisions.md` | decision #168 appended at the true end (`### 168. …`) |
| `docs/decisions/INDEX.md` | row #168 appended |
| `CHANGES.md`, `TESTING.md` | this delivery's records |

## Checks and results

- **Footprint:** `diff -rq` of a fresh untouched `main` pull against the working tree lists exactly the six files above and nothing else (no `backend/`, `frontend/`, `phase-roadmap.md`, `scanner-design.md`, `trading-intelligence-architecture.md`, or `strategy-engine-open-decisions.md` difference).
- **Claim-to-source check:** every claim in the design doc about current code or current docs was turned into a machine check (file/AST/regex against the tarball) — **54 of 54 pass**. Code-level claims (usage, callers, definitions) use Python `ast`, so docstrings and comments are not mistaken for code. Four checks first failed for exactly that reason (docstrings/comments naming an event, `.place_order()`, or `record_strategy_outcome()`; a comment containing "backfill"; and `to_thread(fn, …)` passing the function by reference rather than calling it); the checks were tightened, not the claims. Table below.
- **Relative links** in the new doc: 7 found, 0 unresolved (script: extract `](./…)` targets, `os.path.exists` on each).
- **Structure:** 14 fork headings `### EX-n`, all marked OPEN; 13 findings F1–F13; six fenced blocks (four architecture diagrams plus the slice sketch and the order state machine); no `{…}` placeholders left; no CR characters or tabs; LF endings preserved in the three edited existing files.
- **Decision-log format:** heading is `### 168. …` (the format the repo's grep pattern `^### [0-9]+\.|^[0-9]+\. \*\*` detects); appended after #167; existing decision text untouched.
- **Quotes** of existing docs (`§4.9` lifecycle wording, `base.py`/`ibkr_adapter.py` Governor wording, `runner.py`'s "first thing to mint one", `§18.8`'s "existing `trades` table", `§2` principle 1) were compared verbatim against the source.

## Claim-to-source table (machine-checked)

| ID | Claim in the design doc | Result | Where checked |
|---|---|---|---|
| C1 | All 8 execution-side EventType names exist | PASS | envelope.py:EventType |
| C2 | Payload models exist for GovernorDecision, OrderApproved, PlanRejected, OrderFilled | PASS | models in execution.py = GovernorDecision,OrderApproved,PlanRejected,… |
| C3 | No Pydantic class TradePlanned/OpportunitySelected/PositionAdjusted/PositionClosed anywhere in backend/app/schemas | PASS | grep class defs in schemas/ |
| C4 | Critical set = exactly OrderFilled, PlanRejected, GovernorDecision, OrderApproved | PASS | CRITICAL_EVENT_TYPES = { EventType.ORDER_FILLED, EventType.PLAN_REJEC… |
| C5 | channels.py routes ORDER_APPROVED/PLAN_REJECTED/ORDER_FILLED/GOVERNOR_DECISION/OPPORTUNITY_SELECTED | PASS | channels.py |
| C6 | channels.py has no route for TRADE_PLANNED / POSITION_ADJUSTED / POSITION_CLOSED | PASS | channels.py |
| C7 | No application code (AST: names/attrs/imports, docstrings and comments ignored) other than execution.py/envelope.py/channels.py/dev.py references any of the 8 execution events | PASS | code references elsewhere = 0 |
| C8 | dev.py publishes a GovernorDecision | PASS | api/routes/dev.py |
| C9 | OrderApproved fields = order_id,symbol,side,qty,order_type,limit_price | PASS | execution.py (no fill_id anywhere) |
| C10 | OrderFilled has no symbol field and no fill_id/cumulative_qty/venue | PASS | OrderFilled body |
| C11 | base.py declares BrokerAdapter, OrderRequest, OrderAck, Position + place_order/cancel_order/get_positions | PASS | base.py |
| C12 | OrderAck.status is submitted\|rejected only | PASS | OrderAck body |
| C13 | No order-update/fill callback, client order id, TIF, or bracket method/field in base.py (AST: defs and annotated fields) | PASS | offending identifiers = [] |
| C14 | BrokerAdapter extends MarketDataProvider | PASS | base.py |
| C15 | IBKRAdapter connects readonly=True | PASS | ibkr_adapter.py |
| C16 | IBKRAdapter place_order and cancel_order raise NotImplementedError | PASS | NotImplementedError x3 |
| C17 | get_positions() has zero callers (app/tests/frontend) | PASS | callers=0 |
| C18 | No call to place_order()/cancel_order() anywhere in backend/app or backend/tests (AST calls) | PASS | calls=[] |
| C19 | broker_registry has streaming+historical roles and no execution role | PASS | broker_registry.py |
| C20 | config.py has no dry_run/execution setting | PASS | config.py |
| C21 | No trades/orders/positions/ai_decisions/feature_snapshots/market_events tables | PASS | tables=daily_levels_state,symbols,candles,market_state_history,scanne… |
| C22 | strategy_outcomes and backtests tables exist | PASS | models |
| C23 | Migration head is 0011 | PASS | 0011_level_interaction_backtest_run_isolation.py |
| C24 | StrategyOutcomeRecord: strategy_name/strategy_version/opportunity_id/structural_*/final_*/confidence_at_signal/evidence NOT NULL | PASS | StrategyOutcomeRecord |
| C25 | StrategyOutcomeRecord has is_backtest Boolean and no CheckConstraint | PASS | StrategyOutcomeRecord |
| C26 | StrategyOutcomeRecord has no venue column | PASS | StrategyOutcomeRecord |
| C27 | record_strategy_outcome is sync def, uses SessionLocal, and docstring forbids live wiring without Execution Engine | PASS | performance.py |
| C28 | record_strategy_outcome has exactly one application user (AST name reference; runner.py passes it to asyncio.to_thread): backtest_runner/runner.py | PASS | ['backend/app/backtest_runner/runner.py'] |
| C29 | state_snapshot: capture_strategy_outcome_snapshots + capture_market_state_snapshot + capture_context_snapshot | PASS | state_snapshot.py |
| C30 | MarketStateEngine.get_snapshot returns candle_ts per symbol | PASS | market_state_engine/engine.py |
| C31 | performance_queries._common_filters filters on is_backtest | PASS | performance_queries.py |
| C32 | World View portfolio slot is None | PASS | composite.py |
| C33 | Opportunity has no id, symbol, or entry-price field | PASS | Opportunity body |
| C34 | Backtest Runner mints opportunity_id with uuid4 | PASS | runner.py |
| C35 | OpportunityCache overwrites latest per (symbol, strategy) | PASS | opportunity_cache.py |
| C36 | fill_simulator: simulate_entry/simulate_exit/compute_realized_r/compute_realized_pnl/regular_session_close_utc + InsufficientReplayDataError | PASS | fill_simulator.py |
| C37 | EventBus._consume awaits asyncio.gather over handlers; _safe_call exists | PASS | bus.py |
| C38 | Event Bus is in-memory (asyncio.Queue) with no persistence import | PASS | bus.py |
| C39 | No frontend consumer of orders.status | PASS | matches=0 |
| C40 | No frontend Positions/ApprovalQueue/TradeManagement code | PASS | matches=0 |
| C41 | No TradeRequest/ExecutionMode/ManualConfirm*/PlanAwaiting* code | PASS | matches=0 |
| C42 | MarketClock.trading_day exists | PASS | market_clock.py |
| C43 | PriceUpdated has exchange_ts | PASS | schemas/events/market_data.py |
| C44 | A strategy declares gate_conditions {'session': 'regular'} | PASS | strategy_engine/*.py |
| C45 | FeatureEngine._on_candle_closed exists (subscribe -> queue pattern) | PASS | feature_engine/engine.py |
| C46 | system-design §10.3 TradePlanned row has max_hold_minutes; TIA §18.3 TradePlan has max_hold_seconds | PASS | system-design.md / TIA |
| C47 | system-design §4.8 table: Position Monitor -> PositionAdjusted/PositionClosed -> positions; Performance Intelligence consumes PositionClosed | PASS | system-design.md |
| C48 | system-design still names AlpacaAdapter in §4.1, and folder tree says 'Alpaca deferred, not stubbed' | PASS | system-design.md |
| C49 | TIA §18.5: ExecutionMode owned by Portfolio State; ManualConfirmOrder calls place_order | PASS | TIA §18.5 |
| C50 | TIA §18.8 says filled manual plan recorded in 'the existing `trades` table' | PASS | TIA §18.8 |
| C51 | TradeRequest has no stop field; TradePlan.stop required | PASS | TIA §18.2-18.3 |
| C52 | future-ideas has entries #14, #16, #27 | PASS | future-ideas.md |
| C53 | system-design folder tree names execution_engine/, portfolio_state/, position_monitor/, governor/ | PASS | system-design.md §8 |
| C54 | decision #95 documents Finnhub free-tier IEX-only trade feed | PASS | archive/091-106.md |

## Not covered / limitations

- **Nothing was executed against a real broker.** Every statement about `IBKRAdapter` is about the code as written; its live behavior remains unverified (`future-ideas.md` #27).
- **The design is unvalidated by construction:** no prototype, no load test, no schema migration was attempted. Fill-model parity (EX-8) and outcome-population labelling (EX-2) are proposals whose consequences appear only when a build task implements them.
- The claim check proves cited symbols exist and behave as stated at this `main`; it cannot prove the *proposals* are right. That is what the fork list is for.
- Two files that would normally change with an architecture doc were deliberately left alone per the task boundary: `docs/roadmap/phase-roadmap.md` and `docs/architecture/strategy-engine-open-decisions.md` (see design doc §10, R4).

## Manual merge notes (parallel session)

A sibling task, `phase4-scale-load-measurement`, was specified as file-disjoint from this one but shares the same four delivery files: the two decision-log files, `CHANGES.md`, `TESTING.md`. If it lands first:

1. Renumber this delivery to the next free number. **#168 appears in exactly these places:** the `Status` line of `execution-engine-design.md`, the `### 168.` heading in `confirmed-decisions.md`, the `| 168 |` row in `INDEX.md`, and the title lines of this `CHANGES.md` and `TESTING.md`. (`grep -rn "168" docs/architecture/execution-engine-design.md docs/decisions/confirmed-decisions.md docs/decisions/INDEX.md CHANGES.md TESTING.md` finds them; other hits are unrelated digits.)
2. `confirmed-decisions.md` / `INDEX.md`: keep both entries, the sibling's and this one, in numerical order; re-run the three-source check first.
3. `CHANGES.md`: keep both records. `TESTING.md`: keep this record on top and the sibling's directly below.

## Baseline SHA-256

The five pre-existing files this delivery edits were clean at baseline with these hashes (the new file has none):

```text
docs/architecture/system-design.md d57349d4e69f27ec5ab2055612f925577ca3ce3d8f3209ccc8ab0395dc894500
docs/decisions/INDEX.md 2983e5a2a4b3926aaf3775241b4ebf79a5eb635b7ff2693fb20fdef39777eb2e
docs/decisions/confirmed-decisions.md bc103962ec165a842f06d7eafe6acb827343b8869b15551a8fa16a351d00b4e9
CHANGES.md 4298be8cde53f2e7384bed65971e2bd0ad80d6110b7e87a705c5814f9d2a86d5
TESTING.md 77b9614876044108241380e723cb43467ced6858ee0eb7b4eacf60384fee26ea
```

New file after this delivery: `docs/architecture/execution-engine-design.md 4b3127ea6b287ab949e6510bea7f45b14c488dac17240d195203f5dddc434a27` (recompute after any renumbering).

<!-- Previous delivery record retained below. -->

# TESTING — decision #167: close the two low-risk documentation status-drift follow-ups

## Baseline and evidence

Repository root: `/home/rotate_zero/projects/agentic-trading-os`; branch:
`main`; HEAD and existing `origin/main`: `6c4f2ba22bc46dff9d76980e006ae656bf880777`;
working tree: clean; highest decision: #166. A fetch refresh could not update
`.git/FETCH_HEAD` because the workspace exposes `.git` read-only, so the
existing `origin/main` ref was used and its equality with HEAD was confirmed.
The required direct `git ls-remote` check immediately before assigning #167 was
also attempted, but GitHub DNS was unavailable in this environment; no remote
SHA could be retrieved.

Decision #164's two selected read-only findings are the only changes in scope;
the final decision number is #167 after the local index/log cross-check.
`docs/README.md` called both `diagrams/` and `api/` placeholders; the current
diagram README and `trading-intelligence-overview.md` show that only `api/`
remains a placeholder. The overview is one standalone Mermaid document with
live component flow plus compact internal flowcharts for Feature, Level
Interaction, Market State, Context, Scanner, Strategy Scheduler, and
performance evidence.

The milestone artifact's Phase 3 still has `id: "p3"`, the unchanged exit
criterion, exactly three checklist items, and state keys generated as
`phaseId:itemIndex`. Current implementation evidence confirms Finnhub is
registered only as the genuine real-time streaming provider; Polygon is the
historical provider and delayed polling streaming fallback; and manually
connected IBKR is registered for both streaming and historical roles. Decision
#1 rejects Alpaca as the first broker, while decisions #28–#33 establish the
provider split and its implementation.

## Checks

- `git diff --check`
- exact six-file footprint review
- Phase 3 item-count, `p3` id, exit-criterion, and state-key checks
- provider-role checks against `backend/app/main.py`, the three provider
  implementations/registrations, and decisions #1 and #28–#33
- existing `api/` placeholder row unchanged
- existing decisions and decision archives unchanged

No application tests were run because this delivery changes documentation only.

## Baseline SHA-256

The six editable files were clean at baseline with these hashes:

```text
docs/README.md 1496c430eeacadcac8fedbeb645cd6ef453cbef2b7e6e4c26ffeeaf20f3a81e9
milestone-tracker.html f9d686f85d7bf27427c9bb6c2acac12852763e52559494c20fb14f97ad6307ab
CHANGES.md 819d477f517f4c2ecdfdbedc0f601792ac52fa69f01e70c7b76f63bfc4631462
TESTING.md 327fa7c3f3ac8491d0e2f0f5b7d562ba5670c60b972ddbc1bdac45d154d55a9a
docs/decisions/INDEX.md a28686c5eddb3709f0f58458bd883d86cec3aba0e7dfec610e2add98eb61af67
docs/decisions/confirmed-decisions.md 9fc7e8d9a39129e151686c1e8f2c68c7bc9dd482304dc0d78488a9a38851638d
```
