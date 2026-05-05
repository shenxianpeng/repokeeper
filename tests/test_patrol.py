"""Tests for the RepoKeeper Daily Patrol module (patrol.py)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from repokeeper.patrol import (
    CIFailure,
    DepCheck,
    PatrolReport,
    StaleIssue,
    _fetch_ci_log_snippet,
    _get_gh_token_from_client,
    attempt_ci_auto_fix,
    calculate_health,
    check_bundler_deps,
    check_cargo_deps,
    check_composer_deps,
    check_go_deps,
    check_gradle_deps,
    check_maven_deps,
    check_node_deps,
    check_python_deps,
    create_dependency_upgrade_pr,
    diagnose_ci_failure,
    find_manifests,
    generate_health_summary,
    run_patrol,
    scan_ci_failures,
    scan_dependencies,
    scan_stale_issues,
    summarize_stale_issue,
)

# ── calculate_health ──────────────────────────────────────────────────────────


def test_calculate_health_applies_deductions():
    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
        outdated_deps=[
            DepCheck("critical", "1", "2", True, severity="critical"),
            DepCheck("medium", "1", "2", True, severity="medium"),
        ],
        ci_failures=[
            CIFailure("ci", 1, "https://ex.test/run", datetime(2026, 1, 1), "failure")
        ],
        stale_issues=[
            StaleIssue(
                number=1, title="old", url="https://ex.test/issue",
                author="alice", created_at=datetime(2025, 1, 1),
                last_updated=datetime(2025, 1, 1), days_stale=120,
            )
        ],
    )
    # 100 - 10 - 2 - 5 - 3 = 80
    assert calculate_health(report) == 80


def test_calculate_health_high_severity_dep():
    report = PatrolReport(
        repo="x", scanned_at=datetime(2026, 1, 1),
        outdated_deps=[DepCheck("x", "1", "2", True, severity="high")],
    )
    assert calculate_health(report) == 95


def test_calculate_health_no_deductions():
    report = PatrolReport(repo="x", scanned_at=datetime(2026, 1, 1))
    assert calculate_health(report) == 100


def test_calculate_health_stale_30_90_days():
    report = PatrolReport(
        repo="x", scanned_at=datetime(2026, 1, 1),
        stale_issues=[
            StaleIssue(
                number=1, title="old", url="u", author="a",
                created_at=datetime(2025, 1, 1), last_updated=datetime(2025, 1, 1),
                days_stale=60,
            )
        ],
    )
    assert calculate_health(report) == 99  # -1 for 30-90 days


def test_calculate_health_floor_zero():
    """Score cannot go below zero."""
    report = PatrolReport(
        repo="x", scanned_at=datetime(2026, 1, 1),
        outdated_deps=[DepCheck("x", "1", "2", True, severity="critical")] * 50,
    )
    assert calculate_health(report) == 0


# ── find_manifests ────────────────────────────────────────────────────────────


def test_find_manifests_skips_virtualenvs(tmp_path):
    Path(tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    venv_manifest = tmp_path / ".venv" / "requirements.txt"
    venv_manifest.parent.mkdir()
    venv_manifest.write_text("ignored")

    assert find_manifests(tmp_path) == [tmp_path / "pyproject.toml"]


def test_find_manifests_detects_multiple(tmp_path):
    Path(tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    Path(tmp_path / "package.json").write_text('{"name": "demo"}')
    manifests = find_manifests(tmp_path)
    assert len(manifests) >= 2


# ── check_python_deps ─────────────────────────────────────────────────────────


def test_check_python_deps_finds_outdated(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps([{"name": "requests", "version": "2.0", "latest_version": "3.0"}]),
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    deps = check_python_deps(Path("pyproject.toml"))
    assert len(deps) == 1
    assert deps[0].name == "requests"
    assert deps[0].is_outdated is True


def test_check_python_deps_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""))
    deps = check_python_deps(Path("requirements.txt"))
    assert deps == []


def test_check_python_deps_subprocess_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(cmd=[], timeout=60)))
    deps = check_python_deps(Path("pyproject.toml"))
    assert deps == []


def test_check_python_deps_bad_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr=""))
    deps = check_python_deps(Path("pyproject.toml"))
    assert deps == []


# ── check_node_deps ───────────────────────────────────────────────────────────


def test_check_node_deps_finds_outdated(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=1,  # npm outdated exits 1 when outdated
            stdout=json.dumps({"lodash": {"current": "1.0", "latest": "2.0", "type": "latest"}}),
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    deps = check_node_deps(Path("package.json"))
    assert len(deps) == 1
    assert deps[0].name == "lodash"
    assert deps[0].severity == "high"


def test_check_node_deps_no_tool(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_node_deps(Path("package.json"))
    assert deps == []


# ── check_go_deps ─────────────────────────────────────────────────────────────


def test_check_go_deps_finds_outdated(monkeypatch):
    output = (
        '{"Path": "example.com/lib", "Version": "v1.0.0", '
        '"Update": {"Version": "v2.0.0"}}\n'
        '{"Path": "example.com/other", "Version": "v1.0.0"}\n'
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr=""))
    deps = check_go_deps(Path("go.mod"))
    assert len(deps) == 1
    assert deps[0].name == "example.com/lib"


def test_check_go_deps_no_tool(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_go_deps(Path("go.mod"))
    assert deps == []


def test_check_go_deps_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error"))
    deps = check_go_deps(Path("go.mod"))
    assert deps == []


# ── check_cargo_deps ──────────────────────────────────────────────────────────


def test_check_cargo_deps_finds_outdated(monkeypatch):
    output = json.dumps([
        {"name": "serde", "project": "1.0", "latest": "2.0", "semver": "major"},
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr=""))
    deps = check_cargo_deps(Path("Cargo.toml"))
    assert len(deps) == 1
    assert deps[0].name == "serde"
    assert deps[0].severity == "high"


def test_check_cargo_deps_no_cargo(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_cargo_deps(Path("Cargo.toml"))
    assert deps == []


def test_check_cargo_deps_bad_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr=""))
    deps = check_cargo_deps(Path("Cargo.toml"))
    assert deps == []


# ── check_bundler_deps ────────────────────────────────────────────────────────


def test_check_bundler_deps_finds_outdated(monkeypatch):
    output = "rails (newest 7.0, installed 6.0)\nrack (newest 3.0, installed 2.0)\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr=""))
    deps = check_bundler_deps(Path("Gemfile"))
    assert len(deps) >= 1
    assert any(d.name == "rails" for d in deps)


def test_check_bundler_deps_no_bundler(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_bundler_deps(Path("Gemfile"))
    assert deps == []


# ── check_composer_deps ───────────────────────────────────────────────────────


def test_check_composer_deps_finds_outdated(monkeypatch):
    output = json.dumps({
        "installed": [
            {"name": "laravel/framework", "version": "9.0", "latest": "10.0"},
        ]
    })
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr=""))
    deps = check_composer_deps(Path("composer.json"))
    assert len(deps) == 1
    assert deps[0].name == "laravel/framework"
    assert deps[0].is_outdated is True


def test_check_composer_deps_no_composer(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_composer_deps(Path("composer.json"))
    assert deps == []


# ── check_maven_deps ──────────────────────────────────────────────────────────


def test_check_maven_deps_finds_outdated(monkeypatch):
    output = (
        "[INFO]   com.google.guava:guava ... 30.0-jre -> 33.0-jre\n"
        "[INFO]   org.slf4j:slf4j-api ... 2.0.0 -> 2.0.9\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr=""))
    deps = check_maven_deps(Path("pom.xml"))
    assert len(deps) == 2
    assert deps[0].name == "com.google.guava:guava"


def test_check_maven_deps_no_maven(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_maven_deps(Path("pom.xml"))
    assert deps == []


# ── check_gradle_deps ─────────────────────────────────────────────────────────


def test_check_gradle_deps_finds_outdated(tmp_path, monkeypatch):
    report_dir = tmp_path / "build" / "dependencyUpdates"
    report_dir.mkdir(parents=True)
    report_file = report_dir / "report.json"
    report_file.write_text(json.dumps({
        "outdated": {
            "dependencies": [
                {
                    "group": "com.example", "name": "lib",
                    "version": "1.0", "available": {"release": "2.0"},
                }
            ]
        }
    }))

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    deps = check_gradle_deps(tmp_path / "build.gradle")
    assert len(deps) == 1
    assert deps[0].name == "com.example:lib"


def test_check_gradle_deps_no_gradle(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError()))
    deps = check_gradle_deps(Path("build.gradle.kts"))
    assert deps == []


# ── scan_dependencies ─────────────────────────────────────────────────────────


def test_scan_dependencies_detects_python(tmp_path, monkeypatch):
    Path(tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""))
    monkeypatch.chdir(tmp_path)
    deps = scan_dependencies(tmp_path)
    assert isinstance(deps, list)


def test_scan_dependencies_detects_node(tmp_path, monkeypatch):
    Path(tmp_path / "package.json").write_text('{"name": "x"}')
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=1, stdout="{}", stderr=""))
    monkeypatch.chdir(tmp_path)
    deps = scan_dependencies(tmp_path)
    assert isinstance(deps, list)


def test_scan_dependencies_detects_go(tmp_path, monkeypatch):
    Path(tmp_path / "go.mod").write_text("module example.com/x\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""))
    monkeypatch.chdir(tmp_path)
    deps = scan_dependencies(tmp_path)
    assert isinstance(deps, list)


# ── scan_ci_failures ──────────────────────────────────────────────────────────


def test_scan_ci_failures_finds_failures(monkeypatch):
    """scan_ci_failures returns CIFailure objects for failed runs."""
    from datetime import datetime, timedelta, timezone

    # Create mock workflow with a failed run
    mock_run = MagicMock()
    mock_run.id = 42
    mock_run.html_url = "https://github.com/owner/repo/actions/runs/42"
    mock_run.created_at = datetime.now(timezone.utc)
    mock_run.conclusion = "failure"

    mock_wf = MagicMock()
    mock_wf.name = "CI"
    mock_wf.get_runs.return_value = [mock_run]

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.get_workflows.return_value = [mock_wf]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    failures = scan_ci_failures(mock_gh, "owner/repo",
                                since=datetime.now(timezone.utc) - timedelta(days=7))
    assert len(failures) == 1
    assert failures[0].workflow_name == "CI"
    assert failures[0].run_id == 42
    assert failures[0].conclusion == "failure"


def test_scan_ci_failures_filters_by_time(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old_run = MagicMock()
    old_run.id = 1
    old_run.html_url = "https://github.com/owner/repo/actions/runs/1"
    old_run.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    old_run.conclusion = "failure"

    mock_wf = MagicMock()
    mock_wf.name = "CI"
    mock_wf.get_runs.return_value = [old_run]

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.get_workflows.return_value = [mock_wf]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    # since is 1 day ago, run is 30 days old → filtered out
    failures = scan_ci_failures(mock_gh, "owner/repo",
                                since=datetime.now(timezone.utc) - timedelta(days=1))
    assert len(failures) == 0


def test_scan_ci_failures_skips_successful_runs(monkeypatch):
    from datetime import datetime, timezone

    mock_run = MagicMock()
    mock_run.id = 1
    mock_run.html_url = "https://x"
    mock_run.created_at = datetime.now(timezone.utc)
    mock_run.conclusion = "success"

    mock_wf = MagicMock()
    mock_wf.name = "CI"
    mock_wf.get_runs.return_value = [mock_run]

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.get_workflows.return_value = [mock_wf]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    failures = scan_ci_failures(mock_gh, "owner/repo")
    assert len(failures) == 0


def test_scan_ci_failures_api_error(monkeypatch):
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("API error")
    failures = scan_ci_failures(mock_gh, "owner/repo")
    assert failures == []


# ── _fetch_ci_log_snippet ─────────────────────────────────────────────────────


def test_fetch_ci_log_snippet_success(monkeypatch):
    """_fetch_ci_log_snippet fetches jobs and steps from GitHub API."""
    mock_requester = MagicMock()
    mock_requester.requestJsonAndCheck.return_value = (
        {},  # headers
        {
            "jobs": [
                {
                    "name": "test",
                    "conclusion": "failure",
                    "status": "completed",
                    "steps": [
                        {"name": "Checkout", "conclusion": "success"},
                        {"name": "Run tests", "conclusion": "failure"},
                        {"name": "Deploy", "conclusion": "skipped"},
                    ],
                }
            ]
        },
    )

    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    snippet = _fetch_ci_log_snippet(
        mock_gh, "owner/repo", run_id=42,
        workflow_name="CI", conclusion="failure",
        run_url="https://example.test/run",
    )
    assert "Workflow: CI" in snippet
    assert "Run ID: 42" in snippet
    assert "[failure] test" in snippet
    assert "❌ Step: Run tests" in snippet
    assert "✅ Step: Checkout" in snippet
    assert "⏭ Step: Deploy" in snippet


def test_fetch_ci_log_snippet_no_jobs(monkeypatch):
    mock_requester = MagicMock()
    mock_requester.requestJsonAndCheck.return_value = ({}, {"jobs": []})
    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    snippet = _fetch_ci_log_snippet(
        mock_gh, "owner/repo", run_id=1, workflow_name="CI",
        conclusion="failure", run_url="https://x",
    )
    assert "Workflow: CI" in snippet
    assert "Run ID: 1" in snippet


def test_fetch_ci_log_snippet_api_error(monkeypatch):
    mock_requester = MagicMock()
    mock_requester.requestJsonAndCheck.side_effect = RuntimeError("boom")
    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    snippet = _fetch_ci_log_snippet(
        mock_gh, "owner/repo", run_id=1, workflow_name="CI",
        conclusion="failure", run_url="https://x",
    )
    # Should still have the basic info
    assert "Workflow: CI" in snippet
    assert "Job details unavailable" in snippet


def test_fetch_ci_log_snippet_non_dict_data(monkeypatch):
    mock_requester = MagicMock()
    mock_requester.requestJsonAndCheck.return_value = ({}, "not a dict")
    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    snippet = _fetch_ci_log_snippet(
        mock_gh, "owner/repo", run_id=1, workflow_name="CI",
        conclusion="failure", run_url="https://x",
    )
    assert "Workflow: CI" in snippet


# ── diagnose_ci_failure ───────────────────────────────────────────────────────


def test_diagnose_ci_failure_diagnoses(monkeypatch):
    """diagnose_ci_failure calls LLM with log data."""
    # Mock _fetch_ci_log_snippet
    monkeypatch.setattr(
        "repokeeper.patrol._fetch_ci_log_snippet",
        lambda *a, **kw: "Mock CI log data",
    )

    class Message:
        content = json.dumps({
            "diagnosis": "Flaky test", "suggested_fix": "Retry",
            "auto_fixable": False, "confidence": 0.8,
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()
    mock_gh = MagicMock()

    failure = CIFailure("CI", 1, "https://x", datetime(2026, 1, 1), "failure")
    result = diagnose_ci_failure(failure, mock_llm, mock_gh, "owner/repo")

    assert result.diagnosis == "Flaky test"
    assert result.suggested_fix == "Retry"
    assert result.auto_fixable is False
    assert result.log_snippet == "Mock CI log data"


def test_diagnose_ci_failure_with_fence(monkeypatch):
    """LLM response wrapped in ``` fences is handled."""
    monkeypatch.setattr(
        "repokeeper.patrol._fetch_ci_log_snippet",
        lambda *a, **kw: "log",
    )

    class Message:
        content = '```json\n{"diagnosis": "ok", "suggested_fix": "fix", "auto_fixable": true, "confidence": 0.9}\n```'

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()
    mock_gh = MagicMock()

    failure = CIFailure("CI", 1, "https://x", datetime(2026, 1, 1), "failure")
    result = diagnose_ci_failure(failure, mock_llm, mock_gh, "owner/repo")

    assert result.diagnosis == "ok"
    assert result.auto_fixable is True


def test_diagnose_ci_failure_llm_error(monkeypatch):
    monkeypatch.setattr(
        "repokeeper.patrol._fetch_ci_log_snippet",
        lambda *a, **kw: "log",
    )

    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("LLM down")

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()
    mock_gh = MagicMock()

    failure = CIFailure("CI", 1, "https://x", datetime(2026, 1, 1), "failure")
    result = diagnose_ci_failure(failure, mock_llm, mock_gh, "owner/repo")

    assert "Diagnosis error" in result.diagnosis
    assert result.auto_fixable is False


# ── scan_stale_issues ─────────────────────────────────────────────────────────


def test_scan_stale_issues_finds_stale(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old_issue = MagicMock()
    old_issue.pull_request = None
    old_issue.number = 1
    old_issue.title = "Old bug"
    old_issue.html_url = "https://github.com/owner/repo/issues/1"
    old_issue.user.login = "alice"
    old_issue.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    old_issue.updated_at = datetime.now(timezone.utc) - timedelta(days=120)
    old_issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [old_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    stale = scan_stale_issues(mock_gh, "owner/repo", stale_days=90)
    assert len(stale) == 1
    assert stale[0].number == 1
    assert stale[0].days_stale >= 90


def test_scan_stale_issues_skips_prs(monkeypatch):
    from datetime import datetime, timedelta, timezone

    pr_issue = MagicMock()
    pr_issue.pull_request = True  # This is a PR, not an issue
    pr_issue.number = 2
    pr_issue.title = "PR"
    pr_issue.html_url = "https://x"
    pr_issue.user.login = "bob"
    pr_issue.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    pr_issue.updated_at = datetime.now(timezone.utc) - timedelta(days=120)
    pr_issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [pr_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    stale = scan_stale_issues(mock_gh, "owner/repo")
    assert stale == []


def test_scan_stale_issues_skips_recent(monkeypatch):
    from datetime import datetime, timedelta, timezone

    recent_issue = MagicMock()
    recent_issue.pull_request = None
    recent_issue.number = 3
    recent_issue.title = "Recent"
    recent_issue.html_url = "https://x"
    recent_issue.user.login = "bob"
    recent_issue.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    recent_issue.updated_at = datetime.now(timezone.utc) - timedelta(days=1)
    recent_issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [recent_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    stale = scan_stale_issues(mock_gh, "owner/repo", stale_days=90)
    assert stale == []


def test_scan_stale_issues_api_error(monkeypatch):
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("boom")
    stale = scan_stale_issues(mock_gh, "owner/repo")
    assert stale == []


def test_scan_stale_issues_no_user(monkeypatch):
    """Issue with no user.login should still work."""
    from datetime import datetime, timedelta, timezone

    issue = MagicMock()
    issue.pull_request = None
    issue.number = 1
    issue.title = "Title"
    issue.html_url = "https://x"
    issue.user = None  # No user
    issue.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    issue.updated_at = datetime.now(timezone.utc) - timedelta(days=120)
    issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    stale = scan_stale_issues(mock_gh, "owner/repo", stale_days=90)
    assert len(stale) == 1
    assert stale[0].author == "unknown"


def test_scan_stale_issues_no_updated_at(monkeypatch):
    """Issue with no updated_at falls back to created_at."""
    from datetime import datetime, timedelta, timezone

    issue = MagicMock()
    issue.pull_request = None
    issue.number = 1
    issue.title = "Title"
    issue.html_url = "https://x"
    issue.user.login = "alice"
    issue.created_at = datetime.now(timezone.utc) - timedelta(days=200)
    issue.updated_at = None  # no updated_at
    issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    stale = scan_stale_issues(mock_gh, "owner/repo", stale_days=90)
    assert len(stale) == 1


# ── summarize_stale_issue ─────────────────────────────────────────────────────


def test_summarize_stale_issue_summarizes(monkeypatch):
    class Message:
        content = json.dumps({
            "summary": "This is an old feature request.",
            "suggested_action": "close",
            "reason": "No activity for a year.",
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    issue = StaleIssue(
        number=1, title="Old", url="https://x", author="alice",
        created_at=datetime(2025, 1, 1), last_updated=datetime(2025, 1, 1),
        days_stale=365, labels=["bug"],
    )
    result = summarize_stale_issue(issue, mock_llm)
    assert "old feature request" in result.summary


def test_summarize_stale_issue_llm_error(monkeypatch):
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("LLM down")

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    issue = StaleIssue(
        number=1, title="Old", url="https://x", author="alice",
        created_at=datetime(2025, 1, 1), last_updated=datetime(2025, 1, 1),
        days_stale=365,
    )
    result = summarize_stale_issue(issue, mock_llm)
    assert "Stale issue" in result.summary


# ── generate_health_summary ───────────────────────────────────────────────────


def test_generate_health_summary_includes_sections():
    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
        dependencies_checked=1,
        outdated_deps=[DepCheck("pkg", "1", "2", True, severity="medium")],
        ci_failures=[CIFailure("CI", 1, "https://ex.test/run", datetime(2026, 1, 1), "failure", diagnosis="broken")],
        stale_issues=[
            StaleIssue(
                number=2, title="old", url="https://ex.test/issue",
                author="alice", created_at=datetime(2025, 1, 1),
                last_updated=datetime(2025, 1, 1), days_stale=100,
                summary="needs review",
            )
        ],
    )
    summary = generate_health_summary(report, {})
    assert "Outdated Dependencies" in summary
    assert "CI Failures" in summary
    assert "Stale Issues" in summary


def test_generate_health_summary_includes_warnings():
    report = PatrolReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        warnings=["Disk space low", "Rate limit approaching"],
    )
    summary = generate_health_summary(report, {})
    assert "⚠️ Warnings" in summary
    assert "Disk space low" in summary


def test_generate_health_summary_includes_ci_fixes():
    report = PatrolReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        ci_fixed=["Fixed CI workflow", "Updated test config"],
    )
    summary = generate_health_summary(report, {})
    assert "🔧 Auto-Fixes Applied" in summary
    assert "Fixed CI workflow" in summary


def test_generate_health_summary_empty_report():
    report = PatrolReport(repo="owner/repo", scanned_at=datetime(2026, 1, 1))
    summary = generate_health_summary(report, {})
    assert "Health Score" in summary
    assert "Healthy" in summary


# ── run_patrol ────────────────────────────────────────────────────────────────


def test_run_patrol_disabled_returns_empty_report():
    report = run_patrol(None, None, "owner/repo", profile={"patrol": {"enabled": False}})
    assert report.repo == "owner/repo"
    assert report.health_score == 100


def test_run_patrol_no_ci_failures_no_stale_issues(monkeypatch):
    """run_patrol with no CI failures and no stale issues."""
    monkeypatch.setattr("repokeeper.patrol.scan_dependencies", lambda *a, **kw: [])
    monkeypatch.setattr("repokeeper.patrol.scan_ci_failures", lambda *a, **kw: [])
    monkeypatch.setattr("repokeeper.patrol.scan_stale_issues", lambda *a, **kw: [])

    report = run_patrol(
        MagicMock(), MagicMock(), "owner/repo",
        profile={"patrol": {"enabled": True, "stale_days": 90, "ci_auto_fix": False}},
    )
    assert report.repo == "owner/repo"
    assert report.health_score == 100


def test_run_patrol_skips_ci_auto_fix_when_disabled(monkeypatch):
    """When ci_auto_fix is disabled, failures are diagnosed but not fixed."""
    from datetime import datetime, timezone

    monkeypatch.setattr("repokeeper.patrol.scan_dependencies", lambda *a, **kw: [])
    monkeypatch.setattr("repokeeper.patrol.scan_stale_issues", lambda *a, **kw: [])

    mock_failure = CIFailure("CI", 1, "https://x", datetime.now(timezone.utc), "failure",
                              diagnosis="test", suggested_fix="try again",
                              auto_fixable=True)
    monkeypatch.setattr("repokeeper.patrol.scan_ci_failures",
                         lambda *a, **kw: [mock_failure])

    def fake_diagnose(failure, *args, **kwargs):
        failure.diagnosis = "Mock diagnosis"
        failure.auto_fixable = True
        return failure

    monkeypatch.setattr("repokeeper.patrol.diagnose_ci_failure", fake_diagnose)

    report = run_patrol(
        MagicMock(), MagicMock(), "owner/repo",
        profile={"patrol": {"enabled": True, "stale_days": 90, "ci_auto_fix": False}},
    )
    assert len(report.ci_failures) == 1
    # ci_auto_fix is False, so no auto-fix attempted
    assert len(report.ci_fixed) == 0


# ── create_dependency_upgrade_pr ──────────────────────────────────────────────


def test_create_dependency_upgrade_pr_creates_pr(monkeypatch):
    mock_pr = MagicMock()
    mock_pr.html_url = "https://github.com/owner/repo/pull/99"

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.create_pull.return_value = mock_pr

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    deps = [DepCheck("requests", "1.0", "2.0", True, severity="high")]
    profile = {"patrol": {"auto_upgrade_deps": True}}

    url = create_dependency_upgrade_pr(mock_gh, "owner/repo", deps, profile)
    assert url == "https://github.com/owner/repo/pull/99"
    mock_repo.create_pull.assert_called_once()


def test_create_dependency_upgrade_pr_no_deps():
    url = create_dependency_upgrade_pr(MagicMock(), "owner/repo", [], {})
    assert url is None


def test_create_dependency_upgrade_pr_disabled():
    deps = [DepCheck("x", "1", "2", True)]
    profile = {"patrol": {"auto_upgrade_deps": False}}
    url = create_dependency_upgrade_pr(MagicMock(), "owner/repo", deps, profile)
    assert url is None


def test_create_dependency_upgrade_pr_api_error(monkeypatch):
    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.create_pull.side_effect = RuntimeError("API down")

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    deps = [DepCheck("x", "1", "2", True)]
    profile = {"patrol": {"auto_upgrade_deps": True}}

    url = create_dependency_upgrade_pr(mock_gh, "owner/repo", deps, profile)
    assert url is None


# ── _get_gh_token_from_client ─────────────────────────────────────────────────


def test_get_gh_token_from_client_has_token():
    mock_auth = MagicMock()
    mock_auth.token = "ghp_test123"
    mock_requester = MagicMock()
    mock_requester.auth = mock_auth
    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    token = _get_gh_token_from_client(mock_gh)
    assert token == "ghp_test123"


def test_get_gh_token_from_client_no_auth():
    mock_requester = MagicMock()
    mock_requester.auth = None
    mock_gh = MagicMock()
    mock_gh._Github__requester = mock_requester

    token = _get_gh_token_from_client(mock_gh)
    assert token is None


def test_get_gh_token_from_client_error():
    mock_gh = MagicMock()
    del mock_gh._Github__requester  # Will raise AttributeError

    token = _get_gh_token_from_client(mock_gh)
    assert token is None


# ── attempt_ci_auto_fix ──────────────────────────────────────────────────────


def test_attempt_ci_auto_fix_no_workflow_files(tmp_path, monkeypatch):
    """When no workflow files exist, returns None."""
    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix it", auto_fixable=True,
        log_snippet="log",
    )
    result = attempt_ci_auto_fix(
        failure, MagicMock(), MagicMock(), "owner/repo", {},
        repo_path=tmp_path,
    )
    assert result is None


def test_attempt_ci_auto_fix_llm_skip(tmp_path, monkeypatch):
    """When LLM returns skip:true, returns None."""
    # Create a fake workflow file
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\non: push")

    class Message:
        content = json.dumps({"skip": True, "reason": "not fixable"})

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )
    result = attempt_ci_auto_fix(
        failure, mock_llm, MagicMock(), "owner/repo", {},
        repo_path=tmp_path,
    )
    assert result is None


def test_attempt_ci_auto_fix_no_changes(tmp_path, monkeypatch):
    """When LLM returns empty changes, returns None."""
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\non: push")

    class Message:
        content = json.dumps({
            "skip": False, "summary": "fixed", "commit_message": "ci: fix",
            "changes": {},
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )
    result = attempt_ci_auto_fix(
        failure, mock_llm, MagicMock(), "owner/repo", {},
        repo_path=tmp_path,
    )
    assert result is None


def test_attempt_ci_auto_fix_skips_unsafe_paths(tmp_path, monkeypatch):
    """CI auto-fix ignores paths that would escape repo_path."""
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\non: push")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

    class Message:
        content = json.dumps({
            "skip": False, "summary": "fix", "commit_message": "ci: fix",
            "changes": {"../outside.yml": "bad"},
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp

    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )

    result = attempt_ci_auto_fix(
        failure, _LLMBridge(), MagicMock(), "owner/repo", {},
        repo_path=tmp_path,
    )

    assert result is None
    assert not (tmp_path.parent / "outside.yml").exists()


def test_attempt_ci_auto_fix_creates_pr(tmp_path, monkeypatch):
    """Happy path: LLM returns changes, creates branch and PR."""
    # Setup git repo
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/owner/repo.git"], check=True)

    # Create a workflow file and commit it
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\non: push")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

    # Mock LLM response
    class Message:
        content = json.dumps({
            "skip": False, "summary": "Fixed CI config",
            "commit_message": "ci: fix workflow",
            "changes": {".github/workflows/ci.yml": "name: CI\non: [push, pull_request]"},
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    # Mock GitHub
    mock_pr = MagicMock()
    mock_pr.html_url = "https://github.com/owner/repo/pull/100"

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.create_pull.return_value = mock_pr

    mock_gh = MagicMock()
    # Need _Github__requester for token extraction
    mock_requester = MagicMock()
    mock_auth = MagicMock()
    mock_auth.token = "fake-token"
    mock_requester.auth = mock_auth
    mock_gh._Github__requester = mock_requester
    mock_gh.get_repo.return_value = mock_repo

    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )

    # Override subprocess.run globally for the push to succeed
    original_run = sp.run
    push_called = []
    def mock_push(*args, **kwargs):
        if len(args) >= 1 and isinstance(args[0], list) and args[0][:2] == ["git", "push"]:
            push_called.append(True)
            return sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return original_run(*args, **kwargs)
    monkeypatch.setattr(sp, "run", mock_push)

    result = attempt_ci_auto_fix(
        failure, mock_llm, mock_gh, "owner/repo",
        {"agent": {"model": "deepseek-chat"}},
        repo_path=tmp_path,
    )
    assert result is not None
    assert "Fixed CI" in result
    assert "https://github.com/owner/repo/pull/100" in result


def test_attempt_ci_auto_fix_push_failure(tmp_path, monkeypatch):
    """When git push fails, returns None."""
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text("name: CI\non: push")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

    # Mock LLM
    class Message:
        content = json.dumps({
            "skip": False, "summary": "fix", "commit_message": "ci: fix",
            "changes": {".github/workflows/ci.yml": "name: CI\non: [push]"},
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    # Mock git push to fail
    original_run = sp.run

    def mock_git(*args, **kwargs):
        if args and args[0] == "git" and "push" in args:
            return sp.CompletedProcess(args=[], returncode=1, stdout="", stderr="rejected")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(sp, "run", mock_git)

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_gh = MagicMock()
    mock_requester = MagicMock()
    mock_auth = MagicMock()
    mock_auth.token = "fake-token"
    mock_requester.auth = mock_auth
    mock_gh._Github__requester = mock_requester
    mock_gh.get_repo.return_value = mock_repo

    failure = CIFailure(
        "CI", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )

    result = attempt_ci_auto_fix(
        failure, mock_llm, mock_gh, "owner/repo", {},
        repo_path=tmp_path,
    )
    # Push failed → returns None
    assert result is None


def test_attempt_ci_auto_fix_uses_any_workflow_file(tmp_path, monkeypatch):
    """When workflow_name doesn't match any file, uses first available workflow."""
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/owner/repo.git"], check=True)

    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "deploy.yml").write_text("name: Deploy\non: push")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

    class Message:
        content = json.dumps({
            "skip": False, "summary": "fix", "commit_message": "ci: fix",
            "changes": {".github/workflows/deploy.yml": "name: Deploy\non: [push]"},
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class _LLMBridge:
        def chat(self, system='', messages=None, model='', temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{'role': 'system', 'content': system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type('U', (), {'total_tokens': 0, 'cost_usd': 0.0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model': model})()
            return resp
    mock_llm = _LLMBridge()

    mock_pr = MagicMock()
    mock_pr.html_url = "https://github.com/owner/repo/pull/101"

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_repo.create_pull.return_value = mock_pr

    mock_gh = MagicMock()
    mock_requester = MagicMock()
    mock_auth = MagicMock()
    mock_auth.token = "fake-token"
    mock_requester.auth = mock_auth
    mock_gh._Github__requester = mock_requester
    mock_gh.get_repo.return_value = mock_repo

    failure = CIFailure(
        "SomeOtherWorkflow", 1, "https://x", datetime(2026, 1, 1), "failure",
        diagnosis="broken", suggested_fix="fix", auto_fixable=True,
        log_snippet="log",
    )

    # Mock git push to succeed
    original_run = sp.run
    def mock_push(*args, **kwargs):
        if len(args) >= 1 and isinstance(args[0], list) and args[0][:2] == ["git", "push"]:
            return sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return original_run(*args, **kwargs)
    monkeypatch.setattr(sp, "run", mock_push)

    result = attempt_ci_auto_fix(
        failure, mock_llm, mock_gh, "owner/repo", {},
        repo_path=tmp_path,
    )
    assert result is not None
