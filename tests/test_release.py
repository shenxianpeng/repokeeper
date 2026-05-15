from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from repokeeper.release import (
    ReleaseContext,
    ReleasePullRequest,
    _fallback_release_body,
    _next_patch_tag,
    collect_release_context,
    create_or_update_draft_release,
    draft_release_notes,
    generate_release_summary,
    run_release,
)


class _FakeLLM:
    def __init__(self, content: dict[str, str]):
        self.content = content

    def chat(self, **kwargs):
        return SimpleNamespace(content=json.dumps(self.content))


class _FailingLLM:
    def chat(self, **kwargs):
        raise RuntimeError("down")


def _commit(sha: str, message: str):
    return SimpleNamespace(
        sha=sha,
        html_url=f"https://github.com/owner/repo/commit/{sha}",
        author=SimpleNamespace(login="alice"),
        commit=SimpleNamespace(
            message=message,
            author=SimpleNamespace(name="Alice", date=datetime(2026, 1, 2)),
        ),
    )


def _file(filename: str):
    return SimpleNamespace(filename=filename, status="modified", additions=3, deletions=1)


def _repo_with_release():
    release = SimpleNamespace(
        draft=False,
        prerelease=False,
        tag_name="v1.2.3",
        published_at=datetime(2026, 1, 1),
        created_at=datetime(2026, 1, 1),
        html_url="https://github.com/owner/repo/releases/tag/v1.2.3",
    )
    repo = MagicMock()
    repo.default_branch = "main"
    repo.get_releases.return_value = [release]
    repo.compare.return_value = SimpleNamespace(
        commits=[
            _commit("a" * 40, "feat: direct change"),
            _commit("b" * 40, "fix: from squash PR"),
            _commit("c" * 40, "Merge pull request #9 from owner/branch"),
        ],
        files=[_file("src/app.py")],
    )
    pr = MagicMock()
    pr.number = 5
    pr.title = "fix: from squash PR"
    pr.html_url = "https://github.com/owner/repo/pull/5"
    pr.user = SimpleNamespace(login="bob")
    pr.merged_at = datetime(2026, 1, 2)
    pr.labels = [SimpleNamespace(name="bug")]
    pr.merge_commit_sha = "b" * 40
    pr.get_files.return_value = [_file("src/fix.py")]
    repo.get_pull.return_value = pr
    return repo


def test_next_patch_tag():
    assert _next_patch_tag("v1.2.3") == "v1.2.4"
    assert _next_patch_tag("1.2.3") == "1.2.4"
    assert _next_patch_tag("not-semver") == "v0.1.0"


def test_collect_release_context_keeps_prs_and_direct_commits():
    repo = _repo_with_release()
    gh = MagicMock()
    gh.get_repo.return_value = repo
    gh.search_issues.return_value = [SimpleNamespace(number=5)]

    context = collect_release_context(gh, "owner/repo")

    assert context.base_ref == "v1.2.3"
    assert context.target_ref == "main"
    assert context.tag_name == "v1.2.4"
    assert [pr.number for pr in context.pull_requests] == [5]
    assert [commit.short_sha for commit in context.direct_commits] == ["aaaaaaa"]
    assert context.files[0].filename == "src/app.py"


def test_collect_release_context_accepts_explicit_refs():
    repo = _repo_with_release()
    gh = MagicMock()
    gh.get_repo.return_value = repo
    gh.search_issues.return_value = []

    context = collect_release_context(
        gh,
        "owner/repo",
        base_ref="v2.0.0",
        target_ref="release/2.x",
        tag_name="v2.0.1",
    )

    assert context.base_ref == "v2.0.0"
    assert context.target_ref == "release/2.x"
    assert context.tag_name == "v2.0.1"
    repo.compare.assert_called_once_with("v2.0.0", "release/2.x")


def test_draft_release_notes_uses_llm_json():
    context = ReleaseContext(
        repo="owner/repo",
        base_ref="v1.0.0",
        target_ref="main",
        tag_name="v1.0.1",
        pull_requests=[
            ReleasePullRequest(
                number=1,
                title="feat: add API",
                url="https://github.com/owner/repo/pull/1",
                author="alice",
            )
        ],
    )
    llm = _FakeLLM({"name": "v1.0.1", "body": "## Features\n- Add API (#1)"})

    name, body = draft_release_notes(llm, context, {"agent": {"model": "deepseek-chat"}})

    assert name == "v1.0.1"
    assert "Add API (#1)" in body


def test_draft_release_notes_falls_back_on_llm_error():
    context = ReleaseContext(repo="owner/repo", base_ref="v1", target_ref="main", tag_name="v2")

    name, body = draft_release_notes(_FailingLLM(), context, {"agent": {"model": "deepseek-chat"}})

    assert name == "v2"
    assert "No user-visible changes" in body


def test_fallback_release_body_includes_pr_and_commit_sources():
    context = ReleaseContext(
        repo="owner/repo",
        base_ref="v1",
        target_ref="main",
        tag_name="v2",
        pull_requests=[
            ReleasePullRequest(number=7, title="fix bug", url="", author="alice")
        ],
    )
    context.direct_commits.append(
        SimpleNamespace(title="docs update", short_sha="abcdef0")
    )

    body = _fallback_release_body(context)

    assert "fix bug by @alice (#7)" in body
    assert "docs update (abcdef0)" in body


def test_create_or_update_draft_release_updates_matching_draft():
    draft = MagicMock()
    draft.draft = True
    draft.tag_name = "v1.0.1"
    draft.html_url = "https://github.com/owner/repo/releases/tag/v1.0.1"
    repo = MagicMock()
    repo.get_releases.return_value = [draft]

    action, url = create_or_update_draft_release(repo, "v1.0.1", "main", "v1.0.1", "body")

    assert action == "updated"
    assert url.endswith("v1.0.1")
    draft.update_release.assert_called_once_with(
        name="v1.0.1",
        message="body",
        draft=True,
        prerelease=False,
    )
    repo.create_git_release.assert_not_called()


def test_create_or_update_draft_release_creates_when_missing():
    release = SimpleNamespace(html_url="https://github.com/owner/repo/releases/tag/v1.0.1")
    repo = MagicMock()
    repo.get_releases.return_value = []
    repo.create_git_release.return_value = release

    action, url = create_or_update_draft_release(repo, "v1.0.1", "main", "v1.0.1", "body")

    assert action == "created"
    assert url.endswith("v1.0.1")
    repo.create_git_release.assert_called_once_with(
        tag="v1.0.1",
        name="v1.0.1",
        message="body",
        draft=True,
        prerelease=False,
        target_commitish="main",
    )


def test_run_release_dry_run_does_not_create_release(monkeypatch):
    context = ReleaseContext(repo="owner/repo", base_ref="v1", target_ref="main", tag_name="v2")
    monkeypatch.setattr("repokeeper.release.collect_release_context", lambda *a, **kw: context)
    monkeypatch.setattr("repokeeper.release.draft_release_notes", lambda *a, **kw: ("v2", "body"))
    repo = MagicMock()
    gh = MagicMock()
    gh.get_repo.return_value = repo

    result = run_release(gh, MagicMock(), "owner/repo", profile={}, dry_run=True)

    assert result.action == "dry-run"
    assert result.body == "body"
    repo.create_git_release.assert_not_called()


def test_generate_release_summary_includes_body():
    result = run_release(
        MagicMock(),
        MagicMock(),
        "owner/repo",
        profile={"release": {"enabled": False}},
        tag_name="v1",
    )

    summary = generate_release_summary(result)

    assert "RepoKeeper Draft Release" in summary
    assert "disabled" in summary
