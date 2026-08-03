"""Application-wide logging.

One rotating file per subsystem (engine, risk, broker, data, journal) plus a
combined app.log and console output. Format is structured enough that any trade
decision can be reconstructed from the logs alone.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from optionspilot.config.settings import LoggingConfig

SUBSYSTEMS = ("engine", "risk", "broker", "data", "journal", "backtest", "ui")

#: Loggers owned by a dependency that this application adopts rather than lets
#: the dependency configure. See `uvicorn_logging_kwargs`.
ADOPTED = ("uvicorn",)

_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)-8s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class _UTCFormatter(logging.Formatter):
    import time as _time

    converter = _time.gmtime


def setup_logging(config: LoggingConfig, base_dir: str | Path = ".") -> None:
    """Idempotent: safe to call more than once (replaces our handlers)."""
    log_dir = Path(base_dir) / config.dir
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = _UTCFormatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger("optionspilot")
    root.setLevel(config.level)
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()

    # Windowed (no-console) builds have sys.stderr = None; file logs only.
    import sys
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    combined = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=config.max_bytes,
        backupCount=config.backup_count, encoding="utf-8",
    )
    combined.setFormatter(formatter)
    root.addHandler(combined)

    for name in SUBSYSTEMS:
        sub = logging.getLogger(f"optionspilot.{name}")
        for h in list(sub.handlers):
            sub.removeHandler(h)
            h.close()
        fh = logging.handlers.RotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=config.max_bytes,
            backupCount=config.backup_count, encoding="utf-8",
        )
        fh.setFormatter(formatter)
        sub.addHandler(fh)  # propagates to root too -> also lands in app.log

    # Adopted third-party loggers. uvicorn is started with `log_config=None`
    # (see `uvicorn_logging_kwargs`), so nothing configures these unless we do —
    # and the real root logger has no handlers here, because this function owns
    # the `optionspilot` tree only. Left alone, a uvicorn warning would fall
    # through to `logging.lastResort`, which writes to `sys.stderr`: None in a
    # windowed build. Dropping the transport's errors in exactly the
    # configuration that cannot show a console is not an acceptable outcome.
    for name in ADOPTED:
        adopted = logging.getLogger(name)
        for h in list(adopted.handlers):
            adopted.removeHandler(h)
            h.close()
        for handler in root.handlers:
            adopted.addHandler(handler)


def uvicorn_logging_kwargs() -> dict:
    """Keyword arguments that stop uvicorn configuring logging for us.

    **A windowed PyInstaller build has ``sys.stdout is sys.stderr is None``,
    and uvicorn's default logging config dies on that.**
    ``uvicorn.logging.DefaultFormatter.__init__`` ends with
    ``self.use_colors = sys.stdout.isatty()``, and `Config.__init__` calls
    `configure_logging()`, which runs `dictConfig` over that default — so simply
    *constructing* a `uvicorn.Config` raised ``ValueError: Unable to configure
    formatter 'default'`` and the packaged app died before drawing a window. No
    request, no bind, no server.

    Passing ``use_colors=False`` would stop the crash and leave uvicorn's
    handlers pointed at ``ext://sys.stderr`` — that is, at None — trading a loud
    failure for records that silently vanish. So uvicorn configures nothing:
    `setup_logging` is the single owner of this application's logging, it
    already knows stdio can be absent, and it adopts uvicorn's loggers so their
    records still reach `app.log`.

    Returned from one place rather than written at each call site: there are two
    today (`ui/desktop.py`'s embedded transport and `ui/server.py::serve`) and
    the reasoning is not obvious from `log_config=None` on its own.
    """
    return {"log_config": None}


def get_logger(subsystem: str) -> logging.Logger:
    return logging.getLogger(f"optionspilot.{subsystem}")
