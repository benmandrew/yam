"""Keep yt-dlp current without rebuilding the image.

YouTube breaks yt-dlp's extractor clients on its own schedule, and the version
baked into the image is frozen at build time. This module gives the running
container somewhere else to look: an overlay directory under ``DATA_DIR`` that
pip can install a newer yt-dlp into.

``ensure_overlay_on_path()`` puts that directory ahead of site-packages at
startup, and only when the overlay holds a *newer* version than the baked-in
one. A rebuilt image that ships something newer therefore wins on its own, and
the overlay stops shadowing it without anyone having to clear the volume.

Stable PyPI releases only — nightlies are never fetched.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)

PACKAGE = "yt-dlp"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
# pip resolving + downloading a ~3 MB wheel; generous, since a hung request here
# only ties up one worker thread.
UPDATE_TIMEOUT = 300
PYPI_TIMEOUT = 15


class UpdateError(RuntimeError):
    """An update attempt failed in a way worth showing the user."""


def overlay_dir() -> Path:
    return settings.data_dir / "ytdlp"


def version_key(version: str) -> tuple[int, ...]:
    """Sortable key for a yt-dlp version string.

    Releases are date-shaped (``2026.8.19``) and nightlies append a build
    counter (``2026.8.29.232711.dev0``), so a per-component integer tuple orders
    both correctly. Non-numeric chunks contribute 0 rather than raising, because
    a version we cannot parse must not take the app down.
    """
    parts = []
    for chunk in version.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def overlay_version() -> str | None:
    """Version installed in the overlay, read from its ``.dist-info`` directory
    name so we never have to import the package to find out."""
    try:
        for entry in overlay_dir().glob("yt_dlp-*.dist-info"):
            return entry.name[len("yt_dlp-") : -len(".dist-info")]
    except OSError:
        return None
    return None


def baked_version() -> str | None:
    """Version installed in the image, i.e. what the current ``sys.path``
    resolves. Call before ``ensure_overlay_on_path()`` or it sees the overlay."""
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def ensure_overlay_on_path() -> None:
    """Prepend the overlay to ``sys.path`` when it is newer than the baked-in
    yt-dlp. Must run before anything imports ``yt_dlp``; see ``yam/__init__``."""
    try:
        overlay = overlay_version()
        if overlay is None:
            return
        baked = baked_version()
        if baked is not None and version_key(overlay) <= version_key(baked):
            log.info("yt-dlp overlay %s is not newer than %s; ignoring", overlay, baked)
            return
        path = str(overlay_dir())
        if path not in sys.path:
            sys.path.insert(0, path)
        log.info("using yt-dlp %s from the overlay (image ships %s)", overlay, baked)
    except Exception:  # never let a broken overlay stop the app booting
        log.exception("could not activate the yt-dlp overlay; using the baked-in copy")


def running_version() -> str:
    """The version actually imported by the downloader."""
    from yt_dlp.version import __version__

    return __version__


def latest_version() -> str:
    """Newest stable release on PyPI."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=PYPI_TIMEOUT) as resp:
            return json.load(resp)["info"]["version"]
    except Exception as exc:
        raise UpdateError(f"Could not reach PyPI to check for updates: {exc}") from exc


def update() -> tuple[str, str]:
    """Install the newest stable yt-dlp into the overlay.

    Returns ``(running, latest)``. They are equal when nothing was installed,
    either because the running copy is already current or because it is ahead of
    stable. The new version only takes effect on restart, since ``yt_dlp`` is
    already imported.
    """
    running = running_version()
    latest = latest_version()
    if version_key(running) >= version_key(latest):
        return running, running

    target = overlay_dir()
    target.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-cache-dir",
        "--upgrade",
        "--target",
        str(target),
        f"{PACKAGE}=={latest}",
    ]
    log.info("installing yt-dlp %s into %s", latest, target)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=UPDATE_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"Could not run pip: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        tail = detail[-1] if detail else f"exit status {proc.returncode}"
        raise UpdateError(f"pip failed to install yt-dlp {latest}: {tail}")
    return running, latest
