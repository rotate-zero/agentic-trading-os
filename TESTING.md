# TESTING — decision #66 (Camarilla panel-grouping fix + after-hours feed-status tool)

## What's in this zip

Unzip directly into your project root — paths already match `agentic-trading-os`'s
own layout, nothing to move around:

```
backend/app/api/routes/intelligence.py   — _parse_level_key Camarilla fix
backend/app/api/routes/market.py         — new GET /market/feed-status route
backend/app/services/candle_store.py     — new get_latest_recorded_candle()
backend/tests/test_intelligence_routes.py  — 2 existing assertions updated for the new nesting
backend/tests/test_intelligence_helpers.py — NEW FILE, 4 pure-function tests
backend/tests/test_market_routes.py        — 2 new tests for /market/feed-status
docs/decisions/confirmed-decisions.md      — decision #66 appended
frontend/src/hooks/useIntelligenceState.ts — period-sort fix for non-numeric periods
```

Only these 8 files changed. No migration, no new config, no `pip`/`npm`
package changes.

## Backend

```bash
cd backend
source .venv/bin/activate   # or however you activate your venv
pytest -q
```

Expect **181 passed** (up from 172). Nothing here needs a fresh migration —
no schema changes in this delivery.

If you want to eyeball the actual shape of both fixes against a real
running process:

```bash
uvicorn app.main:app --reload
# in another terminal:
curl "http://localhost:8000/intelligence/state?symbol=AAPL" | python3 -m json.tool
# look for a top-level "camarilla" unit under timeframes.1m.units, with
# "pp"/"r1"-"r4"/"s1"-"s4" nested inside it — not nine separate flat
# "cam_pp"/"cam_r1"/... units like before.

curl "http://localhost:8000/market/feed-status?symbol=AAPL"
# {"symbol": "AAPL", "market_session": "...", "checked_at": "...",
#  "latest_recorded_candle_ts": null or a timestamp,
#  "staleness_seconds": null or a number}
```

## Frontend

```bash
cd frontend
npx tsc -b     # expect only the pre-existing, already-flagged GridPresetPicker.tsx errors — nothing new
npx vite build # expect a clean build
```

No visual change to check — the Camarilla family will just render as one
grouped "CAMARILLA" accordion in the Feature Engine panel instead of nine
separate ones once you're looking at a symbol with previous-day levels
computed, but I haven't click-through tested this in a real browser (no
browser available in this environment — same standing gap every panel
change carries).

## The actual #44 verification — still needs you, not code

`GET /market/feed-status` is a diagnostic tool, not the empirical check
itself — nothing in my sandbox has a route to Finnhub. To actually answer
"does the feed keep delivering through 20:00 ET," during a real trading
day:

1. Have the backend running with Finnhub connected and a symbol subscribed
   (streaming) sometime after ~4:00 PM ET.
2. Every few minutes through the after-hours window, run:
   ```bash
   curl "http://localhost:8000/market/feed-status?symbol=<your symbol>"
   ```
3. `staleness_seconds` should stay small (roughly 60-ish, one candle-width)
   the whole way to 20:00 if the feed is genuinely live that long. If it
   starts climbing well before 20:00 and never comes back down, that's the
   feed stopping early — the actual finding #44 has been waiting on.

Whatever you see, let me know and I'll log it as its own decision entry
the same way D1 got logged once you ran that one.
