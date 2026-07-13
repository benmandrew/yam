"""FastAPI application entry point.

Covers downloads (single-video via the background worker + live /downloads page)
and playback (/media Range streaming, /watch player, thumbnail library grid).
Playlists arrive in Milestone 4.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlmodel import Session, select

from . import disk
from .config import settings
from .db import engine, init_db
from .library import (
    add_video_to_playlist,
    create_custom_playlist,
    delete_playlist,
    delete_video,
    remove_video_from_playlist,
)
from .logging_config import configure_logging
from .models import (
    Job,
    JobStatus,
    JobType,
    Playlist,
    PlaylistOrigin,
    PlaylistVideo,
    Video,
    VideoStatus,
)
from .urls import classify
from .worker import enqueue_pending_for_playlist, run_worker

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
    configure_logging()
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


def _auth_ok(header: str | None) -> bool:
    """Constant-time check of an HTTP Basic Authorization header."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Compare both halves (non-short-circuit) to avoid leaking which was wrong.
    ok_user = secrets.compare_digest(user, settings.basic_auth_user or "")
    ok_pwd = secrets.compare_digest(pwd, settings.basic_auth_pass or "")
    return ok_user and ok_pwd


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # Defense-in-depth atop host TLS; /healthz stays open for container probes.
    if (
        settings.basic_auth_enabled
        and request.url.path != "/healthz"
        and not _auth_ok(request.headers.get("Authorization"))
    ):
        return Response(
            status_code=401, headers={"WWW-Authenticate": 'Basic realm="Yam"'}
        )
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    # Browsers (and some proxies) probe /favicon.ico at the site root and ignore
    # the <link rel="icon"> tag, so serve the PNG here too.
    return FileResponse(BASE_DIR / "static" / "favicon.png", media_type="image/png")


_SORTS = {
    "uploaded": Video.upload_date.desc(),
    "added": Video.downloaded_at.desc(),
    "title": Video.title,
    "longest": Video.duration_s.desc(),
    "largest": Video.filesize.desc(),
}

_PAGE_SIZE = 60


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str | None = None,
    sort: str = "uploaded",
    page: int = 1,
    msg: str | None = None,
):
    stmt = select(Video).where(Video.status == VideoStatus.present)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Video.title.ilike(like), Video.channel.ilike(like)))
    with Session(engine) as session:
        total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
        total_pages = max(1, -(-total // _PAGE_SIZE))  # ceil division
        page = min(max(page, 1), total_pages)
        page_stmt = (
            stmt.order_by(_SORTS.get(sort, _SORTS["uploaded"]))
            .offset((page - 1) * _PAGE_SIZE)
            .limit(_PAGE_SIZE)
        )
        videos = session.exec(page_stmt).all()
        playlists = session.exec(select(Playlist)).all()
        playlist_counts = {
            p.id: len(
                session.exec(
                    select(PlaylistVideo).where(PlaylistVideo.playlist_id == p.id)
                ).all()
            )
            for p in playlists
        }
    custom_playlists = [p for p in playlists if p.origin == PlaylistOrigin.custom]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "videos": videos,
            "playlists": playlists,
            "playlist_counts": playlist_counts,
            "custom_playlists": custom_playlists,
            "q": q or "",
            "sort": sort,
            "page": page,
            "total_pages": total_pages,
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

    if kind == "video" and ident:
        # Guard against re-linking a video we already have or are fetching, so a
        # duplicate paste doesn't spawn a redundant job or overwrite the file.
        if _video_present(ident):
            return _redirect_downloads("That video is already archived.", level="error")
        if _video_in_flight(ident):
            return _redirect_downloads(
                "That video is already downloading.", level="error"
            )

    with Session(engine) as session:
        session.add(Job(type=JobType.video, url=url, target_id=ident))
        session.commit()
    return _redirect_downloads("Queued for download.")


@app.get("/downloads", response_class=HTMLResponse)
def downloads(request: Request, msg: str | None = None):
    return templates.TemplateResponse(request, "downloads.html", {"msg": msg})


@app.get("/config", response_class=HTMLResponse)
def config_view(request: Request):
    total, _used, free = disk.usage()
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "settings": settings,
            "archived": disk.archived_bytes(),
            "disk_total": total,
            "disk_free": free,
        },
    )


@app.get("/api/jobs", response_class=HTMLResponse)
def jobs_partial(request: Request):
    with Session(engine) as session:
        jobs = session.exec(
            select(Job).order_by(Job.created_at.desc()).limit(200)
        ).all()
        # Resolve target ids to Videos/Playlists so rows can show real details
        # (title, channel, thumbnail, duration) instead of the raw URL.
        video_ids = {
            j.target_id for j in jobs if j.type == JobType.video and j.target_id
        }
        playlist_ids = {
            j.target_id for j in jobs if j.type == JobType.playlist and j.target_id
        }
        videos = (
            {
                v.id: v
                for v in session.exec(
                    select(Video).where(Video.id.in_(video_ids))
                ).all()
            }
            if video_ids
            else {}
        )
        playlists = (
            {
                p.id: p
                for p in session.exec(
                    select(Playlist).where(Playlist.id.in_(playlist_ids))
                ).all()
            }
            if playlist_ids
            else {}
        )

    def _detail(job: Job):
        if job.target_id is None:
            return None
        return (videos if job.type == JobType.video else playlists).get(job.target_id)

    # Nest child video jobs under their parent playlist job.
    children: dict[int, list[Job]] = {}
    for job in jobs:
        if job.parent_job_id is not None:
            children.setdefault(job.parent_job_id, []).append(job)
    top_ids = {job.id for job in jobs if job.parent_job_id is None}
    details = {job.id: _detail(job) for job in jobs}
    groups = [
        {
            "job": job,
            "children": sorted(children.get(job.id, []), key=lambda c: c.created_at),
        }
        for job in jobs
        if job.parent_job_id is None or job.parent_job_id not in top_ids
    ]
    return templates.TemplateResponse(
        request, "_jobs.html", {"groups": groups, "details": details}
    )


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
        custom_playlists = session.exec(
            select(Playlist).where(Playlist.origin == PlaylistOrigin.custom)
        ).all()
    return templates.TemplateResponse(
        request,
        "watch.html",
        {
            "video": video,
            "mime": _mime_for(video.ext),
            "playlist": playlist,
            "next_id": next_id,
            "custom_playlists": custom_playlists,
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
    if playlist.origin == PlaylistOrigin.custom:
        videos.sort(key=lambda v: (v.upload_date or "") if v else "")
    first_present = next(
        (v.id for v in videos if v and v.status == VideoStatus.present), None
    )
    return templates.TemplateResponse(
        request,
        "playlist.html",
        {"playlist": playlist, "videos": videos, "first_present": first_present},
    )


@app.get("/playlist/{playlist_id}/thumbnail")
def playlist_thumbnail(playlist_id: str):
    """Serve the first present entry's thumbnail as the playlist cover."""
    with Session(engine) as session:
        links = session.exec(
            select(PlaylistVideo)
            .where(PlaylistVideo.playlist_id == playlist_id)
            .order_by(PlaylistVideo.position)
        ).all()
        for link in links:
            video = session.get(Video, link.video_id)
            if (
                video
                and video.status == VideoStatus.present
                and video.thumbnail_path
                and os.path.exists(video.thumbnail_path)
            ):
                return FileResponse(video.thumbnail_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="No thumbnail available")


@app.post("/api/playlists/{playlist_id}/sync")
def sync_playlist(playlist_id: str):
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if playlist.origin == PlaylistOrigin.custom:
            return _redirect(
                f"/playlist/{playlist_id}",
                "Sync isn't available for custom playlists.",
                level="error",
            )
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        session.add(Job(type=JobType.playlist, url=url, target_id=playlist_id))
        session.commit()
    return _redirect(f"/playlist/{playlist_id}", "Syncing playlist…")


@app.post("/api/playlists/{playlist_id}/retry")
def retry_playlist(playlist_id: str):
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if playlist.origin == PlaylistOrigin.custom:
            return _redirect(
                f"/playlist/{playlist_id}",
                "Retry isn't available for custom playlists.",
                level="error",
            )
    queued = enqueue_pending_for_playlist(playlist_id)
    return _redirect(f"/playlist/{playlist_id}", f"Queued {queued} pending video(s).")


@app.post("/api/playlists")
def create_playlist_route(title: str = Form(...)):
    playlist = create_custom_playlist(title)
    return _redirect(
        f"/playlist/{playlist.id}", f"Created “{playlist.title}”.", level="success"
    )


@app.post("/api/videos/{video_id}/add-to-playlist")
def add_video_to_playlist_route(
    video_id: str, playlist_id: str = Form(...), next_path: str = Form("/")
):
    ok, msg = add_video_to_playlist(playlist_id, video_id)
    return _redirect(next_path, msg, level=_level(ok))


@app.post("/api/playlists/{playlist_id}/videos/{video_id}/remove")
def remove_video_from_playlist_route(playlist_id: str, video_id: str):
    ok, msg = remove_video_from_playlist(playlist_id, video_id)
    return _redirect(f"/playlist/{playlist_id}", msg, level=_level(ok))


@app.post("/api/videos/bulk-add-to-playlist")
def bulk_add_to_playlist(
    video_ids: list[str] = Form(default=[]), playlist_id: str = Form(default="")
):
    if not playlist_id:
        return _redirect("/", "No playlist selected.", level="error")
    if not video_ids:
        return _redirect("/", "No videos selected.", level="error")
    added = 0
    for vid in video_ids:
        ok, _msg = add_video_to_playlist(playlist_id, vid)
        if ok:
            added += 1
    skipped = len(video_ids) - added
    msg = f"Added {added} video(s) to the playlist."
    if skipped:
        msg += f" {skipped} skipped (already in it or unavailable)."
    return _redirect("/", msg, level=_level(added > 0))


@app.post("/api/videos/bulk-delete")
def bulk_delete(video_ids: list[str] = Form(default=[])):
    if not video_ids:
        return _redirect("/", "No videos selected.", level="error")
    deleted = 0
    for vid in video_ids:
        ok, _msg = delete_video(vid)
        if ok:
            deleted += 1
    skipped = len(video_ids) - deleted
    msg = f"Deleted {deleted} video(s)."
    if skipped:
        msg += f" {skipped} skipped (still referenced by a playlist)."
    return _redirect("/", msg, level=_level(deleted > 0))


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


@app.get("/media/{video_id}/subtitles")
def subtitles(video_id: str):
    with Session(engine) as session:
        video = session.get(Video, video_id)
    if (
        video is None
        or not video.subtitle_path
        or not os.path.exists(video.subtitle_path)
    ):
        raise HTTPException(status_code=404, detail="Subtitles not found")
    return FileResponse(video.subtitle_path, media_type="text/vtt")


@app.post("/api/videos/{video_id}/delete")
def delete_video_route(video_id: str):
    ok, msg = delete_video(video_id)
    return _redirect("/", msg, level=_level(ok))


@app.post("/api/playlists/{playlist_id}/delete")
def delete_playlist_route(playlist_id: str):
    ok, msg = delete_playlist(playlist_id)
    return _redirect("/", msg, level=_level(ok))


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
    return _redirect_downloads("Cleared finished downloads.", level="success")


# --- helpers ----------------------------------------------------------------


def _redirect(path: str, msg: str, level: str = "info") -> RedirectResponse:
    from urllib.parse import urlencode

    params = {"msg": msg}
    if level != "info":
        params["level"] = level
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{urlencode(params)}", status_code=303)


def _redirect_downloads(msg: str, level: str = "info") -> RedirectResponse:
    return _redirect("/downloads", msg, level)


def _level(ok: bool) -> str:
    """Flash level for an action that either succeeded or was rejected."""
    return "success" if ok else "error"


def _video_present(video_id: str) -> bool:
    """True only if the video is actually downloaded (not just a playlist
    placeholder row with status=missing)."""
    with Session(engine) as session:
        video = session.get(Video, video_id)
    return video is not None and video.status == VideoStatus.present


def _video_in_flight(video_id: str) -> bool:
    """True if a video job for this id is queued or running (including one
    spawned as a playlist child), so we don't enqueue a duplicate download."""
    with Session(engine) as session:
        job = session.exec(
            select(Job).where(
                Job.type == JobType.video,
                Job.target_id == video_id,
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
        ).first()
    return job is not None


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
