"""yt-dlp integration: download a single video at highest quality.

Best video + best audio, merged to mkv with *no re-encode*, plus
sidecar metadata (info.json) and a jpg thumbnail. A progress callback receives
phase/percent/speed/eta so the worker can surface live progress.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from .config import settings
from .models import JobPhase

# (phase, percent 0..100, speed str|None, eta str|None)
ProgressCb = Callable[[JobPhase, float, str | None, str | None], None]

_MERGE_POLL_INTERVAL = 0.5  # seconds between merge size samples
_MERGE_MAX_PERCENT = 99.0  # the merge only reads 100% once ffmpeg has exited


def friendly_error(exc: Exception) -> str:
    """Turn a raw yt-dlp error into an actionable message. YouTube's anti-bot
    gate ("Sign in to confirm you're not a bot") is the common one and is fixed
    by supplying an authenticated cookies.txt via COOKIES_FILE."""
    msg = str(exc)
    lowered = msg.lower()
    if "confirm you're not a bot" in lowered or "confirm you are not a bot" in lowered:
        hint = (
            "YouTube is asking to confirm you're not a bot. Export a logged-in "
            "cookies.txt and mount it, then set COOKIES_FILE to its path "
            "(see the README)."
        )
        return (
            hint
            if settings.cookies_file is None
            else f"{hint} Current COOKIES_FILE was rejected — re-export fresh cookies."
        )
    return msg


def _ydl_opts(
    progress_hook: Callable[[dict], None],
    postprocessor_hook: Callable[[dict], None],
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        # Prefer H.264 + AAC in mp4 for universal playback (Safari/iOS included);
        # fall back to any mp4, then best-available. No re-encode (muxing only).
        # Tradeoff: H.264 caps at ~1080p (YouTube offers no H.264 above that).
        "format": "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4/webm/mkv",
        "outtmpl": {"default": str(settings.videos_dir / "%(id)s" / "%(id)s.%(ext)s")},
        "writethumbnail": True,
        "writeinfojson": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    }
    if settings.download_subtitles:
        opts.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["en"],
                # WebVTT is the only sub format a browser <track> can read; YouTube
                # serves it natively so no ffmpeg conversion is needed.
                "subtitlesformat": "vtt",
            }
        )
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    return opts


def dir_size(path: Path) -> int:
    """Total size of the files directly in ``path``. Anything that vanishes
    mid-scan is skipped — yt-dlp and ffmpeg rename temporary files as they go."""
    total = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def merge_percent(current: int, base: int, expected: int) -> float:
    """Percentage of the merge done, from the download directory's growth."""
    if expected <= 0:
        return 0.0
    written = max(current - base, 0)
    return min(written / expected * 100, _MERGE_MAX_PERCENT)


class MergeProgress:
    """Estimate ffmpeg's merge progress from the download directory's growth.

    yt-dlp reports nothing while ffmpeg muxes the separate video and audio
    streams into one file, which is the longest part of a large download. The
    merge is a stream copy, so the merged file ends up about the size of the two
    streams put together. Sampling the directory total against its size when the
    merge started gives a usable estimate and needs no knowledge of yt-dlp's
    temporary file names.
    """

    def __init__(self, on_progress: ProgressCb | None) -> None:
        self._on_progress = on_progress
        self._dir: Path | None = None
        self._stream_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def note_stream(self, path: str | None, size: int | None) -> None:
        """Record a finished stream download: where it landed and how big it is."""
        if path:
            self._dir = Path(path).parent
        if size:
            self._stream_bytes += size

    def start(self) -> None:
        """Announce the merge phase, and sample the directory if we can size it."""
        if self._on_progress is None:
            return
        self._on_progress(JobPhase.merging, 0.0, None, None)
        if self._thread is not None or self._dir is None or self._stream_bytes <= 0:
            return
        base = dir_size(self._dir)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll, args=(base,), daemon=True, name="yam-merge-progress"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=_MERGE_POLL_INTERVAL * 4)

    def _poll(self, base: int) -> None:
        while not self._stop.wait(_MERGE_POLL_INTERVAL):
            if self._dir is None or self._on_progress is None:
                return
            pct = merge_percent(dir_size(self._dir), base, self._stream_bytes)
            self._on_progress(JobPhase.merging, pct, None, None)


def _download_phase(info: dict) -> JobPhase:
    """A merged download fetches video and audio as two separate files, each
    with its own 0..100 run. Name them apart so the reset reads as a new phase."""
    audio_only = info.get("vcodec") in (None, "none") and info.get("acodec") not in (
        None,
        "none",
    )
    return JobPhase.downloading_audio if audio_only else JobPhase.downloading_video


def download_video(url: str, on_progress: ProgressCb | None = None) -> dict[str, Any]:
    """Download a single video and return yt-dlp's info dict."""
    merge = MergeProgress(on_progress)

    def hook(d: dict) -> None:
        if on_progress is None:
            return
        phase = _download_phase(d.get("info_dict") or {})
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100) if total else 0.0
            on_progress(phase, pct, d.get("_speed_str"), d.get("_eta_str"))
        elif d["status"] == "finished":
            size = d.get("total_bytes") or d.get("downloaded_bytes")
            merge.note_stream(d.get("filename"), size)
            on_progress(phase, 100.0, None, None)

    def pp_hook(d: dict) -> None:
        # Only the merger is worth a phase of its own; the other postprocessors
        # (thumbnail conversion, moving files into place) finish in moments.
        if d.get("postprocessor") != "Merger":
            return
        if d.get("status") == "started":
            merge.start()
        elif d.get("status") == "finished":
            merge.stop()
            if on_progress is not None:
                on_progress(JobPhase.finishing, 0.0, None, None)

    try:
        with YoutubeDL(_ydl_opts(hook, pp_hook)) as ydl:
            info = ydl.extract_info(url, download=True)
    finally:
        # Guarantees no sampler thread outlives the download it belongs to.
        merge.stop()
    # noplaylist=True guarantees a single-video info dict here.
    return info


def enumerate_playlist(url: str) -> dict[str, Any]:
    """Cheaply list a playlist's entries (no per-video network calls, no
    download). Returns yt-dlp's playlist info dict, with ``entries``."""
    opts: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)
