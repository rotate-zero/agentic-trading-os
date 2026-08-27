# TESTING — Scanner v1 frontend (GET /scanner/state + ScannerPanel)

Unzip at project root. This delivery:
- **Overwrites** `backend/app/main.py` (registers the new `scanner` router — one import line, one `include_router` line, nothing else changed) and `frontend/src/App.tsx` (mounts `<ScannerPanel />` next to `<FeatureEnginePanel />` in both workspace shells) and `frontend/src/services/api-client.ts` (appends `fetchScannerState` + its wire types — nothing existing in that file changed).
- **Adds** `backend/app/scanner/runner.py`, `backend/app/api/routes/scanner.py`, `backend/tests/test_scanner_runner.py`, `frontend/src/hooks/useScannerState.ts`, `frontend/src/components/scanner/ScannerPanel.tsx`.
- Builds on the previous scanner delivery (`app/scanner/scorer.py`, `app/scanner/universe.py`, `tests/test_scanner.py`) — this doesn't replace or change any of that.

## What this is

An on-demand `GET /scanner/state` route (not the continuous
`MarketActivityScanner` from `scanner-design.md` §5 — that's still not
built, see §10/§11 of the doc) plus a real frontend panel that polls it.
Same v1 `ActivityScorer` (unusual volume + volatility-relative move) as
before, same 6-symbol placeholder `TEST_UNIVERSE` — this delivery is
purely "give it a UI," not a scoring change.

## 1. Backend unit tests

```bash
cd backend
pytest tests/test_scanner.py tests/test_scanner_runner.py -v
```

9 tests total (6 scorer, already covered in the previous delivery; 3 new
for `run_scan`'s orchestration — descending rank, skip-not-zero for
symbols with no snapshot at all, and that display features are filtered
to only the 4 scan-relevant keys). All passing, run before this was sent.

## 2. Backend route, manually

```bash
cd backend
uvicorn app.main:app --reload
```

```bash
curl http://127.0.0.1:8000/scanner/state
curl "http://127.0.0.1:8000/scanner/state?symbols=AAPL,TSLA,NVDA"
```

Expect `{"universe": [...], "results": [...], "skipped": [...]}`. A
symbol shows up in `skipped`, not `results`, until Feature Engine has
computed at least one 1m `FeatureSet` for it — that's cold start, not a
bug.

## 3. Frontend, visually

```bash
cd frontend
npm run dev
```

Open the app — a new **Scanner** panel should appear on the right edge,
next to the existing Feature Engine panel, showing a ranked list once
data starts coming in. It polls every 15s; there's also a manual
**Refresh** button in the panel header. A `X/3` badge next to a score
means that reading came from fewer than 2 of the 3 possible inputs — low
confidence, not necessarily low activity.

## Already verified before this was sent (can't be re-verified in this environment)

- `npx tsc -b` — only the known, pre-existing `GridPresetPicker.tsx`
  errors (decision #35). Nothing new from `ScannerPanel.tsx` or
  `useScannerState.ts`.
- `npx vite build` — clean, 81 modules, no errors.
- No live browser check was possible here — visual layout/spacing next
  to `FeatureEnginePanel` is worth a look once you run it.
