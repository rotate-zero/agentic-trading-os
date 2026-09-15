# Strategy Performance Backtest default verification

The inherited `TESTING.md` was deleted before this task-specific record was written.

## Untouched GitHub `main` baseline

Fresh `main` tarball downloaded from `https://codeload.github.com/rotate-zero/agentic-trading-os/tar.gz/refs/heads/main` and extracted under `/tmp/strategy-performance-main`. Its frontend used the repository's existing `frontend/node_modules` via a temporary symlink.

- `cd /tmp/strategy-performance-main/frontend && npx tsc -b` — exit 1, exactly four decision #35 `GridPresetPicker.tsx` errors: missing `GRID_PRESETS`, missing `preset`, missing `setPreset`, and implicit-any `p`.
- `cd /tmp/strategy-performance-main/frontend && npx vite build` — exit 0, 90 modules transformed.

## Changed tree

- `cd frontend && npx tsc -b` — exit 1, the identical four `GridPresetPicker.tsx` errors and no new TypeScript errors.
- `cd frontend && npx vite build` — exit 0, 90 modules transformed.

Direct source trace: `StrategyPerformanceSummary` initializes `view` to `"backtest"`; its `isBacktest` expression is true in Backtest and false in Live; both values are passed explicitly to `usePerformanceAnalytics({ isBacktest })`. The hook's `[isBacktest]` dependency refreshes both fetches on selection change. The unchanged API client sends `is_backtest=true` and `is_backtest=false` to both routes. The header and persistent source line identify the selected provenance while loading, empty, and populated. The two empty messages have separate, mode-specific wording. The existing win-rate and expectancy row renderers remain unchanged; loading hides previous-mode figures during a refetch. No frontend test framework is configured, so this UI wiring was checked by build and direct source trace.

## Boundary comparison

Immediately before packaging, another fresh GitHub `main` tarball was extracted under `/tmp/strategy-performance-final-main`. `diff -rq --exclude=.git --exclude=node_modules --exclude=dist --exclude='*.tsbuildinfo' /tmp/strategy-performance-final-main .` found task changes only in `frontend/src/components/workspace/InfoTab.tsx`, `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, and this `TESTING.md` replacement. It also found pre-existing, uncommitted changes in `backend/tests/test_intelligence_routes.py`, `docs/architecture/trading-intelligence-architecture.md`, `docs/diagrams/README.md`, and `docs/diagrams/trading-intelligence-overview.md`; these were present in `git status` before task edits and are excluded from delivery. Local environment/cache files were also excluded from delivery. The archive itself is accounted for separately.
