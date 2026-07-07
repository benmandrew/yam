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

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .db import engine, init_db
from .models import Job, JobType, Playlist, Video
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


app = FastAPI(title="YAM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as session:
        videos = session.exec(select(Video).order_by(Video.downloaded_at.desc())).all()
        playlists = session.exec(select(Playlist)).all()
    return templates.TemplateResponse(
        request, "index.html", {"videos": videos, "playlists": playlists}
    )


@app.post("/api/download")
def enqueue_download(request: Request, url: str = Form(...)):
    url = url.strip()
    kind, ident = classify(url)

    if kind == "playlist":
        return _redirect_downloads("Playlist downloads arrive in Milestone 4.")

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
def watch(request: Request, video_id: str):
    with Session(engine) as session:
        video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return templates.TemplateResponse(
        request,
        "watch.html",
        {"video": video, "mime": _mime_for(video.ext)},
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


# --- helpers ----------------------------------------------------------------


def _redirect_downloads(msg: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"/downloads?{urlencode({'msg': msg})}", status_code=303)


def _video_present(video_id: str) -> bool:
    with Session(engine) as session:
        return session.get(Video, video_id) is not None
