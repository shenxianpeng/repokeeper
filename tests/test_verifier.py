"""Tests for pre-commit verification helpers (verifier.py)."""

from __future__ import annotations

import subprocess

import pytest

from repokeeper import verifier
from repokeeper.verifier import (
    VerificationResult,
    _has_python_files,
    _normalize_command,
    _output_excerpt,
    discover_verification_commands,
    format_verification_failures,
    format_verification_report,
    run_verification_commands,
    summarize_verification_results,
)

# ── VerificationResult ───────────────────────────────────────────────────────


def test_verification_result_properties():
    result = VerificationResult(command=["pytest", "tests dir"], returncode=0, stdout="", stderr="")

    assert result.display_command == "pytest 'tests dir'"
    assert result.passed is True
    assert VerificationResult(["x"], 1, "", "").passed is False


# ── _has_python_files / _normalize_command ───────────────────────────────────


def test_has_python_files_ignores_skipped_directories(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("x = 1\n")

    assert _has_python_files(tmp_path) is False

    (tmp_path / "app.py").write_text("x = 1\n")
    assert _has_python_files(tmp_path) is True


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ruff check .", ["ruff", "check", "."]),
        (["pytest", "tests"], ["pytest", "tests"]),
        (["pytest", 1], None),
        (42, None),
    ],
)
def test_normalize_command(command, expected):
    assert _normalize_command(command) == expected


# ── discover_verification_commands ───────────────────────────────────────────


def test_discover_uses_configured_commands(tmp_path):
    profile = {"agent": {"verify_commands": ["ruff check .", ["pytest", "-q"], 3, ""]}}

    assert discover_verification_commands(profile, tmp_path) == [
        ["ruff", "check", "."],
        ["pytest", "-q"],
    ]


def test_discover_configured_false_disables_verification(tmp_path):
    assert discover_verification_commands({"agent": {"verify_commands": False}}, tmp_path) == []


def test_discover_configured_non_list_is_ignored(tmp_path):
    assert discover_verification_commands({"agent": {"verify_commands": "ruff"}}, tmp_path) == []


def test_discover_auto_detects_python_project(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert discover_verification_commands({}, tmp_path) == [
        ["ruff", "check", "."],
        ["pytest", "tests"],
    ]


def test_discover_skips_linting_when_disabled_or_tools_missing(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(verifier.shutil, "which", lambda name: None)

    assert discover_verification_commands({"style": {"linting": False}}, tmp_path) == []


def test_discover_auto_detects_go_project(tmp_path, monkeypatch):
    (tmp_path / "go.mod").write_text("module example\n")
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert discover_verification_commands({"style": {"testing": "go test"}}, tmp_path) == [
        ["go", "test", "./..."]
    ]


def test_discover_auto_detects_jest_project(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}\n")
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert discover_verification_commands({"style": {"testing": "jest"}}, tmp_path) == [
        ["npm", "test"]
    ]


def test_discover_unknown_test_runner_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert discover_verification_commands({"style": {"testing": "cargo test"}}, tmp_path) == []


# ── run_verification_commands ────────────────────────────────────────────────


def test_run_verification_commands_runs_each_command(tmp_path, capsys):
    profile = {"agent": {"verify_commands": ["true", "false"]}}

    results = run_verification_commands(profile, tmp_path)

    assert [r.returncode for r in results] == [0, 1]
    assert results[0].passed and not results[1].passed
    assert "[repokeeper] Verifying: true" in capsys.readouterr().out


def test_run_verification_commands_handles_missing_binary(tmp_path):
    profile = {"agent": {"verify_commands": [["definitely-not-a-real-binary-xyz"]]}}

    results = run_verification_commands(profile, tmp_path)

    assert results[0].returncode == 127
    assert "Command not found: definitely-not-a-real-binary-xyz" in results[0].stderr


def test_run_verification_commands_handles_timeout(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 600, output=b"partial", stderr=b"slow")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    results = run_verification_commands({"agent": {"verify_commands": ["sleep 999"]}}, tmp_path)

    assert results[0].returncode == 124
    assert results[0].stdout == "partial"
    assert "slow\nCommand timed out after 600 seconds." in results[0].stderr


def test_run_verification_commands_handles_timeout_without_output(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    results = run_verification_commands({"agent": {"verify_commands": ["sleep 999"]}}, tmp_path)

    assert results[0].stdout == ""
    assert results[0].stderr == "\nCommand timed out after 600 seconds."


def test_run_verification_commands_normalizes_none_output(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    results = run_verification_commands({"agent": {"verify_commands": ["true"]}}, tmp_path)

    assert results[0].stdout == ""
    assert results[0].stderr == ""


# ── Formatting helpers ───────────────────────────────────────────────────────


def test_output_excerpt_truncates_long_output():
    result = VerificationResult(["x"], 1, "a" * 50, "b" * 50)

    excerpt = _output_excerpt(result, max_chars=20)

    assert excerpt.startswith("[truncated to last 20 chars]\n")
    assert excerpt.endswith("b" * 20)
    assert _output_excerpt(VerificationResult(["x"], 0, " ", "\n")) == "(no output)"
    assert _output_excerpt(VerificationResult(["x"], 0, "ok", "")) == "ok"


def test_summarize_verification_results():
    results = [
        VerificationResult(["ruff", "check"], 0, "clean", ""),
        VerificationResult(["pytest"], 1, "", "1 failed"),
    ]

    summary = summarize_verification_results(results)

    assert summary == [
        {"command": "ruff check", "passed": True, "returncode": 0, "output_excerpt": "clean"},
        {"command": "pytest", "passed": False, "returncode": 1, "output_excerpt": "1 failed"},
    ]


def test_format_verification_report_without_results():
    assert format_verification_report([]) == "Verification was not run."


def test_format_verification_report_all_passed():
    report = format_verification_report([VerificationResult(["ruff", "check"], 0, "", "")])

    assert "| `ruff check` | passed | 0 |" in report
    assert "Failure output" not in report


def test_format_verification_report_lists_failures():
    report = format_verification_report(
        [
            VerificationResult(["ruff", "check"], 0, "", ""),
            VerificationResult(["pytest"], 2, "boom", ""),
        ]
    )

    assert "| `pytest` | failed | 2 |" in report
    assert "<details><summary>Failure output</summary>" in report
    assert "#### `pytest`" in report
    assert "```text\nboom\n```" in report


def test_format_verification_failures():
    assert format_verification_failures([VerificationResult(["x"], 0, "", "")]) == ""

    message = format_verification_failures([VerificationResult(["pytest"], 1, "", "err")])

    assert message.startswith("Verification failed before creating a pull request.")
    assert "Command: `pytest`" in message
    assert "Exit code: 1" in message
    assert "```\nerr\n```" in message
