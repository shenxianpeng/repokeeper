from __future__ import annotations

import os
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
    assert templates.joinpath("release.yml").is_file()


def test_agent_workflow_only_triggers_on_explicit_approval():
    workflow = (
        resources.files("repokeeper")
        .joinpath("templates", "workflows", "repokeeper.yml")
        .read_text()
    )

    assert "github.event.label.name == 'agent-todo'" in workflow
    assert "contains(github.event.comment.body, '/repokeeper go')" in workflow
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


def test_release_command_dry_run_summary(monkeypatch, capsys):
    """release --dry-run prints the generated summary without writing a release."""
    from repokeeper.release import ReleaseDraftResult

    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    result = ReleaseDraftResult(
        repo="owner/repo",
        tag_name="v1.0.1",
        name="v1.0.1",
        body="## Changes\n- Test (#1)",
        action="dry-run",
        dry_run=True,
    )
    monkeypatch.setattr(cli, "run_release", lambda *a, **kw: result)

    exit_code = cli.main(["release", "--repo", "owner/repo", "--dry-run"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "RepoKeeper Draft Release" in out
    assert "Test (#1)" in out


def test_release_command_created(monkeypatch, capsys):
    """release command prints draft release URL after writing."""
    from repokeeper.release import ReleaseDraftResult

    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    result = ReleaseDraftResult(
        repo="owner/repo",
        tag_name="v1.0.1",
        name="v1.0.1",
        body="body",
        action="created",
        html_url="https://github.com/owner/repo/releases/tag/v1.0.1",
    )
    monkeypatch.setattr(cli, "run_release", lambda *a, **kw: result)

    exit_code = cli.main(["release", "--repo", "owner/repo"])

    assert exit_code == 0
    assert "Draft release created for v1.0.1" in capsys.readouterr().out


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


def _stub_doctor_probes(monkeypatch, llm_warnings=0, remote_warnings=0):
    """Replace the network probes used by ``doctor`` with deterministic stubs."""
    monkeypatch.setattr(
        cli, "_check_llm_connectivity", lambda key, failed, warnings: (failed, warnings + llm_warnings)
    )
    monkeypatch.setattr(
        cli,
        "_run_remote_checks",
        lambda token, slug, failed, warnings: (failed, warnings + remote_warnings),
    )


def test_doctor_success(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    cli.main(["init", str(tmp_path), "--minimal"])
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    _stub_doctor_probes(monkeypatch, llm_warnings=1)

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
    _stub_doctor_probes(monkeypatch)

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Workflow triggers and permissions" not in output
    assert "issue_comment trigger" in output
    assert "pull request write permission" in output


def test_workflow_required_settings_accepts_root_agent_action(tmp_path):
    workflow = tmp_path / "repokeeper.yml"
    workflow.write_text(
        """
on:
  issue_comment:
  issues:
permissions:
  contents: write
  issues: write
  pull-requests: write
jobs:
  repokeeper:
    steps:
      - uses: shenxianpeng/repokeeper@v1
        with:
          module: agent
""",
        encoding="utf-8",
    )

    assert cli._workflow_has_required_settings(workflow) == []


def test_workflow_required_settings_accepts_local_root_agent_action(tmp_path):
    workflow = tmp_path / "repokeeper.yml"
    workflow.write_text(
        """
on:
  issue_comment:
  issues:
permissions:
  contents: write
  issues: write
  pull-requests: write
jobs:
  repokeeper:
    steps:
      - uses: ./
        with:
          module: agent
""",
        encoding="utf-8",
    )

    assert cli._workflow_has_required_settings(workflow) == []


def test_workflow_required_settings_ignores_comment_only_action_reference(tmp_path):
    workflow = tmp_path / "repokeeper.yml"
    workflow.write_text(
        """
# Example: uses: shenxianpeng/repokeeper@v1
on:
  issue_comment:
  issues:
permissions:
  contents: write
  issues: write
  pull-requests: write
jobs:
  repokeeper:
    steps:
      - run: echo "missing action"
""",
        encoding="utf-8",
    )

    assert "agent action" in cli._workflow_has_required_settings(workflow)


def test_workflow_required_settings_requires_agent_module(tmp_path):
    workflow = tmp_path / "repokeeper.yml"
    workflow.write_text(
        """
on:
  issue_comment:
  issues:
permissions:
  contents: write
  issues: write
  pull-requests: write
jobs:
  repokeeper:
    steps:
      - uses: shenxianpeng/repokeeper@v1
        with:
          module: review
""",
        encoding="utf-8",
    )

    assert "agent action" in cli._workflow_has_required_settings(workflow)


def test_workflow_uses_agent_action_rejects_invalid_yaml():
    assert cli._workflow_uses_agent_action("jobs: [") is False


def test_workflow_uses_agent_action_rejects_non_mapping_documents():
    assert cli._workflow_uses_agent_action("- just-a-list") is False


def test_workflow_uses_agent_action_skips_malformed_jobs():
    workflow = """
jobs:
  one: not-a-job-map
  two:
    steps: not-a-step-list
  three:
    steps:
      - run: echo ok
      - uses: 123
      - uses: actions/checkout@v6
"""

    assert cli._workflow_uses_agent_action(workflow) is False


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


# ── Client factories and workflow copying ───────────────────────────────────


def test_make_llm_client_requires_api_key(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="Missing LLM API key"):
        cli.main(["radar", "--repo", "owner/repo"])


def test_copy_workflows_skips_missing_templates(tmp_path, monkeypatch):
    cli._copy_workflows(tmp_path, ("does-not-exist.yml",))
    assert list((tmp_path / ".github" / "workflows").iterdir()) == []

    monkeypatch.setattr(cli, "__file__", str(tmp_path / "missing" / "cli.py"))
    cli._copy_workflows(tmp_path / "other")
    assert list((tmp_path / "other" / ".github" / "workflows").iterdir()) == []


def test_init_all_workflows_flag(tmp_path):
    exit_code = cli.main(["init", str(tmp_path), "--all-workflows"])

    assert exit_code == 0
    for name in cli.ALL_WORKFLOWS:
        assert (tmp_path / ".github" / "workflows" / name).exists()


def test_profile_validate_reports_issues(tmp_path, capsys):
    (tmp_path / "repokeeper.yml").write_text("maintainer: ''\ntone:\n  style: loud\n")

    exit_code = cli.main(["profile", "validate", "--profile", str(tmp_path / "repokeeper.yml")])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "- maintainer must be a non-empty string" in err
    assert "tone.style" in err


# ── labeler / patrol / pricing commands ─────────────────────────────────────


def test_labeler_command_prints_counts_and_summary(monkeypatch, capsys):
    from datetime import datetime

    from repokeeper.labeler import LabelerReport, LabelerResult

    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.setattr(cli, "_make_github_client", lambda token: object())
    monkeypatch.setattr(cli, "_make_llm_client", lambda key, url: object())
    labeled = LabelerResult(1, "https://x/1", "Bug", action="labeled", applied_labels=["bug"])
    report = LabelerReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1), total_issues=1,
        results=[labeled], labeled=[labeled],
    )
    captured = {}

    def fake_run_labeler(gh, llm, repo, profile, issue_number=None, pr_number=None):
        captured.update(repo=repo, issue_number=issue_number, pr_number=pr_number)
        return report

    monkeypatch.setattr(cli, "run_labeler", fake_run_labeler)

    exit_code = cli.main(["labeler", "--repo", "owner/repo", "--pr", "7", "--summary"])

    assert exit_code == 0
    assert captured == {"repo": "owner/repo", "issue_number": None, "pr_number": 7}
    out = capsys.readouterr().out
    assert "Labeler: 1 labeled, 0 suggested, 0 skipped" in out
    assert "Auto-Labeler Report" in out

    assert cli.main(["labeler", "--repo", "owner/repo", "--issue", "2"]) == 0
    assert captured["issue_number"] == 2
    assert "Auto-Labeler Report" not in capsys.readouterr().out


def test_run_patrol_report_wires_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    gh, llm = object(), object()
    monkeypatch.setattr(cli, "_make_github_client", lambda token: gh)
    monkeypatch.setattr(cli, "_make_llm_client", lambda key, url: llm)
    captured = {}

    def fake_run_patrol(gh_client, llm_client, repo, profile, repo_path=None):
        captured.update(gh=gh_client, llm=llm_client, repo=repo, repo_path=repo_path)
        return type("Report", (), {"health_score": 77})()

    monkeypatch.setattr(cli, "run_patrol", fake_run_patrol)

    exit_code = cli.main(["health", "--repo", "owner/repo", "--path", str(tmp_path)])

    assert exit_code == 0
    assert captured == {"gh": gh, "llm": llm, "repo": "owner/repo", "repo_path": tmp_path}


def test_pricing_command_fresh_data_with_json(monkeypatch, capsys):
    import datetime as _dt

    from repokeeper import llm_client

    monkeypatch.setattr(llm_client, "_PRICING_LAST_UPDATED", _dt.date.today())

    exit_code = cli.main(["pricing", "--json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "LLM Pricing (USD per 1M tokens)" in out
    assert "Age: 0 days\n" in out
    assert "STALE" not in out
    assert "deepseek-chat" in out
    assert '"stale": false' in out

    assert cli.main(["pricing"]) == 0
    assert '"stale"' not in capsys.readouterr().out


def test_pricing_command_warns_when_stale(monkeypatch, capsys):
    import datetime as _dt

    from repokeeper import llm_client

    monkeypatch.setattr(
        llm_client, "_PRICING_LAST_UPDATED", _dt.date.today() - _dt.timedelta(days=120)
    )

    exit_code = cli.main(["pricing"])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "[STALE — pricing may be inaccurate]" in out
    assert "Warning: Pricing data is over 90 days old." in out


# ── agent / review / describe / release commands ────────────────────────────


def test_agent_command_backend_override_and_fix_result(monkeypatch, capsys):
    monkeypatch.delenv("RKP_AGENT_BACKEND", raising=False)
    monkeypatch.setattr(
        cli, "run_agent", lambda **kw: {"fix_applied": True, "pr_url": "https://x/pull/3"}
    )

    exit_code = cli.main(["agent", "--repo", "owner/repo", "--issue", "3", "--backend", "pi"])

    assert exit_code == 0
    assert os.environ["RKP_AGENT_BACKEND"] == "pi"
    assert "Fix pushed to https://x/pull/3" in capsys.readouterr().out
    monkeypatch.delenv("RKP_AGENT_BACKEND", raising=False)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {"review_posted": True, "review_id": "9", "approval_recommendation": "approve", "issues_count": 2},
            "Review posted: approve, 2 issue(s) found (inline, review_id=9)",
        ),
        (
            {"review_posted": True, "incremental": True, "approval_recommendation": "comment"},
            "Review posted (re-review): comment, 0 issue(s) found (re-review, fallback to comment)",
        ),
        ({"review_posted": True}, "Review posted: ?, 0 issue(s) found"),
        ({"review_posted": False, "reason": "skip keyword"}, "Review not posted: skip keyword"),
        ({"review_posted": False}, "Review not posted: unknown"),
    ],
)
def test_review_command_output(monkeypatch, capsys, result, expected):
    captured = {}
    monkeypatch.setattr(cli, "run_review", lambda **kw: captured.update(kw) or result)

    exit_code = cli.main(["review", "--repo", "owner/repo", "--pr", "5"])

    assert exit_code == 0
    assert captured["pr_number"] == 5
    assert captured["repository"] == "owner/repo"
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"description_posted": True, "title_updated": True}, "PR description updated (title updated)"),
        ({"description_posted": True}, "PR description updated\n"),
        ({"description_posted": False, "error": "Empty description"}, "Description not posted: Empty description"),
        ({"description_posted": False}, "Description not posted: unknown"),
    ],
)
def test_describe_command_output(monkeypatch, capsys, result, expected):
    monkeypatch.setattr(cli, "run_describe", lambda **kw: result)

    exit_code = cli.main(["describe", "--repo", "owner/repo", "--pr", "5"])

    assert exit_code == 0
    assert expected in capsys.readouterr().out


def test_release_command_disabled(monkeypatch, capsys):
    from repokeeper.release import ReleaseDraftResult

    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.setattr(cli, "_make_github_client", lambda token: object())
    monkeypatch.setattr(cli, "_make_llm_client", lambda key, url: object())
    result = ReleaseDraftResult(repo="owner/repo", tag_name="", name="", body="", action="disabled")
    monkeypatch.setattr(cli, "run_release", lambda *a, **kw: result)

    exit_code = cli.main(["release", "--repo", "owner/repo"])

    assert exit_code == 0
    assert "Release drafting is disabled." in capsys.readouterr().out


# ── doctor helpers ──────────────────────────────────────────────────────────


def _fake_openai(content):
    class _Completions:
        def create(self, **kwargs):
            if isinstance(content, Exception):
                raise content
            message = type("Message", (), {"content": content})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice] if content != "no-choices" else []})()

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.chat = _Chat()

    return _OpenAI


def test_check_llm_connectivity_success(monkeypatch, capsys):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _fake_openai("hi"))
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test")

    assert cli._check_llm_connectivity("key", 0, 0) == (0, 0)
    assert "[ok] LLM API connectivity - https://llm.test" in capsys.readouterr().out


def test_check_llm_connectivity_empty_response(monkeypatch, capsys):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _fake_openai(None))
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    assert cli._check_llm_connectivity("key", 1, 0) == (1, 1)
    assert "[warn] LLM API connectivity - Empty response from API" in capsys.readouterr().out


def test_check_llm_connectivity_request_error(monkeypatch, capsys):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _fake_openai(RuntimeError("connection refused")))

    assert cli._check_llm_connectivity("key", 0, 2) == (0, 3)
    out = capsys.readouterr().out
    assert "connection refused" in out
    assert "Fix: verify the API key is valid" in out


def test_workflow_uses_agent_action_skips_non_dict_steps():
    text = "jobs:\n  build:\n    steps:\n      - just-a-string\n      - uses: ./\n        with:\n          module: agent\n"

    assert cli._workflow_uses_agent_action(text) is True


def test_doctor_reports_profile_issues_and_labeler_workflow(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    cli.main(["init", str(tmp_path), "--all-workflows"])
    (tmp_path / "repokeeper.yml").write_text("maintainer: ''\n")
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    _stub_doctor_probes(monkeypatch)

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "[missing] Profile validation" in out
    assert "  - maintainer must be a non-empty string" in out
    assert f"Fix: edit {tmp_path / 'repokeeper.yml'}" in out
    assert "[ok] Auto-Labeler workflow" in out


def test_doctor_clean_setup_reports_no_issues(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    cli.main(["init", str(tmp_path), "--all-workflows"])
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _stub_doctor_probes(monkeypatch)

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    assert exit_code == 0
    assert "Doctor found no local setup issues." in capsys.readouterr().out


def test_doctor_warns_when_anthropic_extra_is_missing(tmp_path, monkeypatch, capsys):
    import sys

    (tmp_path / ".git").mkdir()
    cli.main(["init", str(tmp_path), "--all-workflows"])
    monkeypatch.setenv("GITHUB_TOKEN", "tk")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    _stub_doctor_probes(monkeypatch)

    exit_code = cli.main(["doctor", str(tmp_path), "--repo", "owner/repo"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[warn] Anthropic extra" in out
    assert "Doctor found no blocking issues (1 warning(s))." in out


# ── main() argument handling ────────────────────────────────────────────────


def test_main_reads_sys_argv_and_log_format_flag(monkeypatch, tmp_path):
    import sys

    captured = []
    monkeypatch.setattr("repokeeper.logs.setup_logging", lambda fmt=None: captured.append(fmt))
    monkeypatch.setattr(sys, "argv", ["repokeeper", "--log-format", "json", "init", str(tmp_path)])

    assert cli.main() == 0
    assert captured == ["json"]
    assert (tmp_path / "repokeeper.yml").exists()


def test_main_accepts_log_format_with_equals(monkeypatch):
    captured = []
    monkeypatch.setattr("repokeeper.logs.setup_logging", lambda fmt=None: captured.append(fmt))

    with pytest.raises(SystemExit) as exc:
        cli.main(["--log-format=text", "--help"])

    assert exc.value.code == 0
    assert captured == ["text"]


def test_cli_module_entrypoint(monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["repokeeper", "--version"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("repokeeper.cli", run_name="__main__")

    assert exc.value.code == 0
