# CHANGES — pending decision (temp id: `chart-migration-stage-4-flagging`) — Stage 4 of the chart migration executed, nothing to flag

**Decision number intentionally not assigned** — see this delivery's entry in `docs/decisions/confirmed-decisions.md` for why, and for the number to assign at merge time (143, per a three-source check at packaging time, unless another parallel session lands first — a six-way collision, the widest yet in this log).

**No parallel deliveries landed on `main` during this task's own session** — re-pulled and diffed immediately before writing; zero change from the task-start pull.

`feature-engine-chart-migration.md` §7 (Stage 4) asked to confirm the seven frontend indicator files (`sma.ts`, `ema.ts`, `vwap.ts`, `previousDayLevels.ts`, `premarketLevels.ts`, `camarillaPivots.ts`, `vpoc.ts`) have zero remaining callers now that Feature Engine computes all seven server-side, and flag (not delete) whichever are confirmed dead. **They don't have zero remaining callers.** All seven are still directly, and routinely, called by `utils/indicators.ts`'s own dispatcher as a real local-fallback path — this delivery is the investigation and its write-up, not a code change, because there was nothing to flag.

## What changed

- **`docs/architecture/feature-engine-chart-migration.md`** — Stage 0's Status line and §7 (Stage 4)'s checklist both updated to state the actual finding (0 of 7 files dead, and why) rather than the doc's prior framing, which read as though Stage 3's completion had left Stage 4 simply waiting to be done.
- **`docs/decisions/future-ideas.md`** — new entry #26, recording the real trigger condition for ever revisiting this: D4/Stage 5 resolved as 5a (backend computes any requested SMA/EMA/VWAP period, not just the fixed default list) would remove that reason for 3 of the 7 files; the other 4 (the horizontal-level files) have no trigger at all, since their fallback reason (no previous trading day yet, for a newly-scanned symbol) is structural, not a coverage gap.
- **`docs/decisions/confirmed-decisions.md`** / **`INDEX.md`** — this delivery's own entry, with the full per-sub-item (4.1–4.4) reasoning.

## What did not change

- **Every file under `frontend/src/indicators/`** — `sma.ts`, `ema.ts`, `vwap.ts`, `previousDayLevels.ts`, `premarketLevels.ts`, `camarillaPivots.ts`, `vpoc.ts`, `sessions.ts`, `types.ts`. None flagged with a comment, none deleted, none edited at all — there was no dead file among the seven to apply either treatment to.
- **`frontend/src/utils/indicators.ts`** — the dispatcher itself was read closely (it's the entire basis for this delivery's finding) but not edited.
- Nothing under `backend/` — out of scope per this task's own explicit file boundaries, and irrelevant regardless (this task never needed backend changes).
- `App.tsx`, `api-client.ts`, and every file belonging to the three pending frontend panels this task's own prompt named as parallel work (broker, data-feed status, Market State) — untouched and unrelated.

## Why this reads as a finding, not a completed checklist

The task's own instructions anticipated this outcome directly: "if you find one of the seven is NOT actually fully superseded yet... say so explicitly and exclude it from flagging, don't force it to fit." The actual finding is a uniform version of that same case, across all seven at once rather than one exception among them — each still has a real, structural reason (either an unbounded user-configurable period, or an unavoidable cold-start gap) that the completion of Stage 3's backend coverage doesn't and can't close by itself. Decisions #54 and #58 already said as much, in nearly the same words, when Stage 1 first built this fallback pattern — this delivery re-confirmed it directly against the current tree rather than assuming either the doc's older text or a fresh guess, and updated the one place (`feature-engine-chart-migration.md`'s own Status line) that had started to read as though the answer might have changed.
