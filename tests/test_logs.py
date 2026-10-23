"""Tests for the centralized logging configuration (logs.py)."""

from __future__ import annotations

import json
import logging
import sys

import pytest

from repokeeper import logs
from repokeeper.logs import (
    JsonFormatter,
    _detect_format,
    _ensure_logging,
    get_logger,
    log_event,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logger(monkeypatch):
    """Keep the module-level logger and init flag isolated per test."""
    monkeypatch.setattr(logs, "_logging_initialized", False)
    monkeypatch.delenv("RKP_LOG_FORMAT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    yield
    logs.logger.handlers.clear()
    logs.logger.propagate = True


# ── JsonFormatter ────────────────────────────────────────────────────────────


def _record(message: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="repokeeper.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_standard_and_extra_fields():
    record = _record("issue %s", issue_number=42, cost_usd=0.0015)
    record.args = ("done",)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "repokeeper.test"
    assert payload["message"] == "issue done"
    assert payload["issue_number"] == 42
    assert payload["cost_usd"] == 0.0015
    assert "@timestamp" in payload
    assert "args" not in payload
    assert "exception" not in payload


def test_json_formatter_stringifies_non_serializable_extras_and_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = _record("failed", payload={1, 2})
    record.exc_info = exc_info

    payload = json.loads(JsonFormatter().format(record))

    assert payload["payload"] in ("{1, 2}", "{2, 1}")
    assert payload["exception"] == "boom"


# ── _detect_format ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("json", "json"),
        ("JSONL", "json"),
        ("text", "text"),
        ("human", "text"),
        ("", "text"),
        ("xml", "text"),
    ],
)
def test_detect_format_reads_environment(monkeypatch, value, expected):
    monkeypatch.setenv("RKP_LOG_FORMAT", value)

    assert _detect_format() == expected


# ── setup_logging ────────────────────────────────────────────────────────────


def test_setup_logging_json_format_uses_json_formatter_on_stderr():
    setup_logging(level="debug", fmt="json")

    handler = logs.logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert isinstance(handler.formatter, JsonFormatter)
    assert logs.logger.level == logging.DEBUG
    assert logs.logger.propagate is False


def test_setup_logging_github_actions_uses_plain_prefix(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    setup_logging(level=logging.WARNING)

    handler = logs.logger.handlers[0]
    assert handler.stream is sys.stdout
    assert handler.formatter._fmt == "[repokeeper] %(message)s"
    assert logs.logger.level == logging.WARNING


def test_setup_logging_local_terminal_includes_timestamp(monkeypatch):
    monkeypatch.setenv("RKP_LOG_FORMAT", "text")

    setup_logging(level="not-a-level")

    handler = logs.logger.handlers[0]
    assert handler.stream is sys.stderr
    assert "%(asctime)s" in handler.formatter._fmt
    assert logs.logger.level == logging.INFO


def test_setup_logging_auto_detects_json_from_environment(monkeypatch):
    monkeypatch.setenv("RKP_LOG_FORMAT", "json")

    setup_logging()

    assert isinstance(logs.logger.handlers[0].formatter, JsonFormatter)


# ── _ensure_logging / get_logger / log_event ─────────────────────────────────


def test_ensure_logging_configures_once(monkeypatch):
    calls = []
    monkeypatch.setattr(logs, "setup_logging", lambda fmt=None: calls.append(fmt))

    _ensure_logging(fmt="json")
    _ensure_logging(fmt="text")

    assert calls == ["json"]
    assert logs._logging_initialized is True


def test_get_logger_returns_namespaced_child_logger():
    child = get_logger("agent")

    assert child.name == "repokeeper.agent"
    assert logs._logging_initialized is True
    assert logs.logger.handlers


def test_log_event_attaches_extra_fields(capsys):
    setup_logging(fmt="json")

    log_event(logging.INFO, "costed", cost_usd=0.5, pr_number=7)

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["message"] == "costed"
    assert payload["cost_usd"] == 0.5
    assert payload["pr_number"] == 7
