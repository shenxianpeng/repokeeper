"""Tests for the RepoKeeper Code Review Agent (review.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from repokeeper import review
from repokeeper.exceptions import ConfigError, LLMParseError
from repokeeper.llm_client import TokenUsage

# ── get_pr_data ──────────────────────────────────────────────────────────────

def test_get_pr_data_extracts_fields():
    """get_pr_data returns structured PR info with files and comments."""

    class User:
        login = "contributor"

    class File:
        filename = "src/main.py"
        status = "modified"
        additions = 10
        deletions = 3
        changes = 13
        patch = "@@ -1,3 +1,5 @@\n old\n+new"

    class Comment:
        user = User()
        body = "Looks good!"

    class PR:
        number = 42
        title = "Add new feature"
        body = "This PR adds a new feature."
        user = User()
        additions = 15
        deletions = 5
        changed_files = 2

        class base:
            ref = "main"

        class head:
            ref = "feature-branch"

        def get_files(self):
            return [File(), File()]

        def get_issue_comments(self):
            return [Comment()]

    class Repo:
        def get_pull(self, number):
            return PR()

    data = review.get_pr_data(Repo(), 42)
    assert data["number"] == 42
    assert data["title"] == "Add new feature"
    assert data["author"] == "contributor"
    assert data["base_branch"] == "main"
    assert data["head_branch"] == "feature-branch"
    assert len(data["files"]) == 2
    assert data["files"][0]["filename"] == "src/main.py"
    assert data["files"][0]["status"] == "modified"
    assert data["additions"] == 15
    assert data["deletions"] == 5
    assert data["changed_files_count"] == 2
    assert len(data["comments"]) == 1


def test_get_pr_data_no_body():
    """PR with no body defaults to placeholder string."""

    class User:
        login = "dev"

    class PR:
        number = 1
        title = "T"
        body = None
        user = User()
        additions = 0
        deletions = 0
        changed_files = 0

        class base:
            ref = "main"

        class head:
            ref = "branch"

        def get_files(self):
            return []

        def get_issue_comments(self):
            return []

    class Repo:
        def get_pull(self, number):
            return PR()

    data = review.get_pr_data(Repo(), 1)
    assert data["body"] == "(no description)"


def test_get_pr_data_binary_patch():
    """Binary files get placeholder patch string."""

    class User:
        login = "dev"

    class File:
        filename = "image.png"
        status = "added"
        additions = 0
        deletions = 0
        changes = 0
        patch = None

    class PR:
        number = 1
        title = "T"
        body = ""
        user = User()
        additions = 0
        deletions = 0
        changed_files = 1

        class base:
            ref = "main"

        class head:
            ref = "branch"

        def get_files(self):
            return [File()]

        def get_issue_comments(self):
            return []

    class Repo:
        def get_pull(self, number):
            return PR()

    data = review.get_pr_data(Repo(), 1)
    assert "binary or too large" in data["files"][0]["patch"]


# ── check_review_skip_keywords ───────────────────────────────────────────────

def test_check_review_skip_keywords_matches_title():
    pr_data = {"title": "WIP: needs design", "body": "normal"}
    profile = {"agent": {"skip_keywords": ["WIP", "needs design"]}}
    assert review.check_review_skip_keywords(pr_data, profile) == "WIP"


def test_check_review_skip_keywords_matches_body():
    pr_data = {"title": "Fix bug", "body": "This is a breaking change"}
    profile = {"agent": {"skip_keywords": ["breaking change"]}}
    assert review.check_review_skip_keywords(pr_data, profile) == "breaking change"


def test_check_review_skip_keywords_no_match():
    pr_data = {"title": "Add tests", "body": "Improving coverage"}
    profile = {"agent": {"skip_keywords": ["WIP", "RFC"]}}
    assert review.check_review_skip_keywords(pr_data, profile) is None


def test_check_review_skip_keywords_empty():
    pr_data = {"title": "Anything", "body": "stuff"}
    profile = {"agent": {"skip_keywords": []}}
    assert review.check_review_skip_keywords(pr_data, profile) is None


def test_check_review_skip_keywords_no_agent():
    pr_data = {"title": "Anything", "body": "stuff"}
    assert review.check_review_skip_keywords(pr_data, {}) is None


# ── collect_review_context ──────────────────────────────────────────────────

def test_collect_review_context_includes_changed_files(monkeypatch):
    def fake_collect_repo_files(max_files=60, target_tokens=None):
        return {
            "src/main.py": "main content",
            "src/utils.py": "utils content",
            "README.md": "readme",
        }
    monkeypatch.setattr(review, "collect_repo_files", fake_collect_repo_files)

    pr_data = {
        "changed_files": ["src/main.py", "src/utils.py"],
    }
    profile = {"agent": {"max_context_files": 60}}

    context = review.collect_review_context(pr_data, profile)
    assert "src/main.py" in context
    assert "src/utils.py" in context
    assert context["src/main.py"] == "main content"


def test_collect_review_context_adds_non_changed_for_context(monkeypatch):
    def fake_collect_repo_files(max_files=60, target_tokens=None):
        return {
            "src/main.py": "main",
            "tests/test_main.py": "tests",
            "pyproject.toml": "[tool]",
        }
    monkeypatch.setattr(review, "collect_repo_files", fake_collect_repo_files)

    pr_data = {
        "changed_files": ["src/main.py"],
    }
    profile = {"agent": {"max_context_files": 60}}

    context = review.collect_review_context(pr_data, profile)
    assert "src/main.py" in context
    # Non-changed files also included as context
    assert len(context) > 1


def test_collect_review_context_respects_max_files(monkeypatch):
    def fake_collect_repo_files(max_files=60, target_tokens=None):
        return {f"src/file{i}.py": f"content{i}" for i in range(10)}
    monkeypatch.setattr(review, "collect_repo_files", fake_collect_repo_files)

    pr_data = {
        "changed_files": ["src/file0.py", "src/file1.py"],
    }
    profile = {"agent": {"max_context_files": 3}}

    context = review.collect_review_context(pr_data, profile)
    assert "src/file0.py" in context
    assert "src/file1.py" in context
    assert len(context) <= 3 + 2  # changed + max_context


# ── build_review_context_string ──────────────────────────────────────────────

def test_build_review_context_string_has_diff():
    pr_data = {
        "number": 1,
        "files": [
            {"filename": "a.py", "status": "modified",
             "additions": 5, "deletions": 2,
             "patch": "@@ -1 +1 @@\n-old\n+new"},
        ],
        "additions": 5,
        "deletions": 2,
    }
    files = {"a.py": "new content", "b.py": "unchanged"}

    result = review.build_review_context_string(pr_data, files)
    assert "Pull Request Diff" in result
    assert "### a.py" in result
    assert "@@ -1 +1 @@" in result
    # b.py is unchanged and not in diff, so it appears in context section
    assert "Repository Context" in result
    assert "b.py" in result
    # a.py is in diff, so should NOT be in context section
    assert result.count("a.py") <= 2  # once in diff header, once in filename


def test_build_review_context_string_no_context_files():
    pr_data = {
        "number": 1,
        "files": [
            {"filename": "a.py", "status": "added",
             "additions": 1, "deletions": 0,
             "patch": "+new"},
        ],
        "additions": 1,
        "deletions": 0,
    }
    files = {"a.py": "new"}

    result = review.build_review_context_string(pr_data, files)
    assert "Repository Context" not in result  # all files are in diff


# ── call_llm_for_review ─────────────────────────────────────────────────────

def test_call_llm_for_review_returns_parsed_json():
    class Response:
        content = '{"approval_recommendation": "approve", "summary": "LGTM", "issues": [], "style_violations": [], "positive_notes": ["Clean code"], "test_recommendation": "", "security_concerns": []}'
        usage = TokenUsage()

    class Client:
        def chat(self, **kwargs):
            return Response()

    pr_data = {"number": 1, "title": "T", "body": "B",
               "author": "dev", "base_branch": "main", "head_branch": "feat",
               "changed_files_count": 1, "additions": 5, "deletions": 2,
               "comments": []}

    result, usage = review.call_llm_for_review(
        pr_data, "context string",
        {"agent": {"model": "x"}, "style": {}, "tech": {}, "pr": {}},
        Client(),
    )
    assert result["approval_recommendation"] == "approve"
    assert result["positive_notes"] == ["Clean code"]


def test_call_llm_for_review_includes_profile_preferences():
    class Response:
        content = '{"approval_recommendation": "comment", "summary": "OK"}'
        usage = TokenUsage()

    class Client:
        def __init__(self):
            self.last_kwargs = None

        def chat(self, **kwargs):
            self.last_kwargs = kwargs
            return Response()

    client = Client()
    pr_data = {"number": 1, "title": "T", "body": "B",
               "author": "dev", "base_branch": "main", "head_branch": "feat",
               "changed_files_count": 1, "additions": 0, "deletions": 0,
               "comments": []}

    review.call_llm_for_review(
        pr_data, "ctx",
        {
            "agent": {"model": "x"},
            "style": {"code_style": "PEP8 strictly"},
            "tech": {"preferred": ["python3.10+"], "avoid": ["python2"]},
            "pr": {"min_tests": True, "max_files_per_pr": 10},
        },
        client,
    )

    user_content = client.last_kwargs["messages"][0]["content"]
    assert "PEP8 strictly" in user_content
    assert "Preferred tech stack: python3.10+" in user_content
    assert "Tech stack to avoid" in user_content
    assert "Tests are required" in user_content
    assert "Max files per PR: 10" in user_content


def test_call_llm_for_review_retry_on_bad_json():
    call_count = [0]

    class Response:
        usage = TokenUsage()
        def __init__(self, content):
            self.content = content

    class Client:
        def chat(self, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return Response("not json")
            return Response('{"approval_recommendation": "approve", "summary": "ok"}')

    pr_data = {"number": 1, "title": "T", "body": "B",
               "author": "dev", "base_branch": "main", "head_branch": "feat",
               "changed_files_count": 1, "additions": 0, "deletions": 0,
               "comments": []}

    result, usage = review.call_llm_for_review(
        pr_data, "ctx",
        {"agent": {"model": "x"}, "style": {}, "tech": {}, "pr": {}},
        Client(),
    )
    assert result["approval_recommendation"] == "approve"
    assert call_count[0] == 2


def test_call_llm_for_review_exhausts_retries():
    class Response:
        content = "not json"
        usage = TokenUsage()

    class Client:
        def chat(self, **kwargs):
            return Response()

    pr_data = {"number": 1, "title": "T", "body": "B",
               "author": "dev", "base_branch": "main", "head_branch": "feat",
               "changed_files_count": 1, "additions": 0, "deletions": 0,
               "comments": []}

    with pytest.raises(LLMParseError, match="LLM JSON parsing failed"):
        review.call_llm_for_review(
            pr_data, "ctx",
            {"agent": {"model": "x"}, "style": {}, "tech": {}, "pr": {}},
            Client(),
        )


# ── format_review_comment ────────────────────────────────────────────────────

def test_format_review_comment_approve():
    pr_data = {"number": 1, "title": "Fix", "author": "dev",
               "changed_files_count": 1, "additions": 5, "deletions": 2}
    review_data = {
        "approval_recommendation": "approve",
        "summary": "Looks great!",
        "issues": [],
        "style_violations": [],
        "positive_notes": ["Well tested"],
        "test_recommendation": "",
        "security_concerns": [],
    }
    usage = TokenUsage(model="test-model", total_tokens=100, cost_usd=0.001)

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "RepoKeeper Code Review" in comment
    assert "✅" in comment
    assert "Approve" in comment
    assert "Looks great!" in comment
    assert "No issues detected" in comment
    assert "Well tested" in comment
    assert "$0.001000" in comment


def test_format_review_comment_request_changes():
    pr_data = {"number": 2, "title": "Bug", "author": "dev",
               "changed_files_count": 3, "additions": 20, "deletions": 10}
    review_data = {
        "approval_recommendation": "request_changes",
        "summary": "Found critical issues.",
        "issues": [
            {
                "severity": "critical",
                "file": "src/main.py",
                "line": 42,
                "message": "SQL injection vulnerability",
                "suggestion": "Use parameterized queries",
            },
            {
                "severity": "minor",
                "file": "src/utils.py",
                "line": 10,
                "message": "Unused import",
                "suggestion": "Remove the import",
            },
        ],
        "style_violations": [
            {"file": "src/main.py", "description": "Line too long"},
        ],
        "positive_notes": [],
        "test_recommendation": "Add tests for the new endpoint.",
        "security_concerns": ["Possible SQL injection in main.py:42"],
    }
    usage = TokenUsage()

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "🔴" in comment
    assert "Request Changes" in comment
    assert "SQL injection vulnerability" in comment
    # Suggestions appear only in inline comments, not the overview body
    assert "Add tests for the new endpoint" in comment
    assert "Possible SQL injection" in comment


def test_format_review_comment_comment_mode():
    pr_data = {"number": 3, "title": "Refactor", "author": "dev",
               "changed_files_count": 5, "additions": 30, "deletions": 25}
    review_data = {
        "approval_recommendation": "comment",
        "summary": "Nice refactor, a few nits.",
        "issues": [
            {
                "severity": "nit",
                "file": "src/foo.py",
                "line": 15,
                "message": "Variable name could be clearer",
                "suggestion": "Rename to `user_count`",
            },
        ],
        "style_violations": [],
        "positive_notes": ["Clean separation of concerns", "Good test coverage"],
        "test_recommendation": "",
        "security_concerns": [],
    }
    usage = TokenUsage()

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "💬" in comment
    assert "Comment" in comment
    assert "Variable name" in comment
    assert "Good test coverage" in comment


def test_format_review_comment_pipe_escaping():
    """Issue messages with pipe characters are escaped for markdown tables."""
    pr_data = {"number": 1, "title": "T", "author": "dev",
               "changed_files_count": 1, "additions": 0, "deletions": 0}
    review_data = {
        "approval_recommendation": "comment",
        "summary": "ok",
        "issues": [
            {
                "severity": "major",
                "file": "a.py",
                "line": 1,
                "message": "Use a | b instead of c",
                "suggestion": "",
            },
        ],
        "style_violations": [],
        "positive_notes": [],
        "test_recommendation": "",
        "security_concerns": [],
    }
    usage = TokenUsage()

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "a \\| b" in comment  # pipe should be escaped


# ── run_review ───────────────────────────────────────────────────────────────

def test_run_review_missing_config(monkeypatch):
    """Missing required config raises ConfigError."""
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "PR_NUMBER", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REPOKEEPER_GITHUB_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="Missing required configuration"):
        review.run_review()


def test_run_review_disabled_agent(monkeypatch):
    """When agent.implement is false, returns without reviewing."""
    monkeypatch.setattr(review, "load_profile",
                        lambda profile_path=None: {"agent": {"implement": False}})
    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is False
    assert "disabled" in result["reason"]


def test_run_review_skip_keyword_matched(monkeypatch):
    """When skip keyword matches, posts comment and skips."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": ["WIP"]},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "WIP: my feature", "body": "not ready",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is False
    assert "WIP" in result["reason"]
    pr_obj.create_issue_comment.assert_called()


def test_run_review_success_path(monkeypatch):
    """Happy path: full review completes and posts a comment."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": [], "model": "test"},
        "style": {"code_style": "PEP8"},
        "tech": {"preferred": [], "avoid": []},
        "pr": {},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "Add feature", "body": "New feature",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [{"filename": "a.py", "status": "modified",
                       "additions": 3, "deletions": 1, "changes": 4,
                       "patch": "+new"}],
            "changed_files": ["a.py"],
            "comments": [],
            "additions": 3, "deletions": 1, "changed_files_count": 1,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)

    monkeypatch.setattr(review, "collect_review_context",
                        lambda pr_data, profile: {"a.py": "new content"})

    def fake_call_llm(*args, **kwargs):
        return {
            "approval_recommendation": "approve",
            "summary": "LGTM",
            "issues": [],
            "style_violations": [],
            "positive_notes": ["Clean"],
            "test_recommendation": "",
            "security_concerns": [],
        }, TokenUsage(model="test", total_tokens=50, cost_usd=0.0001)

    monkeypatch.setattr(review, "call_llm_for_review", fake_call_llm)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is True
    assert result["approval_recommendation"] == "approve"
    assert result["issues_count"] == 0
    # Two comments: acknowledgment + review
    assert pr_obj.create_issue_comment.call_count >= 2


def test_run_review_with_issues(monkeypatch):
    """Review finds issues and reports them correctly."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": []},
        "style": {}, "tech": {}, "pr": {},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "Buggy code", "body": "",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)
    monkeypatch.setattr(review, "collect_review_context",
                        lambda pr_data, profile: {})

    def fake_call_llm(*args, **kwargs):
        return {
            "approval_recommendation": "request_changes",
            "summary": "Found 2 issues.",
            "issues": [
                {"severity": "critical", "file": "a.py", "line": 1,
                 "message": "Bug", "suggestion": "Fix it"},
                {"severity": "major", "file": "b.py", "line": 10,
                 "message": "Issue", "suggestion": "Fix it"},
            ],
            "style_violations": [],
            "positive_notes": [],
            "test_recommendation": "",
            "security_concerns": [],
        }, TokenUsage()

    monkeypatch.setattr(review, "call_llm_for_review", fake_call_llm)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is True
    assert result["approval_recommendation"] == "request_changes"
    assert result["issues_count"] == 2


def test_run_review_parse_error_posts_error_comment(monkeypatch):
    """When LLM JSON parsing fails, posts error comment."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": []},
        "style": {}, "tech": {}, "pr": {},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "PR", "body": "",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)
    monkeypatch.setattr(review, "collect_review_context",
                        lambda pr_data, profile: {})

    def fake_call_llm(*args, **kwargs):
        raise LLMParseError("Bad JSON")

    monkeypatch.setattr(review, "call_llm_for_review", fake_call_llm)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is False
    assert "Bad JSON" in result["reason"]
    # Error comment was posted
    error_comments = [
        c for c in pr_obj.create_issue_comment.call_args_list
        if "review failed" in str(c).lower() or "could not parse" in str(c).lower()
    ]
    assert len(error_comments) > 0


def test_run_review_exception_posts_error_and_re_raises(monkeypatch):
    """General exception posts error comment and re-raises."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": []},
        "style": {}, "tech": {}, "pr": {},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock
    repo_mock.html_url = "https://github.com/owner/repo"

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "PR", "body": "",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)
    monkeypatch.setattr(review, "collect_review_context",
                        lambda pr_data, profile: {})

    def fake_call_llm(*args, **kwargs):
        raise RuntimeError("Something exploded")

    monkeypatch.setattr(review, "call_llm_for_review", fake_call_llm)

    with pytest.raises(RuntimeError, match="Something exploded"):
        review.run_review(
            gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
        )
    # Error comment was posted
    error_comments = [
        c for c in pr_obj.create_issue_comment.call_args_list
        if "encountered an error" in str(c).lower()
    ]
    assert len(error_comments) > 0


def test_run_review_repo_access_failure(monkeypatch):
    """When repo can't be accessed, returns error without crashing."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True},
    })

    gh_mock = MagicMock()
    gh_mock.get_repo.side_effect = RuntimeError("No access")
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is False
    assert "No access" in result["reason"]


def test_run_review_pr_not_found(monkeypatch):
    """When PR doesn't exist, returns error without crashing."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True},
    })

    repo_mock = MagicMock()
    repo_mock.get_pull.side_effect = RuntimeError("Not found")

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=999, llm_api_key="key",
    )
    assert result["review_posted"] is False
    assert "not found" in result["reason"].lower()


# ── format_review_comment edge cases ────────────────────────────────────────

def test_format_review_comment_no_issues_empty_lists():
    """Review with empty issues and no positives renders cleanly."""
    pr_data = {"number": 1, "title": "T", "author": "dev",
               "changed_files_count": 1, "additions": 0, "deletions": 0}
    review_data = {
        "approval_recommendation": "comment",
        "summary": "Nothing to add.",
        "issues": [],
        "style_violations": [],
        "positive_notes": [],
        "test_recommendation": "",
        "security_concerns": [],
    }
    usage = TokenUsage()

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "No issues detected" in comment
    assert "RepoKeeper Code Review" in comment


def test_format_review_comment_with_security_concerns():
    pr_data = {"number": 1, "title": "T", "author": "dev",
               "changed_files_count": 1, "additions": 0, "deletions": 0}
    review_data = {
        "approval_recommendation": "request_changes",
        "summary": "Security issues.",
        "issues": [],
        "style_violations": [],
        "positive_notes": [],
        "test_recommendation": "",
        "security_concerns": ["Hardcoded secret in config.py"],
    }
    usage = TokenUsage()

    comment = review.format_review_comment(pr_data, review_data, usage, {})
    assert "🔒 Security" in comment
    assert "Hardcoded secret" in comment


# ── _convert_issue_to_review_comment ─────────────────────────────────────────


def test_convert_issue_to_review_comment_basic():
    """Converts LLM issue dict to GitHub review comment format."""
    issue = {
        "file": "src/main.py",
        "line": 42,
        "message": "SQL injection risk",
        "suggestion": "Use parameterized queries",
    }
    comment = review._convert_issue_to_review_comment(issue)
    assert comment["path"] == "src/main.py"
    assert comment["line"] == 42
    assert comment["side"] == "RIGHT"
    assert "SQL injection risk" in comment["body"]
    assert "parameterized queries" in comment["body"]
    assert "```suggestion" in comment["body"]


def test_convert_issue_to_review_comment_no_suggestion():
    """Issue without suggestion field omits the suggestion block."""
    issue = {
        "file": "a.py",
        "line": 10,
        "message": "Unused import",
    }
    comment = review._convert_issue_to_review_comment(issue)
    assert comment["path"] == "a.py"
    assert comment["line"] == 10
    assert "Unused import" in comment["body"]
    assert "```suggestion" not in comment["body"]


def test_convert_issue_to_review_comment_zero_line():
    """Line 0 is clamped to 1."""
    issue = {
        "file": "b.py",
        "line": 0,
        "message": "Test",
    }
    comment = review._convert_issue_to_review_comment(issue)
    assert comment["line"] == 1


# ── post_review_comment (with inline comments) ───────────────────────────────


def test_post_review_comment_with_inline_review(monkeypatch):
    """When review_data contains issues, creates review via API with inline comments."""
    monkeypatch.setattr(review, "logger", MagicMock())

    pr_obj = MagicMock()
    pr_obj.create_review.return_value = MagicMock(id="review-123")

    review_data = {
        "approval_recommendation": "request_changes",
        "summary": "Found issues",
        "issues": [
            {"file": "a.py", "line": 1, "message": "Bad", "suggestion": "Fix"},
        ],
    }

    review_id = review.post_review_comment(
        pr_obj, "# Summary", review_data=review_data, event="REQUEST_CHANGES",
    )
    assert review_id == "review-123"
    pr_obj.create_review.assert_called_once()
    call_kwargs = pr_obj.create_review.call_args[1]
    assert call_kwargs["event"] == "REQUEST_CHANGES"
    assert len(call_kwargs["comments"]) == 1


def test_post_review_comment_fallback_when_no_issues(monkeypatch):
    """When review_data has no issues, falls back to issue comment."""
    pr_obj = MagicMock()

    review_data = {
        "approval_recommendation": "approve",
        "summary": "LGTM",
        "issues": [],
    }

    result = review.post_review_comment(
        pr_obj, "# Summary", review_data=review_data, event="APPROVE",
    )
    assert result is None
    pr_obj.create_review.assert_not_called()
    pr_obj.create_issue_comment.assert_called_once()


def test_post_review_comment_fallback_on_api_error(monkeypatch):
    """When review API throws, falls back to issue comment."""
    monkeypatch.setattr(review, "logger", MagicMock())

    pr_obj = MagicMock()
    pr_obj.create_review.side_effect = RuntimeError("API down")

    review_data = {
        "issues": [{"file": "a.py", "line": 1, "message": "Bad"}],
    }

    result = review.post_review_comment(
        pr_obj, "# Summary", review_data=review_data,
    )
    assert result is None
    pr_obj.create_issue_comment.assert_called_once()


# ── _find_previous_review / _dismiss_review ──────────────────────────────────


def test_find_previous_review_finds_marker():
    """Returns review object whose body contains the RepoKeeper marker."""
    review_obj = MagicMock()
    review_obj.body = "Some text\n🤖 RepoKeeper Code Review\nMore text"

    pr_obj = MagicMock()
    pr_obj.get_reviews.return_value = [review_obj]

    found = review._find_previous_review(pr_obj)
    assert found is review_obj


def test_find_previous_review_no_marker():
    """Returns None when no review has the marker."""
    review_obj = MagicMock()
    review_obj.body = "Plain comment"

    pr_obj = MagicMock()
    pr_obj.get_reviews.return_value = [review_obj]

    found = review._find_previous_review(pr_obj)
    assert found is None


def test_find_previous_review_api_error(monkeypatch):
    """Returns None gracefully when get_reviews fails."""
    pr_obj = MagicMock()
    pr_obj.get_reviews.side_effect = RuntimeError("API error")

    found = review._find_previous_review(pr_obj)
    assert found is None


def test_dismiss_review_calls_dismiss():
    """Dismisses the review with the given message."""
    review_obj = MagicMock()

    result = review._dismiss_review(review_obj, "Outdated")
    assert result is True
    review_obj.dismiss.assert_called_once_with("Outdated")


def test_dismiss_review_handles_error():
    """Returns False when dismiss fails."""
    review_obj = MagicMock()
    review_obj.dismiss.side_effect = RuntimeError("cannot dismiss")

    result = review._dismiss_review(review_obj)
    assert result is False


# ── run_review (incremental / re-review) ─────────────────────────────────────


def test_run_review_incremental_dismisses_previous(monkeypatch):
    """When a previous review exists, it is dismissed and replaced."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"implement": True, "skip_keywords": [], "model": "test"},
        "style": {}, "tech": {}, "pr": {},
    })

    old_review = MagicMock()
    old_review.body = "🤖 RepoKeeper Code Review"

    pr_obj = MagicMock()
    pr_obj.get_reviews.return_value = [old_review]
    pr_obj.create_review.return_value = MagicMock(id="review-456")

    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "Updated PR", "body": "",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)
    monkeypatch.setattr(review, "collect_review_context",
                        lambda pr_data, profile: {})

    def fake_call_llm(*args, **kwargs):
        return {
            "approval_recommendation": "comment",
            "summary": "Updated review",
            "issues": [{"file": "a.py", "line": 1, "message": "New issue", "suggestion": "Fix"}],
            "style_violations": [],
            "positive_notes": [],
            "test_recommendation": "",
            "security_concerns": [],
        }, TokenUsage()

    monkeypatch.setattr(review, "call_llm_for_review", fake_call_llm)

    result = review.run_review(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["review_posted"] is True
    assert result["incremental"] is True
    old_review.dismiss.assert_called_once()


# ── run_describe ────────────────────────────────────────────────────────────


def test_run_describe_missing_config(monkeypatch):
    """Missing required config raises ConfigError."""
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "PR_NUMBER", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REPOKEEPER_GITHUB_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="Missing required configuration"):
        review.run_describe()


def test_run_describe_updates_pr(monkeypatch):
    """Happy path: generates and posts a description."""
    monkeypatch.setattr(review, "load_profile", lambda profile_path=None: {
        "agent": {"model": "test"},
        "style": {}, "tech": {}, "pr": {},
    })

    pr_obj = MagicMock()
    repo_mock = MagicMock()
    repo_mock.get_pull.return_value = pr_obj
    repo_mock.get_repo.return_value = repo_mock

    gh_mock = MagicMock()
    gh_mock.get_repo.return_value = repo_mock
    monkeypatch.setattr(review, "Github", lambda token: gh_mock)
    monkeypatch.setattr(review, "LLMClient", lambda **kw: MagicMock())

    def fake_get_pr_data(repo, num):
        return {
            "number": num, "title": "Add feature", "body": "Old desc",
            "author": "dev", "base_branch": "main", "head_branch": "feat",
            "files": [], "changed_files": [], "comments": [],
            "additions": 0, "deletions": 0, "changed_files_count": 0,
        }
    monkeypatch.setattr(review, "get_pr_data", fake_get_pr_data)

    class Response:
        content = '{"title": "", "body": "# New Feature\\n\\nThis PR adds a new feature."}'
        usage = TokenUsage()

    class Client:
        def chat(self, **kwargs):
            return Response()

    monkeypatch.setattr(review, "LLMClient", lambda **kw: Client())

    result = review.run_describe(
        gh_token="tk", repository="owner/repo", pr_number=1, llm_api_key="key",
    )
    assert result["description_posted"] is True
    assert result["title_updated"] is False
    # PR body was edited
    edit_calls = [c for c in pr_obj.edit.call_args_list if "body" in c[1]]
    assert len(edit_calls) >= 1


