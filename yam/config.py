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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "yam.db"

    @property
    def videos_dir(self) -> Path:
        return self.media_dir / "videos"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
