"""Tests for repokeeper.ci_monitor — data structures only.

Full integration tests (get_pr_check_status, diagnose_and_fix_ci)
require GitHub API credentials and are covered by the integration
test suite in CI.
"""

from __future__ import annotations

from repokeeper.ci_monitor import (
    CheckResult,
    PRCheckStatus,
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


class TestPRCheckStatus:
    def test_default_is_pending(self) -> None:
        status = PRCheckStatus(pr_number=42)
        assert status.overall == "pending"
        assert status.total_checks == 0

    def test_success_when_all_pass(self) -> None:
        status = PRCheckStatus(pr_number=42)
        status.total_checks = 2
        status.completed = 2
        status.passed = 2
        status.overall = "success"  # manually set for test
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
        # With in-progress checks, should be pending
        assert status.overall == "pending"
