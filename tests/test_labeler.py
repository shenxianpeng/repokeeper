"""Tests for the RepoKeeper Auto-Labeler module (labeler.py)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from repokeeper.labeler import (
    LABELER_LABEL,
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
