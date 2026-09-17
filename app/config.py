"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration.

    Secrets default to empty strings; use validate_for_runtime() before bot startup.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    owner_telegram_id: int = 795063564
    database_url: str = "postgresql+asyncpg://aibot:aibot@localhost:5432/aibot"
    master_encryption_key: str = ""
    app_base_url: str = "https://localhost"
    telegram_proxy: str | None = None
    gemini_base_url: str = "https://extraordinary-piroshki-4e3b92.netlify.app"
    alibaba_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    log_level: str = "INFO"
    session_token_ttl_seconds: int = 900

    def validate_for_runtime(self) -> list[str]:
        """Return names of env vars that are required for bot startup but missing."""
        missing: list[str] = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.master_encryption_key:
            missing.append("MASTER_ENCRYPTION_KEY")
        return missing

    @property
    def is_configured(self) -> bool:
        """True when all settings required for runtime are present."""
        return not self.validate_for_runtime()


@lru_cache
def get_settings() -> Settings:
    """Return a cached process-wide Settings instance."""
    return Settings()
