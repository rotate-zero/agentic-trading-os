# TESTING — decision-number reconciliation (#143–#152)

**Docs/comments-only delivery.** No application code changed, no runtime
behavior changed — nothing here needs `pytest` or `npx tsc -b`/`vite
build`. Verification is about correctness of the renumbering itself.

## How this was verified

1. **Exact-match substitution, not regex/fuzzy replace.** Every one of
   the ~35 edits (10 headers, 10 number-paragraphs, 7 `INDEX.md` row
   renumbers + 3 new rows, ~19 forward-reference sites) was applied via
   a Python script asserting the target string occurs in the file
   **exactly once** before replacing it — a zero-match or multi-match
   result aborts the whole run rather than editing the wrong spot. All
   ~35 assertions passed cleanly on every run. One wording defect (the
   first entry's note read "...packaged): . Assigned..." — an empty
   predecessor list, not a match failure) was caught by reading the
   actual output before packaging, not by the assertion; `git checkout`
   reverted the one affected file and the template was fixed before
   re-running.

2. **Sequential-numbering check.** `grep -oE '^### [0-9]+\.'
   docs/decisions/confirmed-decisions.md` produces exactly
   `134, 135, ..., 152` with no gaps or repeats.
   `grep -oE '^\| [0-9]+ \|' docs/decisions/INDEX.md | sort -n | uniq -d`
   is empty (no duplicate numbers across the whole index, not just the
   new range).

3. **Zero-PENDING check.** `grep -c '\[PENDING' confirmed-decisions.md`
   and `grep -c PENDING INDEX.md` are both `0` after the change (were
   10 and 7 respectively before).

4. **Repo-wide sweep for stragglers.** `grep -rln -E "temp id|temp-id|
   temp delivery id|number TBD"` across every `.md`/`.ts`/`.tsx`/`.py`
   file, before and after. Before: 20 files. After: only
   `confirmed-decisions.md` (this reconciliation's own "packaged under
   temp id X" history notes — intentional, same as #136's own "drafted
   and cited throughout as #135" precedent), `future-ideas.md` (future
   idea #27's own still-genuinely-unassigned `ibkr-broker-panel-
   validation-deferred`/`-results` temp ids — correctly untouched, not
   part of this ten-way collision), and
   `docs/architecture/ibkr-broker-panel-validation.md` (same #27 track).

5. **Python syntax check.** `python3 -m py_compile` on the four touched
   `.py` files (`composite.py`, `test_world_view.py`,
   `test_feature_engine.py`, `test_websocket_channels.py`) — all clean.
   The four touched `.ts`/`.tsx` files got comment-only edits (verified
   by reading each diff directly, not assumed) — no import, type, or
   runtime-code lines touched, so a `tsc -b`/`vite build` pass wasn't
   expected to catch anything a direct read didn't already confirm; not
   run, since this sandbox's frontend toolchain cost is better spent
   when actual code changes.

6. **`git diff --stat`** confirms exactly the 15 files this delivery's
   own footprint claims and nothing else: `backend/app/world_view/
   composite.py`, `backend/tests/test_feature_engine.py`,
   `backend/tests/test_websocket_channels.py`,
   `backend/tests/test_world_view.py`,
   `docs/architecture/backtest-runner-design.md`,
   `docs/architecture/feature-engine-chart-migration.md`,
   `docs/architecture/system-design.md`,
   `docs/architecture/trading-intelligence-architecture.md`,
   `docs/decisions/INDEX.md`, `docs/decisions/confirmed-decisions.md`,
   `docs/decisions/future-ideas.md`,
   `frontend/src/components/backtest/BacktestPanel.tsx`,
   `frontend/src/components/backtest/easternTime.ts`,
   `frontend/src/hooks/useMarketState.ts`,
   `frontend/src/services/api-client.ts`.

## How to re-verify after unzipping

```bash
grep -c '\[PENDING' docs/decisions/confirmed-decisions.md   # expect 0
grep -c PENDING docs/decisions/INDEX.md                      # expect 0
grep -oE '^### [0-9]+\.' docs/decisions/confirmed-decisions.md
grep -rln -E "temp id|temp-id|number TBD" \
  --include="*.md" --include="*.ts" --include="*.tsx" --include="*.py" .
```
