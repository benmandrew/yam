"""yt-dlp integration: download a single video at highest quality.

Per PLAN.md: best video + best audio, merged to mkv with *no re-encode*, plus
sidecar metadata (info.json) and a jpg thumbnail. A progress callback receives
percent/speed/eta so the worker can surface live progress.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from yt_dlp import YoutubeDL

from .config import settings

# (percent 0..100, speed str|None, eta str|None)
ProgressCb = Callable[[float, str | None, str | None], None]


def _ydl_opts(progress_hook: Callable[[dict], None]) -> dict[str, Any]:
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
            }
        )
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    return opts


def download_video(url: str, on_progress: ProgressCb | None = None) -> dict[str, Any]:
    """Download a single video and return yt-dlp's info dict."""

    def hook(d: dict) -> None:
        if on_progress is None:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100) if total else 0.0
            on_progress(pct, d.get("_speed_str"), d.get("_eta_str"))
        elif d["status"] == "finished":
            on_progress(100.0, None, None)

    with YoutubeDL(_ydl_opts(hook)) as ydl:
        info = ydl.extract_info(url, download=True)
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
