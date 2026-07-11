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


def get_logger(subsystem: str) -> logging.Logger:
    return logging.getLogger(f"optionspilot.{subsystem}")
