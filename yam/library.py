"""Library management: deletion with the full-delete retention rule, plus
custom (user-created) playlist CRUD.

A video is only ever removed when no playlist references it. Deleting a
YouTube-sourced playlist removes the playlist and its links, then full-deletes
any video left with no other playlist reference (files *and* DB row). Custom
playlists never own their videos, so deleting one only removes the playlist
and its links — referenced videos are always left untouched.
"""

from __future__ import annotations

import shutil
import uuid

from sqlmodel import Session, select

from .config import settings
from .db import engine
from .models import (
    Job,
    JobType,
    Playlist,
    PlaylistOrigin,
    PlaylistVideo,
    Video,
    VideoStatus,
)


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
    """Delete a playlist. YouTube-sourced playlists full-delete any videos they
    orphan; custom playlists never own their videos, so only the playlist and
    its links are removed."""
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None:
            return False, "Playlist not found."
        title = playlist.title
        is_custom = playlist.origin == PlaylistOrigin.custom
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
        if not is_custom:
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
    if is_custom:
        return True, f"Deleted playlist “{title}”."
    return True, f"Deleted playlist “{title}” and {len(orphaned)} orphaned video(s)."


def create_custom_playlist(title: str) -> Playlist:
    """Create a new empty custom (user-created) playlist."""
    with Session(engine) as session:
        playlist = Playlist(
            id=uuid.uuid4().hex,
            title=title.strip() or "Untitled playlist",
            origin=PlaylistOrigin.custom,
        )
        session.add(playlist)
        session.commit()
        session.refresh(playlist)
        return playlist


def add_video_to_playlist(playlist_id: str, video_id: str) -> tuple[bool, str]:
    """Add an already-archived video to a custom playlist."""
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None or playlist.origin != PlaylistOrigin.custom:
            return False, "Custom playlist not found."
        video = session.get(Video, video_id)
        if video is None or video.status != VideoStatus.present:
            return False, "Video isn't available to add."
        existing = session.get(PlaylistVideo, (playlist_id, video_id))
        title = playlist.title
        if existing is not None:
            return False, f"Already in “{title}”."
        session.add(PlaylistVideo(playlist_id=playlist_id, video_id=video_id))
        session.commit()
    return True, f"Added to “{title}”."


def remove_video_from_playlist(playlist_id: str, video_id: str) -> tuple[bool, str]:
    """Remove a video from a custom playlist without touching the Video row."""
    with Session(engine) as session:
        playlist = session.get(Playlist, playlist_id)
        if playlist is None or playlist.origin != PlaylistOrigin.custom:
            return False, "Custom playlist not found."
        link = session.get(PlaylistVideo, (playlist_id, video_id))
        if link is None:
            return False, "Video isn't in this playlist."
        title = playlist.title
        session.delete(link)
        session.commit()
    return True, f"Removed from “{title}”."
