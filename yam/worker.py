"""Background download worker.

A single asyncio loop atomically claims queued jobs (up to
MAX_CONCURRENT_DOWNLOADS), runs each blocking yt-dlp download in a thread, and
writes progress/results back to SQLite. On startup, jobs left ``running`` by a
previous crash are reset to ``queued``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from .config import settings
from .db import engine
from .downloader import download_video
from .models import Job, JobStatus, Video, VideoStatus

log = logging.getLogger("yam.worker")

_POLL_INTERVAL = 2.0
_PROGRESS_MIN_INTERVAL = 1.0  # seconds between DB progress writes


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_worker(stop: asyncio.Event) -> None:
    """Main worker loop; runs until ``stop`` is set."""
    _reset_orphaned_jobs()
    inflight: set[int] = set()
    while not stop.is_set():
        free = settings.max_concurrent_downloads - len(inflight)
        for job_id in _claim_jobs(free):
            inflight.add(job_id)
            asyncio.create_task(_run_job(job_id, inflight))
        try:
            await asyncio.wait_for(stop.wait(), timeout=_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass


async def _run_job(job_id: int, inflight: set[int]) -> None:
    try:
        await asyncio.to_thread(_download_job_blocking, job_id)
    except Exception:
        log.exception("download job %s failed", job_id)
    finally:
        inflight.discard(job_id)


# --- DB helpers -------------------------------------------------------------


def _reset_orphaned_jobs() -> None:
    with Session(engine) as session:
        rows = session.exec(select(Job).where(Job.status == JobStatus.running)).all()
        for job in rows:
            job.status = JobStatus.queued
            job.updated_at = _utcnow()
            session.add(job)
        if rows:
            log.info("reset %d orphaned running job(s) to queued", len(rows))
        session.commit()


def _claim_jobs(limit: int) -> list[int]:
    """Atomically move up to ``limit`` queued jobs to running; return their ids."""
    if limit <= 0:
        return []
    claimed: list[int] = []
    with Session(engine) as session:
        jobs = session.exec(
            select(Job)
            .where(Job.status == JobStatus.queued)
            .order_by(Job.created_at)
            .limit(limit)
        ).all()
        for job in jobs:
            job.status = JobStatus.running
            job.progress = 0.0
            job.updated_at = _utcnow()
            session.add(job)
            claimed.append(job.id)
        session.commit()
    return claimed


def _download_job_blocking(job_id: int) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        url = job.url

    last_write = {"t": 0.0}

    def on_progress(pct: float, speed: str | None, eta: str | None) -> None:
        now = time.monotonic()
        if pct < 100 and now - last_write["t"] < _PROGRESS_MIN_INTERVAL:
            return
        last_write["t"] = now
        with Session(engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.progress = round(pct, 1)
            job.speed = speed
            job.eta = eta
            job.updated_at = _utcnow()
            session.add(job)
            session.commit()

    try:
        info = download_video(url, on_progress)
        _save_video(info)
        _finish_job(job_id, JobStatus.done, target_id=info.get("id"))
    except Exception as exc:  # noqa: BLE001 - recorded on the job row
        _finish_job(job_id, JobStatus.error, error=str(exc))
        raise


def _finish_job(
    job_id: int,
    status: JobStatus,
    *,
    target_id: str | None = None,
    error: str | None = None,
) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        if status == JobStatus.done:
            job.progress = 100.0
        if target_id:
            job.target_id = target_id
        job.error_msg = error
        job.updated_at = _utcnow()
        session.add(job)
        session.commit()


def _save_video(info: dict[str, Any]) -> None:
    """Upsert a Video row from a yt-dlp info dict."""
    vid = info["id"]
    requested = (info.get("requested_downloads") or [{}])[0]
    video_dir = settings.videos_dir / vid

    file_path = requested.get("filepath")
    if not file_path:
        ext = requested.get("ext") or info.get("ext") or "mkv"
        file_path = str(video_dir / f"{vid}.{ext}")

    thumbnail = next((str(p) for p in video_dir.glob(f"{vid}.jpg")), None)

    filesize = requested.get("filesize") or info.get("filesize")
    if not filesize and file_path and os.path.exists(file_path):
        filesize = os.path.getsize(file_path)

    with Session(engine) as session:
        video = session.get(Video, vid) or Video(id=vid, title=info.get("title") or vid)
        video.title = info.get("title") or video.title
        video.channel = info.get("uploader") or info.get("channel")
        video.channel_id = info.get("channel_id")
        video.description = info.get("description")
        video.duration_s = int(info["duration"]) if info.get("duration") else None
        video.upload_date = info.get("upload_date")
        video.thumbnail_path = thumbnail
        video.file_path = file_path
        video.filesize = filesize
        video.width = info.get("width")
        video.height = info.get("height")
        video.ext = requested.get("ext") or Path(file_path).suffix.lstrip(".") or None
        video.vcodec = info.get("vcodec")
        video.acodec = info.get("acodec")
        video.downloaded_at = _utcnow()
        video.status = VideoStatus.present
        session.add(video)
        session.commit()
