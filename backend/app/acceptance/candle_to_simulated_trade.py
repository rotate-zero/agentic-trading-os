"""Candle-to-simulated-trade acceptance: closed candles -> real producers -> real Gap strategy -> outcome.

WHAT THIS PROVES
    One deterministic, SYNTHETIC path, driven through the REAL FastAPI lifespan (`app.main`) against a
    real PostgreSQL database, in which nothing between the candles and the strategy is injected:

        CandleClosed (1m, supplied) --> FeatureEngine --> FeaturesUpdated (calculated)
                                              |-----------> MarketStateEngine --> MarketStateChanged (calculated)
        StrategyScheduler + GapStrategy.evaluate()  --> OpportunityCreated (strategy-generated, structural levels)
        --> AuthorizerStub --> ExecutionEngine --> SimulatedVenue --> Portfolio State --> Position Monitor
        (target) --> ExecutionEngine exit --> close fill --> OutcomeRecorder --> exactly one strategy_outcomes row

    plus a contrasting candle sequence (same warm-up, same trend/volume regime, NO opening gap) that
    reaches the strategy through the same producers and produces no opportunity and no entry.

WHY GAP
    Gap is a registered production strategy whose setup is fully determined by candles plus one supplied
    prior-day reference: `regular_open` (first regular-session candle's open) and `gap_pct` are calculated by
    FeatureEngine, `trend_score`/`volume_regime_score` by MarketStateEngine, and Gap's own MATCH needs a
    2% gap, trend >= 60 and volume regime >= 45 on a candle inside the first 60 minutes. It fires on a
    single candle, so the run is short and the numbers are reviewable by hand. Its default configuration and
    the existing `StrategyScheduler.default_registry` seam are used unchanged, and the numbers
    (open 95, close 100, prior close 92) deliberately equal the S6 scenario of the existing
    simulated-MVP acceptance, so the only difference from S6 is that nothing upstream is injected.

WHAT IS SUPPLIED (inputs) vs WHAT IS CALCULATED (outputs under test)
    SUPPLIED  - the 1m CandleClosed OHLCV for the prior session (warm-up) and the trading day, published on
                the Event Bus (the same event TickIngestBridge publishes); CandleRecorder persists them;
              - synthetic daily (1d) history served by `FixtureCandleProvider` through the existing
                `broker_registry` historical role (average daily volume and daily ATR baselines);
              - the ContextEngine's per-symbol evaluation (all external providers disabled);
              - PriceUpdated reference/trigger observations and SimulatedVenue ticks (entry fill, target);
              - the wall clock for the authorizer, venue and Position Monitor.
    CALCULATED - `pdc`, `regular_open`, `gap_dollars`, `gap_pct`, `sma_20`, `sma_20_slope_angle`,
                `session_volume`, `rvol` (FeatureEngine); `trend_score`, `volume_regime_score`
                (MarketStateEngine); the Opportunity and its structural invalidation/target (GapStrategy).
    `pdc` is calculated from the supplied prior-session candles, and `rvol` from the supplied daily history,
    so those two are calculated *from* supplied inputs; the milestones recompute them independently.

WHAT THIS DOES NOT PROVE
    Tick acquisition (no ticks, no TickIngestBridge/CandleAggregator), real-feed coverage, strategy
    profitability, opportunity ranking, or broker execution. It is a synthetic candle-to-trade path.

HOW IT STAYS HONEST
    * Never publishes FeaturesUpdated, MarketStateChanged or OpportunityCreated; the module has no code path
      that does, and the milestones fail if the producers do not.
    * Never inserts a ledger or outcome row. The only substituted pieces are inputs and clocks, exactly as in
      the existing simulated-MVP acceptance (which supplies the controlled-input patches); this command also
      leaves the production outcome-snapshot capture UNPATCHED.
    * Requires an explicitly selected, disposable, migrated, EMPTY PostgreSQL database and never truncates
      or cleans anything. The password is never printed.

EXIT CODES: 0 PASS, 1 a milestone failed, 2 a precondition failed (nothing started), 3 watchdog expired.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from app.acceptance import simulated_mvp as base
from app.acceptance.simulated_mvp import (
    GAP_STRATEGY,
    GAP_TARGET,
    GAP_VERSION,
    STOP_LEVEL,
    AcceptanceFailure,
    Checker,
    Report,
    _LogRing,
)

SCOPE_STATEMENT = (
    "Scope: synthetic closed 1m candles drive the REAL FeatureEngine and MarketStateEngine; the real "
    "StrategyScheduler/Gap strategy generates the opportunity; the SIMULATED authorizer -> execution -> "
    "SimulatedVenue -> Portfolio State -> Position Monitor -> OutcomeRecorder path records one outcome against "
    "PostgreSQL. This proves a synthetic candle-to-trade path, NOT tick acquisition, real-feed coverage, "
    "profitability or broker execution."
)

# --- supplied inputs ---------------------------------------------------------------------------
# 2026-09-17 (Thu) and 2026-09-18 (Fri) are consecutive NYSE sessions in EDT: 09:30 ET == 13:30Z.
PRIOR_SESSION_OPEN = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)
SESSION_OPEN = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)

SYMBOL_GAP = "ZZCTGP"        # opening gap + trend + volume: the selected setup is present
SYMBOL_NO_GAP = "ZZCTNG"     # identical warm-up/regime, opens at the prior close: the setup is absent

WARMUP_CANDLES = 45          # >= 2*20-1 closes, so sma_20 and its slope are warm on the first day candle
WARMUP_STEP = 0.10           # prior-session close rises $0.10 per candle -> a genuine up-trend into the gap
PRIOR_CLOSE = 92.0           # the supplied prior session's last close == the calculated `pdc`
WARMUP_VOLUME = 1_000

GAP_OPEN = 95.0              # first regular-session open for SYMBOL_GAP: gap = +3.00 (3.26%)
NO_GAP_OPEN = 92.1           # first regular-session open for SYMBOL_NO_GAP: gap = +0.10 (0.11%) < 2%
DAY_HIGH, DAY_CLOSE, DAY_VOLUME = 100.2, 100.0, 5_000
GAP_LOW, NO_GAP_LOW = 94.9, 92.0
DEFAULT_MIN_GAP_PCT, TREND_THRESHOLD, VOLUME_THRESHOLD = 2.0, 60.0, 45.0   # Gap v1 defaults (asserted, not set)
RVOL_LOOKBACK_DAYS, SESSION_MINUTES = 5, 390


def _candle(symbol_ts: datetime, open_: float, high: float, low: float, close: float, volume: int) -> dict:
    return {"timeframe": "1m", "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "candle_ts": symbol_ts}


def prior_session_candles() -> list[dict]:
    """The supplied warm-up: a gentle, deterministic up-trend whose LAST close is PRIOR_CLOSE."""
    candles: list[dict] = []
    previous: float | None = None
    for i in range(WARMUP_CANDLES):
        close = round(PRIOR_CLOSE - WARMUP_STEP * (WARMUP_CANDLES - 1 - i), 2)
        open_ = previous if previous is not None else round(close - WARMUP_STEP, 2)
        candles.append(_candle(PRIOR_SESSION_OPEN + timedelta(minutes=i), open_,
                               round(max(open_, close) + 0.05, 2), round(min(open_, close) - 0.05, 2),
                               close, WARMUP_VOLUME))
        previous = close
    return candles


def session_candle(symbol: str) -> dict:
    """The one supplied trading-day candle. The two symbols differ ONLY in the opening print (and low)."""
    if symbol == SYMBOL_GAP:
        return _candle(SESSION_OPEN, GAP_OPEN, DAY_HIGH, GAP_LOW, DAY_CLOSE, DAY_VOLUME)
    return _candle(SESSION_OPEN, NO_GAP_OPEN, DAY_HIGH, NO_GAP_LOW, DAY_CLOSE, DAY_VOLUME)


def expected_gap(symbol: str) -> tuple[float, float, float]:
    """(gap_dollars, gap_pct, regular_open) recomputed independently from the SUPPLIED candles."""
    regular_open = session_candle(symbol)["open"]
    dollars = regular_open - prior_session_candles()[-1]["close"]
    return dollars, 100.0 * dollars / PRIOR_CLOSE, regular_open


def expected_sma20() -> float:
    closes = [c["close"] for c in prior_session_candles()] + [DAY_CLOSE]
    return sum(closes[-20:]) / 20.0


def fmt(value: Any, digits: int = 4) -> str:
    """Format a possibly-absent calculated value for a milestone note without raising on absence."""
    return "absent" if value is None else f"{value:.{digits}f}"


def parse_ts(value: Any) -> datetime:
    ts = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class CandleAcceptance(base.Acceptance):
    """Reuses the simulated-MVP acceptance's helpers (ledger/API reads, drains, entry, protective close)
    and replaces ONLY the opportunity source: candles + real producers instead of injected events."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.evaluations: list[tuple[str, datetime, bool]] = []   # (symbol, candle_ts, produced_opportunity)
        self.daily_volumes: list[int] = []
        self.published_by_harness: list[str] = []                 # event types THIS module put on the bus

    # --- controlled inputs: as the simulated-MVP acceptance, but with REAL outcome-snapshot capture ------
    @contextlib.contextmanager
    def controlled_inputs(self):
        production_capture = self.d.governor_module.capture_strategy_outcome_snapshots
        with super().controlled_inputs():
            self.d.governor_module.capture_strategy_outcome_snapshots = production_capture
            yield

    @contextlib.contextmanager
    def historical_input(self):
        """Supply synthetic daily history through the existing `broker_registry` historical role."""
        from app.backtest_runner.fixture_daily_history import build_daily_history_candles
        from app.backtest_runner.fixture_provider import FixtureCandleProvider

        history = build_daily_history_candles(SESSION_OPEN.date())
        self.daily_volumes = [int(c.volume) for c in history]
        provider = FixtureCandleProvider({(s, "1d"): history for s in (SYMBOL_GAP, SYMBOL_NO_GAP)})
        self.d.broker_registry.set_historical_provider(provider)
        try:
            yield
        finally:
            self.d.broker_registry.clear_historical_provider()

    @contextlib.contextmanager
    def recording_gap_strategy(self):
        """Isolate Gap via the scheduler's registry seam and RECORD (never alter) its evaluate() results."""
        d, outer = self.d, self
        with self.gap_only_registry():
            original = d.scheduler_module.default_registry

            def registry(active_from):
                strategies = original(active_from)
                for strategy in strategies:
                    real_evaluate = strategy.evaluate

                    async def recording(symbol, market_state, features, context, _real=real_evaluate):
                        result = await _real(symbol, market_state, features, context)
                        outer.evaluations.append((symbol, features.candle_ts, result is not None))
                        return result

                    strategy.evaluate = recording
                return strategies

            d.scheduler_module.default_registry = registry
            try:
                yield
            finally:
                d.scheduler_module.default_registry = original

    # --- supplied inputs ------------------------------------------------------------------------------
    def publish(self, envelope: Any) -> None:
        """Every event this harness puts on the bus goes through here, so the run can prove which types it supplied
        (the base class's `send_price` and this class's `publish_candle` both route through it)."""
        self.published_by_harness.append(str(envelope.event_type))
        super().publish(envelope)

    def publish_candle(self, symbol: str, candle: dict) -> None:
        from app.event_bus.events import make_envelope
        from app.schemas.events.market_data import CandleClosed

        self.publish(make_envelope(self.d.EventType.CANDLE_CLOSED, CandleClosed(**candle), symbol=symbol))

    def events_of(self, event_type: Any, symbol: str, *, timeframe: str | None = "1m") -> list[Any]:
        found = []
        for env in list(self.events):
            if env.event_type != event_type or env.symbol != symbol:
                continue
            if timeframe is not None and env.payload.get("timeframe") != timeframe:
                continue
            found.append(env)
        return found

    def event_at(self, event_type: Any, symbol: str, candle_ts: datetime) -> Any | None:
        return next((e for e in self.events_of(event_type, symbol)
                     if parse_ts(e.payload["candle_ts"]) == candle_ts), None)

    def drain_ingest(self, timeout: float = 10.0) -> None:
        """Bounded drain of CandleRecorder's and FeatureEngine's queues (then the shared bus drain)."""
        import asyncio

        from app.feature_engine.engine import get_feature_engine

        engine = get_feature_engine()
        recorder_queues = [engine._queue]
        recorder = getattr(self.d.app.state, "candle_recorder", None)
        if recorder is not None:
            recorder_queues.append(recorder._queue)

        async def bounded():
            async def drain():
                for queue in recorder_queues:
                    await queue.join()
            await asyncio.wait_for(drain(), timeout)

        self.client.portal.call(bounded)
        self.settle(timeout)

    # --- milestones -----------------------------------------------------------------------------------
    def check_scheduler_isolation(self) -> None:
        d, c = self.d, self.c
        strategies = d.scheduler_module.get_strategy_scheduler()._strategies
        c.check("lifespan-installed scheduler has only the real registered Gap v1 and its default configuration",
                len(strategies) == 1 and isinstance(strategies[0], d.GapStrategy)
                and strategies[0].config == d.gap_default_config(strategies[0].config.active_from)
                and strategies[0].config.params["min_gap_pct"] == DEFAULT_MIN_GAP_PCT
                and strategies[0].config.params["trend_score_threshold"] == TREND_THRESHOLD
                and strategies[0].config.params["volume_regime_threshold"] == VOLUME_THRESHOLD,
                lambda: f"registered={[(s.name, s.config.version) for s in strategies]}")

    def warm_up(self) -> None:
        """Supply the prior session's candles for both symbols and wait for the real producers to drain them."""
        c, d = self.c, self.d
        warmup = prior_session_candles()
        c.check("supplied warm-up: prior-session candles end at the reference close and rise into it",
                len(warmup) == WARMUP_CANDLES and warmup[-1]["close"] == PRIOR_CLOSE
                and all(b["close"] > a["close"] for a, b in zip(warmup, warmup[1:])),
                lambda: f"first={warmup[0]['close']} last={warmup[-1]['close']}",
                note=f"{WARMUP_CANDLES} candles, {warmup[0]['close']} -> {warmup[-1]['close']}")
        for symbol in (SYMBOL_GAP, SYMBOL_NO_GAP):
            for candle in warmup:
                self.publish_candle(symbol, candle)

        def warm_state():
            return {s: len(self.events_of(d.EventType.FEATURES_UPDATED, s)) for s in (SYMBOL_GAP, SYMBOL_NO_GAP)}

        c.wait("FeatureEngine publishes one 1m FeaturesUpdated per supplied warm-up candle, for both symbols",
               lambda: all(n == WARMUP_CANDLES for n in warm_state().values()), warm_state)
        self.drain_ingest()
        from app.services import candle_store

        def persisted():
            end = PRIOR_SESSION_OPEN + timedelta(hours=8)
            return {s: len(candle_store.get_recorded_candles(s, "1m", PRIOR_SESSION_OPEN, end))
                    for s in (SYMBOL_GAP, SYMBOL_NO_GAP)}

        c.wait("CandleRecorder has persisted the prior session (the source of the calculated `pdc`)",
               lambda: all(n == WARMUP_CANDLES for n in persisted().values()), persisted)

    def feed_session_candle(self, symbol: str) -> tuple[Any, Any]:
        """Supply the trading-day candle; wait for the REAL FeaturesUpdated and MarketStateChanged for it."""
        c, d = self.c, self.d
        candle = session_candle(symbol)
        self.clock.now = SESSION_OPEN + timedelta(minutes=1)   # the candle has closed; the session is regular
        self.client.portal.call(d.get_context_engine().evaluate_for_symbol, symbol)   # controlled context
        self.publish_candle(symbol, candle)
        ts = candle["candle_ts"]
        c.wait(f"{symbol}: FeatureEngine calculates and publishes FeaturesUpdated for the supplied session candle",
               lambda: self.event_at(d.EventType.FEATURES_UPDATED, symbol, ts) is not None,
               lambda: f"1m FeaturesUpdated seen={len(self.events_of(d.EventType.FEATURES_UPDATED, symbol))}")
        c.wait(f"{symbol}: MarketStateEngine calculates and publishes MarketStateChanged for the same candle",
               lambda: self.event_at(d.EventType.MARKET_STATE_CHANGED, symbol, ts) is not None,
               lambda: f"1m MarketStateChanged seen={len(self.events_of(d.EventType.MARKET_STATE_CHANGED, symbol))}")
        return (self.event_at(d.EventType.FEATURES_UPDATED, symbol, ts),
                self.event_at(d.EventType.MARKET_STATE_CHANGED, symbol, ts))

    def check_calculated_inputs(self, symbol: str, features_env: Any, state_env: Any) -> dict:
        """Calculated features/state vs values recomputed independently from the SUPPLIED candles/history."""
        c = self.c
        f = features_env.payload["features"]
        state = state_env.payload
        dollars, pct, regular_open = expected_gap(symbol)
        avg_volume = sum(self.daily_volumes[-RVOL_LOOKBACK_DAYS:]) / RVOL_LOOKBACK_DAYS
        expected_rvol = DAY_VOLUME / (avg_volume * 1 / SESSION_MINUTES)   # elapsed floored at 1 minute
        c.check(f"{symbol}: calculated gap features equal the values recomputed from the supplied candles",
                f.get("pdc") == PRIOR_CLOSE and f.get("regular_open") == regular_open
                and math.isclose(f.get("gap_dollars", math.nan), dollars, abs_tol=1e-6)
                and math.isclose(f.get("gap_pct", math.nan), pct, abs_tol=1e-4),
                lambda: f"calculated={ {k: f.get(k) for k in ('pdc', 'regular_open', 'gap_dollars', 'gap_pct')} } "
                        f"expected={(PRIOR_CLOSE, regular_open, dollars, pct)}",
                note=f"pdc={f.get('pdc')} regular_open={f.get('regular_open')} gap_pct={fmt(f.get('gap_pct'))}")
        c.check(f"{symbol}: calculated sma_20, session_volume and rvol equal independent recomputation; "
                "trend slope is warm",
                math.isclose(f.get("sma_20", math.nan), expected_sma20(), abs_tol=1e-4)
                and f.get("session_volume") == DAY_VOLUME
                and math.isclose(f.get("rvol", math.nan), expected_rvol, rel_tol=1e-4)
                and f.get("sma_20_slope_angle") is not None,
                lambda: f"sma_20={f.get('sma_20')} (expected {expected_sma20():.6f}) "
                        f"session_volume={f.get('session_volume')} rvol={f.get('rvol')} (expected {expected_rvol:.4f})",
                note=f"rvol={fmt(f.get('rvol'), 3)} sma_20_slope_angle={fmt(f.get('sma_20_slope_angle'), 3)}")
        c.check(f"{symbol}: calculated market state clears the trend and volume gates "
                f"(trend_score >= {TREND_THRESHOLD:g}, volume_regime_score >= {VOLUME_THRESHOLD:g})",
                state["trend_score"] >= TREND_THRESHOLD and state["volume_regime_score"] >= VOLUME_THRESHOLD,
                lambda: f"market_state={state}",
                note=f"trend={fmt(state['trend_score'], 1)} volume={fmt(state['volume_regime_score'], 1)}")
        return f

    def opportunity_from_candles(self, symbol: str) -> None:
        """The `opportunity_input` for `enter_position`: supply the candle, then require the strategy's own output."""
        c, d = self.c, self.d
        features_env, state_env = self.feed_session_candle(symbol)
        f = self.check_calculated_inputs(symbol, features_env, state_env)
        c.wait("Gap evaluate() causes StrategyScheduler to publish OpportunityCreated (never injected here)",
               lambda: len(self.gap_events(symbol)) == 1, lambda: f"events={len(self.gap_events(symbol))}")
        opportunity = d.Opportunity.model_validate(self.gap_events(symbol)[0].payload)
        conditions = opportunity.evidence.get("conditions", {})
        state = state_env.payload
        c.check("opportunity carries Gap v1, the calculated conditions and recorded structural levels",
                opportunity.strategy == GAP_STRATEGY and opportunity.version == GAP_VERSION
                and opportunity.direction == "BUY" and opportunity.status == "actionable"
                and (opportunity.structural_invalidation, opportunity.structural_target) == (STOP_LEVEL, GAP_TARGET)
                and opportunity.setup_detected_at == SESSION_OPEN
                and conditions.get("pdc") == f.get("pdc") and conditions.get("regular_open") == f.get("regular_open")
                and conditions.get("gap_pct") == f.get("gap_pct") and conditions.get("gap_dollars") == f.get("gap_dollars")
                and conditions.get("close") == DAY_CLOSE
                and conditions.get("trend_score") == state["trend_score"]
                and conditions.get("volume_regime_score") == state["volume_regime_score"],
                lambda: f"opportunity={opportunity.model_dump(mode='json')}",
                note=f"stop={opportunity.structural_invalidation} target={opportunity.structural_target}")
        evaluated = [e for e in self.evaluations if e[0] == symbol]
        c.check("the registered Gap strategy's evaluate() ran on the calculated inputs and returned the proposal",
                evaluated and evaluated[-1] == (symbol, SESSION_OPEN, True), lambda: f"evaluations={evaluated}")

    def check_no_synthetic_inputs(self) -> None:
        """The harness supplied only CandleClosed and PriceUpdated; the upstream events the strategy consumed were
        produced by application workers (and were observed on the bus for the traded symbol)."""
        c, d = self.c, self.d
        supplied = set(self.published_by_harness)
        allowed = {str(d.EventType.CANDLE_CLOSED), str(d.EventType.PRICE_UPDATED)}
        produced = {name: len(self.events_of(et, SYMBOL_GAP, timeframe=None)) for name, et in
                    (("FeaturesUpdated", d.EventType.FEATURES_UPDATED),
                     ("MarketStateChanged", d.EventType.MARKET_STATE_CHANGED),
                     ("OpportunityCreated", d.EventType.OPPORTUNITY_CREATED))}
        c.check("the harness published only CandleClosed and PriceUpdated; FeaturesUpdated, MarketStateChanged and "
                "OpportunityCreated were all produced by application workers",
                supplied == allowed and all(n > 0 for n in produced.values()),
                lambda: f"harness published {sorted(supplied)}; producer events seen for {SYMBOL_GAP}: {produced}",
                note=f"harness types={sorted(t.split('.')[-1] for t in supplied)}; producer events={produced}")

    # --- scenarios ------------------------------------------------------------------------------------
    def positive(self) -> None:
        c, d = self.c, self.d
        c.begin("C1", "Scenario C1 — candles -> real FeatureEngine/MarketStateEngine -> real Gap -> "
                      "simulated trade -> outcome")
        self.clock.now = SESSION_OPEN
        self.expect_startup_ready()
        self.check_scheduler_isolation()
        flat = self.portfolio()
        c.check("Portfolio State starts flat (GET /intelligence/portfolio-state)",
                flat is not None and flat["positions"] == [] and flat["open_position_count"] == 0, lambda: f"{flat}")
        self.warm_up()
        ev = self.enter_position(SYMBOL_GAP, label="Gap", strategy_name=GAP_STRATEGY,
                                 opportunity_input=self.opportunity_from_candles, target_level=GAP_TARGET)
        self.expect_public_open_position(ev)
        trade = self.trade(SYMBOL_GAP, GAP_STRATEGY)
        c.check("approved trade retains Gap strategy/version and the strategy-recorded structural thesis",
                trade.strategy_version == GAP_VERSION
                and (trade.thesis.get("structural_invalidation"), trade.thesis.get("structural_target"))
                == (STOP_LEVEL, GAP_TARGET),
                lambda: f"version={trade.strategy_version} thesis={trade.thesis}")
        self.protective_close(ev, reason="target", level=GAP_TARGET, trigger_tick=111.0, fill_price=111.0,
                              label="Gap target")

        def recorded() -> bool:
            with d.SessionLocal() as s:
                row = s.get(d.Trade, ev.trade_id)
                return row.outcome_status == "recorded" and row.outcome_id is not None

        c.wait("OutcomeRecorder records and links the candle-originated trade", recorded)
        self.settle()
        rows = self.outcomes(ev.trade_id, strategy_name=None)
        trade = self.trade(SYMBOL_GAP, GAP_STRATEGY)
        c.check("exactly one linked simulated Gap v1 outcome preserves strategy and structural attribution",
                len(rows) == 1 and trade.outcome_id == rows[0].outcome_id
                and rows[0].strategy_name == GAP_STRATEGY and rows[0].strategy_version == GAP_VERSION
                and rows[0].execution_mode == "simulated" and rows[0].is_backtest is False
                and rows[0].exit_reason == "target"
                and (float(rows[0].structural_invalidation), float(rows[0].structural_target))
                == (STOP_LEVEL, GAP_TARGET),
                lambda: f"outcomes={len(rows)} trade_link={trade.outcome_id}"
                        + (f" outcome={rows[0].outcome_id}" if rows else ""))
        entry_state = rows[0].market_state_at_entry
        c.check("outcome entry snapshot was captured from the real MarketStateEngine (production capture unpatched)",
                bool(entry_state) and entry_state.get("trend_score", 0) >= TREND_THRESHOLD,
                lambda: f"market_state_at_entry={entry_state}")
        self.check_no_synthetic_inputs()

    def contrast(self) -> None:
        c, d = self.c, self.d
        c.begin("C2", "Scenario C2 — contrasting candle sequence (no opening gap): no opportunity, no entry")
        trades_before = self.count_trades()
        features_env, state_env = self.feed_session_candle(SYMBOL_NO_GAP)
        f = self.check_calculated_inputs(SYMBOL_NO_GAP, features_env, state_env)
        c.check("contrast: the gap is below Gap's 2% minimum while trend and volume gates are cleared "
                "(the setup alone is missing)",
                f.get("gap_pct") is not None and f["gap_pct"] < DEFAULT_MIN_GAP_PCT,
                lambda: f"gap_pct={f.get('gap_pct')}", note=f"gap_pct={fmt(f.get('gap_pct'))}")
        self.send_price(SYMBOL_NO_GAP, DAY_CLOSE)   # same reference-price input the positive run supplied
        self.settle()                               # deterministic barrier: every queue drained, handlers finished
        c.check("contrast: the real Gap strategy evaluated the calculated inputs and returned no proposal",
                (SYMBOL_NO_GAP, SESSION_OPEN, False) in self.evaluations, lambda: f"evaluations={self.evaluations}")
        orders = self.api("/intelligence/execution-orders", symbol=SYMBOL_NO_GAP)["orders"]
        positions = self.api("/intelligence/execution-positions", symbol=SYMBOL_NO_GAP)["positions"]
        c.check("contrast: after the barrier there is no OpportunityCreated, authorized trade, entry order or position",
                not self.gap_events(SYMBOL_NO_GAP) and self.trade(SYMBOL_NO_GAP, GAP_STRATEGY) is None
                and not orders and not positions and self.count_trades() == trades_before,
                lambda: f"opportunities={len(self.gap_events(SYMBOL_NO_GAP))} orders={len(orders)} "
                        f"positions={len(positions)} trades={self.count_trades()} (before {trades_before})")
        self.venue_tick(SYMBOL_NO_GAP, DAY_CLOSE)
        self.settle()
        c.check("contrast: a venue observation at the same price still creates no fill or position",
                not self.api("/intelligence/execution-fills", symbol=SYMBOL_NO_GAP)["fills"]
                and not self.api("/intelligence/execution-positions", symbol=SYMBOL_NO_GAP)["positions"],
                "a fill or position exists for the contrast symbol")

    def count_trades(self) -> int:
        with self.d.SessionLocal() as s:
            return s.query(self.d.Trade).count()

    def run(self) -> None:
        self._import()
        with self.controlled_inputs():
            with self.recording_gap_strategy():
                with self.lifespan():
                    with self.historical_input():
                        self.positive()
                        self.contrast()


# =================================================================================================
# CLI
# =================================================================================================
PROVENANCE = (
    "Inputs SUPPLIED: 1m CandleClosed (prior session warm-up + one session candle per symbol), synthetic 1d history "
    "(FixtureCandleProvider via broker_registry), ContextEngine evaluation with providers disabled, PriceUpdated "
    "and SimulatedVenue observations, wall clock.",
    "Outputs CALCULATED: pdc, regular_open, gap_dollars, gap_pct, sma_20, sma_20_slope_angle, session_volume, rvol "
    "(FeatureEngine); trend_score, volume_regime_score (MarketStateEngine); the opportunity and its structural "
    "levels (GapStrategy via StrategyScheduler).",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candle_to_simulated_trade_acceptance",
        description="Prove a synthetic closed-candle -> real FeatureEngine/MarketStateEngine -> real Gap strategy -> "
                    "simulated trade -> outcome path against a disposable, migrated, EMPTY PostgreSQL database. "
                    + SCOPE_STATEMENT,
        epilog="Select the database with POSTGRES_HOST/PORT/DB/USER/PASSWORD (environment or backend/.env) and "
               "repeat its name in --database. The command never truncates or cleans a database.")
    parser.add_argument("--database", required=False, metavar="NAME",
                        help="name of the disposable database (must equal POSTGRES_DB and contain "
                             "'acceptance', 'disposable', 'scratch' or 'test')")
    parser.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS",
                        help="bound for each observed-milestone wait (default 20)")
    parser.add_argument("--watchdog", type=float, default=300.0, metavar="SECONDS",
                        help="overall time limit; a hang is reported as a failure (default 300)")
    return parser


def main(argv: Sequence[str] | None = None, *, out: Any = None,
         inspect: Callable[[str], tuple[str | None, dict[str, int]]] | None = None,
         head: Callable[[], str] | None = None,
         runner: Callable[[CandleAcceptance], None] | None = None) -> int:
    """Run the acceptance. `inspect`/`head`/`runner` are injection seams for the unit tests only."""
    args = build_parser().parse_args(argv)
    report = Report(out=out)
    report.line("Candle-to-simulated-trade acceptance")
    report.line(SCOPE_STATEMENT)
    for line in PROVENANCE:
        report.line(line)

    # --- preconditions: nothing below starts an application worker ---
    base.apply_safe_environment()
    from app.core.config import get_settings

    settings = get_settings()
    report.secrets = (settings.postgres_password,)
    report.line(f"Target database: {base.describe_target(settings)} (password not shown)")
    checker = Checker(report, wait_timeout=args.timeout)
    checker.begin("P", "Preconditions (read-only; no application worker is started)")

    problems = base.validate_target_selection(args.database, settings.postgres_db)
    if problems:
        for text in problems:
            report.fail("P.1", "disposable database explicitly selected", text)
        report.line()
        report.line("RESULT: FAIL (precondition P.1: database selection) — nothing was started or modified")
        return 2
    report.ok("P.1", "disposable database explicitly selected", f"database={settings.postgres_db}")

    problems = base.validate_settings_for_scenarios(settings)
    if problems:
        for text in problems:
            report.fail("P.2", "simulated execution settings admit the scenario", text)
        report.line("RESULT: FAIL (precondition P.2: settings) — nothing was started or modified")
        return 2
    report.ok("P.2", "external providers disabled; simulated execution only; limits admit one entry at 100",
              "FINNHUB/POLYGON keys blanked, execution_mode=simulated")

    try:
        head_revision = (head or base.alembic_head_revision)()
        revision, counts = (inspect or base.inspect_database)(settings.database_url)
    except Exception as exc:  # unreachable database, bad credentials, ...
        report.fail("P.3", "database reachable and migrated", f"{type(exc).__name__}: {exc}")
        report.line("RESULT: FAIL (precondition P.3: database connection) — nothing was started or modified")
        return 2
    problems = base.evaluate_database_state(revision, head_revision, counts)
    migration_problems = [text for kind, text in problems if kind == "migration"]
    empty_problems = [text for kind, text in problems if kind == "empty"]
    if migration_problems:
        for text in migration_problems:
            report.fail("P.3", "database migrated to head", text)
        report.line("RESULT: FAIL (precondition P.3: database not migrated) — nothing was started or modified")
        return 2
    report.ok("P.3", "database migrated to head", f"revision={revision}")
    if empty_problems:
        for text in empty_problems:
            report.fail("P.4", "database is empty before any worker starts", text)
        report.line("RESULT: FAIL (precondition P.4: database not empty) — nothing was started or modified")
        return 2
    report.ok("P.4", "database is empty before any worker starts",
              f"{len(counts)} tables checked; only the migration's own 6-symbol scanner seed present")

    # --- scenarios ---
    ring = _LogRing()
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.addHandler(ring)
    root_logger.setLevel(logging.INFO)
    watchdog = base._start_watchdog(args.watchdog, report)
    acceptance = CandleAcceptance(report, checker, settings, ring)
    started = time.monotonic()
    exit_code = 0
    try:
        (runner or CandleAcceptance.run)(acceptance)
    except AcceptanceFailure as failure:
        exit_code = 1
        report.line()
        report.line(f"RESULT: FAIL — failed milestone {failure.milestone_id}: {failure.title}")
        if failure.detail:
            report.line(f"  detail: {failure.detail}")
        if ring.records:
            report.line("  recent application warnings/errors (most recent last):")
            for record in list(ring.records)[-8:]:
                report.line(f"    {record}")
    except Exception as exc:  # an unexpected crash is also a failure, with the milestone in progress named
        exit_code = 1
        report.line()
        report.line(f"RESULT: FAIL — unexpected {type(exc).__name__} during milestone {report.current}: {exc}")
    finally:
        watchdog.cancel()
        root_logger.removeHandler(ring)
        root_logger.setLevel(previous_level)
    if exit_code == 0:
        report.line()
        reads = sum(acceptance.api_reads.values())
        report.line(f"RESULT: PASS — {len(report.passed)} milestones passed in {time.monotonic() - started:.1f}s; "
                    f"{reads} public API reads across {len(acceptance.api_reads)} routes")
    for line in PROVENANCE:
        report.line(line)
    report.line(SCOPE_STATEMENT)
    report.line("The database is left as evidence and is not cleaned. Recreate a fresh migrated database before rerunning.")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
