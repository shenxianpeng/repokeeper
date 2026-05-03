"""Tests for the RepoKeeper Community Radar module (radar.py)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from repokeeper.radar import (
    RadarHit,
    RadarReport,
    _build_search_query,
    classify_hit,
    filter_hits,
    generate_issue_draft,
    generate_radar_summary,
    notify_maintainer,
    run_radar,
    scan_discussions,
    scan_issues,
)

# ── Helper ────────────────────────────────────────────────────────────────────


def _hit(category: str, confidence: float) -> RadarHit:
    return RadarHit(
        source="issue",
        repo="owner/repo",
        number=1,
        title="Crash on startup",
        body="The app crashes.",
        url="https://example.test/1",
        author="alice",
        created_at=datetime(2026, 1, 1),
        matched_keyword="crash",
        category=category,
        confidence=confidence,
    )


# ── _build_search_query ───────────────────────────────────────────────────────


def test_build_search_query_single_keyword():
    q = _build_search_query(["bug"], "owner/repo")
    assert q == 'repo:owner/repo ("bug")'


def test_build_search_query_multiple_keywords():
    q = _build_search_query(["bug", "crash", "security"], "owner/repo")
    assert "repo:owner/repo" in q
    assert '"bug"' in q
    assert '"crash"' in q
    assert "OR" in q


# ── filter_hits ───────────────────────────────────────────────────────────────


def test_filter_hits_keeps_non_noise_above_threshold():
    hits = [_hit("bug", 0.9), _hit("feature_request", 0.69), _hit("noise", 1.0)]

    assert filter_hits(hits, confidence_threshold=0.7) == [hits[0]]


def test_filter_hits_empty():
    assert filter_hits([], 0.5) == []


def test_filter_hits_all_below_threshold():
    hits = [_hit("bug", 0.3), _hit("feature_request", 0.5)]
    assert filter_hits(hits, confidence_threshold=0.7) == []


# ── classify_hit ──────────────────────────────────────────────────────────────


def test_classify_hit_parses_llm_json():
    class Message:
        content = json.dumps({
            "category": "bug", "confidence": 0.95, "summary": "Crash",
            "suggested_title": "Fix crash", "suggested_labels": ["bug"],
            "action_needed": True,
        })

    class Choice:
        message = Message()

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [Choice()]})()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = classify_hit(_hit("", 0), Client())
    assert hit.category == "bug"
    assert hit.confidence == 0.95
    assert hit.suggested_labels == ["bug"]
    assert hit.action_needed is True


def test_classify_hit_with_fence():
    """JSON wrapped in ``` fences."""
    class Message:
        content = '```json\n{"category":"feature_request","confidence":0.8,"summary":"Add X","suggested_title":"Feature: X","suggested_labels":["enhancement"],"action_needed":true}\n```'

    class Choice:
        message = Message()

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [Choice()]})()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = classify_hit(_hit("", 0), Client())
    assert hit.category == "feature_request"
    assert hit.confidence == 0.8


def test_classify_hit_llm_error():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("LLM down")

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = classify_hit(_hit("", 0), Client())
    # Falls back to "noise" / 0.0 on error
    assert hit.category == "noise"
    assert hit.confidence == 0.0


# ── scan_issues ───────────────────────────────────────────────────────────────


def test_scan_issues_finds_keywords(monkeypatch):
    from datetime import datetime, timezone

    mock_issue = MagicMock()
    mock_issue.pull_request = None  # Not a PR
    mock_issue.number = 1
    mock_issue.title = "Bug: app crashes"
    mock_issue.body = "When I click, it crashes."
    mock_issue.html_url = "https://github.com/owner/repo/issues/1"
    mock_issue.user.login = "alice"
    mock_issue.created_at = datetime.now(timezone.utc)
    mock_issue.updated_at = datetime.now(timezone.utc)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["crash", "bug"])
    assert len(hits) == 1  # will match "crash" first, break
    assert hits[0].source == "issue"
    assert hits[0].matched_keyword in ("crash", "bug")


def test_scan_issues_skips_pull_requests(monkeypatch):
    mock_pr = MagicMock()
    mock_pr.pull_request = True  # This is a PR
    mock_pr.number = 2
    mock_pr.title = "fix bug"
    mock_pr.body = "body"
    mock_pr.html_url = "https://x"
    mock_pr.user.login = "bob"
    mock_pr.created_at = datetime(2026, 1, 1)
    mock_pr.updated_at = datetime(2026, 1, 1)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_pr]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["bug"])
    assert hits == []


def test_scan_issues_filters_by_time(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old_issue = MagicMock()
    old_issue.pull_request = None
    old_issue.number = 1
    old_issue.title = "old bug"
    old_issue.body = "body"
    old_issue.html_url = "https://x"
    old_issue.user.login = "alice"
    old_issue.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    old_issue.updated_at = datetime.now(timezone.utc) - timedelta(days=100)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [old_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    since = datetime.now(timezone.utc) - timedelta(days=1)
    hits = scan_issues(mock_gh, "owner/repo", ["bug"], since=since)
    assert hits == []


def test_scan_issues_respects_max_results(monkeypatch):
    issues = []
    for i in range(10):
        mock_issue = MagicMock()
        mock_issue.pull_request = None
        mock_issue.number = i
        mock_issue.title = f"bug {i}"
        mock_issue.body = "body"
        mock_issue.html_url = f"https://x/{i}"
        mock_issue.user.login = "alice"
        mock_issue.created_at = datetime(2026, 1, 1)
        mock_issue.updated_at = datetime(2026, 1, 1)
        issues.append(mock_issue)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = issues

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["bug"], max_results=3)
    assert len(hits) <= 3


def test_scan_issues_no_user(monkeypatch):
    mock_issue = MagicMock()
    mock_issue.pull_request = None
    mock_issue.number = 1
    mock_issue.title = "bug report"
    mock_issue.body = "body"
    mock_issue.html_url = "https://x"
    mock_issue.user = None  # No user
    mock_issue.created_at = datetime(2026, 1, 1)
    mock_issue.updated_at = datetime(2026, 1, 1)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["bug"])
    assert len(hits) == 1
    assert hits[0].author == "unknown"


def test_scan_issues_empty_body(monkeypatch):
    mock_issue = MagicMock()
    mock_issue.pull_request = None
    mock_issue.number = 1
    mock_issue.title = "security issue"
    mock_issue.body = None  # No body
    mock_issue.html_url = "https://x"
    mock_issue.user.login = "alice"
    mock_issue.created_at = datetime(2026, 1, 1)
    mock_issue.updated_at = datetime(2026, 1, 1)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["security"])
    assert len(hits) == 1


# ── scan_discussions ──────────────────────────────────────────────────────────


def test_scan_discussions_finds_keywords(monkeypatch):
    """scan_discussions uses PyGithub's get_discussions and finds matches."""
    mock_discussion = MagicMock()
    mock_discussion.number = 5
    mock_discussion.title = "Security vulnerability"
    mock_discussion.body_text = "There is a SQL injection"
    mock_discussion.url = "https://github.com/owner/repo/discussions/5"
    mock_discussion.author.login = "alice"
    mock_discussion.created_at = datetime(2026, 1, 1)
    mock_discussion.updated_at = datetime(2026, 1, 2)

    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = [mock_discussion]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["security"])
    assert len(hits) == 1
    assert hits[0].source == "discussion"
    assert hits[0].number == 5
    assert hits[0].matched_keyword == "security"


def test_scan_discussions_no_keywords():
    """Empty keywords returns empty list immediately."""
    hits = scan_discussions(MagicMock(), "owner/repo", [])
    assert hits == []


def test_scan_discussions_no_get_discussions_method(monkeypatch):
    """Older PyGithub without get_discussions returns empty."""
    mock_repo = MagicMock()
    # Simulate no get_discussions attribute
    if hasattr(mock_repo, "get_discussions"):
        del mock_repo.get_discussions

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["bug"])
    assert hits == []


def test_scan_discussions_respects_max_results(monkeypatch):
    discussions = []
    for i in range(10):
        d = MagicMock()
        d.number = i
        d.title = f"security {i}"
        d.body_text = "body"
        d.url = f"https://x/{i}"
        d.author.login = "alice"
        d.created_at = datetime(2026, 1, 1)
        d.updated_at = datetime(2026, 1, 1)
        discussions.append(d)

    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = discussions

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["security"], max_results=5)
    assert len(hits) <= 5


def test_scan_discussions_filters_by_time(monkeypatch):
    from datetime import datetime, timedelta, timezone

    old_discussion = MagicMock()
    old_discussion.number = 1
    old_discussion.title = "old security"
    old_discussion.body_text = "body"
    old_discussion.url = "https://x"
    old_discussion.author.login = "alice"
    old_discussion.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    old_discussion.updated_at = datetime.now(timezone.utc) - timedelta(days=100)

    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = [old_discussion]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    since = datetime.now(timezone.utc) - timedelta(days=1)
    hits = scan_discussions(mock_gh, "owner/repo", ["security"], since=since)
    assert hits == []


def test_scan_discussions_no_author(monkeypatch):
    """Discussion with no author should still work."""
    mock_discussion = MagicMock()
    mock_discussion.number = 1
    mock_discussion.title = "bug report"
    mock_discussion.body_text = "body"
    mock_discussion.url = "https://x"
    mock_discussion.author = None
    mock_discussion.created_at = datetime(2026, 1, 1)
    mock_discussion.updated_at = datetime(2026, 1, 1)

    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = [mock_discussion]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["bug"])
    assert len(hits) == 1
    assert hits[0].author == "unknown"


def test_scan_discussions_no_body(monkeypatch):
    """Discussion with no body_text should fall back and still match title."""
    mock_discussion = MagicMock()
    # Remove body_text and body to test fallback
    mock_discussion.number = 1
    mock_discussion.title = "crash bug"
    mock_discussion.url = "https://x"
    mock_discussion.author.login = "alice"
    mock_discussion.created_at = datetime(2026, 1, 1)
    mock_discussion.updated_at = datetime(2026, 1, 1)
    # Make body_text and body return empty string
    type(mock_discussion).body_text = ""
    type(mock_discussion).body = ""

    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = [mock_discussion]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["crash"])
    assert len(hits) == 1


def test_scan_discussions_api_error(monkeypatch):
    """When get_discussions raises, returns empty list."""
    mock_repo = MagicMock()
    mock_repo.get_discussions.side_effect = RuntimeError("GraphQL error")

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_discussions(mock_gh, "owner/repo", ["bug"])
    assert hits == []


# ── generate_issue_draft ─────────────────────────────────────────────────────


def test_generate_issue_draft_generates():
    class Message:
        content = json.dumps({
            "title": "Fix crash", "body": "## Description\nApp crashes.",
            "labels": ["bug", "high-priority"],
        })

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = _hit("bug", 0.9)
    draft = generate_issue_draft(hit, Client(), {"tone": {"language": "en", "style": "friendly"}})
    assert draft["title"] == "Fix crash"
    assert "App crashes" in draft["body"]
    assert draft["labels"] == ["bug", "high-priority"]


def test_generate_issue_draft_with_fence():
    class Message:
        content = '```json\n{"title": "Fix X", "body": "Body", "labels": ["bug"]}\n```'

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = _hit("bug", 0.9)
    draft = generate_issue_draft(hit, Client(), {"tone": {}})
    assert draft["title"] == "Fix X"


def test_generate_issue_draft_falls_back_on_llm_error():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("bad json")

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    hit = _hit("bug", 0.9)
    hit.suggested_title = "Suggested"
    hit.suggested_labels = ["bug"]

    draft = generate_issue_draft(hit, Client(), {"tone": {"language": "en"}})
    assert draft["title"] == "Suggested"
    assert draft["labels"] == ["bug"]


# ── notify_maintainer ────────────────────────────────────────────────────────


def test_notify_maintainer_no_actionable_hits_returns_empty():
    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        total_scanned=1, hits=[],
    )
    assert notify_maintainer({}, report) == {}


def test_notify_maintainer_no_notification_config(monkeypatch):
    """When no notification config is set, returns empty results."""
    hit = _hit("bug", 0.9)
    hit.action_needed = True
    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        total_scanned=1, hits=[hit],
    )
    result = notify_maintainer({}, report)
    assert result == {}


def test_notify_maintainer_with_email(monkeypatch):
    """When email is configured, attempts to send email."""
    import smtplib

    mock_smtp = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **kw: mock_smtp)
    monkeypatch.setenv("RKP_SMTP_USER", "test@test.com")
    monkeypatch.setenv("RKP_SMTP_PASS", "password")

    hit = _hit("bug", 0.9)
    hit.action_needed = True
    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        total_scanned=1, hits=[hit],
    )
    profile = {"notifications": {"email": "maintainer@test.com"}}
    result = notify_maintainer(profile, report)
    assert "email" in result
    assert result["email"] is True


def test_notify_maintainer_email_no_smtp_config(monkeypatch):
    """Email fails gracefully when SMTP not configured."""
    monkeypatch.delenv("RKP_SMTP_USER", raising=False)
    monkeypatch.delenv("RKP_SMTP_PASS", raising=False)

    hit = _hit("bug", 0.9)
    hit.action_needed = True
    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        total_scanned=1, hits=[hit],
    )
    profile = {"notifications": {"email": "maintainer@test.com"}}
    result = notify_maintainer(profile, report)
    assert result.get("email") is False or "email" not in result


# ── generate_radar_summary ────────────────────────────────────────────────────


def test_generate_radar_summary_with_bugs_and_features():
    hit_bug = RadarHit(
        source="issue", repo="owner/repo", number=1, title="Crash bug",
        body="desc", url="https://x/1", author="alice",
        created_at=datetime(2026, 1, 1), matched_keyword="crash",
        category="bug", confidence=0.9, summary="App crashes on load",
    )
    hit_feat = RadarHit(
        source="issue", repo="owner/repo", number=2, title="Dark mode",
        body="desc", url="https://x/2", author="bob",
        created_at=datetime(2026, 1, 2), matched_keyword="feature request",
        category="feature_request", confidence=0.8, summary="Add dark mode",
    )

    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 3),
        total_scanned=10, hits=[hit_bug, hit_feat],
        bugs=[hit_bug], feature_requests=[hit_feat],
    )
    summary = generate_radar_summary(report)
    assert "Community Radar Report" in summary
    assert "🐛 Bugs" in summary
    assert "Crash bug" in summary
    assert "💡 Feature Requests" in summary
    assert "Dark mode" in summary


def test_generate_radar_summary_empty():
    report = RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1),
        total_scanned=10,
    )
    summary = generate_radar_summary(report)
    assert "No actionable hits found" in summary


# ── run_radar ────────────────────────────────────────────────────────────────


def test_run_radar_disabled_returns_empty_report():
    report = run_radar(None, None, "owner/repo", profile={"radar": {"enabled": False}})
    assert report.total_scanned == 0
    assert report.hits == []


def test_run_radar_no_keywords(monkeypatch):
    """When no keywords configured, returns empty report."""
    report = run_radar(MagicMock(), MagicMock(), "owner/repo",
                        profile={"radar": {"enabled": True, "keywords": []}})
    assert report.total_scanned == 0


def test_run_radar_scans_issues(monkeypatch):
    """run_radar scans issues and classifies them."""
    from datetime import datetime, timezone

    mock_issue = MagicMock()
    mock_issue.pull_request = None
    mock_issue.number = 1
    mock_issue.title = "Bug report"
    mock_issue.body = "something crashed"
    mock_issue.html_url = "https://x"
    mock_issue.user.login = "alice"
    mock_issue.created_at = datetime.now(timezone.utc)
    mock_issue.updated_at = datetime.now(timezone.utc)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_issue]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    # Mock classify_hit to set category/confidence
    def fake_classify(hit, *args, **kwargs):
        hit.category = "bug"
        hit.confidence = 0.9
        hit.summary = "Crash reported"
        hit.action_needed = True
        return hit

    monkeypatch.setattr("repokeeper.radar.classify_hit", fake_classify)

    # Mock scan_discussions to return empty
    monkeypatch.setattr("repokeeper.radar.scan_discussions",
                         lambda *a, **kw: [])

    report = run_radar(
        mock_gh, MagicMock(), "owner/repo",
        profile={
            "radar": {"enabled": True, "keywords": ["crash"], "confidence_threshold": 0.7},
            "agent": {"model": "deepseek-chat"},
        },
    )
    assert len(report.hits) == 1
    assert report.hits[0].category == "bug"


def test_run_radar_uses_default_profile():
    """run_radar loads default profile when none provided."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("repokeeper.radar.load_profile",
                         lambda: {"radar": {"enabled": False}})
    report = run_radar(MagicMock(), MagicMock(), "owner/repo")
    assert report.total_scanned == 0
    monkeypatch.undo()
