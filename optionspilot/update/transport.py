"""The only module that touches the network.

Everything above this (github_api, downloader) receives an ``opener`` callable
and never imports ``urllib`` directly, so tests inject a fake transport and run
fully offline and deterministically. The real implementation here centralises
the boring-but-critical networking policy the milestone calls for:

  * conservative **timeouts** (a slow/hung GitHub must never delay the app);
  * bounded **retries with exponential backoff** on transient failures;
  * **offline tolerance** — a name-resolution/connection error is a normal,
    expected outcome, surfaced as :class:`NetworkError`, never a crash;
  * **proxy compatibility** — ``urllib`` honours ``HTTP(S)_PROXY`` env vars via
    the default opener, so corporate proxies work with no extra code;
  * a descriptive **User-Agent** (GitHub rejects requests without one).

The ``opener`` protocol is intentionally tiny: ``opener(url, headers, timeout)``
returns a file-like/response object that is a context manager exposing
``.read(n)``, ``.status``/``.getcode()`` and ``.headers`` — satisfied by both
``http.client.HTTPResponse`` (real) and the fakes in the test suite.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from optionspilot import __version__
from optionspilot.core.logging_setup import get_logger

log = get_logger("update")

USER_AGENT = f"OptionsPilot-Updater/{__version__} (+https://github.com/vereglover-star/OptionsPilot)"

# Conservative by design: the launch-time check runs in the background, but even
# so a hung socket must free itself quickly and never accumulate.
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 2          # => up to 3 attempts total
DEFAULT_BACKOFF = 0.5        # seconds; doubles each retry


class NetworkError(Exception):
    """A transport-level failure (offline, DNS, timeout, HTTP >= 400).

    ``retryable`` distinguishes transient failures (timeouts, 5xx, connection
    resets) from permanent ones (404, 401) so callers can decide whether a
    retry is worthwhile. ``status`` carries the HTTP code when there was one.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


class Response(Protocol):  # pragma: no cover - structural type only
    status: int

    def read(self, amt: int = ...) -> bytes: ...
    def __enter__(self) -> "Response": ...
    def __exit__(self, *exc) -> bool | None: ...


# opener(url, headers, timeout) -> Response (context manager)
Opener = Callable[[str, dict, float], Response]


def urllib_open(url: str, headers: dict | None = None,
                timeout: float = DEFAULT_TIMEOUT) -> Response:
    """Default real transport: an authenticated-if-configured GET via urllib.

    Raises :class:`NetworkError` (never a bare ``URLError``/``HTTPError``) so the
    layers above have a single exception type to reason about. urllib's default
    opener consults the standard proxy environment variables automatically.
    """
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https host
    except urllib.error.HTTPError as exc:
        # 4xx are permanent; 5xx are worth retrying.
        raise NetworkError(f"HTTP {exc.code} for {url}", status=exc.code,
                           retryable=exc.code >= 500) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Offline / DNS / connection reset / timeout — all transient.
        raise NetworkError(f"network error for {url}: {exc}", retryable=True) from exc


def with_retries(fn: Callable[[], "Response"], *, retries: int = DEFAULT_RETRIES,
                 backoff: float = DEFAULT_BACKOFF,
                 sleep: Callable[[float], None] = time.sleep):
    """Call ``fn`` with bounded exponential backoff on *retryable* failures.

    A permanent failure (e.g. HTTP 404) is re-raised immediately — retrying a
    404 only wastes time. Returns whatever ``fn`` returns on success.
    """
    attempt = 0
    delay = backoff
    while True:
        try:
            return fn()
        except NetworkError as exc:
            attempt += 1
            if not exc.retryable or attempt > retries:
                raise
            log.debug("update transport retry %d/%d after %.1fs: %s",
                      attempt, retries, delay, exc.message)
            sleep(delay)
            delay *= 2
