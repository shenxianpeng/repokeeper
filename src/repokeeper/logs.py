"""Centralized logging configuration for RepoKeeper.

Provides a ``setup_logging`` function that configures structured logging
with sensible defaults for both CLI and GitHub Actions environments.

Supports two output formats:

- **text** (default) — human-readable lines with timestamp and level.
- **json** — one JSON object per line, ideal for log aggregation tools
  (Datadog, Splunk, ELK, etc.).  Enable via ``RKP_LOG_FORMAT=json``.

The ``JsonFormatter`` emits fields that follow the Elastic Common Schema
convention: ``@timestamp``, ``level``, ``logger``, ``message``, plus any
extra fields passed via ``logging.LogRecord`` attributes.
"""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from datetime import datetime, timezone

# Module-level logger for internal use
logger = logging.getLogger("repokeeper")


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Output fields:
        ``@timestamp``, ``level``, ``logger``, ``module``, ``message``,
        plus any extra fields set on the record (e.g. ``issue_number``,
        ``pr_number``, ``cost_usd``, ``token_count``).
    """

    _RESERVED = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "getMessage", "levelname", "levelno", "lineno",
        "module", "msecs", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread",
        "threadName",
    })

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, object] = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                # Only include JSON-serializable extras
                try:
                    _json.dumps(value)
                    obj[key] = value
                except (TypeError, ValueError):
                    obj[key] = str(value)

        if record.exc_info and record.exc_info[1]:
            obj["exception"] = str(record.exc_info[1])

        return _json.dumps(obj, default=str, ensure_ascii=False)


def _detect_format() -> str:
    """Detect the log format from environment or CI context.

    Returns ``"json"`` or ``"text"``.
    """
    fmt = os.environ.get("RKP_LOG_FORMAT", "").lower()
    if fmt in ("json", "jsonl"):
        return "json"
    if fmt in ("text", "human", ""):
        return "text"
    # GHA environment: stay with text by default (structured logs
    # are less readable in raw Actions output).
    return "text"


def setup_logging(level: int | str = "INFO", fmt: str | None = None) -> None:
    """Configure the root RepoKeeper logger with a consistent format.

    In GitHub Actions, emits log lines with ``[repokeeper]`` prefix so
    they render cleanly in workflow step output.  On a local terminal
    the format includes a timestamp.

    Set ``RKP_LOG_FORMAT=json`` (or pass ``fmt="json"``) to get
    JSON-structured output suitable for ingestion by log aggregators.

    Args:
        level: Logging level as a string (``"DEBUG"``, ``"INFO"``, etc.)
               or an int from the :mod:`logging` module.
        fmt: Output format: ``"text"`` (default), ``"json"``, or ``None``
             to auto-detect from ``RKP_LOG_FORMAT``.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    if fmt is None:
        fmt = _detect_format()

    handler: logging.Handler

    if fmt == "json":
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
    elif os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub Actions: minimal prefix, no timestamp needed
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[repokeeper] %(message)s"))
    else:
        # Local terminal: include timestamp and level
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)-5s] %(name)s: %(message)s")
        )

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


_logging_initialized: bool = False


def _ensure_logging(fmt: str | None = None) -> None:
    """Lazily call :func:`setup_logging` once per process.

    Args:
        fmt: Forwarded to :func:`setup_logging` on first call.
    """
    global _logging_initialized
    if not _logging_initialized:
        setup_logging(fmt=fmt)
        _logging_initialized = True


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the ``repokeeper`` namespace.

    Automatically configures logging on first use if it has not been
    explicitly set up.

    Args:
        name: Logger name, e.g. ``"agent"`` (the ``"repokeeper."`` prefix
              is added automatically).

    Returns:
        A configured :class:`logging.Logger`.
    """
    _ensure_logging()
    return logging.getLogger(f"repokeeper.{name}")


# ── Helper for structured logging with extra fields ─────────────────────


def log_event(level: int, message: str, **extra: object) -> None:
    """Log a message with structured extra fields.

    Extra fields (e.g. ``issue_number=42``, ``cost_usd=0.0015``) are
    emitted as top-level keys in JSON mode and ignored in text mode.

    Args:
        level: A :mod:`logging` level constant (e.g. ``logging.INFO``).
        message: Human-readable message.
        **extra: Extra key-value pairs to attach to the log record.
    """
    logger.log(level, message, extra=extra)
