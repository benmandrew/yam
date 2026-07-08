"""Worker persistence: _save_video, _save_playlist, dedup, enqueue."""

from __future__ import annotations

from sqlmodel import Session, select

from yam.config import settings
from yam.db import engine
from yam.models import Job, JobType, Playlist, PlaylistVideo, Video, VideoStatus
from yam.worker import (
    _save_playlist,
    _save_video,
    enqueue_pending_for_playlist,
)


def _make_video_files(vid: str, ext: str = "mp4", data: bytes = b"x" * 100) -> str:
    """Create the on-disk layout _save_video inspects; return the video path."""
    vdir = settings.videos_dir / vid
    vdir.mkdir(parents=True, exist_ok=True)
    path = vdir / f"{vid}.{ext}"
    path.write_bytes(data)
    (vdir / f"{vid}.jpg").write_bytes(b"jpg")
    return str(path)


def test_save_video_upserts_row():
    path = _make_video_files("vid1")
    info = {
        "id": "vid1",
        "title": "Test Video",
        "uploader": "Some Channel",
        "channel_id": "UC123",
        "duration": 65,
        "upload_date": "20240102",
        "width": 1920,
        "height": 1080,
        "vcodec": "avc1.640028",
        "acodec": "mp4a.40.2",
        "requested_downloads": [{"filepath": path, "ext": "mp4", "filesize": 100}],
    }
    _save_video(info)

    with Session(engine) as s:
        v = s.get(Video, "vid1")
    assert v is not None
    assert v.title == "Test Video"
    assert v.channel == "Some Channel"
    assert v.duration_s == 65
    assert v.upload_date == "20240102"
    assert v.ext == "mp4"
    assert v.filesize == 100
    assert v.status == VideoStatus.present
    assert v.thumbnail_path is not None and v.thumbnail_path.endswith(".jpg")


def test_save_video_promotes_missing_placeholder():
    # A playlist may have created a `missing` placeholder first.
    with Session(engine) as s:
        s.add(Video(id="vid2", title="vid2", status=VideoStatus.missing))
        s.commit()
    path = _make_video_files("vid2")
    _save_video(
        {
            "id": "vid2",
            "title": "Real Title",
            "requested_downloads": [{"filepath": path, "ext": "mp4"}],
        }
    )
    with Session(engine) as s:
        v = s.get(Video, "vid2")
    assert v.status == VideoStatus.present
    assert v.title == "Real Title"


def _playlist_parent_job() -> int:
    with Session(engine) as s:
        job = Job(type=JobType.playlist, url="https://youtube.com/playlist?list=PL1")
        s.add(job)
        s.commit()
        return job.id


def _playlist_info(entries):
    return {
        "id": "PL1",
        "title": "My Playlist",
        "uploader": "Chan",
        "entries": [{"id": vid, "title": t} for vid, t in entries],
    }


def test_save_playlist_creates_links_placeholders_and_jobs():
    parent = _playlist_parent_job()
    _save_playlist(_playlist_info([("a", "A"), ("b", "B")]), parent_job_id=parent)

    with Session(engine) as s:
        pl = s.get(Playlist, "PL1")
        links = s.exec(
            select(PlaylistVideo)
            .where(PlaylistVideo.playlist_id == "PL1")
            .order_by(PlaylistVideo.position)
        ).all()
        vids = {v.id: v for v in s.exec(select(Video)).all()}
        child_jobs = s.exec(select(Job).where(Job.type == JobType.video)).all()

    assert pl is not None and pl.title == "My Playlist"
    assert [(link.video_id, link.position) for link in links] == [("a", 0), ("b", 1)]
    assert vids["a"].status == VideoStatus.missing
    assert {j.target_id for j in child_jobs} == {"a", "b"}
    assert all(j.parent_job_id == parent for j in child_jobs)


def test_save_playlist_dedups_on_resync():
    parent = _playlist_parent_job()
    info = _playlist_info([("a", "A"), ("b", "B")])
    _save_playlist(info, parent_job_id=parent)
    _save_playlist(info, parent_job_id=parent)  # re-sync: jobs still queued

    with Session(engine) as s:
        child_jobs = s.exec(select(Job).where(Job.type == JobType.video)).all()
    assert len(child_jobs) == 2  # no duplicate jobs enqueued


def test_save_playlist_skips_already_present_video():
    parent = _playlist_parent_job()
    with Session(engine) as s:
        s.add(Video(id="a", title="A", status=VideoStatus.present))
        s.commit()
    _save_playlist(_playlist_info([("a", "A"), ("b", "B")]), parent_job_id=parent)

    with Session(engine) as s:
        jobs = {
            j.target_id
            for j in s.exec(select(Job).where(Job.type == JobType.video)).all()
        }
    assert jobs == {"b"}  # present "a" not re-enqueued (dedup across playlists)


def test_save_playlist_prunes_removed_links():
    parent = _playlist_parent_job()
    _save_playlist(_playlist_info([("a", "A"), ("b", "B")]), parent_job_id=parent)
    _save_playlist(_playlist_info([("a", "A")]), parent_job_id=parent)  # "b" removed

    with Session(engine) as s:
        remaining = s.exec(
            select(PlaylistVideo).where(PlaylistVideo.playlist_id == "PL1")
        ).all()
        # Video row for "b" is kept; only the link is pruned.
        assert s.get(Video, "b") is not None
    assert {link.video_id for link in remaining} == {"a"}


def test_enqueue_pending_for_playlist():
    parent = _playlist_parent_job()
    _save_playlist(_playlist_info([("a", "A"), ("b", "B")]), parent_job_id=parent)
    # Clear the jobs the sync enqueued so the pending entries have no active job.
    with Session(engine) as s:
        for j in s.exec(select(Job).where(Job.type == JobType.video)).all():
            s.delete(j)
        s.commit()

    assert enqueue_pending_for_playlist("PL1") == 2
    assert enqueue_pending_for_playlist("PL1") == 0  # now they have active jobs
