from __future__ import annotations

import importlib
from pathlib import Path

from repokeeper import agent
from repokeeper.agent import (
    build_context_string,
    call_llm,
    check_skip_keywords,
    collect_repo_files,
    create_pr,
    get_issue_data,
    run_agent,
    validate_implementation,
)


def test_agent_module_imports_without_ci_environment(monkeypatch):
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "ISSUE_NUMBER"):
        monkeypatch.delenv(name, raising=False)

    module = importlib.import_module("repokeeper.agent")

    assert hasattr(module, "run_agent")


def test_check_skip_keywords_matches_title_and_body():
    issue = {"title": "Needs design before implementation", "body": "normal body"}
    profile = {"agent": {"skip_keywords": ["needs design"]}}

    assert check_skip_keywords(issue, profile) == "needs design"


def test_validate_implementation_enforces_file_count_and_branch_prefix():
    implementation = {
        "branch_name": "feature/bad",
        "changes": {"a.py": "", "b.py": ""},
        "new_files": {"c.py": ""},
    }
    profile = {"pr": {"max_files_per_pr": 2}}

    issues = validate_implementation(implementation, profile)

    assert "Implementation touches 3 files (max: 2)" in issues
    assert "branch_name must start with 'repokeeper/'" in issues


def test_collect_repo_files_skips_large_and_unsupported_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("README.md").write_text("hello")
    Path("data.bin").write_text("ignored")
    Path("big.py").write_text("x" * 40_001)

    files = collect_repo_files()

    assert files == {"README.md": "hello"}
    assert "### README.md" in build_context_string(files)


def test_get_issue_data_extracts_recent_comments():
    class User:
        login = "alice"

    class Label:
        name = "bug"

    class Comment:
        user = User()
        body = "comment"

    class Issue:
        number = 7
        title = "Title"
        body = ""
        labels = [Label()]

        def get_comments(self):
            return [Comment()]

    class Repo:
        def get_issue(self, number):
            assert number == 7
            return Issue()

    assert get_issue_data(Repo(), 7) == {
        "number": 7,
        "title": "Title",
        "body": "(no description)",
        "labels": ["bug"],
        "comments": [{"author": "alice", "body": "comment"}],
    }


def test_call_llm_strips_json_fence():
    class Message:
        content = '```json\n{"skip": true, "reason": "too broad"}\n```'

    class Choice:
        message = Message()

    class Completions:
        def create(self, **kwargs):
            return type("Response", (), {"choices": [Choice()]})()

    class Client:
        chat = type("Chat", (), {"completions": Completions()})()

    result = call_llm(
        {"number": 1, "title": "title", "body": "body", "comments": []},
        "context",
        {"agent": {"model": "deepseek-chat"}, "style": {}, "tech": {}},
        Client(),
    )

    assert result == {"skip": True, "reason": "too broad"}


def test_create_pr_returns_url():
    class Pull:
        html_url = "https://example.test/pr/1"

    class Repo:
        default_branch = "main"

        def create_pull(self, **kwargs):
            assert kwargs["head"] == "repokeeper/test"
            assert "Closes #1" in kwargs["body"]
            return Pull()

    url = create_pr(
        Repo(),
        {"number": 1},
        {"summary": "done", "commit_message": "fix: thing"},
        "repokeeper/test",
        ["a.py"],
        {},
    )

    assert url == "https://example.test/pr/1"


def test_run_agent_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(agent, "load_profile", lambda profile_path=None: {"agent": {"implement": False}})

    result = run_agent(
        gh_token="token",
        repository="owner/repo",
        issue_number=1,
        llm_api_key="key",
    )

    assert result == {
        "skip": True,
        "reason": "Agent implementation disabled in profile.",
        "pr_url": None,
    }
