"""Application settings, read from environment variables (and `.env` when present)."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_KEYS = 2


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required by the spec
    llm_api_keys: str = ""
    database_url: str = "sqlite:///./data/app.db"
    allowed_origins: str = "http://localhost:3000"
    enable_failure_simulation: bool = False

    # LLM
    llm_model: str = "groq/openai/gpt-oss-20b"
    llm_models: str = ""  # optional, one model per key, comma-separated
    llm_api_base: str | None = None
    llm_timeout_seconds: float = 30.0
    default_system_prompt: str = "You are a helpful, concise assistant."
    history_max_messages: int = 0  # 0 = send the whole history

    # Cache
    cache_type: Literal["local", "disk"] = "local"
    cache_dir: str = "/data/llm-cache"
    cache_ttl_seconds: int = 86_400
    cache_max_items: int = 1_000

    # Misc
    enable_docs: bool = False
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check(self) -> Settings:
        # Messages must never echo key values: they end up in startup logs.
        if len(self.api_keys) < MIN_KEYS:
            raise ValueError(f"LLM_API_KEYS must contain at least {MIN_KEYS} comma-separated keys")
        models = _split_csv(self.llm_models)
        if models and len(models) != len(self.api_keys):
            raise ValueError("LLM_MODELS must list exactly one model per key in LLM_API_KEYS")
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must look like sqlite:////absolute/path.db or sqlite:///relative.db")
        return self

    @cached_property
    def api_keys(self) -> list[str]:
        return _split_csv(self.llm_api_keys)

    @cached_property
    def models(self) -> list[str]:
        """Model string for each key, in key order."""
        return _split_csv(self.llm_models) or [self.llm_model] * len(self.api_keys)

    @cached_property
    def origins(self) -> list[str]:
        return _split_csv(self.allowed_origins)

    @cached_property
    def database_path(self) -> Path:
        # sqlite:////data/app.db -> /data/app.db ; sqlite:///./app.db -> ./app.db
        return Path(self.database_url.removeprefix("sqlite:///"))


class ConfigError(RuntimeError):
    pass


def load_settings(**overrides: object) -> Settings:
    """Build settings, turning validation errors into a short message without input values."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from None
