from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from repokeeper import cli
from repokeeper.exceptions import AuthError, ConfigError


def test_cli_help_exits_success(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "AI-powered open source maintainer agent" in capsys.readouterr().out


def test_init_writes_profile_and_workflows(tmp_path):
    exit_code = cli.main(["init", str(tmp_path), "--workflows"])

    assert exit_code == 0
    assert (tmp_path / "repokeeper.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "repokeeper.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "radar.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "patrol.yml").exists()


def test_init_minimal_writes_only_agent_workflow(tmp_path):
    exit_code = cli.main(["init", str(tmp_path), "--minimal"])

    assert exit_code == 0
    assert (tmp_path / "repokeeper.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "repokeeper.yml").exists()
    assert not (tmp_path / ".github" / "workflows" / "radar.yml").exists()
    assert not (tmp_path / ".github" / "workflows" / "patrol.yml").exists()


def test_workflow_templates_are_package_data():
    templates = resources.files("repokeeper").joinpath("templates", "workflows")

    assert templates.joinpath("repokeeper.yml").is_file()
    assert templates.joinpath("radar.yml").is_file()
    assert templates.joinpath("patrol.yml").is_file()


def test_agent_workflow_only_triggers_on_explicit_approval():
    workflow = (
        resources.files("repokeeper")
        .joinpath("templates", "workflows", "repokeeper.yml")
        .read_text()
    )

    assert "github.event.label.name == 'agent-todo'" in workflow
    assert "contains(github.event.comment.body, '@repokeeper go')" in workflow
    assert "repokeeper-candidate" not in workflow


def test_init_refuses_to_overwrite_existing_profile(tmp_path, capsys):
    Path(tmp_path / "repokeeper.yml").write_text("maintainer: alice\n")

    exit_code = cli.main(["init", str(tmp_path)])

    assert exit_code == 1
    assert "already exists" in capsys.readouterr().err


def test_profile_validate_command(tmp_path, capsys):
    (tmp_path / "repokeeper.yml").write_text("maintainer: alice\n")

    exit_code = cli.main(["profile", "validate", "--profile", str(tmp_path / "repokeeper.yml")])

    assert exit_code == 0
    assert "Profile is valid" in capsys.readouterr().out


def test_health_command_prints_score(monkeypatch, capsys):
    report = type("Report", (), {"health_score": 91})()
    monkeypatch.setattr(cli, "_run_patrol_report", lambda args: report)

    exit_code = cli.main(["health", "--repo", "owner/repo"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "91"


def test_profile_show_command(tmp_path, capsys):
    """profile show prints merged YAML."""
    (tmp_path / "repokeeper.yml").write_text("maintainer: bob\n")
    exit_code = cli.main(["profile", "show", "--profile", str(tmp_path / "repokeeper.yml")])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "maintainer: bob" in output


def test_radar_command_summary(monkeypatch, capsys):
    """radar --summary prints markdown summary."""
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    from datetime import datetime

    from repokeeper.radar import RadarHit, RadarReport

    report = RadarReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
        total_scanned=100,
        hits=[
            RadarHit(
                source="issue", repo="owner/repo", number=1,
                title="Bug", body="desc", url="https://ex.test/1",
                author="alice", created_at=datetime(2026, 1, 1),
                matched_keyword="bug", category="bug", confidence=0.9,
            )
        ],
    )
    monkeypatch.setattr(cli, "run_radar", lambda *a, **kw: report)

    exit_code = cli.main(["radar", "--repo", "owner/repo", "--summary"])
    assert exit_code == 0


def test_radar_command_no_summary(monkeypatch, capsys):
    """radar without --summary just prints hit count."""
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    report = type("Report", (), {"repo": "owner/repo", "hits": []})()
    monkeypatch.setattr(cli, "run_radar", lambda *a, **kw: report)

    exit_code = cli.main(["radar", "--repo", "owner/repo"])
    assert exit_code == 0
    assert "0 actionable hits" in capsys.readouterr().out


def test_patrol_command_summary(monkeypatch, capsys):
    """patrol --summary prints health summary."""
    from datetime import datetime

    from repokeeper.patrol import PatrolReport

    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(cli, "_run_patrol_report", lambda args: report)

    exit_code = cli.main(["patrol", "--repo", "owner/repo", "--summary"])
    assert exit_code == 0


def test_init_without_workflows(tmp_path, capsys):
    """init without --workflows only writes profile."""
    exit_code = cli.main(["init", str(tmp_path)])
    assert exit_code == 0
    assert (tmp_path / "repokeeper.yml").exists()
    assert not (tmp_path / ".github" / "workflows").exists()


def test_init_with_force_overwrite(tmp_path):
    """init --force overwrites existing profile."""
    p = tmp_path / "repokeeper.yml"
    p.write_text("old content")
    exit_code = cli.main(["init", str(tmp_path), "--force"])
    assert exit_code == 0
    assert p.read_text() != "old content"


def test_missing_repo_argument_fails(capsys):
    """Commands requiring --repo fail without it."""
    with pytest.raises(SystemExit):
        cli.main(["radar"])


def test_agent_command_skip_result(monkeypatch, capsys):
    """agent command prints skip reason."""
    monkeypatch.setattr(cli, "run_agent", lambda **kw: {"skip": True, "reason": "test skip"})
    exit_code = cli.main(["agent", "--repo", "owner/repo", "--issue", "42"])
    assert exit_code == 0
    assert "Skipped: test skip" in capsys.readouterr().out


def test_agent_command_success(monkeypatch, capsys):
    """agent command prints PR URL on success."""
    monkeypatch.setattr(cli, "run_agent", lambda **kw: {"skip": False, "reason": "", "pr_url": "https://example.test/pr/1"})
    exit_code = cli.main(["agent", "--repo", "owner/repo", "--issue", "42"])
    assert exit_code == 0
    assert "PR created: https://example.test/pr/1" in capsys.readouterr().out


def test_patrol_without_summary(monkeypatch, capsys):
    """patrol without --summary prints stats line."""
    from datetime import datetime

    from repokeeper.patrol import PatrolReport

    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
    )
    monkeypatch.setattr(cli, "_run_patrol_report", lambda args: report)

    exit_code = cli.main(["patrol", "--repo", "owner/repo"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Patrol: health=100/100" in captured.out


def test_default_path_for_init():
    """init without path defaults to current directory."""
    # Just verify the argument default
    parser = cli.build_parser()
    args = parser.parse_args(["init"])
    assert args.path == "."


def test_cli_version_matches_package(capsys):
    import repokeeper

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert f"repokeeper {repokeeper.__version__}" in capsys.readouterr().out


def test_doctor_reports_missing_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("REPOKEEPER_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    exit_code = cli.main(["doctor", str(tmp_path)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Doctor found" in output
    assert "Git repository" in output
    assert "LLM API key" in output
    assert "Fix: repokeeper init . --minimal" in output


def test_doctor_success(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    cli.main(["init", str(tmp_path), "--minimal"])
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Doctor found no blocking issues" in out


def test_doctor_reports_incomplete_workflow(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "repokeeper.yml").write_text("maintainer: alice\n")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "repokeeper.yml").write_text("name: broken\n")
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Workflow triggers and permissions" not in output
    assert "issue_comment trigger" in output
    assert "pull request write permission" in output


def test_cmd_radar_missing_token(tmp_path, monkeypatch):
    """radar fails with clear message when GITHUB_TOKEN missing."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("REPOKEEPER_GITHUB_TOKEN", raising=False)
    with pytest.raises(AuthError, match="Missing GitHub token"):
        cli.main(["radar", "--repo", "owner/repo"])


def test_cmd_agent_missing_llm_key(tmp_path, monkeypatch):
    """agent command raises ConfigError when LLM key is missing."""
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY or OPENAI_API_KEY"):
        cli.main(["agent", "--repo", "owner/repo", "--issue", "1"])


# ── _run_remote_checks ──────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


def test_run_remote_checks_success_with_discussions(monkeypatch, capsys):
    """200 + has_discussions=True → both checks pass."""
    def fake_get(url, headers, timeout):
        return _FakeResponse(200, {"has_discussions": True})

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 0
    assert warnings == 0
    out = capsys.readouterr().out
    assert "[ok] Token can access owner/repo" in out
    assert "[ok] Discussions enabled" in out


def test_run_remote_checks_success_no_discussions(monkeypatch, capsys):
    """200 + has_discussions=False → warns about missing discussions."""
    def fake_get(url, headers, timeout):
        return _FakeResponse(200, {"has_discussions": False})

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 0
    assert warnings == 1
    out = capsys.readouterr().out
    assert "[warn] Discussions enabled" in out


def test_run_remote_checks_404(monkeypatch, capsys):
    """404 → failed + fix hint."""
    def fake_get(url, headers, timeout):
        return _FakeResponse(404)

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 1
    assert warnings == 0
    out = capsys.readouterr().out
    assert "404 — repo not found" in out


def test_run_remote_checks_401(monkeypatch, capsys):
    """401 → warning (not failure) + fix hint."""
    def fake_get(url, headers, timeout):
        return _FakeResponse(401)

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 0
    assert warnings == 1
    out = capsys.readouterr().out
    assert "401 — token is invalid" in out


def test_run_remote_checks_other_status(monkeypatch, capsys):
    """Unexpected HTTP status → warning."""
    def fake_get(url, headers, timeout):
        return _FakeResponse(500)

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 0
    assert warnings == 1
    out = capsys.readouterr().out
    assert "HTTP 500" in out


def test_run_remote_checks_connection_error(monkeypatch, capsys):
    """ConnectionError → warning."""
    def fake_get(url, headers, timeout):
        raise ConnectionError("no route to host")

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    failed, warnings = cli._run_remote_checks("tk", "owner/repo", 0, 0)
    assert failed == 0
    assert warnings == 1
    out = capsys.readouterr().out
    assert "request failed" in out


# ── agent --dry-run ─────────────────────────────────────────────────────────


def test_agent_dry_run_cli(monkeypatch, capsys):
    """agent --dry-run prints plan JSON and returns 0."""
    monkeypatch.setattr(
        cli,
        "run_agent",
        lambda **kw: {
            "skip": True,
            "reason": "dry-run",
            "plan": {"branch_name": "repokeeper/issue-1-fix", "summary": "fix"},
        },
    )
    exit_code = cli.main(["agent", "--repo", "owner/repo", "--issue", "1", "--dry-run"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "repokeeper/issue-1-fix" in out


def test_agent_dry_run_passes_flag(monkeypatch):
    """--dry-run flag is passed through to run_agent."""
    captured = {}
    monkeypatch.setattr(cli, "run_agent", lambda **kw: captured.update(kw) or {"skip": True, "reason": "dry-run"})
    cli.main(["agent", "--repo", "owner/repo", "--issue", "1", "--dry-run"])
    assert captured.get("dry_run") is True
