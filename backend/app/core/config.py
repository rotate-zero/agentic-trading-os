"""
Settings, env loading. Single source of truth for configuration —
nothing else in the app should read os.environ directly.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Trading Workspace API"
    debug: bool = False

    # --- Database (plain PostgreSQL — see docs/decisions/confirmed-decisions.md #2) ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "trading_workspace"
    postgres_user: str = "trading"
    postgres_password: str = "trading"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # --- Market Clock ---
    market_timezone: str = "America/New_York"

    # --- IBKR (Phase 3) ---
    # Default port 4002 = IB Gateway PAPER trading. 4001 = Gateway live,
    # 7497 = TWS paper, 7496 = TWS live. Paper is the default on purpose —
    # switching to a live port is an explicit, deliberate choice, not an
    # accident of leaving a default in place.
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 1

    # --- Polygon.io (Phase 3 — free/Basic tier: 15-min delayed, 5 REST calls/min) ---
    # No WebSocket on this tier — PolygonAdapter polls instead. See
    # docs/decisions/confirmed-decisions.md #30. Optional: the app boots
    # fine with this unset, it just skips auto-connecting Polygon.
    polygon_api_key: str | None = None
    polygon_poll_interval_seconds: int = 60  # matches the underlying 1-min bar granularity — polling faster wastes budget for no new data
    polygon_max_calls_per_minute: int = 5

    # --- Finnhub (Phase 3 — free tier: genuine real-time WebSocket streaming,
    # but historical stock candles are paywalled: confirmed 403 on free keys,
    # not assumed). See docs/decisions/confirmed-decisions.md #32.
    finnhub_api_key: str | None = None
    finnhub_max_calls_per_minute: int = 60

    # --- CORS (frontend dev server) ---
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    # --- Feature Engine (Phase 4) ---
    # SMA periods computed on every 1m CandleClosed and published as
    # FeaturesUpdated (feature_engine/engine.py). Also the period set used
    # for 5m/15m/1h (confirmed decision #51) — one shared list rather than
    # a per-timeframe config, since nothing today needs them to differ;
    # revisit if that stops being true.
    feature_engine_sma_periods: list[int] = [9, 20, 50]

    # EMA periods (confirmed decision #52) — 9/20 match the two legacy
    # chart presets (WorkspaceContext.tsx's EMA9/EMA20), not copied from
    # feature_engine_sma_periods, since chart usage never included EMA50.
    feature_engine_ema_periods: list[int] = [9, 20]
    # How many periods' worth of history EMA seeds itself over before
    # publishing — see indicators/ema.py::ema()'s own docstring for the
    # convergence math this default is based on.
    feature_engine_ema_seed_multiplier: int = 5

    # --- Feature Engine: aggregated timeframes (5m/15m/1h — confirmed decision #51) ---
    # How far back to look, on cold start only, when backfilling the
    # rolling SMA window for an aggregated timeframe via
    # candle_aggregator.aggregate_from_recorded(). Generous on purpose —
    # 1h's largest configured period (50) needs 50 PRIOR closed 1h bars,
    # which at ~6.5h of regular session per trading day spans well over a
    # week of calendar time. Live-boundary bucket completion (the common
    # case, not cold start) never uses this — see engine.py's module
    # docstring.
    feature_engine_aggregated_lookback_days: int = 30

    # --- Feature Engine: previous-day levels (PDH/PDL/PDC, Camarilla — confirmed decision #56) ---
    # How far back to search for "the most recent FULLY ELAPSED trading
    # day" (candle_ts) — generous for the same reason as the setting
    # above: skipping a long weekend or holiday cluster still needs to
    # find a real prior day within this window. Unlike that setting, this
    # one is never used for warm-up convergence — either a previous day
    # exists somewhere in this window or PDH/PDL/PDC/Camarilla are simply
    # absent (same "not enough history yet" state
    # frontend/src/indicators/sessions.ts's own getPreviousTradingDayCandles()
    # already has and documents).
    feature_engine_previous_day_lookback_days: int = 10

    # --- Feature Engine: Daily Levels (confirmed decision #59; daily-levels-design.md) ---
    # How many days of 1D candle history to cluster. NOT yet empirically
    # verified against a real Polygon key (design doc §2 / decision #59's
    # D1 — no network access to Polygon exists in the environment this
    # default was written in). 180 is the spec's own starting point;
    # Saqib's own fallback is 90 if 180 turns out to hit depth/rate-limit
    # problems in practice — change this one value, nothing else, if so.
    daily_levels_lookback_days: int = 180
    # The clustering "aura" — two points count toward the same level if
    # they're within this fraction of the cluster's own eventual average
    # (indicators/daily_levels.py's corrected whole-cluster test, not a
    # naive stale-average check — see that module's docstring for why the
    # distinction matters). 0.002 = 0.2%, the spec's own number.
    daily_levels_cluster_pct: float = 0.002
    # §1.1's same-candle validity gate — a cluster must draw from at
    # least this many DISTINCT 1D candles to count as a real level, even
    # though `strength` itself counts total points (open+close), not
    # candles. Prevents one small-range/doji candle's own open and close
    # from manufacturing a "level" by itself.
    daily_levels_min_distinct_candles: int = 2
    # Day-over-day proximity-reconciliation tolerance for level identity
    # (design doc §4) — NOT YET USED. Stage 1 (the current build) mints a
    # fresh level_id every day rather than reconciling against yesterday's
    # survivors; this setting exists now so Stage 2 has a config knob
    # ready rather than needing a second config-file round trip, but
    # nothing reads it yet. Starting default matches the clustering
    # tolerance above — day-over-day drift and within-cluster spread are
    # different questions that happen to share a starting value, not
    # proven to need the same one; revisit once Stage 2 can observe real
    # drift.
    daily_levels_identity_match_pct: float = 0.002

    # --- Feature Engine: ATR (confirmed decisions #67/#68; feature-engine-indicator-expansion.md) ---
    # Wilder ATR period, over 1D bars — the timeframe itself is NOT
    # configurable (deliberately, per the original design brief: "we
    # intentionally do not want an intraday ATR as part of this initial
    # feature set" — hardcoded "1d" in engine.py, not a second setting
    # here). 14 is the spec's own starting point, same "one value to
    # change, nothing else" shape as daily_levels_lookback_days above.
    feature_engine_atr_period: int = 14

    # --- Feature Engine: Linear Regression + KAMA (confirmed decisions #67/#68; feature-engine-indicator-expansion.md §4) ---
    # `list[dict]`, not a flat `list[int]` like SMA/EMA above — regression
    # and KAMA need independent (timeframe, period[, ...]) pairs where the
    # TIMEFRAME list itself is indicator-specific (both start scoped to
    # 1m+5m only, unlike SMA/EMA's uniform fan-out across every timeframe
    # this engine computes). This is the single source of truth for both
    # which periods AND which timeframes — FeatureEngine.__init__ parses
    # and validates each entry (period >= 2, timeframe non-empty) rather
    # than this file doing it, matching Daily Levels' own precedent of
    # keeping config.py itself minimal and putting real structure at the
    # point of use.
    feature_engine_regression_configs: list[dict] = [
        {"timeframe": "1m", "period": 9},
        {"timeframe": "5m", "period": 9},
    ]
    # `er_period` alone doesn't fully define classic Kaufman KAMA — the
    # original design brief flagged this itself — so `fast_period`/
    # `slow_period` are explicit fields here, not hardcoded constants
    # inside engine.py.
    feature_engine_kama_configs: list[dict] = [
        {"timeframe": "1m", "er_period": 9, "fast_period": 2, "slow_period": 30},
        {"timeframe": "5m", "er_period": 9, "fast_period": 2, "slow_period": 30},
    ]
    # Mirrors feature_engine_ema_seed_multiplier's role, applied to
    # `slow_period` instead of `period` — the parameter that drives the
    # longest memory in KAMA's recursion (indicators/kama.py's own
    # docstring has the full reasoning).
    feature_engine_kama_seed_multiplier: int = 5

    # --- Feature Engine: Relative Volume (confirmed decision #71) ---
    # Average DAILY volume lookback, in complete trading days strictly
    # before today — same "strictly prior" convention PDC/ATR already
    # use. Resolved as 5 (not the 7 initially mentioned) directly by
    # Saqib. Settings-only, no constructor override — same precedent as
    # feature_engine_atr_period above (5 is already small enough for
    # tests to use directly).
    feature_engine_rvol_lookback_days: int = 5

    # --- Pre-market volume ratio (docs/architecture/premarket-accumulator-design.md) ---
    # Separate from feature_engine_rvol_lookback_days on purpose, even
    # though both default to 5 — regular-session RVOL and pre-market
    # volume ratio are DIFFERENT baselines (a symbol's daily volume vs.
    # its own pre-market volume aren't the same distribution), so tuning
    # one shouldn't silently move the other.
    feature_engine_premarket_lookback_days: int = 5

    # --- Trading Intelligence: Level Interaction Engine (confirmed decision #46) ---
    # Aura width as a fraction (0.002 = 0.2%), applied uniformly to every
    # level_key FeatureEngine publishes. Per-level-type override is real
    # follow-up work if some level types need a different width later.
    trading_intelligence_aura_pct: float = 0.002

    # --- Scanner: Activity Scorer (docs/architecture/scanner-design.md §2/§8) ---
    # v1 composite score = rvol + ATR-normalized |gap_pct| + ATR-normalized
    # |session_pct_change|. Spread tightness deliberately absent — needs
    # live L1 bid/ask, gated on the IBKR market data subscription (§9),
    # not built yet.
    #
    # gap/session_change weighted to 0.0 as of 2026-08-27, Saqib's call:
    # score on RVOL alone for now while testing the pipeline. Both terms
    # still exist and run in scorer.py — a symbol's `inputs_available`
    # count still reflects whether gap_pct/session_pct_change actually
    # had data, even though neither currently moves the score. Flip
    # these back above 0 to bring them back into the ranking; no code
    # change needed.
    scanner_weight_rvol: float = 1.0
    scanner_weight_gap: float = 0.0
    scanner_weight_session_change: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
