# Testing this delivery — Level Interaction Engine (decision #46)

This is Step 2, on top of Step 1 (decision #45) already in your repo.

## 0. What's in this zip

New:
- `backend/app/trading_intelligence/__init__.py`
- `backend/app/trading_intelligence/level_interaction_engine.py`
- `backend/app/schemas/events/level_interaction.py`
- `backend/app/models/trading_intelligence.py`
- `backend/alembic/versions/0002_level_interaction_state_and_events.py`
- `backend/tests/test_level_interaction_engine.py`

Modified (since Step 1 — additive edits only):
- `backend/app/core/market_clock.py` — added `trading_day()`
- `backend/app/schemas/events/features.py` — added `close` to `FeatureSet` (see decision #46 for why)
- `backend/app/feature_engine/engine.py` — includes `close` in the published payload
- `backend/app/schemas/events/envelope.py` — added `LEVEL_INTERACTION_CHANGED` event type
- `backend/app/db/base.py` — registered the new model module
- `backend/app/core/config.py` — added `trading_intelligence_aura_pct`
- `backend/app/main.py` — wires `LevelInteractionEngine` into the lifespan

Docs: `system-design.md`, `confirmed-decisions.md` (decision #46), `phase-roadmap.md` — all updated.

Unzip and copy `backend/` and `docs/` on top of your repo root, same as Step 1.

## 1. Run the migration

```bash
cd agentic-trading-os/backend
source venv/bin/activate
alembic upgrade head
```

Should apply `0002` on top of `0001`. Confirm the two new tables exist:

```bash
psql -d <your_db> -c "\dt level_interaction_*"
```

## 2. Run the full suite

```bash
pytest -v
```

**Expect: 108 passed** (98 from Step 1 + 10 new in `test_level_interaction_engine.py`). This is the "pytest on both updates" you asked for — it's one suite, so a regression in either engine, or in how they interact through the shared `close` field, would show up here.

To run just the new tests:

```bash
pytest tests/test_level_interaction_engine.py -v
```

Worth reading if only a couple: `test_restart_survival_mid_touch` (persists a touch via one engine instance, resolves it correctly via a **brand-new** instance with zero in-memory state — proves the DB backfill path actually works) and `test_gap_through_is_always_conquered` (the edge case where price jumps straight through the Aura with no candle ever closing inside it).

## 3. Confirm it wires into the real app

```bash
uvicorn app.main:app --reload
```

Startup log should now show three engines, in order:

```
CandleRecorder started — persisting CandleClosed events to Postgres
FeatureEngine started — computing SMA[9, 20, 50] on 1m CandleClosed
LevelInteractionEngine started — aura=0.200% on FeaturesUpdated
```

## 4. Two things I made a judgment call on — flagged in decision #46, worth your read

1. **Gap-through anchor price.** When price jumps straight from below the Aura to above it (or vice versa) with no candle ever closing inside — I anchor `distance_pct` to the *arrival* candle's level value, not the level value from just before the gap. Reasonable either way; confirm this matches what you had in mind.
2. **Cold-start-unknown-origin.** If this process's very first observation of a level is already inside the Aura, there's no known entry side — I leave that touch's outcome unclassified (`status="unclassified"`) rather than guessing. Should only matter right after a restart, and only for whichever symbols happen to be mid-touch at that exact moment.

Neither blocks using this — just flagging per your own "customize per your understanding, then tell me" instruction from earlier.

## 5. What's *not* covered yet

Same caveat as Step 1: no real market tick has flowed through either engine. Everything above is synthetic events on a real bus against real Postgres — the next real checkpoint is watching `LevelInteractionChanged` fire during an actual live session.

Also not built (deliberately, flagged in decision #46): a live "still holding, updating every candle" event — right now you only get an event at touch start and at resolution, not a running update while a touch is still open. Easy follow-up if you want that.
