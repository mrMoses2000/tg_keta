"""
tg_keto.config — Application settings loaded from environment variables.

All secrets and configuration are read from .env via pydantic-settings.
No hardcoded values except safe defaults.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    Central configuration.
    Reads from .env file at project root (or environment variables).
    """

    # -- Telegram -------------------------------------------------------
    telegram_bot_token: str = Field(..., description="Bot API token from @BotFather")
    telegram_webhook_secret: str = Field(..., description="Secret for X-Telegram-Bot-Api-Secret-Token header")

    # -- Webhook --------------------------------------------------------
    webhook_mode: str = Field("ngrok", description="'ngrok' or 'domain'")
    webhook_domain: str = Field("https://localhost", description="Public HTTPS domain for webhook")
    webhook_port: int = Field(8080, description="Local port for the webhook HTTP server")
    webhook_path: str = Field("/webhook", description="URL path for incoming updates")

    # -- Supabase (recipes + users, via REST) ---------------------------
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_service_role_key: str = Field(..., description="Supabase service_role key (bypasses RLS)")

    # -- Local Postgres (state tables) ----------------------------------
    postgres_host: str = Field("localhost")
    postgres_port: int = Field(5432)
    postgres_db: str = Field("keto_bot")
    postgres_user: str = Field("keto_bot")
    postgres_password: str = Field("change_me_in_production")

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # -- Redis ----------------------------------------------------------
    redis_url: str = Field("redis://localhost:6379/0")

    # -- LLM ------------------------------------------------------------
    llm_cli_command: str = Field("gemini", description="CLI binary name (gemini or codex)")
    llm_cli_flags: str = Field("-p", description="Flags before the prompt text")
    llm_output_format: str = Field("json", description="--output-format value if supported")
    max_llm_concurrency: int = Field(1, ge=1, le=10)
    llm_timeout_seconds: int = Field(60, ge=10, le=300)

    # -- Behaviour ------------------------------------------------------
    bot_language: str = Field("ru")
    knowledge_mode: str = Field("off", description="'off' or 'on'")
    send_typing_indicator: bool = Field(True)
    send_placeholder_message: bool = Field(True)
    max_context_messages: int = Field(10, ge=1, le=50)
    recipe_cache_ttl_seconds: int = Field(300, ge=60, le=3600)

    # -- Logging --------------------------------------------------------
    log_level: str = Field("INFO")
    log_format: str = Field("json", description="'json' or 'text'")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Singleton — import this everywhere
settings = Settings()
