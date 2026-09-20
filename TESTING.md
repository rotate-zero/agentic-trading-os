# TESTING — open-decisions-d-item-audit (decision #158)

Docs-only audit. **No code, no tests, no other `docs/architecture/*.md` touched.** Nothing here is executable, so there is no suite to run; "testing" means re-checking each claim against the code and the decision log. Commands to do that yourself are in §3.

Base: `main`, pulled fresh at task start and again immediately before writing the entry — both pulls byte-identical (`diff -rq`), `INDEX.md` last row #157, `confirmed-decisions.md` tail #157, archives `001-060` … `122-133`, all agreeing. Slug `open-decisions-d-item-audit`; number #158 assigned at delivery.

---

## 1. What changed (exactly four files)

| File | Change |
|---|---|
| `docs/architecture/strategy-engine-open-decisions.md` | **Two lines only.** Line 29 (D17's status cell — the first two cells are byte-identical) and line 4 (the header sentence listing which rows are open). Every other line is byte-identical to `main`. |
| `docs/decisions/confirmed-decisions.md` | New entry #158 appended. |
| `docs/decisions/INDEX.md` | New row #158 appended. |
| `TESTING.md` | This file (replaced, delete-first). |

`CHANGES.md` is **not** touched — the task's file list didn't include it. Add an entry if you want one; it's a one-paragraph summary of §2.

---

## 2. What the audit found

**Result: D17 was the only row whose status had been overtaken. Zero additional stale rows.**

### D17 — corrected

| | |
|---|---|
| **Said** | "Open, deliberately not resolved by decision #120 … no code anywhere resolves it either way yet." |
| **Now says** | **Resolved for the Backtest Runner path only — decision #128, option (a). The live path is still open.** #120's original reasoning is kept; a "correction history" sentence records that the old wording was accurate when written and overtaken by #128. |
| **Justified by** | Decision #128's "D17, as-built" record, **confirmed in the code, not just taken from the prompt** (below). |

How the runner actually handles it (`backend/app/backtest_runner/runner.py`, `BacktestRunner.run()`):

```
 candle i reached in replay loop
        │
        ├─ i == entry_fill.entry_candle_index ?
        │      capture_strategy_outcome_snapshots()          (real entry_filled_at)
        │         ├─ market_state OR context is None ─► DiscardedSignal("D17: entry snapshot unavailable…")
        │         │                                      pending = None   (trade voided — option (a))
        │         └─ both present ─► pending.entry_snapshots = snapshots
        │
        ├─ i == exit_fill.exit_candle_index ?
        │      capture_strategy_outcome_snapshots()          (real exit_filled_at)
        │         ├─ either is None ─► DiscardedSignal("D17: exit snapshot unavailable…")
        │         └─ both present ─► _build_strategy_outcome()  ← only ASSERTS non-None (backstop)
        │                             └─► record_strategy_outcome()   (runner.py:399, the sole caller)
        └─ pending = None either way
```

Note the handling is in `run()`, **not** in `_build_strategy_outcome()`, which only asserts. Both discard paths have Runner-level tests (`test_backtest_runner_regression.py`, entry-side and exit-side). Option (b) was **not** taken: the four snapshot fields are still non-optional `dict`s and the ORM columns still `nullable=False`, exactly as §5 locks them.

**Why "path only", not "fully resolved":** D17's own row names Execution Engine/Position Monitor as the still-hypothetical live caller, and neither exists — no such module under `backend/app/`; `record_strategy_outcome()` has exactly one importer/caller (`runner.py`); `IBKRAdapter.place_order()` still raises `NotImplementedError`; `OrderFilled` is a schema/event-type with no consumer. The live-path half of the row is exactly as open as before, and the row says so.

### Header sentence — corrected (the one non-row edit)

Said: "as of this split D1, D3, D4, D5, D6, D8, and D11 are still genuinely open." Now: D1, D3, D4, D5, D6, **D7**, D8, D11 open; **D9 and D17 resolved only in part**. The old list omitted D7 (open since #89) and D17 (open since #120) even at the #142 split. This is a count correction, not a status change. **Easy to drop** if you'd rather keep the audit strictly to rows — revert line 4 only.

### Confirmed accurate, untouched

D1, D3, D4, D5, D6, D7, D8, D9 (residual), D11 — each checked against the code *and* the full decision log. One-line evidence per row is in the decision entry. Resolved rows: status-level check; D2, D13, D14, D16 also spot-checked against code.

---

## 3. How to verify (all read-only)

```bash
# fresh tarball, as usual
curl -sL https://codeload.github.com/rotate-zero/agentic-trading-os/tar.gz/refs/heads/main | tar -xzf - --strip-components=1

# D17 — the None handling and its discard reasons
grep -n "DiscardedSignal\|D17\|capture_strategy_outcome_snapshots" backend/app/backtest_runner/runner.py

# D17 — sole caller of record_strategy_outcome() outside its own definition/tests
grep -rn "record_strategy_outcome" backend/app --include=*.py | grep -E "import|to_thread"

# D17 — live path still absent
find backend/app -iname "*execution*" -o -iname "*position*" ; grep -n "NotImplementedError" backend/app/broker_adapters/ibkr_adapter.py
grep -rn "OrderFilled" backend/app --include=*.py

# D17 — §5 fields still required
grep -nE "market_state_at_(entry|exit)|context_at_(entry|exit)" backend/app/schemas/performance.py backend/app/models/trading_intelligence.py

# footprint after applying this delivery (expect exactly the four files in §1)
diff -rq <fresh-clone> <your-tree> | grep -v "^Only in"
```

---

## 4. What wasn't covered

- **No tests run** — docs-only.
- **D4's readiness-check claims** (empty `strategy_outcomes`/`backtests`, IBKR Gateway unreachable) describe database/sandbox state outside the repo; not re-verified. Nothing in code or the log contradicts them.
- **Resolved rows** were audited at status level; only D2, D13, D14, D16 were spot-checked against code.
- **"Sole caller"** rests on a search of `backend/app/` as of this audit. A later caller (e.g. the sweep work, which would still go through `BacktestRunner`) wouldn't change the live-path-open conclusion.

---

## 5. For your call — not decided here

**D13.** D13 (Resolved: import `Opportunity` directly, don't move it into `schemas/events/`) reasoned "no cross-module consumer today — only `base_strategy.py` and the 7 strategy files." Since #128 that premise is false: `backtest_runner/fill_simulator.py` and `runner.py` both import `Opportunity` from `strategy_engine.base_strategy`. Its *status* still stands (they import directly, as D13 says), but D13's own criterion for moving the class was "a genuine cross-module consumer," so whether its revisit condition has now been met is an architecture call. Row left as written.

Noted for a future task (outside this task's file boundary, all overtaken by #128 being a real caller): `strategy-engine-design.md` §5 ("a real (unwired) writer"); docstrings in `trading_intelligence/performance.py` ("only caller today is this task's own test suite"), `models/trading_intelligence.py` ("no real caller wired yet"), `trading_intelligence/state_snapshot.py` ("no migration exists yet"), `schemas/performance.py` (D17 "deliberately UNRESOLVED"). Also D9's row names only First Pullback/Reversal as direct `get_snapshot()` callers (VWAP does too) and says "a Scheduler that doesn't exist yet" — dated wording, not a status error.

---

## 6. Manual-merge notes (parallel session)

A separate session may be building the fixture-scenario batch/sweep endpoint. File-disjoint from this delivery (it owns `backend/app/backtest_runner/` and `api/routes/backtest.py`), but three shared files can collide:

- **Decision number.** If that session lands first with #158, renumber this entry to **#159** in exactly these places: the `### 158.` header in `confirmed-decisions.md`, the `| 158 |` row in `INDEX.md`, and the three `#158` references in `strategy-engine-open-decisions.md` (line 4 once, line 29 twice — `grep -n "#158" docs/architecture/strategy-engine-open-decisions.md`). Also the "renumber to #159" sentence inside the entry's own status paragraph. Note the collision inline, per the standing protocol.
- **`confirmed-decisions.md` / `INDEX.md`:** both append-only — if the other session also appended, keep both entries and re-check the numbering; don't overwrite either.
- **`TESTING.md`:** delete-first replaces the whole file. If the other delivery also replaced it, combine as separate sections rather than picking one.
- **`CHANGES.md`:** untouched here, so no conflict from this side.
- `confirmed-decisions.md` is now ~215,000 bytes, far past the ~100KB rollover trigger — already flagged and deliberately deferred since #150. Not performed here; do it in a dedicated task when no parallel session is appending.
