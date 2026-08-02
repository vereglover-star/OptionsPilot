"""Stream the installer asset to a scratch directory, with progress + cancel.

Downloads exactly one thing — the :class:`ReleaseAsset` the GitHub client
identified as the installer — into ``%TEMP%\\OptionsPilotUpdater`` (overridable
for tests). While streaming it reports a :class:`DownloadProgress` snapshot
(bytes done/total, smoothed speed, ETA) through a callback, and it honours a
``threading.Event`` so the user can cancel at any time. The file is written to a
``.part`` temp name and only renamed to its final name once fully received, so a
cancelled or failed download never leaves a truncated file that could be
mistaken for a complete installer.

Network access is the injected ``opener`` (transport.py); tests stream from an
in-memory fake with no sockets involved.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Callable

from optionspilot.core.logging_setup import get_logger
from optionspilot.update.models import DownloadProgress, DownloadResult, ReleaseAsset
from optionspilot.update.transport import (
    DEFAULT_TIMEOUT,
    NetworkError,
    Opener,
    urllib_open,
)

log = get_logger("update")

ProgressCallback = Callable[[DownloadProgress], None]

DEFAULT_CHUNK = 256 * 1024   # 256 KiB — responsive progress without syscall spam
_SUBDIR = "OptionsPilotUpdater"


def default_download_dir() -> Path:
    """``%TEMP%\\OptionsPilotUpdater`` (or the platform temp equivalent)."""
    return Path(tempfile.gettempdir()) / _SUBDIR


class _Cancelled(Exception):
    """Internal signal that the caller requested cancellation mid-stream."""


class Downloader:
    """Streams a single installer asset to disk with progress + cancellation."""

    def __init__(self, *, opener: Opener = urllib_open,
                 chunk_size: int = DEFAULT_CHUNK,
                 timeout: float = DEFAULT_TIMEOUT,
                 clock: Callable[[], float] = time.monotonic):
        self._opener = opener
        self._chunk = chunk_size
        self._timeout = timeout
        self._clock = clock

    #: Ceiling on a text asset read into memory. A SHA256SUMS manifest for this
    #: project is a few hundred bytes; 256 KB is generous and still bounds a
    #: hostile or corrupt response, which is the point — this content is read
    #: straight into memory rather than streamed to disk.
    MAX_TEXT_BYTES = 256 * 1024

    def fetch_text(self, asset: ReleaseAsset,
                   max_bytes: int | None = None) -> str | None:
        """Read a small text asset into memory, or ``None`` if unavailable.

        Used for the SHA256SUMS manifest. Returns ``None`` rather than raising
        on any failure — a checksum manifest that cannot be fetched must not
        break the update flow; it degrades the assurance level instead, and
        :func:`validation.validate` decides what that means.

        Note the asymmetry with :meth:`download`: that method is deliberately
        restricted to a recognised installer, because its output is EXECUTED.
        This one is only ever parsed for a hex digest.
        """
        limit = self.MAX_TEXT_BYTES if max_bytes is None else int(max_bytes)
        try:
            with self._opener(asset.download_url, {}, self._timeout) as resp:
                raw = resp.read(limit + 1)
        except Exception:  # noqa: BLE001 - any failure is "no manifest"
            log.debug("could not fetch %s", asset.name, exc_info=True)
            return None
        if raw is None:
            return None
        if len(raw) > limit:
            log.warning("%s exceeds %d bytes — refusing to parse it",
                        asset.name, limit)
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        return str(raw)

    def download(self, asset: ReleaseAsset, *,
                 dest_dir: Path | str | None = None,
                 progress_cb: ProgressCallback | None = None,
                 cancel: "object | None" = None) -> DownloadResult:
        """Download ``asset`` into ``dest_dir``.

        ``cancel`` is any object with an ``is_set()`` method (a
        ``threading.Event``); when it becomes set the download stops promptly and
        returns a cancelled result. Returns a :class:`DownloadResult`; the only
        exceptions that escape are truly unexpected programming errors, not
        network or cancellation conditions.
        """
        dest_dir = Path(dest_dir) if dest_dir is not None else default_download_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / asset.name
        part = dest_dir / (asset.name + ".part")

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        def emit(p: DownloadProgress) -> None:
            if progress_cb is not None:
                try:
                    progress_cb(p)
                except Exception:  # noqa: BLE001 - a UI callback must never break the download
                    log.debug("progress callback raised; ignoring", exc_info=True)

        try:
            if cancelled():
                raise _Cancelled()
            self._cleanup(part)
            written = self._stream(asset, part, cancelled, emit)
        except _Cancelled:
            self._cleanup(part)
            result = DownloadResult(path=None, size=0, cancelled=True)
            emit(DownloadProgress(cancelled=True, done=True))
            return result
        except NetworkError as exc:
            self._cleanup(part)
            result = DownloadResult(path=None, size=0, error=exc.message)
            emit(DownloadProgress(error=exc.message, done=True))
            return result
        except OSError as exc:
            self._cleanup(part)
            msg = self._friendly_os_error(exc)
            result = DownloadResult(path=None, size=0, error=msg)
            emit(DownloadProgress(error=msg, done=True))
            return result

        # Fully received — atomically promote .part to the final name.
        os.replace(part, final)
        emit(DownloadProgress(downloaded=written, total=asset.size or written,
                              done=True, path=str(final)))
        return DownloadResult(path=str(final), size=written)

    # ── internals ────────────────────────────────────────────────────────────
    def _stream(self, asset: ReleaseAsset, part: Path,
                cancelled: Callable[[], bool], emit: ProgressCallback) -> int:
        headers = {"Accept": "application/octet-stream"}
        start = self._clock()
        last_emit = 0.0
        downloaded = 0
        with self._opener(asset.download_url, headers, self._timeout) as resp:
            total = self._content_length(resp) or asset.size or 0
            with open(part, "wb") as fh:
                while True:
                    if cancelled():
                        raise _Cancelled()
                    chunk = resp.read(self._chunk)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    now = self._clock()
                    # Throttle UI updates to ~10/sec; always emit the first chunk.
                    if now - last_emit >= 0.1 or last_emit == 0.0:
                        last_emit = now
                        emit(self._progress(downloaded, total, start, now))
        return downloaded

    def _progress(self, downloaded: int, total: int, start: float,
                  now: float) -> DownloadProgress:
        elapsed = max(now - start, 1e-6)
        speed = downloaded / elapsed
        eta = ((total - downloaded) / speed) if (total and speed > 0) else None
        return DownloadProgress(downloaded=downloaded, total=total,
                                speed_bps=speed, eta_seconds=eta)

    @staticmethod
    def _content_length(resp) -> int:
        for getter in ("getheader", "get"):
            headers = getattr(resp, "headers", None)
            fn = getattr(resp, getter, None) or (getattr(headers, getter, None)
                                                 if headers else None)
            if fn:
                try:
                    val = fn("Content-Length")
                    if val:
                        return int(val)
                except (TypeError, ValueError):
                    pass
        return 0

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            log.debug("could not remove partial download %s", path, exc_info=True)

    @staticmethod
    def _friendly_os_error(exc: OSError) -> str:
        import errno
        if exc.errno == errno.ENOSPC:
            return ("Not enough free disk space to download the update. "
                    "Free some space and try again.")
        return f"Could not save the update: {exc.strerror or exc}"
