"""Download phases and the merge progress estimate."""

from __future__ import annotations

import time

from sqlmodel import Session

from yam import downloader
from yam.db import engine
from yam.downloader import MergeProgress, dir_size, merge_percent
from yam.models import Job, JobPhase, JobStatus, JobType
from yam.worker import _download_video_job


def test_dir_size_sums_files_only(tmp_path):
    (tmp_path / "a.part").write_bytes(b"x" * 10)
    (tmp_path / "b.part").write_bytes(b"x" * 5)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c").write_bytes(b"x" * 100)
    assert dir_size(tmp_path) == 15


def test_dir_size_of_missing_dir_is_zero(tmp_path):
    assert dir_size(tmp_path / "gone") == 0


def test_merge_percent_tracks_growth():
    assert merge_percent(1000, 1000, 1000) == 0.0
    assert merge_percent(1500, 1000, 1000) == 50.0
    # Clamped: the merge is only complete once ffmpeg exits.
    assert merge_percent(2500, 1000, 1000) == 99.0
    # Never negative, and never a divide-by-zero.
    assert merge_percent(900, 1000, 1000) == 0.0
    assert merge_percent(2000, 1000, 0) == 0.0


def test_merge_progress_reports_phase_and_growth(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_MERGE_POLL_INTERVAL", 0.01)
    seen: list[tuple[JobPhase, float]] = []

    stream = tmp_path / "vid.f137.mp4.part"
    stream.write_bytes(b"x" * 100)

    merge = MergeProgress(lambda phase, pct, speed, eta: seen.append((phase, pct)))
    merge.note_stream(str(stream), 100)
    merge.start()
    # Half the merged output written.
    (tmp_path / "vid.temp.mp4.part").write_bytes(b"x" * 50)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and len(seen) < 2:
        time.sleep(0.01)
    merge.stop()

    assert seen[0] == (JobPhase.merging, 0.0)
    assert seen[-1][0] == JobPhase.merging
    assert 40 <= seen[-1][1] <= 60


def test_merge_progress_without_sizes_still_announces_phase(tmp_path):
    seen = []
    merge = MergeProgress(lambda phase, pct, speed, eta: seen.append((phase, pct)))
    merge.start()  # no note_stream: nothing to measure against
    merge.stop()
    assert seen == [(JobPhase.merging, 0.0)]


def test_download_job_records_each_phase(monkeypatch):
    with Session(engine) as s:
        job = Job(type=JobType.video, url="u", status=JobStatus.running)
        s.add(job)
        s.commit()
        job_id = job.id

    written: list[tuple[str, float]] = []

    def fake_download(url, on_progress):
        for phase, pct in [
            (JobPhase.downloading_video, 40.0),
            (JobPhase.downloading_audio, 10.0),
            (JobPhase.merging, 50.0),
        ]:
            on_progress(phase, pct, None, None)
            with Session(engine) as s:
                row = s.get(Job, job_id)
                written.append((row.phase, row.progress))
        return {"id": "vid1"}

    monkeypatch.setattr("yam.worker.download_video", fake_download)
    monkeypatch.setattr("yam.worker._save_video", lambda info: None)

    _download_video_job(job_id, "u")

    # Each phase change writes through immediately, despite the 1s throttle.
    assert written == [
        (JobPhase.downloading_video, 40.0),
        (JobPhase.downloading_audio, 10.0),
        (JobPhase.merging, 50.0),
    ]
    with Session(engine) as s:
        row = s.get(Job, job_id)
    assert row.status == JobStatus.done
    assert row.phase is None
    assert row.progress == 100.0


def test_jobs_page_shows_phase_label(client):
    with Session(engine) as s:
        s.add(
            Job(
                type=JobType.video,
                url="u",
                status=JobStatus.running,
                phase=JobPhase.merging,
                progress=42.0,
            )
        )
        s.commit()

    body = client.get("/api/jobs").text
    assert "Merging audio and video" in body
    assert "42.0%" in body


def test_jobs_page_sweeps_while_finishing(client):
    with Session(engine) as s:
        s.add(
            Job(
                type=JobType.video,
                url="u",
                status=JobStatus.running,
                phase=JobPhase.finishing,
            )
        )
        s.commit()

    body = client.get("/api/jobs").text
    assert "bar--sweeping" in body
    assert "Finishing up" in body
    assert "0.0%" not in body  # no percentage to show for this phase


def test_ydl_opts_register_both_hook_kinds():
    """A misspelled option key would be ignored in silence, so check yt-dlp
    actually picked up the postprocessor hook the merge phase depends on."""
    from yt_dlp import YoutubeDL

    from yam.downloader import _ydl_opts

    def progress(d):
        pass

    def postprocessor(d):
        pass

    with YoutubeDL(_ydl_opts(progress, postprocessor)) as ydl:
        assert progress in ydl._progress_hooks
        assert postprocessor in ydl._postprocessor_hooks
