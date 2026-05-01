from __future__ import annotations

from datetime import datetime

from repokeeper.radar import (
    RadarHit,
    classify_hit,
    filter_hits,
    generate_issue_draft,
    notify_maintainer,
    run_radar,
)


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


def test_filter_hits_keeps_non_noise_above_threshold():
    hits = [_hit("bug", 0.9), _hit("feature_request", 0.69), _hit("noise", 1.0)]

    assert filter_hits(hits, confidence_threshold=0.7) == [hits[0]]


def test_classify_hit_parses_llm_json():
    class Message:
        content = '{"category":"bug","confidence":0.95,"summary":"Crash","suggested_title":"Fix crash","suggested_labels":["bug"],"action_needed":true}'

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


def test_notify_maintainer_no_actionable_hits_returns_empty():
    report = type("Report", (), {"repo": "owner/repo", "hits": [_hit("bug", 0.9)]})()

    assert notify_maintainer({}, report) == {}


def test_run_radar_disabled_returns_empty_report():
    report = run_radar(None, None, "owner/repo", profile={"radar": {"enabled": False}})

    assert report.total_scanned == 0
    assert report.hits == []
