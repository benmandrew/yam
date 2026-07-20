"""Thumbnail post-processing: downscale YouTube thumbnails to display size.

yt-dlp fetches YouTube's highest-resolution thumbnail (up to 1280x720, often
100-200 KB), but the library grid renders each one at ~240px wide. We downscale
once to ``THUMBNAIL_MAX_WIDTH`` (2x the display size, for retina) so the browser
isn't pulling a multi-hundred-KB image just to draw a small card. ffmpeg is
already a runtime dep (stream muxing), so we reuse it rather than adding Pillow.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("yam.thumbnails")

# 2x the ~240px grid card so it stays crisp on high-DPI displays.
THUMBNAIL_MAX_WIDTH = 480


def _probe_width(path: Path) -> int | None:
    """Return the pixel width of an image via ffprobe, or None if it can't be
    read (treated as "resize it anyway" — the scale filter never upscales)."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return int(out)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def resize_thumbnail(path: str | Path, max_width: int = THUMBNAIL_MAX_WIDTH) -> bool:
    """Downscale a JPEG thumbnail in place to at most ``max_width`` px wide.

    Idempotent and safe to call repeatedly: images already at/under the cap are
    left untouched, and the scale filter never upscales. The rewrite goes to a
    temp file then ``os.replace``s the original, so a crash mid-encode can't
    leave a truncated thumbnail. Failures are logged and swallowed — an oversized
    thumbnail is a cosmetic issue, not a reason to fail a download.

    Returns True if the file was rewritten, False if left as-is (or on failure).
    """
    p = Path(path)
    if not p.is_file():
        return False
    width = _probe_width(p)
    if width is not None and width <= max_width:
        return False

    fd, tmp = tempfile.mkstemp(suffix=".jpg", dir=str(p.parent))
    os.close(fd)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(p),
                # min() caps width without ever upscaling; -2 keeps aspect with
                # an even height (some JPEG paths dislike odd dimensions).
                "-vf",
                f"scale='min({max_width},iw)':-2",
                "-q:v",
                "3",
                tmp,
            ],
            check=True,
            capture_output=True,
        )
        os.replace(tmp, p)
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("thumbnail resize failed for %s: %s", p, exc)
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False
