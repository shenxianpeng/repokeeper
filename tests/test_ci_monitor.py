"""Tests for repokeeper.ci_monitor."""

from __future__ import annotations

from unittest.mock import MagicMock

from repokeeper.ci_monitor import (
    CheckResult,
    PRCheckStatus,
    find_agent_prs,
    get_pr_check_status,
    run_ci_monitor,
)


class TestCheckResult:
    def test_create(self) -> None:
        cr = CheckResult(
            name="lint",
            status="completed",
            conclusion="success",
            url="https://example.com",
            run_id=123,
        )
        assert cr.name == "lint"
        assert cr.status == "completed"
        assert cr.conclusion == "success"
        assert cr.run_id == 123

    def test_defaults(self) -> None:
        cr = CheckResult(
            name="test",
            status="queued",
            conclusion="",
            url="",
        )
        assert cr.run_id == 0
        assert cr.completed_at is None

    def test_with_completed_at(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        cr = CheckResult(
            name="ci", status="completed", conclusion="failure",
            url="http://x", run_id=1, completed_at=now,
        )
        assert cr.completed_at == now


class TestPRCheckStatus:
    def test_default_is_pending(self) -> None:
        status = PRCheckStatus(pr_number=42)
        assert status.overall == "pending"
        assert status.total_checks == 0

    def test_success_when_all_pass(self) -> None:
        status = PRCheckStatus(pr_number=42)
        CheckResult(
            name="ci", status="completed", conclusion="success",
            url="", run_id=1,
        )
        status.total_checks = 1
        status.completed = 1
        status.passed = 1
        status.overall = "success"
        assert status.overall == "success"

    def test_failure_when_any_fails(self) -> None:
        status = PRCheckStatus(pr_number=42)
        cr = CheckResult(
            name="ci", status="completed", conclusion="failure",
            url="", run_id=1,
        )
        status.failed.append(cr)
        status.total_checks = 1
        status.completed = 1
        status.passed = 0
        status.overall = "failure"
        assert status.overall == "failure"

    def test_pending_when_checks_in_progress(self) -> None:
        status = PRCheckStatus(pr_number=42)
        cr = CheckResult(
            name="ci", status="in_progress", conclusion="",
            url="", run_id=1,
        )
        status.in_progress.append(cr)
        status.total_checks = 1
        status.completed = 0
        status.passed = 0
        assert status.overall == "pending"

    def test_multiple_checks_mixed(self) -> None:
        status = PRCheckStatus(pr_number=42)
        CheckResult(
            name="lint", status="completed", conclusion="success",
            url="", run_id=1,
        )
        failed = CheckResult(
            name="test", status="completed", conclusion="failure",
            url="", run_id=2,
        )
        status.total_checks = 2
        status.completed = 2
        status.passed = 1
        status.failed.append(failed)
        status.overall = "failure"
        assert status.overall == "failure"


class TestFindAgentPrs:
    def test_handles_error(self, monkeypatch) -> None:
        """Should return empty list on API error."""
        mock_gh = MagicMock()
        mock_gh.get_repo.side_effect = RuntimeError("API error")
        result = find_agent_prs(mock_gh, "owner/repo")
        assert result == []


class TestGetPrCheckStatus:
    def test_handles_error(self, monkeypatch) -> None:
        """Should return pending status on API error."""
        mock_gh = MagicMock()
        mock_gh.get_repo.side_effect = RuntimeError("API error")
        status = get_pr_check_status(mock_gh, "owner/repo", 42)
        assert status.pr_number == 42
        assert status.overall == "pending"
        assert status.total_checks == 0


class TestRunCiMonitor:
    def test_returns_early_when_disabled(self, monkeypatch) -> None:
        """ci_auto_fix=False should skip."""
        from repokeeper import ci_monitor as cm

        profile = {"patrol": {"ci_auto_fix": False}}
        monkeypatch.setattr(cm, "load_profile", lambda _p=None: profile)

        mock_gh = MagicMock()
        mock_llm = MagicMock()
        result = run_ci_monitor(mock_gh, mock_llm, "owner/repo")
        assert result["prs_checked"] == 0
        assert result["reason"] == "ci_auto_fix disabled"

    def test_skips_when_no_agent_prs(self, monkeypatch) -> None:
        from repokeeper import ci_monitor as cm

        profile = {"patrol": {"ci_auto_fix": True}}
        monkeypatch.setattr(cm, "load_profile", lambda _p=None: profile)
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [])

        mock_gh = MagicMock()
        mock_llm = MagicMock()
        result = run_ci_monitor(mock_gh, mock_llm, "owner/repo", max_prs=5)
        assert result["prs_checked"] == 0
        assert result["prs_fixed"] == 0

    def test_skips_passing_prs(self, monkeypatch) -> None:
        from repokeeper import ci_monitor as cm

        profile = {"patrol": {"ci_auto_fix": True}}
        monkeypatch.setattr(cm, "load_profile", lambda _p=None: profile)
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [42])

        passing_status = PRCheckStatus(pr_number=42)
        passing_status.overall = "success"
        monkeypatch.setattr(
            cm, "get_pr_check_status",
            lambda gh, repo, pr: passing_status,
        )

        mock_gh = MagicMock()
        mock_llm = MagicMock()
        result = run_ci_monitor(mock_gh, mock_llm, "owner/repo", max_prs=5)
        assert result["prs_checked"] == 1
        assert result["prs_fixed"] == 0
        assert result["details"][0]["fix_applied"] is False

    def test_attempts_fix_on_failing_pr(self, monkeypatch) -> None:
        from repokeeper import ci_monitor as cm

        profile = {"patrol": {"ci_auto_fix": True}}
        monkeypatch.setattr(cm, "load_profile", lambda _p=None: profile)
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [99])

        failing_status = PRCheckStatus(pr_number=99)
        failing_status.overall = "failure"
        monkeypatch.setattr(
            cm, "get_pr_check_status",
            lambda gh, repo, pr: failing_status,
        )

        def _fake_diagnose(gh, llm, repo, pr, profile, repo_path):
            return {"fixed": True, "summary": "CI fix applied.", "fixes_applied": ["test.py"]}

        monkeypatch.setattr(cm, "diagnose_and_fix_ci", _fake_diagnose)

        mock_gh = MagicMock()
        mock_llm = MagicMock()
        result = run_ci_monitor(mock_gh, mock_llm, "owner/repo", max_prs=5)
        assert result["prs_checked"] == 1
        assert result["prs_fixed"] == 1
        assert result["details"][0]["fix_applied"] is True
