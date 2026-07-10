"""SQLModel tables. Mirrors the data model in PLAN.md.

Videos are keyed by YouTube id and stored once on disk; playlists reference
videos through the `playlist_video` link table, so a video in several playlists
is only downloaded a single time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VideoStatus(str, Enum):
    present = "present"
    missing = "missing"
    error = "error"


class JobType(str, Enum):
    video = "video"
    playlist = "playlist"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"
    skipped = "skipped"


class PlaylistOrigin(str, Enum):
    youtube = "youtube"
    custom = "custom"


class PlaylistVideo(SQLModel, table=True):
    """Association between a playlist and a video, preserving playlist order."""

    __tablename__ = "playlist_video"

    playlist_id: str = Field(foreign_key="playlist.id", primary_key=True)
    video_id: str = Field(foreign_key="video.id", primary_key=True)
    position: int = 0


class Video(SQLModel, table=True):
    id: str = Field(primary_key=True)  # YouTube video id
    title: str
    channel: Optional[str] = None
    channel_id: Optional[str] = None
    description: Optional[str] = None
    duration_s: Optional[int] = None
    upload_date: Optional[str] = None  # YYYYMMDD as returned by yt-dlp
    thumbnail_path: Optional[str] = None
    subtitle_path: Optional[str] = None  # WebVTT sidecar when DOWNLOAD_SUBTITLES
    file_path: Optional[str] = None
    filesize: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    ext: Optional[str] = None  # varies by source codec (mkv/webm/mp4)
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    status: VideoStatus = VideoStatus.present


class Playlist(SQLModel, table=True):
    id: str = Field(primary_key=True)  # YouTube playlist id
    title: str
    channel: Optional[str] = None
    description: Optional[str] = None
    thumbnail_path: Optional[str] = None
    added_at: datetime = Field(default_factory=_utcnow)
    last_synced_at: Optional[datetime] = None
    # Nullable/default-less so `_add_missing_columns` can auto-migrate existing
    # SQLite DBs (see db.py). Treat None as PlaylistOrigin.youtube everywhere.
    origin: Optional[PlaylistOrigin] = None


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: JobType
    url: str
    target_id: Optional[str] = None  # video/playlist id, once resolved
    status: JobStatus = JobStatus.queued
    progress: float = 0.0  # 0..100
    speed: Optional[str] = None
    eta: Optional[str] = None
    error_msg: Optional[str] = None
    parent_job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
