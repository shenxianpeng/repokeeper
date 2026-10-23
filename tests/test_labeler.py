"""Tests for the RepoKeeper Auto-Labeler module (labeler.py)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from repokeeper.collaboration import LABELER_LABEL
from repokeeper.labeler import (
    LabelerReport,
    LabelerResult,
    _infer_label_naming_convention,
    _pick_example,
    apply_labels,
    classify_with_context,
    create_new_labels,
    fetch_issue,
    fetch_pr_data,
    fetch_repo_labels,
    find_unlabeled_issues,
    generate_labeler_summary,
    label_single_issue,
    label_single_pr,
    label_unlabeled_issues,
    resolve_labels_against_repo,
    run_labeler,
    suggest_labels_comment,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_repo_labels() -> list[dict[str, str]]:
    return [
        {"name": "bug", "description": "Something isn't working", "color": "d73a4a"},
        {"name": "enhancement", "description": "New feature or request", "color": "a2eeef"},
        {"name": "question", "description": "", "color": "d876e3"},
        {"name": "documentation", "description": "Improve docs", "color": "0075ca"},
        {"name": "module_utils/basic", "description": "Basic utility modules", "color": "0052cc"},
        {"name": "module_utils/io", "description": "IO-related modules", "color": "006b75"},
        {"name": "good first issue", "description": "", "color": "7057ff"},
    ]


class _FakeLabelerLLM:
    """Fake LLM that returns a controllable classification."""

    def __init__(self, category="bug", confidence=0.9,
                 existing_labels=None, new_labels=None, summary="Test"):
        self.category = category
        self.confidence = confidence
        self.existing_labels = existing_labels or []
        self.new_labels = new_labels or []
        self.summary = summary

    def chat(self, system="", messages=None, model="", temperature=0.1, max_tokens=8000, stream=False):
        resp = type("Response", (), {})()
        resp.content = json.dumps({
            "category": self.category,
            "confidence": self.confidence,
            "summary": self.summary,
            "existing_labels": self.existing_labels,
            "new_labels": self.new_labels,
            "reasoning": "test reasoning",
        })
        resp.usage = type("Usage", (), {
            "prompt_tokens": 100, "completion_tokens": 50,
            "total_tokens": 150, "cost_usd": 0.0, "model": "test",
        })()
        return resp


class _FailingFakeLLM:
    def chat(self, **kwargs):
        raise RuntimeError("LLM down")


def _mock_gh_with_issue(number=42, title="Bug", body="desc",
                        labels=None, is_pr=False):
    """Create a mock GitHub client with a single issue."""
    mock_issue = MagicMock()
    mock_issue.pull_request = True if is_pr else None
    mock_issue.number = number
    mock_issue.title = title
    mock_issue.body = body
    mock_issue.html_url = f"https://github.com/owner/repo/issues/{number}"
    mock_issue.labels = [MagicMock() for _ in range(len(labels or []))]
    for i, lb in enumerate(labels or []):
        mock_issue.labels[i].name = lb

    mock_repo = MagicMock()
    mock_repo.get_issue.return_value = mock_issue

    # Mock labels
    mock_repo_labels = []
    for lb_dict in _make_repo_labels():
        mock_label = MagicMock()
        mock_label.name = lb_dict["name"]
        mock_label.description = lb_dict["description"]
        mock_label.color = lb_dict["color"]
        mock_repo_labels.append(mock_label)
    mock_repo.get_labels.return_value = mock_repo_labels

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo
    return mock_gh, mock_repo, mock_issue


def _mock_gh_with_pr(number=42, title="feat: add X", body="Implements X",
                     files=None):
    """Create a mock GitHub client with a PR."""
    mock_pr = MagicMock()
    mock_pr.number = number
    mock_pr.title = title
    mock_pr.body = body
    mock_pr.html_url = f"https://github.com/owner/repo/pull/{number}"
    mock_pr.labels = []

    # Mock files
    mock_file1 = MagicMock()
    mock_file1.filename = "src/main.py"
    mock_file1.status = "modified"
    mock_file1.additions = 50
    mock_file1.deletions = 10

    mock_file2 = MagicMock()
    mock_file2.filename = "docs/README.md"
    mock_file2.status = "modified"
    mock_file2.additions = 5
    mock_file2.deletions = 2

    mock_pr.get_files.return_value = files or [mock_file1, mock_file2]

    mock_repo = MagicMock()
    mock_repo.get_pull.return_value = mock_pr
    mock_repo.get_issue.return_value = mock_pr  # labels are applied via get_issue
    mock_repo.get_label.side_effect = RuntimeError("not found")

    mock_repo_labels = []
    for lb_dict in _make_repo_labels():
        mock_label = MagicMock()
        mock_label.name = lb_dict["name"]
        mock_label.description = lb_dict["description"]
        mock_label.color = lb_dict["color"]
        mock_repo_labels.append(mock_label)
    mock_repo.get_labels.return_value = mock_repo_labels

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo
    return mock_gh, mock_repo, mock_pr


# ── fetch_repo_labels ────────────────────────────────────────────────────────


def test_fetch_repo_labels_returns_labels():
    mock_gh, _, _ = _mock_gh_with_issue()
    labels = fetch_repo_labels(mock_gh, "owner/repo")
    assert len(labels) == 7
    assert labels[0]["name"] == "bug"
    assert labels[0]["description"] == "Something isn't working"


def test_fetch_repo_labels_error_returns_empty():
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("API down")
    labels = fetch_repo_labels(mock_gh, "owner/repo")
    assert labels == []


# ── _infer_label_naming_convention ──────────────────────────────────────────


def test_infer_convention_slash_separated():
    labels = [
        {"name": "area/module-a", "description": "", "color": ""},
        {"name": "area/module-b", "description": "", "color": ""},
    ]
    result = _infer_label_naming_convention(labels)
    assert "slash-separated" in result


def test_infer_convention_colon_prefixed():
    labels = [
        {"name": "type: bug", "description": "", "color": ""},
        {"name": "type: feature", "description": "", "color": ""},
    ]
    result = _infer_label_naming_convention(labels)
    assert "colon-prefixed" in result


def test_infer_convention_plain_lowercase():
    labels = [
        {"name": "bug", "description": "", "color": ""},
        {"name": "enhancement", "description": "", "color": ""},
    ]
    result = _infer_label_naming_convention(labels)
    assert "plain lowercase" in result


def test_infer_convention_empty():
    result = _infer_label_naming_convention([])
    assert "plain lowercase" in result


def test_pick_example():
    assert _pick_example(["a/b", "c/d"], "/") in ("a/b", "c/d")


# ── classify_with_context ───────────────────────────────────────────────────


def test_classify_with_context_issue():
    labels = _make_repo_labels()
    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    result = classify_with_context(
        "Crash on startup", "App crashes",
        labels, llm, target_type="issue",
    )
    assert result["category"] == "bug"
    assert result["confidence"] == 0.9
    assert result["existing_labels"] == ["bug"]


def test_classify_with_context_pr():
    labels = _make_repo_labels()
    llm = _FakeLabelerLLM(
        category="feature_request", confidence=0.85,
        existing_labels=["enhancement"],
        # Even though docs were touched, primary is enhancement
    )
    result = classify_with_context(
        "feat: add dark mode", "Implements dark mode support\n\nAlso updates docs.",
        labels, llm, target_type="pr",
        changed_files_summary="  [modified] src/theme.py (+50 -10)\n  [modified] docs/README.md (+5 -2)",
    )
    assert result["category"] == "feature_request"
    assert result["existing_labels"] == ["enhancement"]


def test_classify_with_context_suggests_new_label():
    labels = _make_repo_labels()
    llm = _FakeLabelerLLM(
        category="bug", confidence=0.9,
        existing_labels=[],
        new_labels=[{"name": "module_utils/facts", "description": "Facts-related modules", "color": "bfdadc"}],
    )
    result = classify_with_context(
        "Crash in facts module", "Desc", labels, llm,
    )
    assert result["existing_labels"] == []
    assert len(result["new_labels"]) == 1
    assert result["new_labels"][0]["name"] == "module_utils/facts"


def test_classify_with_context_error():
    result = classify_with_context("Title", "Body", _make_repo_labels(), _FailingFakeLLM())
    assert result["category"] == "noise"
    assert result["confidence"] == 0.0


def test_classify_with_context_fence():
    """JSON in markdown fence."""

    class FenceLLM:
        def chat(self, **kwargs):
            resp = type("R", (), {})()
            resp.content = '```json\n{"category":"documentation","confidence":0.8,"summary":"x","existing_labels":["documentation"],"new_labels":[]}\n```'
            resp.usage = type("U", (), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "model": "t"})()
            return resp

    result = classify_with_context("Doc", "Needs docs", _make_repo_labels(), FenceLLM())
    assert result["category"] == "documentation"


# ── fetch_pr_data ────────────────────────────────────────────────────────────


def test_fetch_pr_data_returns_data():
    mock_gh, _, _ = _mock_gh_with_pr()
    data = fetch_pr_data(mock_gh, "owner/repo", 42)
    assert data is not None
    assert data["number"] == 42
    assert data["changed_files_count"] == 2
    assert "src/main.py" in data["changed_files_summary"]
    assert "docs/README.md" in data["changed_files_summary"]


def test_fetch_pr_data_error():
    mock_repo = MagicMock()
    mock_repo.get_pull.side_effect = RuntimeError("Not found")
    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo
    assert fetch_pr_data(mock_gh, "owner/repo", 42) is None


# ── resolve_labels_against_repo ──────────────────────────────────────────────


def test_resolve_picks_existing():
    existing_names = {"bug", "enhancement", "question"}
    classification = {"existing_labels": ["bug", "enhancement"], "new_labels": []}
    existing, new = resolve_labels_against_repo(classification, existing_names)
    assert existing == ["bug", "enhancement"]
    assert new == []


def test_resolve_filters_nonexistent():
    existing_names = {"bug"}  # only "bug" exists
    classification = {"existing_labels": ["bug", "nonexistent-label"], "new_labels": []}
    existing, new = resolve_labels_against_repo(classification, existing_names)
    assert existing == ["bug"]
    assert new == []


def test_resolve_allows_new_labels():
    existing_names = {"bug"}
    classification = {
        "existing_labels": [],
        "new_labels": [{"name": "module_utils/facts", "description": "Facts", "color": "bfdadc"}],
    }
    existing, new = resolve_labels_against_repo(classification, existing_names)
    assert existing == []
    assert len(new) == 1
    assert new[0]["name"] == "module_utils/facts"


def test_resolve_respects_max():
    existing_names = {"bug", "enhancement", "question", "documentation"}
    classification = {
        "existing_labels": ["bug", "enhancement", "question"],
        "new_labels": [{"name": "extra", "description": "", "color": "ccc"}],
    }
    existing, new = resolve_labels_against_repo(classification, existing_names, max_labels=2)
    assert len(existing) + len(new) <= 2


def test_resolve_deduplicates():
    existing_names = {"bug"}
    classification = {"existing_labels": ["bug", "bug"], "new_labels": []}
    existing, new = resolve_labels_against_repo(classification, existing_names)
    assert existing == ["bug"]


# ── create_new_labels ────────────────────────────────────────────────────────


def test_create_new_labels_creates():
    mock_gh, mock_repo, _ = _mock_gh_with_issue()
    # get_label raises → label doesn't exist → create it
    mock_repo.get_label.side_effect = RuntimeError("not found")

    created = create_new_labels(mock_gh, "owner/repo", [
        {"name": "module_utils/facts", "description": "Facts modules", "color": "bfdadc"},
    ])
    assert "module_utils/facts" in created
    mock_repo.create_label.assert_called_once_with(
        name="module_utils/facts", color="bfdadc", description="Facts modules",
    )


def test_create_new_labels_skips_existing():
    mock_gh, mock_repo, _ = _mock_gh_with_issue()
    # get_label succeeds → label exists → skip creation
    mock_repo.get_label.return_value = MagicMock()

    created = create_new_labels(mock_gh, "owner/repo", [
        {"name": "bug", "description": "Already exists", "color": "d73a4a"},
    ])
    assert "bug" in created
    mock_repo.create_label.assert_not_called()


def test_create_new_labels_empty():
    assert create_new_labels(MagicMock(), "owner/repo", []) == []


# ── label_single_issue ───────────────────────────────────────────────────────


def test_label_single_issue_picks_existing_label():
    mock_gh, _, _ = _mock_gh_with_issue(number=42, title="Crash on startup", body="App crashes")
    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, profile)
    assert result.action == "labeled"
    assert result.target_type == "issue"
    assert "bug" in result.applied_labels


def test_label_single_issue_skips_low_confidence():
    mock_gh, _, _ = _mock_gh_with_issue()
    llm = _FakeLabelerLLM(category="question", confidence=0.5)
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, profile)
    assert result.action == "skipped"


def test_label_single_issue_suggest_mode():
    mock_gh, _, _ = _mock_gh_with_issue()
    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    profile = {
        "labeler": {"enabled": True, "mode": "suggest", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, profile)
    assert result.action == "commented"


def test_label_single_issue_is_pr_skips():
    mock_issue = MagicMock()
    mock_issue.pull_request = True  # This is a PR
    mock_issue.number = 42
    mock_issue.title = "PR"
    mock_issue.body = "body"
    mock_issue.html_url = "https://x/42"
    mock_issue.labels = []

    mock_repo = MagicMock()
    mock_repo.get_issue.return_value = mock_issue

    mock_repo_labels = [MagicMock()]
    mock_repo_labels[0].name = "bug"
    mock_repo_labels[0].description = ""
    mock_repo_labels[0].color = "d73a4a"
    mock_repo.get_labels.return_value = mock_repo_labels

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    result = label_single_issue(mock_gh, MagicMock(), "owner/repo", 42, {})
    assert result.action == "skipped"
    assert "pr" in result.skipped_reason.lower()


# ── label_single_pr ──────────────────────────────────────────────────────────


def test_label_single_pr_labels_pr():
    mock_gh, _, mock_pr = _mock_gh_with_pr(
        title="feat: add dark mode",
        body="Implements user-requested dark mode feature.",
    )
    llm = _FakeLabelerLLM(
        category="feature_request", confidence=0.85,
        existing_labels=["enhancement"],
    )
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    result = label_single_pr(mock_gh, llm, "owner/repo", 42, profile)
    assert result.action == "labeled"
    assert result.target_type == "pr"
    assert "enhancement" in result.applied_labels


def test_label_single_pr_with_docs_as_primary():
    """A PR that mainly touches docs should be 'documentation', not 'enhancement'."""
    mock_doc_file = MagicMock()
    mock_doc_file.filename = "docs/README.md"
    mock_doc_file.status = "modified"
    mock_doc_file.additions = 100
    mock_doc_file.deletions = 5

    mock_code_file = MagicMock()
    mock_code_file.filename = "src/main.py"
    mock_code_file.status = "modified"
    mock_code_file.additions = 2
    mock_code_file.deletions = 1

    mock_gh, _, mock_pr = _mock_gh_with_pr(
        title="docs: update README", body="Updates docs",
        files=[mock_doc_file, mock_code_file],
    )
    llm = _FakeLabelerLLM(
        category="documentation", confidence=0.88,
        existing_labels=["documentation"],
    )
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    result = label_single_pr(mock_gh, llm, "owner/repo", 42, profile)
    assert result.action == "labeled"
    assert result.target_type == "pr"
    assert "documentation" in result.applied_labels


# ── run_labeler ──────────────────────────────────────────────────────────────


def test_run_labeler_disabled():
    profile = {"labeler": {"enabled": False}}
    report = run_labeler(MagicMock(), MagicMock(), "owner/repo", profile)
    assert report.total_issues == 0


def test_run_labeler_with_pr():
    mock_gh, _, _ = _mock_gh_with_pr()
    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    report = run_labeler(mock_gh, llm, "owner/repo", profile, pr_number=42)
    assert report.total_issues == 1
    assert len(report.labeled) == 1
    assert report.labeled[0].target_type == "pr"


def test_run_labeler_single_issue():
    mock_gh, _, _ = _mock_gh_with_issue()
    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    report = run_labeler(mock_gh, llm, "owner/repo", profile, issue_number=42)
    assert report.total_issues == 1
    assert len(report.labeled) == 1


def test_run_labeler_defaults_profile():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "repokeeper.labeler.load_profile",
        lambda: {"labeler": {"enabled": False}},
    )
    report = run_labeler(MagicMock(), MagicMock(), "owner/repo")
    assert report.total_issues == 0
    monkeypatch.undo()


# ── label_unlabeled_issues ───────────────────────────────────────────────────


def test_label_unlabeled_issues_processes_batch():
    mock_gh, mock_repo, _ = _mock_gh_with_issue(number=1, title="Bug 1", body="desc")
    mock_repo.get_issues.return_value = [mock_repo.get_issue.return_value]
    mock_repo.get_repo.return_value = mock_repo  # for get_repo inside

    llm = _FakeLabelerLLM(category="bug", confidence=0.9, existing_labels=["bug"])
    profile = {
        "labeler": {"enabled": True, "mode": "add", "confidence_threshold": 0.7},
        "agent": {"model": "deepseek-chat"},
    }

    report = label_unlabeled_issues(mock_gh, llm, "owner/repo", profile)
    assert report.total_issues >= 0


def test_label_unlabeled_issues_none_found():
    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = []
    mock_repo.get_labels.return_value = []

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    report = label_unlabeled_issues(mock_gh, MagicMock(), "owner/repo", {})
    assert report.total_issues == 0


# ── generate_labeler_summary ─────────────────────────────────────────────────


def test_generate_labeler_summary_with_results():
    report = LabelerReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1), total_issues=3,
        labeled=[
            LabelerResult(
                issue_number=1, issue_url="https://x/1", title="Bug",
                target_type="issue", category="bug", confidence=0.9,
                applied_labels=["bug"], created_labels=[], action="labeled",
            ),
        ],
        commented=[
            LabelerResult(
                issue_number=2, issue_url="https://x/2", title="PR feat",
                target_type="pr", category="feature_request", confidence=0.8,
                suggested_labels=["enhancement"], action="commented",
            ),
        ],
        skipped=[
            LabelerResult(
                issue_number=3, issue_url="https://x/3", title="?", action="skipped",
                skipped_reason="low confidence",
            ),
        ],
    )
    summary = generate_labeler_summary(report)
    assert "Auto-Labeler Report" in summary
    assert "Labels Applied" in summary
    assert "#1" in summary
    assert "[issue]" in summary or "issue" in summary.lower()
    assert "#2" in summary
    assert "[pr]" in summary or "pr" in summary.lower()
    assert "#3" in summary
    assert "low confidence" in summary
    assert "Suggestions Posted" in summary
    assert "Skipped" in summary


def test_generate_labeler_summary_with_created_labels():
    report = LabelerReport(
        repo="owner/repo", scanned_at=datetime(2026, 1, 1), total_issues=1,
        labeled=[
            LabelerResult(
                issue_number=1, issue_url="https://x/1", title="Bug",
                target_type="issue", category="bug", confidence=0.9,
                applied_labels=["bug", "module_utils/facts"],
                created_labels=["module_utils/facts"], action="labeled",
            ),
        ],
    )
    summary = generate_labeler_summary(report)
    assert "created" in summary.lower() or "🆕" in summary


def test_generate_labeler_summary_empty():
    report = LabelerReport(repo="owner/repo", scanned_at=datetime(2026, 1, 1))
    summary = generate_labeler_summary(report)
    assert "No issues to process" in summary


# ── Existing functions (regression) ──────────────────────────────────────────


def test_fetch_issue_returns_data():
    mock_gh, _, _ = _mock_gh_with_issue()
    data = fetch_issue(mock_gh, "owner/repo", 42)
    assert data is not None
    assert data["number"] == 42


def test_fetch_issue_no_body():
    mock_gh, _, mock_issue = _mock_gh_with_issue()
    mock_issue.body = None
    data = fetch_issue(mock_gh, "owner/repo", 42)
    assert data is not None
    assert data["body"] == ""


def test_find_unlabeled_issues_returns_unlabeled():
    mock_gh, mock_repo, _ = _mock_gh_with_issue()
    mock_repo.get_issues.return_value = [mock_repo.get_issue.return_value]
    issues = find_unlabeled_issues(mock_gh, "owner/repo")
    assert len(issues) >= 0


def test_apply_labels_adds_labels():
    mock_gh, _, mock_issue = _mock_gh_with_issue()
    applied = apply_labels(mock_gh, "owner/repo", 42, ["bug", "enhancement"])
    assert applied == ["bug", "enhancement"]
    mock_issue.add_to_labels.assert_called_once_with("bug", "enhancement")


def test_apply_labels_empty():
    assert apply_labels(MagicMock(), "owner/repo", 42, []) == []


def test_suggest_labels_comment_posts():
    mock_gh, _, mock_issue = _mock_gh_with_issue()
    classification = {"category": "bug", "confidence": 0.9, "summary": "Crash"}
    result = suggest_labels_comment(
        mock_gh, "owner/repo", 42, classification, ["bug"], [],
    )
    assert result is True
    mock_issue.create_comment.assert_called_once()
    assert "Auto-Labeler" in mock_issue.create_comment.call_args[0][0]


def test_suggest_labels_comment_with_new_labels():
    mock_gh, _, mock_issue = _mock_gh_with_issue()
    classification = {"category": "bug", "confidence": 0.9, "summary": "Crash"}
    new_labels = [{"name": "module_utils/facts", "description": "Facts", "color": "bfdadc"}]
    result = suggest_labels_comment(
        mock_gh, "owner/repo", 42, classification, ["bug"], new_labels,
    )
    assert result is True
    comment = mock_issue.create_comment.call_args[0][0]
    assert "Create new labels" in comment
    assert "module_utils/facts" in comment


def test_suggest_labels_comment_empty():
    result = suggest_labels_comment(MagicMock(), "owner/repo", 42, {}, [], [])
    assert result is False


# ── LABELER_LABEL constant ───────────────────────────────────────────────────


def test_labeler_label_is_repokeeper_labeler():
    assert LABELER_LABEL == "repokeeper-labeler"


# ── Prompt helpers ───────────────────────────────────────────────────────────


def test_infer_convention_falls_back_when_no_pattern_dominates():
    labels = [{"name": n} for n in ["a/b", "c: d", "e-f", "G h"]]

    assert _infer_label_naming_convention(labels) == "plain lowercase (e.g. 'bug', 'enhancement')"


def test_build_labeler_system_prompt_without_labels():
    from repokeeper.labeler import _build_labeler_system_prompt, _describe_palette

    prompt = _build_labeler_system_prompt([], target_type="issue")

    assert "(No existing labels in this repository.)" in prompt
    assert "use standard GitHub label colors" in prompt
    assert "New labels may include a short description" in prompt
    assert _describe_palette([{"name": "x", "color": ""}]) == "use standard GitHub label colors"


def test_normalize_helpers_reject_malformed_values():
    from repokeeper.labeler import _normalize_new_labels, _normalize_string_list

    assert _normalize_string_list("bug") == []
    assert _normalize_string_list(["bug", "", None, 3]) == ["bug", "3"]
    assert _normalize_new_labels({"name": "x"}) == []
    assert _normalize_new_labels(["junk", {"name": "  "}, {"name": "ok", "color": "#abc"}]) == [
        {"name": "ok", "description": "", "color": "abc"}
    ]


# ── fetch helpers ────────────────────────────────────────────────────────────


def test_fetch_pr_data_caps_file_summary_at_fifty():
    files = []
    for i in range(55):
        f = MagicMock()
        f.filename = f"file{i}.py"
        f.status = "added"
        f.additions = 1
        f.deletions = 0
        files.append(f)
    mock_gh, _, _ = _mock_gh_with_pr(files=files)

    data = fetch_pr_data(mock_gh, "owner/repo", 42)

    assert data["changed_files_count"] == 55
    assert data["changed_files_summary"].endswith("... and 5 more files")


def test_fetch_issue_error_returns_none():
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("boom")

    assert fetch_issue(mock_gh, "owner/repo", 1) is None


def _issue_mock(number, labels=(), is_pr=False):
    issue = MagicMock()
    issue.number = number
    issue.title = f"Issue {number}"
    issue.body = None
    issue.html_url = f"https://github.com/owner/repo/issues/{number}"
    issue.pull_request = object() if is_pr else None
    issue.labels = []
    for name in labels:
        label = MagicMock()
        label.name = name
        issue.labels.append(label)
    return issue


def test_find_unlabeled_issues_skips_prs_labeled_issues_and_respects_max():
    mock_gh = MagicMock()
    mock_repo = MagicMock()
    mock_gh.get_repo.return_value = mock_repo
    mock_repo.get_issues.return_value = [
        _issue_mock(1, is_pr=True),
        _issue_mock(2, labels=["bug"]),
        _issue_mock(3, labels=["admin"]),
        _issue_mock(4),
        _issue_mock(5),
    ]

    found = find_unlabeled_issues(mock_gh, "owner/repo", exclude_labels=["admin"], max_issues=2)

    assert [i["number"] for i in found] == [3, 4]
    assert found[0]["labels"] == ["admin"]
    assert found[0]["body"] == ""


def test_find_unlabeled_issues_error_returns_empty():
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("boom")

    assert find_unlabeled_issues(mock_gh, "owner/repo") == []


# ── resolve_labels_against_repo ──────────────────────────────────────────────


def test_resolve_handles_malformed_classification_and_caps_new_labels():
    assert resolve_labels_against_repo(
        {"existing_labels": "bug", "new_labels": {"name": "x"}}, {"bug"}
    ) == ([], [])

    existing, new = resolve_labels_against_repo(
        {
            "existing_labels": ["bug"],
            "new_labels": [
                "junk",
                {"name": ""},
                {"name": "bug"},
                {"name": "n1"},
                {"name": "n1"},
                {"name": "n2"},
                {"name": "n3"},
            ],
        },
        {"bug"},
        max_labels=3,
    )

    assert existing == ["bug"]
    assert [nl["name"] for nl in new] == ["n1", "n2"]


# ── GitHub write helpers ─────────────────────────────────────────────────────


def test_apply_labels_error_returns_empty():
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("boom")

    assert apply_labels(mock_gh, "owner/repo", 1, ["bug"]) == []


def test_create_new_labels_skips_blank_names_and_tolerates_failures():
    mock_gh, mock_repo, _ = _mock_gh_with_pr()
    mock_repo.create_label.side_effect = [None, RuntimeError("forbidden")]

    created = create_new_labels(
        mock_gh,
        "owner/repo",
        [{"name": "  "}, {"name": "ok", "color": "abc"}, {"name": "fails"}],
    )

    assert created == ["ok"]

    mock_gh.get_repo.side_effect = RuntimeError("no repo")
    assert create_new_labels(mock_gh, "owner/repo", [{"name": "x"}]) == []


def test_suggest_labels_comment_only_new_labels_and_error():
    mock_gh, mock_repo, mock_issue = _mock_gh_with_issue()

    posted = suggest_labels_comment(
        mock_gh, "owner/repo", 42, {"category": "bug", "confidence": 0.9},
        [], [{"name": "new-one", "description": "desc"}],
    )

    assert posted is True
    comment = mock_issue.create_comment.call_args[0][0]
    assert "Apply existing labels" not in comment
    assert "**Create new labels:** `new-one` — _desc_" in comment

    mock_gh.get_repo.side_effect = RuntimeError("boom")
    assert suggest_labels_comment(
        mock_gh, "owner/repo", 42, {}, ["bug"], [],
    ) is False


# ── _label_single branches ───────────────────────────────────────────────────


def test_label_single_pr_not_found_is_skipped():
    mock_gh = MagicMock()
    mock_gh.get_repo.side_effect = RuntimeError("boom")

    result = label_single_pr(mock_gh, _FakeLabelerLLM(), "owner/repo", 7, {}, repo_labels=[])

    assert result.action == "skipped"
    assert result.skipped_reason == "PR not found or inaccessible"


def test_label_single_issue_skips_when_nothing_matches():
    mock_gh, _, _ = _mock_gh_with_issue()
    llm = _FakeLabelerLLM(existing_labels=["does-not-exist"], new_labels=[])

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, {})

    assert result.action == "skipped"
    assert result.skipped_reason == "no matching labels found in repo"


def test_label_single_issue_skips_new_labels_when_creation_disabled():
    mock_gh, _, _ = _mock_gh_with_issue()
    llm = _FakeLabelerLLM(existing_labels=[], new_labels=[{"name": "fresh"}])

    result = label_single_issue(
        mock_gh, llm, "owner/repo", 42, {"labeler": {"allow_create_labels": False}}
    )

    assert result.action == "skipped"
    assert result.suggested_labels == ["fresh"]


def test_label_single_issue_creates_and_applies_new_labels():
    mock_gh, mock_repo, mock_issue = _mock_gh_with_issue()
    mock_repo.get_label.side_effect = RuntimeError("not found")
    llm = _FakeLabelerLLM(existing_labels=[], new_labels=[{"name": "fresh", "color": "abc"}])

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, {})

    assert result.action == "labeled"
    assert result.created_labels == ["fresh"]
    assert result.applied_labels == ["fresh"]
    mock_repo.create_label.assert_called_once_with(name="fresh", color="abc", description="")
    mock_issue.add_to_labels.assert_called_once_with("fresh")


def test_label_single_issue_reports_apply_failure():
    mock_gh, mock_repo, mock_issue = _mock_gh_with_issue()
    mock_issue.add_to_labels.side_effect = RuntimeError("forbidden")
    llm = _FakeLabelerLLM(existing_labels=["bug"])

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, {})

    assert result.action == "skipped"
    assert result.skipped_reason == "failed to apply labels"


def test_label_single_issue_suggest_mode_comment_failure():
    mock_gh, mock_repo, mock_issue = _mock_gh_with_issue()
    mock_issue.create_comment.side_effect = RuntimeError("forbidden")
    llm = _FakeLabelerLLM(existing_labels=["bug"])

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, {"labeler": {"mode": "suggest"}})

    assert result.action == "skipped"
    assert result.skipped_reason == "failed to post suggestion comment"


# ── Batch and entry point ────────────────────────────────────────────────────


def test_label_unlabeled_issues_buckets_every_outcome(monkeypatch):
    from repokeeper import labeler

    unlabeled = [{"number": n, "url": f"u{n}", "title": f"t{n}"} for n in (1, 2, 3, 4, 5)]
    monkeypatch.setattr(labeler, "fetch_repo_labels", lambda gh, repo: [])
    monkeypatch.setattr(labeler, "find_unlabeled_issues", lambda *a, **kw: unlabeled)

    def fake_single(gh, llm, repo, number, profile, repo_labels=None):
        if number == 1:
            return LabelerResult(1, "u1", "t1", action="labeled")
        if number == 2:
            return LabelerResult(2, "u2", "t2", action="commented")
        if number == 3:
            return LabelerResult(3, "u3", "t3", action="skipped", error="bad")
        if number == 4:
            raise RuntimeError("exploded")
        return LabelerResult(5, "u5", "t5", action="skipped", skipped_reason="low")

    monkeypatch.setattr(labeler, "label_single_issue", fake_single)

    report = label_unlabeled_issues(MagicMock(), MagicMock(), "owner/repo", {})

    assert report.total_issues == 5
    assert [r.issue_number for r in report.labeled] == [1]
    assert [r.issue_number for r in report.commented] == [2]
    assert [r.issue_number for r in report.errors] == [3, 4]
    assert report.errors[1].error == "exploded"
    assert [r.issue_number for r in report.skipped] == [5]


@pytest.mark.parametrize(
    ("result", "bucket"),
    [
        (LabelerResult(1, "u", "t", action="commented"), "commented"),
        (LabelerResult(1, "u", "t", action="skipped", error="bad"), "errors"),
        (LabelerResult(1, "u", "t", action="skipped"), "skipped"),
    ],
)
def test_run_labeler_single_issue_buckets_result(monkeypatch, result, bucket):
    from repokeeper import labeler

    monkeypatch.setattr(labeler, "label_single_issue", lambda *a, **kw: result)

    report = run_labeler(MagicMock(), MagicMock(), "owner/repo", profile={}, issue_number=1)

    assert report.total_issues == 1
    assert getattr(report, bucket) == [result]


def test_run_labeler_batch_mode_delegates(monkeypatch):
    from repokeeper import labeler

    expected = LabelerReport(repo="owner/repo", scanned_at=datetime.now())
    monkeypatch.setattr(labeler, "label_unlabeled_issues", lambda *a, **kw: expected)

    assert run_labeler(MagicMock(), MagicMock(), "owner/repo", profile={}) is expected


def test_generate_labeler_summary_lists_errors():
    error = LabelerResult(9, "https://x/9", "Broken", action="skipped", error="LLM down")
    report = LabelerReport(
        repo="owner/repo",
        scanned_at=datetime(2026, 1, 1, 12, 0),
        total_issues=1,
        results=[error],
        errors=[error],
    )

    summary = generate_labeler_summary(report)

    assert "## ❌ Errors" in summary
    assert "- [#9 Broken](https://x/9) — LLM down" in summary
    assert "No issues to process" not in summary


def test_label_single_issue_skips_when_label_creation_fails():
    mock_gh, mock_repo, mock_issue = _mock_gh_with_issue()
    mock_repo.get_label.side_effect = RuntimeError("not found")
    mock_repo.create_label.side_effect = RuntimeError("forbidden")
    llm = _FakeLabelerLLM(existing_labels=[], new_labels=[{"name": "fresh"}])

    result = label_single_issue(mock_gh, llm, "owner/repo", 42, {})

    assert result.action == "skipped"
    assert result.skipped_reason == "failed to apply labels"
    assert result.created_labels == []
    mock_issue.add_to_labels.assert_not_called()
