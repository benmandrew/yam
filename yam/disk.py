"""Disk-usage stats and the free-space guard for downloads.

`archived_bytes` sums stored video sizes from the DB; `usage`/`free_bytes` report
the filesystem backing MEDIA_DIR. The worker calls `has_min_free_space` before
starting a video download so a full disk fails the job cleanly instead of leaving
a truncated file.
"""

from __future__ import annotations

import shutil

from sqlalchemy import func
from sqlmodel import Session, select

from .config import settings
from .db import engine
from .models import Video, VideoStatus


def archived_bytes() -> int:
    """Total on-disk size of present videos, per the DB's recorded filesizes."""
    with Session(engine) as session:
        total = session.exec(
            select(func.sum(Video.filesize)).where(Video.status == VideoStatus.present)
        ).one()
    return int(total or 0)


def usage() -> shutil._ntuple_diskusage:
    """(total, used, free) bytes for the filesystem holding MEDIA_DIR."""
    return shutil.disk_usage(settings.media_dir)


def free_bytes() -> int:
    return usage().free


def has_min_free_space() -> tuple[bool, int]:
    """Return (ok, free_bytes). ok is True when the guard is disabled
    (MIN_FREE_SPACE_MB=0) or free space is at/above the threshold."""
    free = free_bytes()
    ok = settings.min_free_space_bytes <= 0 or free >= settings.min_free_space_bytes
    return ok, free
