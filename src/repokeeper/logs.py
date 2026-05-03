"""Centralized logging configuration for RepoKeeper.

Provides a ``setup_logging`` function that configures structured logging
with sensible defaults for both CLI and GitHub Actions environments.
"""

from __future__ import annotations

import logging
import os
import sys

# Module-level logger for internal use
logger = logging.getLogger("repokeeper")


def setup_logging(level: int | str = "INFO") -> None:
    """Configure the root RepoKeeper logger with a consistent format.

    In GitHub Actions, emits log lines with ``[repokeeper]`` prefix so
    they render cleanly in workflow step output.  On a local terminal
    the format includes a timestamp.

    Args:
        level: Logging level as a string (``"DEBUG"``, ``"INFO"``, etc.)
               or an int from the :mod:`logging` module.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    handler: logging.Handler
    fmt: str

    if os.environ.get("GITHUB_ACTIONS") == "true":
        # GitHub Actions: minimal prefix, no timestamp needed
        fmt = "[repokeeper] %(message)s"
        handler = logging.StreamHandler(sys.stdout)
    else:
        # Local terminal: include timestamp and level
        fmt = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(logging.Formatter(fmt))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the ``repokeeper`` namespace.

    Args:
        name: Logger name, e.g. ``"repokeeper.agent"``.

    Returns:
        A configured :class:`logging.Logger`.
    """
    return logging.getLogger(f"repokeeper.{name}")
