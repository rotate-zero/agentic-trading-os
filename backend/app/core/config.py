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

    # --- CORS (frontend dev server) ---
    cors_allow_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
