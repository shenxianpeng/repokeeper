"""Tests for the RepoKeeper Community Radar module (radar.py)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from repokeeper.collaboration import AGENT_TODO_LABEL, CANDIDATE_LABEL
from repokeeper.radar import (
    RADAR_LABEL,
    RadarHit,
    RadarReport,
    _build_radar_issue_body,
    _build_search_query,
    _create_radar_issue,
    _extract_source_url_from_marker,
    _find_existing_radar_issue,
    _process_radar_hit,
    _radar_marker,
    _update_existing_radar_issue,
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
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

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
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

    hit = classify_hit(_hit("", 0), Client())
    assert hit.category == "feature_request"
    assert hit.confidence == 0.8


def test_classify_hit_llm_error():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("LLM down")

    class Client:
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

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
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

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
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

    hit = _hit("bug", 0.9)
    draft = generate_issue_draft(hit, Client(), {"tone": {}})
    assert draft["title"] == "Fix X"


def test_generate_issue_draft_falls_back_on_llm_error():
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("bad json")

    class Client:
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

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
    assert "Waiting for Maintainer Approval" in summary
    assert "agent-todo" in summary


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


# ── radar_marker ─────────────────────────────────────────────────────────────


def test_radar_marker_embeds_url():
    url = "https://github.com/owner/repo/discussions/42"
    marker = _radar_marker(url)
    assert url in marker
    assert marker.startswith("<!-- repokeeper-radar:")
    assert marker.endswith("-->")


def test_extract_source_url_from_marker_found():
    body = f"Some text. {_radar_marker('https://x.com/1')} More text."
    assert _extract_source_url_from_marker(body) == "https://x.com/1"


def test_extract_source_url_from_marker_not_found():
    assert _extract_source_url_from_marker("Plain text, no marker.") is None


# ── _build_radar_issue_body ──────────────────────────────────────────────────


def test_build_radar_issue_body_includes_header_marker_footer():
    hit = _hit("bug", 0.9)
    hit.url = "https://github.com/owner/repo/discussions/1"
    hit.author = "alice"
    hit.source = "discussion"

    body = _build_radar_issue_body("## Description\nApp crashes.", hit)

    assert "Reported by @alice" in body
    assert "original discussion" in body
    assert "https://github.com/owner/repo/discussions/1" in body
    assert _radar_marker(hit.url) in body
    assert "Created by [RepoKeeper]" in body
    assert "App crashes" in body


def test_build_radar_issue_body_strips_existing_header():
    hit = _hit("bug", 0.9)
    hit.url = "https://x.com/1"
    hit.author = "alice"
    hit.source = "issue"

    draft = "> **Reported by @alice** in [original issue](https://x.com/1)\n\n## Body"
    body = _build_radar_issue_body(draft, hit)

    # Should only have one "Reported by" line
    assert body.count("Reported by @alice") == 1
    assert "## Body" in body


# ── _find_existing_radar_issue ───────────────────────────────────────────────


def test_find_existing_radar_issue_by_marker():
    source_url = "https://github.com/owner/repo/discussions/5"
    marker = _radar_marker(source_url)

    mock_existing = MagicMock()
    mock_existing.number = 10
    mock_existing.body = f"Some issue body\n{marker}\nFooter"
    mock_existing.title = "Some title"

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_existing]

    result = _find_existing_radar_issue(mock_repo, source_url, "Different title")
    assert result is not None
    assert result.number == 10


def test_find_existing_radar_issue_by_title_fallback():
    source_url = "https://github.com/owner/repo/discussions/5"

    mock_existing = MagicMock()
    mock_existing.number = 10
    mock_existing.body = "No marker here"
    mock_existing.title = "  Fix crash on startup  "

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_existing]

    result = _find_existing_radar_issue(mock_repo, source_url, "Fix crash on startup")
    assert result is not None
    assert result.number == 10


def test_find_existing_radar_issue_not_found():
    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = []

    result = _find_existing_radar_issue(mock_repo, "https://x.com/new", "New title")
    assert result is None


def test_find_existing_radar_issue_api_error():
    mock_repo = MagicMock()
    mock_repo.get_issues.side_effect = RuntimeError("API error")

    result = _find_existing_radar_issue(mock_repo, "https://x.com/1", "Title")
    assert result is None


# ── _create_radar_issue ──────────────────────────────────────────────────────


def test_create_radar_issue_creates_with_labels():
    hit = _hit("bug", 0.9)
    hit.url = "https://github.com/owner/repo/discussions/1"
    hit.suggested_title = "Fix crash on startup"
    hit.author = "alice"
    hit.source = "discussion"

    mock_created = MagicMock()
    mock_created.number = 42
    mock_created.title = "Fix crash on startup"
    mock_created.html_url = "https://github.com/owner/repo/issues/42"

    mock_repo = MagicMock()
    mock_repo.create_issue.return_value = mock_created

    result = _create_radar_issue(
        mock_repo, hit,
        draft_body="## Description\nApp crashes.",
        labels=["bug", "high-priority"],
    )

    assert result["issue_number"] == 42
    assert result["issue_url"] == "https://github.com/owner/repo/issues/42"
    assert result["source_url"] == hit.url

    # Verify create_issue was called with correct args
    call_args = mock_repo.create_issue.call_args
    kwargs = call_args[1] if call_args[1] else call_args[0] if call_args[0] else {}
    if isinstance(kwargs, tuple):
        title, body = kwargs[0], kwargs[1] if len(kwargs) > 1 else ""
    else:
        title = kwargs.get("title", "")
        body = kwargs.get("body", "")
    assert title == "Fix crash on startup"
    assert _radar_marker(hit.url) in body
    assert "Created by [RepoKeeper]" in body
    assert "RepoKeeper Candidate" in body
    labels = kwargs.get("labels", [])
    assert CANDIDATE_LABEL in labels
    assert RADAR_LABEL in labels
    assert AGENT_TODO_LABEL not in labels


def test_create_radar_issue_raises_on_failure():
    hit = _hit("bug", 0.9)
    hit.suggested_title = "Test"
    hit.url = "https://x.com/1"

    mock_repo = MagicMock()
    mock_repo.create_issue.side_effect = RuntimeError("GitHub down")

    with pytest.raises(RuntimeError, match="Failed to create issue"):
        _create_radar_issue(mock_repo, hit, "body", ["bug"])


# ── _update_existing_radar_issue ─────────────────────────────────────────────


def test_update_existing_radar_issue_adds_comment():
    hit = _hit("bug", 0.9)
    hit.url = "https://github.com/owner/repo/discussions/1"
    hit.matched_keyword = "crash"
    hit.source = "discussion"

    mock_issue = MagicMock()
    mock_issue.number = 10
    mock_issue.html_url = "https://github.com/owner/repo/issues/10"

    result = _update_existing_radar_issue(mock_issue, hit)

    assert result["issue_number"] == 10
    assert result["action"] == "commented"
    mock_issue.create_comment.assert_called_once()
    comment = mock_issue.create_comment.call_args[0][0]
    assert "renewed activity" in comment
    assert hit.url in comment


def test_update_existing_radar_issue_comment_fails_gracefully():
    hit = _hit("bug", 0.9)
    hit.url = "https://x.com/1"
    hit.matched_keyword = "crash"
    hit.source = "issue"

    mock_issue = MagicMock()
    mock_issue.number = 10
    mock_issue.html_url = "https://x.com/10"
    mock_issue.create_comment.side_effect = RuntimeError("Cannot comment")

    result = _update_existing_radar_issue(mock_issue, hit)
    # Should still return result even if comment fails
    assert result["issue_number"] == 10


# ── _process_radar_hit ───────────────────────────────────────────────────────


def test_process_radar_hit_creates_new_issue():
    class Message:
        content = json.dumps({
            "title": "Fix new bug", 
            "body": "## Description\nBug details.", 
            "labels": ["bug"],
        })

    class Completions:
        def create(self, **kwargs):
            return type("R", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class Client:
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

    hit = _hit("bug", 0.9)
    hit.url = "https://github.com/owner/repo/discussions/5"
    hit.source = "discussion"
    hit.author = "alice"

    mock_created = MagicMock()
    mock_created.number = 99
    mock_created.title = "Fix new bug"
    mock_created.html_url = "https://github.com/owner/repo/issues/99"

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = []  # No existing issues
    mock_repo.create_issue.return_value = mock_created

    result = _process_radar_hit(
        hit, mock_repo, Client(),
        {"tone": {"language": "en", "style": "friendly"}, "agent": {"model": "deepseek-chat"}},
    )
    assert result is not None
    assert result["issue_number"] == 99
    assert "action" not in result  # Created, not updated


def test_process_radar_hit_updates_existing_issue():
    class Message:
        content = json.dumps({
            "title": "Existing bug", 
            "body": "## Description\nBug details.", 
            "labels": ["bug"],
        })

    class Completions:
        def create(self, **kwargs):
            return type("R", (), {"choices": [type("C", (), {"message": Message()})()]})()

    class Client:
        def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
            all_msgs = [{"role": "system", "content": system}] + (messages or [])
            resp = Completions().create(model=model, messages=all_msgs, temperature=temperature, max_tokens=max_tokens)
            resp.content = resp.choices[0].message.content
            resp.usage = type("U", (), {"total_tokens": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "model": model})()
            return resp

    hit = _hit("bug", 0.9)
    hit.url = "https://github.com/owner/repo/discussions/5"
    hit.source = "discussion"
    hit.author = "alice"

    mock_existing = MagicMock()
    mock_existing.number = 10
    mock_existing.body = _radar_marker(hit.url)
    mock_existing.title = "Existing bug"
    mock_existing.html_url = "https://github.com/owner/repo/issues/10"

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [mock_existing]

    result = _process_radar_hit(
        hit, mock_repo, Client(),
        {"tone": {"language": "en"}, "agent": {"model": "deepseek-chat"}},
    )
    assert result is not None
    assert result["action"] == "commented"
    assert result["issue_number"] == 10


# ── run_radar with auto_create_issue ─────────────────────────────────────────


def test_run_radar_auto_creates_issues(monkeypatch):
    """When auto_create_issue is True, run_radar creates issues."""
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
    mock_repo.get_issues.return_value = []  # No existing radar issues for dedup

    # scan_issues calls get_issues first, so we need to give it the hits
    def get_issues_side_effect(*args, **kwargs):
        return [mock_issue]
    mock_repo.get_issues = MagicMock()
    mock_repo.get_issues.side_effect = [
        [mock_issue],   # First call: scan_issues
        [],             # Second call: _find_existing_radar_issue → no dupes
    ]

    mock_created = MagicMock()
    mock_created.number = 42
    mock_created.title = "Fix bug"
    mock_created.html_url = "https://github.com/owner/repo/issues/42"
    mock_repo.create_issue.return_value = mock_created

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    # Mock classify_hit
    def fake_classify(hit, *args, **kwargs):
        hit.category = "bug"
        hit.confidence = 0.9
        hit.summary = "Crash reported"
        hit.action_needed = True
        return hit

    # Mock generate_issue_draft to return a proper draft
    def fake_generate_draft(hit, *args, **kwargs):
        return {"title": "Fix crash", "body": "## Description\nApp crashes.", "labels": ["bug"]}

    monkeypatch.setattr("repokeeper.radar.classify_hit", fake_classify)
    monkeypatch.setattr("repokeeper.radar.generate_issue_draft", fake_generate_draft)
    monkeypatch.setattr("repokeeper.radar.scan_discussions", lambda *a, **kw: [])

    report = run_radar(
        mock_gh, MagicMock(), "owner/repo",
        profile={
            "radar": {
                "enabled": True,
                "keywords": ["crash"],
                "confidence_threshold": 0.7,
                "auto_create_issue": True,
            },
            "agent": {"model": "deepseek-chat"},
        },
    )
    assert len(report.issues_created) == 1
    assert report.issues_created[0]["issue_number"] == 42


def test_run_radar_skips_duplicates(monkeypatch):
    """When an existing radar issue is found, comment instead of creating."""
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

    # Simulate existing issue with matching marker
    existing = MagicMock()
    existing.number = 10
    existing.body = _radar_marker("https://x")  # matches the hit URL
    existing.title = "Existing issue"
    existing.html_url = "https://github.com/owner/repo/issues/10"

    # Return existing issue in the deduplication list AND the scan list
    mock_repo.get_issues.side_effect = [
        [mock_issue],      # First call (scan_issues)
        [existing],        # Second call (_find_existing_radar_issue)
    ]

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    def fake_classify(hit, *args, **kwargs):
        hit.category = "bug"
        hit.confidence = 0.9
        hit.summary = "Crash reported"
        hit.action_needed = True
        return hit

    monkeypatch.setattr("repokeeper.radar.classify_hit", fake_classify)
    monkeypatch.setattr("repokeeper.radar.scan_discussions", lambda *a, **kw: [])

    report = run_radar(
        mock_gh, MagicMock(), "owner/repo",
        profile={
            "radar": {
                "enabled": True,
                "keywords": ["crash"],
                "confidence_threshold": 0.7,
                "auto_create_issue": True,
            },
            "agent": {"model": "deepseek-chat"},
        },
    )
    # Should update (comment), not create
    assert len(report.issues_created) == 0
    assert len(report.issues_updated) == 1
    assert report.issues_updated[0]["action"] == "commented"


# ── generate_radar_summary with issues ───────────────────────────────────────


def test_generate_radar_summary_includes_issues_created():
    report = RadarReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 3),
        total_scanned=10,
        hits=[],
        issues_created=[
            {"issue_number": 42, "issue_url": "https://x.com/42", "source_url": "https://x.com/disc/1"},
        ],
    )
    summary = generate_radar_summary(report)
    assert "📝 Issues Created" in summary
    assert "#42" in summary
    assert "https://x.com/disc/1" in summary


def test_generate_radar_summary_includes_issues_updated():
    report = RadarReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 3),
        total_scanned=10,
        hits=[],
        issues_updated=[
            {"issue_number": 10, "issue_url": "https://x.com/10", "source_url": "https://x.com/disc/5", "action": "commented"},
        ],
    )
    summary = generate_radar_summary(report)
    assert "🔄 Issues Updated" in summary
    assert "#10" in summary


# ── RADAR_LABEL constant ─────────────────────────────────────────────────────


def test_radar_label_is_repokeeper_radar():
    assert RADAR_LABEL == "repokeeper-radar"


# ── Local scanning: non-matching items ───────────────────────────────────────


def _issue_mock(number, title, body="", updated=None):
    from datetime import timezone

    issue = MagicMock()
    issue.pull_request = None
    issue.number = number
    issue.title = title
    issue.body = body
    issue.html_url = f"https://github.com/owner/repo/issues/{number}"
    issue.user.login = "alice"
    issue.created_at = datetime.now(timezone.utc)
    issue.updated_at = updated or datetime.now(timezone.utc)
    return issue


def test_scan_issues_ignores_issues_without_keyword_match():
    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [
        _issue_mock(1, "Docs typo", "nothing relevant"),
        _issue_mock(2, "Crash on start", "it crashes"),
    ]
    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    hits = scan_issues(mock_gh, "owner/repo", ["crash"])

    assert [h.number for h in hits] == [2]


def test_scan_discussions_ignores_discussions_without_keyword_match():
    from datetime import timezone

    discussion = MagicMock()
    discussion.number = 1
    discussion.title = "Welcome"
    discussion.body = "hello"
    discussion.created_at = datetime.now(timezone.utc)
    discussion.updated_at = datetime.now(timezone.utc)
    mock_repo = MagicMock()
    mock_repo.get_discussions.return_value = [discussion]
    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    assert scan_discussions(mock_gh, "owner/repo", ["crash"]) == []


# ── Cross-repo scanning ──────────────────────────────────────────────────────


def _search_issue(number, title, body="", repo_name="other/project", updated=None, user=True):
    from datetime import timezone

    issue = MagicMock()
    issue.number = number
    issue.title = title
    issue.body = body
    issue.html_url = f"https://github.com/{repo_name}/issues/{number}"
    issue.user = MagicMock(login="bob") if user else None
    issue.created_at = datetime.now(timezone.utc)
    issue.updated_at = updated or datetime.now(timezone.utc)
    issue.repository = MagicMock(full_name=repo_name) if repo_name else None
    return issue


def test_scan_global_issues_matches_keywords_and_metadata():
    from datetime import timedelta, timezone

    from repokeeper.radar import scan_global_issues

    old = datetime.now(timezone.utc) - timedelta(days=30)
    mock_gh = MagicMock()
    mock_gh.search_issues.return_value = [
        _search_issue(1, "myproject crashes", "stack trace"),
        _search_issue(2, "unrelated", "nothing"),
        _search_issue(3, "old crash", updated=old),
        _search_issue(4, "crash without repo", repo_name="", user=False),
        _search_issue(5, "crash beyond limit"),
    ]

    hits = scan_global_issues(
        mock_gh,
        '"myproject" is:issue',
        ["crash"],
        since=datetime.now(timezone.utc) - timedelta(days=1),
        max_results=3,
    )

    # The stale issue (#3) is skipped without counting toward max_results.
    assert [h.number for h in hits] == [1, 4]
    assert hits[0].repo == "other/project"
    assert hits[0].author == "bob"
    assert hits[0].matched_keyword == "crash"
    assert hits[1].repo == "unknown"
    assert hits[1].author == "unknown"
    assert hits[1].body == ""


def test_scan_global_issues_without_keywords_includes_everything_and_handles_errors():
    from repokeeper.radar import scan_global_issues

    mock_gh = MagicMock()
    mock_gh.search_issues.return_value = [_search_issue(1, "anything", None)]

    hits = scan_global_issues(mock_gh, '"myproject"', [])

    assert len(hits) == 1
    assert hits[0].matched_keyword == '"myproject"'

    mock_gh.search_issues.side_effect = RuntimeError("rate limited")
    assert scan_global_issues(mock_gh, '"myproject"', []) == []


def _discussion_node(number, title, body="", **overrides):
    node = {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/other/project/discussions/{number}",
        "createdAt": "2026-01-02T03:04:05Z",
        "updatedAt": "2026-01-03T03:04:05Z",
        "author": {"login": "carol"},
        "repository": {"nameWithOwner": "other/project"},
    }
    node.update(overrides)
    return node


def test_scan_global_discussions_parses_graphql_nodes():
    from datetime import timezone

    from repokeeper.radar import scan_global_discussions

    requester = MagicMock()
    requester.graphql_query.return_value = {
        "data": {
            "search": {
                "nodes": [
                    None,
                    _discussion_node(1, "myproject crash", "details"),
                    _discussion_node(2, "unrelated", "nothing"),
                    _discussion_node(3, "old crash", updatedAt="2025-01-01T00:00:00Z"),
                    _discussion_node(
                        4, "crash minimal", body=None, createdAt="", updatedAt=None,
                        author=None, repository=None,
                    ),
                ]
            }
        }
    }
    mock_gh = MagicMock()
    mock_gh._Github__requester = requester

    hits = scan_global_discussions(
        mock_gh,
        '"myproject" type:discussion',
        ["crash"],
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert [h.number for h in hits] == [1, 4]
    assert hits[0].repo == "other/project"
    assert hits[0].author == "carol"
    assert hits[0].created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert hits[1].repo == "unknown"
    assert hits[1].author == "unknown"
    assert hits[1].body == ""
    variables = requester.graphql_query.call_args.kwargs["variables"]
    assert variables == {"query": '"myproject" type:discussion', "first": 30}


def test_scan_global_discussions_without_keywords_and_errors():
    from repokeeper.radar import scan_global_discussions

    requester = MagicMock()
    requester.graphql_query.return_value = {
        "data": {"search": {"nodes": [_discussion_node(1, "anything")]}}
    }
    mock_gh = MagicMock()
    mock_gh._Github__requester = requester

    hits = scan_global_discussions(mock_gh, '"myproject"', [])
    assert len(hits) == 1
    assert hits[0].matched_keyword == '"myproject"'

    requester.graphql_query.side_effect = RuntimeError("graphql down")
    assert scan_global_discussions(mock_gh, '"myproject"', []) == []


# ── Notification channels ────────────────────────────────────────────────────


def _actionable_report():
    hit = _hit("bug", 0.9)
    hit.action_needed = True
    return RadarReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1), total_scanned=1, hits=[hit],
    )


def test_notify_maintainer_telegram_and_wechat(monkeypatch):
    posts: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        return MagicMock(status_code=200)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.delenv("RKP_TELEGRAM_CHAT_ID", raising=False)

    result = notify_maintainer(
        {"notifications": {"telegram": "123:bot-token", "wechat": "https://wechat.test/hook"}},
        _actionable_report(),
    )

    assert result == {"telegram": True, "wechat": True}
    assert posts[0][0] == "https://api.telegram.org/botbot-token/sendMessage"
    assert posts[0][1]["chat_id"] == "123"
    assert "RepoKeeper Radar" in posts[0][1]["text"]
    assert posts[1][0] == "https://wechat.test/hook"
    assert posts[1][1]["markdown"]["content"].startswith("## 🔭 RepoKeeper Radar")


def test_send_telegram_reads_chat_id_from_env_and_reports_errors(monkeypatch):
    from repokeeper.radar import _send_telegram

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(json)
        return MagicMock(status_code=403)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setenv("RKP_TELEGRAM_CHAT_ID", "env-chat")

    assert _send_telegram("plain-token", "hello") is False
    assert captured["chat_id"] == "env-chat"

    def failing_post(url, json=None, timeout=None):
        raise ConnectionError("offline")

    monkeypatch.setattr("requests.post", failing_post)
    assert _send_telegram("plain-token", "hello") is False


def test_send_wechat_reports_errors(monkeypatch):
    from repokeeper.radar import _send_wechat

    def failing_post(url, json=None, timeout=None):
        raise ConnectionError("offline")

    monkeypatch.setattr("requests.post", failing_post)

    assert _send_wechat("https://wechat.test/hook", "t", "c") is False


def test_send_email_reports_smtp_errors(monkeypatch):
    import smtplib

    from repokeeper.radar import _send_email

    def failing_smtp(*args, **kwargs):
        raise smtplib.SMTPException("refused")

    monkeypatch.setattr(smtplib, "SMTP", failing_smtp)
    monkeypatch.setenv("RKP_SMTP_USER", "user")
    monkeypatch.setenv("RKP_SMTP_PASS", "pass")

    assert _send_email("to@test", "subject", "body") is False


# ── run_radar branches ───────────────────────────────────────────────────────


def _classify_as_bug(hit, *args, **kwargs):
    hit.category = "bug"
    hit.confidence = 0.9
    hit.action_needed = True
    return hit


def test_run_radar_cross_repo_mode_builds_queries(monkeypatch):
    calls: dict[str, str] = {}

    def fake_issues(gh, query, keywords, since=None):
        calls["issues"] = query
        return [_hit("", 0.0)]

    def fake_discussions(gh, query, keywords, since=None):
        calls["discussions"] = query
        return []

    monkeypatch.setattr("repokeeper.radar.scan_global_issues", fake_issues)
    monkeypatch.setattr("repokeeper.radar.scan_global_discussions", fake_discussions)
    monkeypatch.setattr("repokeeper.radar.classify_hit", _classify_as_bug)
    monkeypatch.setattr(
        "repokeeper.radar.generate_issue_draft",
        lambda hit, *a, **kw: {"title": "Drafted", "labels": ["bug"]},
    )
    notified: list[RadarReport] = []
    monkeypatch.setattr("repokeeper.radar.notify_maintainer", lambda p, r: notified.append(r))

    report = run_radar(
        MagicMock(), MagicMock(), "owner/project",
        profile={"radar": {"cross_repo_search": True}, "agent": {"model": "m"}},
    )

    assert calls == {
        "issues": '"project" is:issue is:open -repo:owner/project',
        "discussions": '"project" type:discussion -repo:owner/project',
    }
    assert report.hits[0].suggested_title == "Drafted"
    assert notified == [report]

    run_radar(
        MagicMock(), MagicMock(), "project",
        profile={"radar": {"cross_repo_search": True, "cross_repo_query": "custom"}},
    )
    assert calls["issues"] == "custom is:issue is:open -repo:project"

    run_radar(MagicMock(), MagicMock(), "project", profile={"radar": {"cross_repo_search": True}})
    assert calls["issues"] == '"project" is:issue is:open -repo:project'


def test_run_radar_auto_create_handles_skipped_and_failed_hits(monkeypatch):
    hits = [_hit("", 0.0), _hit("", 0.0)]
    hits[1].url = "https://example.test/2"
    monkeypatch.setattr("repokeeper.radar.scan_issues", lambda *a, **kw: hits)
    monkeypatch.setattr("repokeeper.radar.scan_discussions", lambda *a, **kw: [])
    monkeypatch.setattr("repokeeper.radar.classify_hit", _classify_as_bug)

    def fake_process(hit, gh_repo, llm, profile):
        if hit.url.endswith("/1"):
            return None
        raise RuntimeError("github refused")

    monkeypatch.setattr("repokeeper.radar._process_radar_hit", fake_process)
    monkeypatch.setattr(
        "repokeeper.radar.generate_issue_draft",
        lambda hit, *a, **kw: {"title": "Fallback", "labels": ["needs-triage"]},
    )
    monkeypatch.setattr("repokeeper.radar.notify_maintainer", lambda p, r: {})

    report = run_radar(
        MagicMock(), MagicMock(), "owner/repo",
        profile={"radar": {"keywords": ["crash"], "auto_create_issue": True}},
    )

    assert report.issues_created == []
    assert report.issues_updated == []
    assert hits[0].suggested_title == ""
    assert hits[1].suggested_title == "Fallback"
    assert hits[1].suggested_labels == ["needs-triage"]


def test_run_radar_skips_notification_without_actionable_hits(monkeypatch):
    monkeypatch.setattr("repokeeper.radar.scan_issues", lambda *a, **kw: [_hit("", 0.0)])
    monkeypatch.setattr("repokeeper.radar.scan_discussions", lambda *a, **kw: [])

    def classify_noise(hit, *args, **kwargs):
        hit.category = "noise"
        hit.confidence = 0.2
        return hit

    monkeypatch.setattr("repokeeper.radar.classify_hit", classify_noise)

    def fail_notify(profile, report):
        raise AssertionError("must not notify")

    monkeypatch.setattr("repokeeper.radar.notify_maintainer", fail_notify)

    report = run_radar(
        MagicMock(), MagicMock(), "owner/repo", profile={"radar": {"keywords": ["crash"]}},
    )

    assert report.hits == []
    assert len(report.noise) == 1


def test_find_existing_radar_issue_skips_unrelated_issues():
    unrelated = MagicMock()
    unrelated.body = "no marker here"
    unrelated.title = "Something else"
    unrelated.number = 3
    gh_repo = MagicMock()
    gh_repo.get_issues.return_value = [unrelated]

    assert _find_existing_radar_issue(gh_repo, "https://example.test/1", "Crash") is None
