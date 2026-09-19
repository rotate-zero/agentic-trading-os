# CHANGES — decision-number reconciliation (#143–#152)

Base: `main` @ `dc0393d` ("zip it"), re-pulled immediately before packaging
(three-source re-check: `INDEX.md` tail, `confirmed-decisions.md` tail,
`docs/decisions/archive/` file list — all unchanged since the prior
session's own last check: last real number #142, archive unchanged
through `122-133.md`).

## What changed

Not a code delivery — this is the merge-time reconciliation of ten
same-day parallel deliveries that had each packaged under a temp id and
independently observed "next available is 143," per Saqib's own standing
rule against minting real numbers during parallel work. Assigned
**#143–#152**, in the order each entry's own text already recorded via
its self-reported N-way collision count (the order each was packaged):

| # | Temp id |
|---|---|
| 143 | `data-feed-status-indicator` |
| 144 | `broker-connection-panel` |
| 145 | `ibkr-historical-backtest-provider` |
| 146 | `market-state-changed-websocket-channel` |
| 147 | `market-state-frontend-surfacing` |
| 148 | `chart-migration-stage-4-flagging` |
| 149 | `flaky-test-cluster-rootcause` |
| 150 | `world-view-v1` |
| 151 | `market-state-websocket-upgrade` |
| 152 | `backtest-panel-ibkr-real-data-option` |

No design decisions were re-opened or re-litigated — every entry's own
substance (rationale, footprint, test results) is untouched verbatim;
only the number-assignment paragraph at the top of each was replaced
with a short "assigned at merge-time reconciliation" note, and the
header changed from `[PENDING — temp id: ...]` to `N. <title>`.

- `docs/decisions/confirmed-decisions.md` — all ten headers renumbered,
  all ten number-assignment paragraphs replaced.
- `docs/decisions/INDEX.md` — the seven rows that already existed as
  `**PENDING**` placeholders were renumbered and had their now-resolved
  "number intentionally unassigned" tail sentences removed. **Three rows
  were added for the first time** — `world-view-v1` (#150),
  `market-state-websocket-upgrade` (#151), and
  `backtest-panel-ibkr-real-data-option` (#152) had no `INDEX.md` row at
  all until now, a real gap in this log's own practice (the first
  instance, `world-view-v1`, was already flagged by
  `market-state-websocket-upgrade`'s own entry; the third,
  `backtest-panel-ibkr-real-data-option`, had gone unflagged until this
  reconciliation's own repo-wide check found it too).
- Every forward-reference call site any of the ten entries' own text
  named — plus a repo-wide grep for `temp id`/`temp-id`/`number TBD` to
  catch any it didn't — updated to cite the real number directly:
  `frontend/src/services/api-client.ts`,
  `frontend/src/hooks/useMarketState.ts`,
  `frontend/src/components/backtest/BacktestPanel.tsx`,
  `frontend/src/components/backtest/easternTime.ts`,
  `backend/app/world_view/composite.py`,
  `backend/tests/test_world_view.py`,
  `backend/tests/test_feature_engine.py`,
  `backend/tests/test_websocket_channels.py`,
  `docs/architecture/system-design.md` (§4.1, §4.2, §10.3 ×2 plus an
  ASCII-diagram label),
  `docs/architecture/trading-intelligence-architecture.md` (§4, plus two
  ASCII-diagram labels),
  `docs/architecture/feature-engine-chart-migration.md` (§0, §7),
  `docs/architecture/backtest-runner-design.md` (§7 ×2, plus one
  reference belonging to #145 and one belonging to #146 in the same
  section), `docs/decisions/future-ideas.md` (#25's resolution note, #26's
  own investigation note).

**Verification, not just pattern-matched replace:** every substitution
was applied as an exact whole-string match asserted to occur exactly
once, aborting loudly on a mismatch rather than guessing — so nothing
was silently skipped or double-applied. A repo-wide sweep after every
edit confirms zero remaining `temp id`/`temp-id`/`number TBD` markers
outside this file's own history notes and `future-ideas.md` #27 (a
genuinely separate, still-deferred item, correctly left alone).

## Deliberately NOT done

- **Archive rollover.** `confirmed-decisions.md` is ~154KB, well past the
  ~100KB rollover trigger `docs/decisions/README.md` documents, and has
  been repeatedly flagged as ready "once the first PENDING entry gets a
  real number" — true as of this reconciliation. Not bundled in here:
  it's a distinct, separate operation (move to
  `archive/134-152.md`, update `INDEX.md`'s file-location column for
  every one of those 19 entries, start a fresh open file) that wasn't
  explicitly confirmed. Trivial to do as an immediate follow-up now that
  every number in range is final — say the word.
- No code was touched. No test was re-run (nothing here changes runtime
  behavior).
