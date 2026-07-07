"""Library management: deletion with the full-delete retention rule.

A video is only ever removed when no playlist references it. Deleting a playlist
removes the playlist and its links, then full-deletes any video left with no
other playlist reference (files *and* DB row).
"""

from __future__ import annotations

import shutil

from sqlmodel import Session, select

from .config import settings
from .db import engine
from .models import Job, JobType, Playlist, PlaylistVideo, Video


def _remove_video_dir(video_id: str) -> None:
    shutil.rmtree(settings.videos_dir / video_id, ignore_errors=True)


def _purge_video_rows(session: Session, video: Video) -> None:
    """Delete a Video row and its associated video-download jobs (not files)."""
    jobs = session.exec(
        select(Job).where(Job.type == JobType.video, Job.target_id == video.id)
    ).all()
    for job in jobs:
        session.delete(job)
    session.delete(video)


def delete_video(video_id: str) -> tuple[bool, str]:
    """Delete a standalone video. Refused if any playlist still references it."""
    with Session(engine) as session:
        video = session.get(Video, video_id)
        if video is None:
            return False, "Video not found."
        title = video.title
        links = session.exec(
            select(PlaylistVideo).where(PlaylistVideo.video_id == video_id)
        ).all()
        if links:
            return (
                False,
                f"Can't delete “{title}” — it's in {len(links)} playlist(s). "
                "Delete the playlist instead.",
            )
        _purge_video_rows(session, video)
        session.commit()
    _remove_video_dir(video_id)
    return True, f"Deleted “{title}”."


def delete_playlist(playlist_id: str) -> tuple[bool, str]:
    """Delete a playlist and full-delete any videos it orphans."""
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None:
            return False, "Playlist not found."
        title = playlist.title
        links = session.exec(
            select(PlaylistVideo).where(PlaylistVideo.playlist_id == playlist_id)
        ).all()
        video_ids = [link.video_id for link in links]
        # Remove this playlist's links first (and flush) so the orphan check
        # sees them gone and the playlist delete is FK-safe.
        for link in links:
            session.delete(link)
        session.flush()

        orphaned: list[str] = []
        for vid in video_ids:
            still_linked = session.exec(
                select(PlaylistVideo).where(PlaylistVideo.video_id == vid)
            ).first()
            if still_linked is None:
                video = session.get(Video, vid)
                if video is not None:
                    _purge_video_rows(session, video)
                    orphaned.append(vid)
        session.delete(playlist)
        session.commit()

    for vid in orphaned:
        _remove_video_dir(vid)
    return True, f"Deleted playlist “{title}” and {len(orphaned)} orphaned video(s)."
