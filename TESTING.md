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

<!-- Previous delivery record retained below. -->

# TESTING — decision #166: explicitly exclude the deferred GridPresetPicker sketch

## Baseline and validation

Starting repository: `/home/rotate_zero/projects/agentic-trading-os`, branch
`main`, commit `1fd925bef115233bcae812d2fade0e7db5238b2b`, clean and equal to
`origin/main` after the required fetch. The refreshed `main` already included
the parallel sweep-filter delivery at decision #165.

Before the change, `cd frontend && npx tsc -b` exited 1 with exactly these four
known decision-#35 errors:

- `src/components/workspace/GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.`
- `src/components/workspace/GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.`
- `src/components/workspace/GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.`
- `src/components/workspace/GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.`

After the change, `cd frontend && npx tsc -b` passed with zero TypeScript
errors, and `npm run build` passed (`tsc -b && vite build`). The compiler file
list from `npx tsc --noEmit --listFiles -p tsconfig.json` contains no
`GridPresetPicker.tsx`.

## Reachability and integrity

Before and after editing, `rg -n "GridPresetPicker" frontend/src` reported only
the component's own declaration/file path; no application file imports it. The
component therefore remains unreachable, and TypeScript will still include it if
a live file imports it in the future.

The start SHA-256 for
`frontend/src/components/workspace/GridPresetPicker.tsx` was
`cfae235139c8a7c1f270db32a54a80fb4e2c5ba13381e4adbad1813bb5e89599`; the final
hash is identical. `GridPicker.tsx`, `WorkspaceContext.tsx`, and
`types/workspace.ts` also remain unchanged. The exact compiler exclusion is:

```json
"exclude": ["src/components/workspace/GridPresetPicker.tsx"]
```

No strictness, emit, library-check, dependency, script, live-source, or backend
behavior was changed. Workspace preset save/export was deliberately not
implemented, the sketch was not repaired or deleted, and no backend tests were
run because no backend code changed.

## Collision, continuity, and footprint

The required initial fetch found `HEAD == origin/main` at the start commit. A
final collision fetch was performed immediately before decision-log numbering;
the parallel sweep-filter delivery was already landed and its bookkeeping was
refreshed rather than overwritten. The final check compared all seven allowed
files with their start versions, then confirmed the refreshed decision index/log
tail and archive ranges were contiguous and non-overlapping before assigning the
next decision number.

`git diff --check` passed. Markdown links in edited files resolve. The complete
diff contains only intentional changes, and the exact changed-file footprint is:

1. `frontend/tsconfig.json`
2. `backend/README.md`
3. `docs/decisions/future-ideas.md`
4. `docs/decisions/INDEX.md`
5. `docs/decisions/confirmed-decisions.md`
6. `CHANGES.md`
7. `TESTING.md`

The delivery archive contains exactly these seven root-relative files and no
wrapper directory, build output, cache, logs, node modules, Git metadata, or
unchanged files.
