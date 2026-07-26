"""Pure presentation helpers for the update dialog.

No I/O, no state — just functions that turn the updater's value objects into the
human-friendly strings and the JSON payload the front-end dialog renders. Kept
separate from the service so the formatting is trivially unit-testable and the
dialog's contract with the backend is defined in one place.

Release-note rendering is intentionally conservative: GitHub bodies are
markdown, but the desktop UI has no bundled markdown library and must stay
offline and CDN-free (see CLAUDE.md). We therefore return the raw markdown plus
a lightweight, dependency-free HTML rendering of the common subset (headings,
lists, bold/italic, code, links) with **all input escaped first**, so a release
body can never inject markup into the page.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from optionspilot.update.models import (
    DownloadProgress,
    ReleaseInfo,
    UpdateCheckResult,
    UpdatePhase,
)


def format_bytes(n: float) -> str:
    """Human-readable size, e.g. ``42.3 MB``. Binary-ish decimal (÷1000) units,
    matching how browsers and installers report download sizes."""
    if n is None or n < 0:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1000 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1000.0
    return f"{size:.1f} TB"


def format_speed(bps: float) -> str:
    if not bps or bps <= 0:
        return "—"
    return format_bytes(bps) + "/s"


def format_eta(seconds: float | None) -> str:
    """``eta_seconds`` -> ``about 2m 5s`` / ``about 40s`` / ``—``."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(round(seconds))
    if seconds < 1:
        return "less than a second"
    if seconds < 60:
        return f"about {seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"about {minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"about {hours}h {minutes}m"


def estimate_download_time(size_bytes: int, assumed_bps: float = 6_000_000) -> str:
    """A rough ETA for the dialog *before* a download starts, from a conservative
    assumed throughput (~6 MB/s ≈ 48 Mbps). Clearly an estimate, hence 'about'."""
    if not size_bytes or size_bytes <= 0:
        return "—"
    return format_eta(size_bytes / assumed_bps)


def format_published(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%B %-d, %Y") if _supports_dash() else dt.strftime("%B %d, %Y")


def _supports_dash() -> bool:
    # strftime("%-d") is POSIX-only; on Windows it raises. Detect once.
    try:
        datetime(2020, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False


# ── minimal, safe markdown -> html (offline, no dependencies) ────────────────

_INLINE_RULES = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), r"<em>\1</em>"),
    # [text](http/https url) only — never javascript: or other schemes.
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"),
     r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>'),
]


def _inline(text: str) -> str:
    for pattern, repl in _INLINE_RULES:
        text = pattern.sub(repl, text)
    return text


def render_release_notes_html(markdown: str) -> str:
    """Render the common markdown subset to safe HTML.

    Everything is HTML-escaped *before* our own tags are introduced, so no
    content from the release body can inject markup. Supports headings, bullet/
    numbered lists, and inline code/bold/italic/links — enough for a changelog.
    """
    if not markdown or not markdown.strip():
        return "<p><em>No release notes provided.</em></p>"

    out: list[str] = []
    list_open = False

    def close_list():
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = html.escape(raw.rstrip())
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 2, 6)  # #→h3, so notes never h1
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or numbered:
            if not list_open:
                out.append("<ul>")
                list_open = True
            item = (bullet or numbered).group(1)
            out.append(f"<li>{_inline(item)}</li>")
        else:
            close_list()
            out.append(f"<p>{_inline(stripped)}</p>")
    close_list()
    return "\n".join(out)


# ── dialog payloads ──────────────────────────────────────────────────────────

def release_payload(release: ReleaseInfo) -> dict:
    """The dict the front-end dialog renders for an available update."""
    installer = release.installer
    size = installer.size if installer else 0
    return {
        "version": str(release.version),
        "tag": release.tag,
        "name": release.name,
        "notes_markdown": release.notes,
        "notes_html": render_release_notes_html(release.notes),
        "published_at": release.published_at.isoformat() if release.published_at else None,
        "published_display": format_published(release.published_at),
        "prerelease": release.prerelease,
        "html_url": release.html_url,
        "download_size": size,
        "download_size_display": format_bytes(size) if size else "—",
        "estimated_time": estimate_download_time(size),
        "has_installer": release.has_installer,
    }


def check_result_payload(result: UpdateCheckResult) -> dict:
    """The dict returned to the UI from a check (manual or automatic)."""
    payload = {
        "current_version": str(result.current),
        "latest_version": str(result.latest) if result.latest else None,
        "update_available": result.update_available,
        "checked_at": result.checked_at.isoformat(),
        "error": result.error,
        "release": None,
    }
    if result.release is not None and result.update_available:
        payload["release"] = release_payload(result.release)
    return payload


def progress_payload(progress: DownloadProgress, phase: UpdatePhase) -> dict:
    """The dict the dialog polls while downloading."""
    return {
        "phase": phase.value,
        "downloaded": progress.downloaded,
        "total": progress.total,
        "percent": round(progress.percent, 1),
        "downloaded_display": format_bytes(progress.downloaded),
        "total_display": format_bytes(progress.total) if progress.total else "—",
        "speed_display": format_speed(progress.speed_bps),
        "eta_display": format_eta(progress.eta_seconds),
        "done": progress.done,
        "cancelled": progress.cancelled,
        "error": progress.error,
    }
