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
from .downloader import download_video, enumerate_playlist
from .models import (
    Job,
    JobStatus,
    JobType,
    Playlist,
    PlaylistVideo,
    Video,
    VideoStatus,
)

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
        await asyncio.to_thread(_process_job_blocking, job_id)
    except Exception:
        log.exception("job %s failed", job_id)
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


def _process_job_blocking(job_id: int) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job_type = job.type
        url = job.url

    if job_type == JobType.playlist:
        _enumerate_playlist_job(job_id, url)
    else:
        _download_video_job(job_id, url)


def _download_video_job(job_id: int, url: str) -> None:
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


# --- playlists --------------------------------------------------------------


def _enumerate_playlist_job(job_id: int, url: str) -> None:
    try:
        info = enumerate_playlist(url)
        _save_playlist(info, parent_job_id=job_id)
        _finish_job(job_id, JobStatus.done, target_id=info.get("id"))
    except Exception as exc:  # noqa: BLE001 - recorded on the job row
        _finish_job(job_id, JobStatus.error, error=str(exc))
        raise


def _save_playlist(info: dict[str, Any], *, parent_job_id: int) -> None:
    """Upsert the Playlist and its ordered entries, then enqueue downloads for
    any entries not already present or in flight (dedup across playlists)."""
    pid = info["id"]
    entries = [e for e in (info.get("entries") or []) if e and e.get("id")]

    with Session(engine) as session:
        playlist = session.get(Playlist, pid) or Playlist(
            id=pid, title=info.get("title") or pid
        )
        playlist.title = info.get("title") or playlist.title
        playlist.channel = info.get("uploader") or info.get("channel")
        playlist.description = info.get("description")
        playlist.last_synced_at = _utcnow()
        session.add(playlist)
        session.commit()

    for position, entry in enumerate(entries):
        vid = entry["id"]
        _upsert_placeholder_video(vid, entry.get("title"))
        _upsert_link(pid, vid, position)
        if not _video_present(vid) and not _active_video_job(vid):
            _enqueue_video_job(vid, parent_job_id=parent_job_id)

    # On re-sync, drop links for entries no longer in the upstream playlist
    # (files/rows are kept — deletion is a separate, explicit action).
    _prune_removed_links(pid, {entry["id"] for entry in entries})


def _prune_removed_links(playlist_id: str, current_ids: set[str]) -> None:
    with Session(engine) as session:
        stale = session.exec(
            select(PlaylistVideo).where(
                PlaylistVideo.playlist_id == playlist_id,
                PlaylistVideo.video_id.not_in(current_ids),
            )
        ).all()
        for link in stale:
            session.delete(link)
        session.commit()


def _upsert_placeholder_video(vid: str, title: str | None) -> None:
    """Record a not-yet-downloaded entry as a `missing` Video so the playlist
    view can show it with a title before its file exists."""
    with Session(engine) as session:
        video = session.get(Video, vid)
        if video is None:
            session.add(Video(id=vid, title=title or vid, status=VideoStatus.missing))
            session.commit()
        elif title and (not video.title or video.title == vid):
            video.title = title
            session.add(video)
            session.commit()


def _upsert_link(pid: str, vid: str, position: int) -> None:
    with Session(engine) as session:
        link = session.get(PlaylistVideo, (pid, vid))
        if link is None:
            session.add(PlaylistVideo(playlist_id=pid, video_id=vid, position=position))
        else:
            link.position = position
            session.add(link)
        session.commit()


def _video_present(vid: str) -> bool:
    with Session(engine) as session:
        video = session.get(Video, vid)
        return video is not None and video.status == VideoStatus.present


def _active_video_job(vid: str) -> bool:
    with Session(engine) as session:
        row = session.exec(
            select(Job).where(
                Job.type == JobType.video,
                Job.target_id == vid,
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
        ).first()
    return row is not None


def _enqueue_video_job(vid: str, *, parent_job_id: int | None = None) -> None:
    url = f"https://www.youtube.com/watch?v={vid}"
    with Session(engine) as session:
        session.add(
            Job(
                type=JobType.video,
                url=url,
                target_id=vid,
                parent_job_id=parent_job_id,
            )
        )
        session.commit()


def enqueue_pending_for_playlist(playlist_id: str) -> int:
    """Enqueue downloads for a playlist's still-missing entries that have no
    active job. Returns how many were queued."""
    with Session(engine) as session:
        links = session.exec(
            select(PlaylistVideo)
            .where(PlaylistVideo.playlist_id == playlist_id)
            .order_by(PlaylistVideo.position)
        ).all()
        video_ids = [link.video_id for link in links]

    queued = 0
    for vid in video_ids:
        if not _video_present(vid) and not _active_video_job(vid):
            _enqueue_video_job(vid)
            queued += 1
    return queued
