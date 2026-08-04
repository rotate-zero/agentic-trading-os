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


@lru_cache
def get_settings() -> Settings:
    return Settings()
