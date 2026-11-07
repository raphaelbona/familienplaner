from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Familienplaner API"
    secret_key: str = "change-me-in-.env"
    access_token_expire_minutes: int = 60 * 24 * 14

    database_url: str = "postgresql+psycopg://familienplaner:familienplaner@localhost:5432/familienplaner"
    redis_url: str = "redis://localhost:6379/0"

    hosting_mode: str = "self_hosted"  # "ch_cloud" | "self_hosted"
    cors_origins: list[str] = ["http://localhost:5173"]

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    vision_provider: Literal["anthropic", "ollama"] = "anthropic"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    ollama_base_url: str = "http://ollama:11434"
    ollama_vision_model: str = "llama3.2-vision"
    ollama_text_model: str = "llama3.2"

    recipe_search_enabled: bool = True
    recipe_search_domains: str = "chefkoch.de"
    llm_recipe_check_enabled: bool = False

    upload_dir: str = "/app/uploads"

    calendar_sync_poll_minutes: int = 15
    # Interval of the Google import job (the other sync jobs keep calendar_sync_poll_minutes).
    google_sync_poll_minutes: int = 15

    # Timezone all-day events from external calendars are anchored in (IANA name).
    app_timezone: str = "Europe/Zurich"

    # Used to build links in outgoing emails (e.g. password reset)
    app_base_url: str = "http://localhost:8090"

    # Optional: if unset, password-reset links are logged instead of emailed
    # (see SELF_HOSTING.md) -- keeps the flow usable without an SMTP relay.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    # Web Push (see SELF_HOSTING.md for how to generate a VAPID key pair).
    # Push stays a no-op wherever these are unset.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@familienplaner.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
