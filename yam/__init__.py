"""Yam — self-hosted YouTube video & playlist archiver."""

from .ytdlp_update import ensure_overlay_on_path

__version__ = "0.1.0"

# Activating the overlay has to happen before anything imports `yt_dlp`, and
# `yam.downloader` imports it at module scope. Package import is the one point
# guaranteed to run first, so it lives here rather than in the app lifespan.
ensure_overlay_on_path()
