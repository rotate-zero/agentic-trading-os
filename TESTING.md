# TESTING — decision #164: documentation status synchronization

## What changed

Documentation only. The roadmap living status now reflects the verified
as-built boundary through the current `main` baseline; the Scanner design's
header no longer says that no application code exists; the decision index
introduction no longer hardcodes an upper bound; and one append-only decision
records the synchronization. `CHANGES.md` and this task-specific verification
record move with those edits.

No backend or frontend suites were run. No application behavior changed, and
those suites cannot validate whether prose accurately summarizes source and
decision history. Verification therefore targets repository evidence,
Markdown/link integrity, decision continuity, collision safety, and the exact
six-file footprint.

## Starting state and independent baseline

- Repository: `/home/rotate_zero/projects/agentic-trading-os`
- Starting branch: `main`
- Starting commit: `640e4eef38c4932ae16861d73d2856bc3dc451b7`
- Starting `git status --short`: empty
- Initial independent GitHub `main` snapshot: `/tmp/tmp.sdwfxT922G`

The working tree and initial snapshot hashes matched exactly:

```text
65e69ffcb30c0e0a0ebbdd0f9119e82de42448c065e79a93b6b921741a848341  docs/roadmap/phase-roadmap.md
e47ceb1ced006254d79860bc2dc9364f902029ee472ebe10f82e289876e9cf3f  docs/architecture/scanner-design.md
7d3d317174fa9317c24681eb87a00877a611519551b2a37ed91d932f5e4dc388  docs/decisions/INDEX.md
53cafb60929502a335d34f520c63cb818a8f152e879610d3fafb669569775fc6  docs/decisions/confirmed-decisions.md
b4481fe7d80b6fbf8381318a7006076319d1a94c2c929bf8f086927c36dfcea9  CHANGES.md
a4c65fe0408e5386a39132d0bf5a3a2648b494e72af09f055234adad21a378c6  TESTING.md
```

The final independent upstream re-pull and collision result are recorded below
after the required immediate-before-numbering check.

## Evidence inspected

The evidence pass covered the required documentation, decision ranges and
later superseding entries, plus the implementation paths behind every new
status claim. Focused source proof included:

```text
backend/app/feature_engine/indicators/__init__.py
  exports SMA, EMA, ATR, Camarilla, Daily Levels, Gap, KAMA, Regression,
  RVOL, Session Change, VPOC and VWAP indicator functions.

backend/app/schemas/events/market_state.py
  class MarketState
  class CrossSymbolState

backend/app/context_engine/engine.py
  default global providers: [CalendarProvider()]
  default symbol providers: [FundamentalsProvider(), NewsFlagProvider()]

backend/app/strategy_engine/scheduler.py
  default_registry() constructs ORBStrategy, GapStrategy,
  VolumeSpikeStrategy, FirstPullbackStrategy, ReversalStrategy,
  MomentumStrategy and VWAPStrategy.
  StrategyScheduler validates and enforces gate_conditions centrally.

backend/app/scanner/runner.py
  run_scan() is request-driven orchestration over FeatureEngine snapshots.
backend/app/api/routes/scanner.py
  GET /scanner/state and GET/POST/DELETE /scanner/universe exist.
frontend/src/hooks/useScannerState.ts
frontend/src/hooks/useScannerUniverse.ts
frontend/src/components/scanner/ScannerPanel.tsx
  polling, universe editing, ranked display and persisted panel controls exist.

backend/app/trading_intelligence/opportunity_cache.py
  class OpportunityCache; latest item per (symbol, strategy), no ranking.
backend/app/trading_intelligence/opportunity_view.py
  get_opportunity_conflicts(); agreement/conflict view, explicitly non-scoring.

backend/app/trading_intelligence/performance.py
  record_strategy_outcome(); real BacktestRunner caller, no live caller.
backend/app/trading_intelligence/performance_queries.py
  get_win_rate_by_hour(); get_expectancy_by_session_type().
backend/app/api/routes/intelligence.py
  strategy-outcome, conflict, performance, backtest-run and world-view reads.
backend/app/backtest_runner/runner.py
backend/app/api/routes/backtest.py
  BacktestRunner and fixture/IBKR/sweep HTTP paths.

backend/app/world_view/composite.py
  class WorldView; portfolio=None.
backend/app/schemas/events/execution.py
  class GovernorDecision (schema only).
backend/app/broker_adapters/ibkr_adapter.py
  place_order() raises NotImplementedError.
```

Decision evidence used includes #1–#10, #28–#35, #45–#71, #83–#86,
#87–#128, #131, #133, #137–#145, #150, #154, #158–#163. The decision
index and current open log were read through their tails, and the relevant
archived entries were inspected rather than inferred from number ranges.

## Prompt claims checked against repository evidence

- The initial prompt expected an index through #162. Current `main` contains a
  contiguous #163 row and final #163 live decision; the later repository state
  governs final numbering.
- Phase 3 remains unverified against a real IBKR Gateway/TWS/account, but the
  roadmap's “passport renewal pending” reason is no longer the current record.
  Decisions #144–#145 and `ibkr-broker-panel-validation.md` record no reachable
  paper session and a deliberate deferral. Polygon, unlike the old roadmap
  claim, was later exercised with a real configured key and real minute data.
- Scanner application code and frontend surfaces exist. Its continuous
  `MarketActivityScanner`, `ScanCadenceSchedule`, top-N Scheduler/LiveTickRelay
  promotion, Discovered tier, and spread scoring/filtering do not.
- All seven strategies are registered. Decision/Planning, Portfolio State,
  live Execution, and Position Monitor modules remain absent. Governor has an
  event schema but no rule engine. World View exists and returns `portfolio`
  as `null`.
- The core Scanner implementation does not have a dedicated introduction row
  in the decision index; decision #102 records its later pre-market scoring
  integration. The documentation therefore does not invent a missing decision
  number for the earlier Scanner files.

## Read-only drift sweep (§5; reported, not fixed)

No finding blocks this delivery; the authorized roadmap/header corrections can
be accurate without changing any of these files.

- **Related follow-up — `docs/README.md`:** `diagrams/` is still called “just a
  placeholder,” contradicted by `docs/diagrams/trading-intelligence-overview.md`
  and the non-placeholder `docs/diagrams/README.md`. The adjacent `api/`
  placeholder description remains accurate: `docs/api/README.md` says the
  contract folder is currently empty.
- **Related follow-up — `milestone-tracker.html`:** Phase 3 is still titled
  “First real data — Alpaca adapter” and lists `AlpacaAdapter`, contradicted by
  decision #1 and the implemented IBKR/Finnhub/Polygon provider path. Its later
  phase checklist is also a plan, not an as-built status surface; this task did
  not reinterpret or update it.
- **Related follow-up — `docs/architecture/scanner-design.md` body:** §5 still
  says Strategy Engine does not exist, and §11 says the Scanner panel lacks
  resize/collapse persistence and a universe editor. Decisions #99–#119 and
  current `StrategyScheduler`, `ScannerPanel`, `WorkspaceContext`,
  `useScannerUniverse`, and scanner universe routes contradict those body
  statements. Only the header Status line was authorized to change.
- **Related follow-up — `docs/decisions/future-ideas.md` #13:** its deferral
  rationale says `strategy_engine/` does not exist and there is no possible
  `StrategyOutcome` evidence. The seven-strategy engine exists (#99–#119), and
  Backtest Runner can write persisted outcomes (#120/#128). The idea may still
  be deferred, but that rationale needs a separately approved refresh.
- **Related follow-up — `docs/architecture/backtest-runner-design.md` opening
  design text:** it says `strategy_engine/` does not exist and the harness is
  “not built now,” contradicted by decisions #99–#119 and #128 onward. Later
  as-built notes in the same file preserve the true current state, so this is
  localized historical/design-prose drift rather than a blocker.
- **Related follow-up — `docs/architecture/trading-intelligence-architecture.md`
  decision-#92 build note:** it says Market State and `MarketStateChanged` do
  not exist and says the provider signature should be revisited once M2 lands.
  M2/M3 landed in decisions #93/#97. The current Context interface still omits
  Market State, so reconciling that note requires an architectural decision,
  not a silent wording edit.
- **Unrelated/accurate candidate — `backend/app/scanner/runner.py`:** “none of
  that exists yet” refers specifically to continuous `MarketActivityScanner`,
  `ScanCadenceSchedule`, and LiveTickRelay promotion. Those pieces are indeed
  absent; the docstring immediately distinguishes the real on-demand runner.
  It is not stale and was left untouched.
- **Unrelated/accurate candidate — `docs/api/README.md`:** the external API
  contract folder is still empty apart from its README. The placeholder claim
  is accurate even though application routes exist elsewhere.

## Final collision and numbering check

Immediately before appending the decision and index row, current GitHub `main`
was downloaded into a second, independent snapshot at `/tmp/tmp.37m0iudiI8`.
Its six editable-file hashes were unchanged from the initial snapshot:

```text
65e69ffcb30c0e0a0ebbdd0f9119e82de42448c065e79a93b6b921741a848341  docs/roadmap/phase-roadmap.md
e47ceb1ced006254d79860bc2dc9364f902029ee472ebe10f82e289876e9cf3f  docs/architecture/scanner-design.md
7d3d317174fa9317c24681eb87a00877a611519551b2a37ed91d932f5e4dc388  docs/decisions/INDEX.md
53cafb60929502a335d34f520c63cb818a8f152e879610d3fafb669569775fc6  docs/decisions/confirmed-decisions.md
b4481fe7d80b6fbf8381318a7006076319d1a94c2c929bf8f086927c36dfcea9  CHANGES.md
a4c65fe0408e5386a39132d0bf5a3a2648b494e72af09f055234adad21a378c6  TESTING.md
```

No collision existed. The final upstream `INDEX.md` row and final live-log
heading were both #163. Archive ranges `001-060`, `061-079`, `080-090`,
`091-106`, `107-121`, `122-133`, and `134-160` were contiguous and
non-overlapping, followed by live decisions #161–#163. Therefore baseline
`B=163` and the next free decision was `N=164`.

## Validation commands and results

### Footprint and diff review

`diff -rq` against the final snapshot (excluding only local environment/cache
artifacts and the delivery zip) reported exactly:

```text
CHANGES.md
TESTING.md
docs/architecture/scanner-design.md
docs/decisions/INDEX.md
docs/decisions/confirmed-decisions.md
docs/roadmap/phase-roadmap.md
```

Before archive creation, `git status --short` reported those same six files as
modified and no other tracked or untracked delivery file. `git diff --check`
returned no output. The complete diff was reviewed file by file: every hunk is
intentional; the roadmap changes are confined to `Status (living)`, Scanner has
one header-line change, the decision log has one appended entry, the index has
only its introduction plus row #164 changed, and the two root records are the
authorized task-specific replacements.

### Focused evidence proof

Representative grep output (the full evidence pass also covered the indicator
exports, routes, UI hooks/panel, and cited decision ranges):

```text
backend/app/schemas/events/market_state.py:26:class MarketState(BaseModel):
backend/app/schemas/events/market_state.py:39:class CrossSymbolState(BaseModel):
backend/app/scanner/runner.py:48:def run_scan(
backend/app/api/routes/scanner.py:39:@router.get("/state")
backend/app/api/routes/scanner.py:77:@router.get("/universe")
backend/app/api/routes/scanner.py:82:@router.post("/universe")
backend/app/api/routes/scanner.py:91:@router.delete("/universe/{symbol}")
backend/app/strategy_engine/scheduler.py:200:def default_registry(active_from: datetime) -> list[Strategy]:
backend/app/strategy_engine/scheduler.py:217-223:the seven strategy constructors
backend/app/trading_intelligence/opportunity_cache.py:87:class OpportunityCache:
backend/app/trading_intelligence/opportunity_view.py:166:def get_opportunity_conflicts(
backend/app/trading_intelligence/performance.py:30:def record_strategy_outcome(
backend/app/trading_intelligence/performance_queries.py:304:def get_win_rate_by_hour(
backend/app/trading_intelligence/performance_queries.py:363:def get_expectancy_by_session_type(
backend/app/world_view/composite.py:72:class WorldView:
backend/app/world_view/composite.py:92:portfolio=None,
backend/app/schemas/events/execution.py:14:class GovernorDecision(BaseModel):
backend/app/broker_adapters/ibkr_adapter.py:232:raise NotImplementedError(
frontend/src/hooks/useScannerState.ts:17:export function useScannerState(
frontend/src/hooks/useScannerUniverse.ts:18:export function useScannerUniverse(
frontend/src/components/scanner/ScannerPanel.tsx:173:export function ScannerPanel() {
```

`backend/app/context_engine/engine.py` imports and defaults exactly
`CalendarProvider`, `FundamentalsProvider`, and `NewsFlagProvider` (lines
75–77 and 101–103). `backend/app/feature_engine/indicators/__init__.py` lines
28–41 export the cited indicator functions. Searches for the designed Scanner
remainder found only documentation/future-facing comments for
`MarketActivityScanner` and `ScanCadenceSchedule`; the scorer explicitly says
spread tightness is absent, while `LiveTickRelay.set_active_symbols` exists but
has no Scanner promotion caller.

### Links, continuity, immutability, and placeholders

The relative-Markdown-link checker examined 12 links across all six edited
files and reported `broken_relative_links=0`. Structural decision validation
reported:

```text
index_rows 164 first 1 last 164 unique 164
index_contiguous True
001-060.md 1 60 60 contiguous True
061-079.md 61 79 19 contiguous True
080-090.md 80 90 11 contiguous True
091-106.md 91 106 16 contiguous True
107-121.md 107 121 15 contiguous True
122-133.md 122 133 12 contiguous True
134-160.md 134 160 27 contiguous True
archive_plus_live 1 164 164 unique 164
headings_contiguous True
live [161, 162, 163, 164]
```

A byte-prefix comparison proved all 28,768 pre-existing bytes of
`confirmed-decisions.md` identical to final upstream; comparison of index rows
#1–#163 likewise returned no diff. Focused grep returned no temporary decision
identifier or `BASELINE` placeholder in the six files. The final index row and
final confirmed-decision heading are both #164.

### Archive inspection

The host image did not initially expose `zip`/`unzip`; their Debian packages
were downloaded and extracted under `/tmp` without installing them system-wide.
The final `unzip -l docs-status-drift-sync.zip` inspection reported six files
and exactly the repository-root-relative paths in the footprint above, with no
enclosing directory, metadata, cache, log, or unchanged file.

## Not covered

- No backend/frontend test suites: application behavior did not change.
- No live broker/provider session, browser interaction, load test, or
  100-symbol/no-dropped-ticks demonstration was attempted.
- No product decision was made about the Scanner document's `DRAFT` label or
  splitting Phase 5 and Phase 6 in the roadmap table.
- No report-only drift finding above was corrected.

## Manual merge notes

The six editable files were clean and identical to initial GitHub `main` at
task start. If a parallel session changes any of them before application, do
not combine mechanically: re-check decision numbering and manually preserve
both append-only log entries and both task-specific root documents. Files
outside the six-file boundary are intentionally absent from this delivery.

## Intended footprint

```text
CHANGES.md
TESTING.md
docs/architecture/scanner-design.md
docs/decisions/INDEX.md
docs/decisions/confirmed-decisions.md
docs/roadmap/phase-roadmap.md
```
