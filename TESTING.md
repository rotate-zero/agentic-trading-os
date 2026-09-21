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
