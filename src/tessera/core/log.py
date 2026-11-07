"""Logging setup: readable lines on the console, JSON lines in the data home."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from tessera.core.clock import isoformat, utc_now

_CONFIGURED_MARK = "_tessera_handler"


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line. Extra fields passed via ``extra={...}`` are included."""

    _STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "at": isoformat(utc_now()),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in self._STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(verbosity: int = 0, log_dir: Path | None = None) -> None:
    """Configure the ``tessera`` logger.

    ``verbosity``: -1 quiet (warnings), 0 normal (info), 1+ debug.
    ``log_dir``: when given, also write JSON lines to ``log_dir/tessera.log`` (rotated).
    Safe to call more than once; previous Tessera handlers are replaced.
    """
    logger = logging.getLogger("tessera")
    for handler in list(logger.handlers):
        if getattr(handler, _CONFIGURED_MARK, False):
            logger.removeHandler(handler)
            handler.close()

    level = logging.WARNING if verbosity < 0 else logging.DEBUG if verbosity > 0 else logging.INFO
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    setattr(console, _CONFIGURED_MARK, True)
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "tessera.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonLineFormatter())
        setattr(file_handler, _CONFIGURED_MARK, True)
        logger.addHandler(file_handler)
