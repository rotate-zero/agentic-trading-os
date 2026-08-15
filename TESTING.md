# Testing this delivery — feedback fixes from your first browser session (decision #49)

Step 5, on top of Steps 1–4 already in your repo. Thanks for the real click-through — this is exactly the feedback that's most useful.

## 0. What's in this zip

Modified:
- `backend/app/trading_intelligence/level_interaction_engine.py` — `get_snapshot()` redesigned, `seconds_in_zone`/`distance_pct` always present now
- `backend/tests/test_level_interaction_engine.py` — updated + one new test
- `backend/tests/test_intelligence_routes.py` — updated for the new shape
- `frontend/src/services/api-client.ts` — wire types updated to match
- `frontend/src/components/intelligence/FeatureEnginePanel.tsx` — always shows duration/distance, widened resize handle

Docs: `confirmed-decisions.md` (decision #49).

Unzip and copy `backend/` and `frontend/` and `docs/` on top of your repo root.

## 1. The three things, in order

**Timeframe stuck at 1m** — expected, not a bug. Feature Engine only computes `1m` right now (decisions #45/#46). Extending it is real, already-flagged, deferred work.

**Resize handle** — widened from 6px to 10px, bumped its priority, and made it faintly visible at rest instead of invisible until hover. I'm being direct about this: I compared it line-by-line against Info's own handle and the logic is identical, so I can't be certain this is actually the root cause without a real browser to test in. **Please tell me if this fixes it** — and if you get a moment, try dragging Info's own handle too. If that one's also stiff, it tells us this was never specific to the new panel.

**Missing variables** — real gap, fixed properly. `zone`, `touches today`, `distance`, and `time in zone` now all show regardless of whether the level is currently being held or just sitting steadily above/below. Distance now shows the SMA-as-zero percentage you asked about in both cases — anchored to where the touch began while actively holding, live against the current SMA value otherwise (details in decision #49 if you want the reasoning).

**Time format:** shipped both together — e.g. `4m 12s (4 candles)` — rather than picking one. Reasoning's in decision #49; happy to change if you'd rather have just one.

## 2. Run it

```bash
cd agentic-trading-os/backend
source venv/bin/activate
pytest -q
```
**Expect: 114 passed.**

```bash
cd ../frontend
npx tsc -b 2>&1 | grep -v "GridPresetPicker"   # expect empty
npx vite build                                  # expect success
npm run dev
```

Then the same as before — backend running, frontend running, type a symbol into the Feature Engine panel.

## 3. What I still can't verify myself

Same limitation as last time: no browser here. Everything above is confirmed via `pytest`, `tsc`, and `vite build`, plus a real backend boot confirming the new response shape — but not confirmed by actually looking at it or dragging anything. Your next look is what actually closes this out.
