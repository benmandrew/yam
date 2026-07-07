"""Runtime configuration, sourced entirely from environment variables.

Kept dependency-free (plain dataclass + os.environ) so the skeleton has no
pydantic-settings requirement. See PLAN.md "Configuration" for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    media_dir: Path = Path(os.environ.get("MEDIA_DIR", "/media"))
    data_dir: Path = Path(os.environ.get("DATA_DIR", "/data"))
    max_concurrent_downloads: int = _env_int("MAX_CONCURRENT_DOWNLOADS", 2)
    download_subtitles: bool = _env_bool("DOWNLOAD_SUBTITLES", False)
    cookies_file: str | None = os.environ.get("COOKIES_FILE") or None
    # Refuse to start a download when free space on MEDIA_DIR drops below this
    # (0 disables the guard).
    min_free_space_mb: int = _env_int("MIN_FREE_SPACE_MB", 500)
    # Optional HTTP Basic auth (defense-in-depth atop host TLS); active only when
    # both are set.
    basic_auth_user: str | None = os.environ.get("BASIC_AUTH_USER") or None
    basic_auth_pass: str | None = os.environ.get("BASIC_AUTH_PASS") or None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "yam.db"

    @property
    def videos_dir(self) -> Path:
        return self.media_dir / "videos"

    @property
    def min_free_space_bytes(self) -> int:
        return self.min_free_space_mb * 1024 * 1024

    @property
    def basic_auth_enabled(self) -> bool:
        return bool(self.basic_auth_user and self.basic_auth_pass)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
