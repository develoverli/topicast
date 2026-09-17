"""Runtime configuration.

Two sources:

* **Environment** (`TOPICAST_*`) — process-level settings and secrets (`Settings`).
* **YAML file** — bots, chats, aliases and webhook templates (`AppConfig`).
  Values may reference environment variables with `${VAR}` or `${VAR:-default}`.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_FILE = Path("config.yaml")
DEFAULT_DATA_DIR = Path("data")
MIN_SECRET_KEY_LENGTH = 32
DEFAULT_BOT = "default"

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"

Name = Annotated[str, Field(pattern=_NAME_PATTERN)]


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


class Level(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Settings(BaseSettings):
    """Process settings, read from `TOPICAST_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="TOPICAST_", extra="ignore")

    secret_key: SecretStr = Field(
        description="Pepper used to hash API keys. Changing it invalidates every key.",
    )
    config_file: Path = DEFAULT_CONFIG_FILE
    data_dir: Path = DEFAULT_DATA_DIR
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    log_format: Literal["json", "console"] = "json"
    docs_enabled: bool = True
    metrics_enabled: bool = True
    wait_timeout: float = Field(default=30.0, gt=0, le=120)
    max_upload_mb: int = Field(default=50, ge=1, le=2000)
    retention_days: int = Field(default=7, ge=1)
    idempotency_hours: int = Field(default=24, ge=1)
    poll_interval: float = Field(default=1.0, gt=0, le=60)
    max_attempts: int = Field(default=5, ge=1, le=50)
    trusted_proxies: str = "127.0.0.1"
    cors_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Browser origins allowed to call the API, comma separated. "
            "Empty (the default) disables CORS. The wildcard is rejected on purpose."
        ),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _check_origins(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for origin in value:
            if origin == "*":
                msg = (
                    "TOPICAST_CORS_ORIGINS does not accept '*': any website could then use a "
                    "visitor's API key. List the origins explicitly."
                )
                raise ValueError(msg)
            parsed = urlparse(origin.rstrip("/"))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
                msg = f"invalid origin '{origin}': use scheme://host[:port], e.g. https://panel.example.com"
                raise ValueError(msg)
            cleaned.append(f"{parsed.scheme}://{parsed.netloc}")
        return cleaned

    @model_validator(mode="after")
    def _check_secret(self) -> Self:
        if len(self.secret_key.get_secret_value()) < MIN_SECRET_KEY_LENGTH:
            msg = f"TOPICAST_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters"
            raise ValueError(msg)
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / "topicast.db"

    @property
    def spool_dir(self) -> Path:
        return self.data_dir / "spool"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BotConfig(_Strict):
    token: SecretStr
    rate_per_second: float = Field(default=30.0, gt=0)


class ChatConfig(_Strict):
    id: int | str = Field(description="Numeric chat id (e.g. -100123...) or @channelusername.")

    @field_validator("id", mode="before")
    @classmethod
    def _numeric_ids_stay_numeric(cls, value: Any) -> Any:
        """`${CHAT_ID}` arrives as a string; keep numeric ids as ints so groups are detected."""
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        return value

    bot: Name = DEFAULT_BOT
    rate_per_minute: float | None = Field(
        default=None,
        gt=0,
        description="Messages per minute. Defaults to 20 for groups, 60 otherwise.",
    )

    @property
    def is_group(self) -> bool:
        return isinstance(self.id, int) and self.id < 0

    @property
    def effective_rate_per_minute(self) -> float:
        if self.rate_per_minute is not None:
            return self.rate_per_minute
        return 20.0 if self.is_group else 60.0


class AliasConfig(_Strict):
    chat: Name
    topic: int | None = Field(default=None, ge=1)
    dedupe_window: int | None = Field(default=None, ge=0)
    silent_levels: frozenset[Level] | None = None
    description: str | None = None


class Defaults(_Strict):
    dedupe_window: int = Field(default=60, ge=0, description="Seconds. 0 disables dedupe.")
    silent_levels: frozenset[Level] = frozenset({Level.INFO, Level.SUCCESS})
    level_prefix: dict[Level, str] = Field(
        default_factory=lambda: {
            Level.INFO: "ℹ️",
            Level.SUCCESS: "✅",
            Level.WARNING: "⚠️",
            Level.ERROR: "❌",
            Level.CRITICAL: "🚨",
        }
    )


class GitHubHookConfig(_Strict):
    secret: SecretStr | None = Field(
        default=None,
        description="Webhook secret. When set, X-Hub-Signature-256 is required and verified.",
    )
    events: frozenset[str] | None = Field(
        default=None, description="Allowed event names. Default: all supported events."
    )


class HooksConfig(_Strict):
    templates: dict[Name, str] = Field(default_factory=dict)
    github: GitHubHookConfig = GitHubHookConfig()


class AppConfig(_Strict):
    bots: dict[Name, BotConfig]
    chats: dict[Name, ChatConfig]
    aliases: dict[Name, AliasConfig]
    defaults: Defaults = Defaults()
    hooks: HooksConfig = HooksConfig()

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        errors: list[str] = []
        for name, chat in self.chats.items():
            if chat.bot not in self.bots:
                errors.append(f"chats.{name}.bot references unknown bot '{chat.bot}'")
        for name, alias in self.aliases.items():
            if alias.chat not in self.chats:
                errors.append(f"aliases.{name}.chat references unknown chat '{alias.chat}'")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def dedupe_window(self, alias: str) -> int:
        value = self.aliases[alias].dedupe_window
        return self.defaults.dedupe_window if value is None else value

    def silent_levels(self, alias: str) -> frozenset[Level]:
        value = self.aliases[alias].silent_levels
        return self.defaults.silent_levels if value is None else value

    def chat_for(self, alias: str) -> tuple[str, ChatConfig]:
        chat_name = self.aliases[alias].chat
        return chat_name, self.chats[chat_name]


def _interpolate(value: Any, env: dict[str, str], path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            k: _interpolate(v, env, f"{path}.{k}" if path else str(k)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_interpolate(v, env, f"{path}[{i}]") for i, v in enumerate(value)]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group("name"), match.group("default")
        if name in env:
            return env[name]
        if default is not None:
            return default
        msg = f"{path}: environment variable '{name}' is not set"
        raise ConfigError(msg)

    return _ENV_PATTERN.sub(replace, value)


def load_app_config(path: Path, env: dict[str, str] | None = None) -> AppConfig:
    """Read, interpolate and validate the YAML configuration file."""
    if not path.is_file():
        msg = f"config file not found: {path} (run `topicast init` to create one)"
        raise ConfigError(msg)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        msg = f"{path}: invalid YAML: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path}: top level must be a mapping"
        raise ConfigError(msg)
    data = _interpolate(raw, dict(os.environ) if env is None else env)
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        msg = f"{path}: {exc}"
        raise ConfigError(msg) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
