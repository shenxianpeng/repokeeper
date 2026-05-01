from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repokeeper.patrol import (
    CIFailure,
    DepCheck,
    PatrolReport,
    StaleIssue,
    calculate_health,
    find_manifests,
    generate_health_summary,
    run_patrol,
)


def test_calculate_health_applies_deductions():
    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
        outdated_deps=[
            DepCheck("critical", "1", "2", True, severity="critical"),
            DepCheck("medium", "1", "2", True, severity="medium"),
        ],
        ci_failures=[
            CIFailure("ci", 1, "https://example.test/run", datetime(2026, 1, 1), "failure")
        ],
        stale_issues=[
            StaleIssue(
                number=1,
                title="old",
                url="https://example.test/issue",
                author="alice",
                created_at=datetime(2025, 1, 1),
                last_updated=datetime(2025, 1, 1),
                days_stale=120,
            )
        ],
    )

    assert calculate_health(report) == 80


def test_find_manifests_skips_virtualenvs(tmp_path):
    Path(tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    venv_manifest = tmp_path / ".venv" / "requirements.txt"
    venv_manifest.parent.mkdir()
    venv_manifest.write_text("ignored")

    assert find_manifests(tmp_path) == [tmp_path / "pyproject.toml"]


def test_generate_health_summary_includes_sections():
    report = PatrolReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1),
        dependencies_checked=1,
        outdated_deps=[DepCheck("pkg", "1", "2", True, severity="medium")],
        ci_failures=[CIFailure("CI", 1, "https://example.test/run", datetime(2026, 1, 1), "failure", diagnosis="broken")],
        stale_issues=[
            StaleIssue(
                number=2,
                title="old",
                url="https://example.test/issue",
                author="alice",
                created_at=datetime(2025, 1, 1),
                last_updated=datetime(2025, 1, 1),
                days_stale=100,
                summary="needs review",
            )
        ],
    )

    summary = generate_health_summary(report, {})

    assert "Outdated Dependencies" in summary
    assert "CI Failures" in summary
    assert "Stale Issues" in summary


def test_run_patrol_disabled_returns_empty_report():
    report = run_patrol(None, None, "owner/repo", profile={"patrol": {"enabled": False}})

    assert report.repo == "owner/repo"
    assert report.health_score == 100
