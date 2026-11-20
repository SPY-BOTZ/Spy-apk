"""Environment configuration loader and validator."""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when required environment configuration is missing or invalid."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _require_int_env(name: str) -> int:
    raw = _require_env(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got: {raw!r}") from exc


def _resolve_base_url(port: int) -> str:
    explicit = os.getenv("BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    koyeb_domain = os.getenv("KOYEB_PUBLIC_DOMAIN", "").strip().rstrip("/")
    if koyeb_domain:
        if koyeb_domain.startswith(("http://", "https://")):
            return koyeb_domain
        return f"https://{koyeb_domain}"

    return f"http://localhost:{port}"


@dataclass(frozen=True, slots=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    storage_channel_id: int
    tmdb_api_key: str
    firebase_db_url: str
    firebase_database_secret: str
    port: int
    base_url: str
    fallback_poster_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        port_raw = os.getenv("PORT", "8000").strip()
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(f"PORT must be an integer, got: {port_raw!r}") from exc

        firebase_db_url = _require_env("FIREBASE_DB_URL").rstrip("/")
        if not firebase_db_url.startswith(("http://", "https://")):
            raise ConfigError("FIREBASE_DB_URL must start with http:// or https://")

        fallback_poster = os.getenv(
            "FALLBACK_POSTER_URL",
            "https://placehold.co/500x750/0f172a/f8fafc.png?text=No+Poster",
        ).strip()

        return cls(
            api_id=_require_int_env("API_ID"),
            api_hash=_require_env("API_HASH"),
            bot_token=_require_env("BOT_TOKEN"),
            owner_id=_require_int_env("OWNER_ID"),
            storage_channel_id=_require_int_env("STORAGE_CHANNEL_ID"),
            tmdb_api_key=_require_env("TMDB_API_KEY"),
            firebase_db_url=firebase_db_url,
            firebase_database_secret=os.getenv("FIREBASE_DATABASE_SECRET", "").strip(),
            port=port,
            base_url=_resolve_base_url(port),
            fallback_poster_url=fallback_poster,
        )
