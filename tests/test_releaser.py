"""Tests for the Draft Release Generator module."""

from __future__ import annotations

from datetime import datetime

from repokeeper.releaser import (
    CommitEntry,
    ReleaseDraft,
    ReleaseReport,
    _bump_version,
    _find_latest_tag,
    format_commit_list,
    generate_release_summary,
)


class TestCommitEntry:
    """CommitEntry data class."""

    def test_create_entry(self):
        entry = CommitEntry(
            sha="abc123", author="alice",
            date=datetime(2026, 1, 1), message="fix: typo",
        )
        assert entry.sha == "abc123"
        assert entry.author == "alice"
        assert not entry.is_pr_merge

    def test_pr_merge_entry(self):
        entry = CommitEntry(
            sha="def456", author="bob",
            date=datetime(2026, 2, 1), message="Merge pull request #42 from feature",
            is_pr_merge=True, pr_number=42, pr_title="Add new feature",
        )
        assert entry.is_pr_merge
        assert entry.pr_number == 42
        assert entry.pr_title == "Add new feature"


class TestReleaseDraft:
    """ReleaseDraft data class."""

    def test_default_prerelease(self):
        draft = ReleaseDraft(tag_name="v1.0.0", target_commitish="main",
                             title="v1.0.0", notes="## What's Changed")
        assert not draft.is_prerelease

    def test_prerelease_flag(self):
        draft = ReleaseDraft(tag_name="v1.0.0-rc1", target_commitish="main",
                             title="v1.0.0-rc1", notes="## What's Changed",
                             is_prerelease=True)
        assert draft.is_prerelease


class TestReleaseReport:
    """ReleaseReport data class."""

    def test_empty_report(self):
        report = ReleaseReport(repo="owner/repo", generated_at=datetime(2026, 1, 1))
        assert report.commits_scanned == 0
        assert report.since_tag == ""
        assert report.draft is None
        assert report.release_url == ""
        assert report.error == ""

    def test_with_draft(self):
        draft = ReleaseDraft(tag_name="v1.1.0", target_commitish="main",
                             title="v1.1.0", notes="fix: bug")
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=10,
            since_tag="v1.0.0",
            draft=draft,
            release_url="https://github.com/owner/repo/releases/tag/v1.1.0",
        )
        assert report.commits_scanned == 10
        assert report.since_tag == "v1.0.0"
        assert report.draft is not None
        assert report.draft.tag_name == "v1.1.0"
        assert "v1.1.0" in report.release_url


class TestBumpVersion:
    """_bump_version utility."""

    def test_v_prefix(self):
        assert _bump_version("v1.2.3") == "v1.2.4"

    def test_no_v_prefix(self):
        assert _bump_version("1.2.3") == "1.2.4"

    def test_two_part_version(self):
        assert _bump_version("v1.2") == "v1.2.1"

    def test_empty_string(self):
        assert _bump_version("") == "v0.1.0"

    def test_non_semver(self):
        assert _bump_version("release-1") == "release-1.1"


class TestFindLatestTag:
    """_find_latest_tag with mocked repo."""

    def test_no_tags(self):
        class FakeRepo:
            def get_tags(self):
                return []

        tag, sha = _find_latest_tag(FakeRepo())
        assert tag == ""
        assert sha == ""

    def test_with_tags(self):
        class FakeCommit:
            sha = "commit123"

        class FakeTag:
            name = "v1.0.0"
            commit = FakeCommit()

        class FakeRepo:
            def get_tags(self):
                return [FakeTag()]

        tag, sha = _find_latest_tag(FakeRepo())
        assert tag == "v1.0.0"
        assert sha == "commit123"

    def test_get_tags_raises(self):
        class FakeRepo:
            def get_tags(self):
                raise Exception("API error")

        tag, sha = _find_latest_tag(FakeRepo())
        assert tag == ""
        assert sha == ""


class TestFormatCommitList:
    """format_commit_list utility."""

    def test_empty_commits(self):
        result = format_commit_list([], "owner/repo", "v1.0.0")
        assert "Repository: owner/repo" in result
        assert "Previous tag: v1.0.0" in result
        assert "0 total" in result

    def test_single_commit(self):
        commits = [
            CommitEntry(
                sha="abc123", author="alice",
                date=datetime(2026, 1, 1), message="fix: typo",
            ),
        ]
        result = format_commit_list(commits, "owner/repo", "v1.0.0")
        assert "1 total" in result
        assert "abc123" in result or "abc12" in result
        assert "alice" in result
        assert "fix: typo" in result

    def test_pr_merge_commit(self):
        commits = [
            CommitEntry(
                sha="def456", author="bob",
                date=datetime(2026, 2, 1), message="Merge pull request #42 from feature",
                is_pr_merge=True, pr_number=42,
            ),
        ]
        result = format_commit_list(commits, "owner/repo", "v1.0.0")
        assert "#42" in result

    def test_no_previous_tag(self):
        result = format_commit_list([], "owner/repo", "")
        assert "(no previous tag)" in result


class TestGenerateReleaseSummary:
    """generate_release_summary utility."""

    def test_error_report(self):
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=5,
            error="Something went wrong",
        )
        summary = generate_release_summary(report)
        assert "Error" in summary
        assert "Something went wrong" in summary

    def test_no_draft(self):
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=0,
        )
        summary = generate_release_summary(report)
        assert "No new commits" in summary

    def test_with_draft(self):
        draft = ReleaseDraft(
            tag_name="v2.0.0",
            target_commitish="main",
            title="v2.0.0 - Major release",
            notes="## What's Changed\n\n* fix: critical bug in abc123\n* feat: new API",
        )
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=10,
            since_tag="v1.0.0",
            draft=draft,
            release_url="https://github.com/owner/repo/releases/tag/v2.0.0",
        )
        summary = generate_release_summary(report)
        assert "v2.0.0" in summary
        assert "Major release" in summary
        assert "v1.0.0" in summary
        assert "View on GitHub" in summary

    def test_draft_with_long_notes(self):
        draft = ReleaseDraft(
            tag_name="v3.0.0",
            target_commitish="main",
            title="v3.0.0",
            notes="\n".join(f"* change {i}" for i in range(25)),
        )
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=25,
            since_tag="v2.0.0",
            draft=draft,
        )
        summary = generate_release_summary(report)
        assert "more lines" in summary
        assert "v3.0.0" in summary

    def test_complete_report(self):
        draft = ReleaseDraft(
            tag_name="v1.5.0",
            target_commitish="main",
            title="v1.5.0 - Minor release",
            notes="## What's Changed\n\n* fix: resolved issue",
            is_prerelease=True,
        )
        report = ReleaseReport(
            repo="owner/repo",
            generated_at=datetime(2026, 1, 1),
            commits_scanned=3,
            since_tag="v1.4.0",
            draft=draft,
            release_url="https://github.com/owner/repo/releases/tag/v1.5.0",
        )
        summary = generate_release_summary(report)
        assert "Prerelease: Yes" in summary or "prerelease" in summary.lower()
