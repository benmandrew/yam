"""FastAPI application entry point.

Covers downloads (single-video via the background worker + live /downloads page)
and playback (/media Range streaming, /watch player, thumbnail library grid).
Playlists arrive in Milestone 4.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlmodel import Session, select

from .db import engine, init_db
from .library import delete_playlist, delete_video
from .models import Job, JobStatus, JobType, Playlist, PlaylistVideo, Video, VideoStatus
from .urls import classify
from .worker import run_worker

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Container -> MIME for the <video> source. mkv has no reliable browser MIME;
# it is only a last-resort fallback container (see downloader.py).
_MIME_BY_EXT = {
    "webm": "video/webm",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mkv": "video/x-matroska",
}


def _mime_for(ext: str | None) -> str:
    return _MIME_BY_EXT.get((ext or "").lower(), "application/octet-stream")


def _fmt_ytdate(value: str | None) -> str:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value or ""


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


templates.env.filters["ytdate"] = _fmt_ytdate
templates.env.filters["duration"] = _fmt_duration


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(stop))
    try:
        yield
    finally:
        stop.set()
        await worker_task


app = FastAPI(title="Yam", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


_SORTS = {
    "uploaded": Video.upload_date.desc(),
    "added": Video.downloaded_at.desc(),
    "title": Video.title,
    "longest": Video.duration_s.desc(),
    "largest": Video.filesize.desc(),
}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str | None = None,
    sort: str = "uploaded",
    msg: str | None = None,
):
    stmt = select(Video).where(Video.status == VideoStatus.present)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Video.title.ilike(like), Video.channel.ilike(like)))
    stmt = stmt.order_by(_SORTS.get(sort, _SORTS["uploaded"]))
    with Session(engine) as session:
        videos = session.exec(stmt).all()
        playlists = session.exec(select(Playlist)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "videos": videos,
            "playlists": playlists,
            "q": q or "",
            "sort": sort,
            "msg": msg,
        },
    )


@app.post("/api/download")
def enqueue_download(request: Request, url: str = Form(...)):
    url = url.strip()
    kind, ident = classify(url)

    if kind == "playlist":
        with Session(engine) as session:
            session.add(Job(type=JobType.playlist, url=url, target_id=ident))
            session.commit()
        return _redirect_downloads("Playlist queued — enumerating videos…")

    if kind == "video" and ident and _video_present(ident):
        return _redirect_downloads("That video is already archived.")

    with Session(engine) as session:
        session.add(Job(type=JobType.video, url=url, target_id=ident))
        session.commit()
    return _redirect_downloads("Queued for download.")


@app.get("/downloads", response_class=HTMLResponse)
def downloads(request: Request, msg: str | None = None):
    return templates.TemplateResponse(request, "downloads.html", {"msg": msg})


@app.get("/api/jobs", response_class=HTMLResponse)
def jobs_partial(request: Request):
    with Session(engine) as session:
        jobs = session.exec(
            select(Job).order_by(Job.created_at.desc()).limit(100)
        ).all()
    return templates.TemplateResponse(request, "_jobs.html", {"jobs": jobs})


@app.get("/watch/{video_id}", response_class=HTMLResponse)
def watch(
    request: Request,
    video_id: str,
    list_id: str | None = Query(default=None, alias="list"),
):
    with Session(engine) as session:
        video = session.get(Video, video_id)
        if video is None:
            raise HTTPException(status_code=404, detail="Video not found")
        playlist = session.get(Playlist, list_id) if list_id else None
        next_id = _next_in_playlist(session, list_id, video_id) if playlist else None
    return templates.TemplateResponse(
        request,
        "watch.html",
        {
            "video": video,
            "mime": _mime_for(video.ext),
            "playlist": playlist,
            "next_id": next_id,
        },
    )


@app.get("/playlist/{playlist_id}", response_class=HTMLResponse)
def playlist_view(request: Request, playlist_id: str):
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        links = session.exec(
            select(PlaylistVideo)
            .where(PlaylistVideo.playlist_id == playlist_id)
            .order_by(PlaylistVideo.position)
        ).all()
        videos = [session.get(Video, link.video_id) for link in links]
    first_present = next(
        (v.id for v in videos if v and v.status == VideoStatus.present), None
    )
    return templates.TemplateResponse(
        request,
        "playlist.html",
        {"playlist": playlist, "videos": videos, "first_present": first_present},
    )


@app.get("/media/{video_id}")
def media(video_id: str):
    """Stream the video file. FileResponse honors Range headers, so the
    browser can seek (206 Partial Content)."""
    with Session(engine) as session:
        video = session.get(Video, video_id)
    if video is None or not video.file_path or not os.path.exists(video.file_path):
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(video.file_path, media_type=_mime_for(video.ext))


@app.get("/media/{video_id}/thumbnail")
def thumbnail(video_id: str):
    with Session(engine) as session:
        video = session.get(Video, video_id)
    if (
        video is None
        or not video.thumbnail_path
        or not os.path.exists(video.thumbnail_path)
    ):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(video.thumbnail_path, media_type="image/jpeg")


@app.post("/api/videos/{video_id}/delete")
def delete_video_route(video_id: str):
    _ok, msg = delete_video(video_id)
    return _redirect("/", msg)


@app.post("/api/playlists/{playlist_id}/delete")
def delete_playlist_route(playlist_id: str):
    _ok, msg = delete_playlist(playlist_id)
    return _redirect("/", msg)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int):
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job and job.status == JobStatus.error:
            job.status = JobStatus.queued
            job.progress = 0.0
            job.error_msg = None
            session.add(job)
            session.commit()
    return _redirect_downloads("Retrying…")


@app.post("/api/jobs/clear")
def clear_jobs():
    with Session(engine) as session:
        finished = session.exec(
            select(Job).where(
                Job.status.in_([JobStatus.done, JobStatus.error, JobStatus.skipped])
            )
        ).all()
        for job in finished:
            session.delete(job)
        session.commit()
    return _redirect_downloads("Cleared finished downloads.")


# --- helpers ----------------------------------------------------------------


def _redirect(path: str, msg: str) -> RedirectResponse:
    from urllib.parse import urlencode

    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{urlencode({'msg': msg})}", status_code=303)


def _redirect_downloads(msg: str) -> RedirectResponse:
    return _redirect("/downloads", msg)


def _video_present(video_id: str) -> bool:
    """True only if the video is actually downloaded (not just a playlist
    placeholder row with status=missing)."""
    with Session(engine) as session:
        video = session.get(Video, video_id)
    return video is not None and video.status == VideoStatus.present


def _next_in_playlist(
    session: Session, playlist_id: str | None, current_id: str
) -> str | None:
    if not playlist_id:
        return None
    links = session.exec(
        select(PlaylistVideo)
        .where(PlaylistVideo.playlist_id == playlist_id)
        .order_by(PlaylistVideo.position)
    ).all()
    ids = [link.video_id for link in links]
    if current_id in ids:
        index = ids.index(current_id)
        if index + 1 < len(ids):
            return ids[index + 1]
    return None
