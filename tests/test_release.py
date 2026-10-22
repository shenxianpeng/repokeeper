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


# ── Release lookup helpers ───────────────────────────────────────────────────


def _release(tag: str, *, draft=False, prerelease=False, published_at=None, created_at=None):
    return SimpleNamespace(
        draft=draft,
        prerelease=prerelease,
        tag_name=tag,
        published_at=published_at,
        created_at=created_at,
        html_url=f"https://github.com/owner/repo/releases/tag/{tag}",
    )


def test_latest_published_release_skips_drafts_and_prereleases():
    from repokeeper.release import _latest_published_release

    repo = MagicMock()
    repo.get_releases.return_value = [
        _release("v2.0.0-draft", draft=True),
        _release("v2.0.0-rc1", prerelease=True),
        _release("v1.0.0"),
    ]

    assert _latest_published_release(repo).tag_name == "v1.0.0"
    assert _latest_published_release(repo, include_prereleases=True).tag_name == "v2.0.0-rc1"

    repo.get_releases.side_effect = RuntimeError("api down")
    assert _latest_published_release(repo) is None


def test_published_release_for_tag_filters_and_handles_errors():
    from repokeeper.release import _published_release_for_tag

    repo = MagicMock()
    repo.get_releases.return_value = [
        _release("v1.0.0", draft=True),
        _release("v1.0.0", prerelease=True),
        _release("v0.9.0"),
        _release("v1.0.0"),
    ]

    assert _published_release_for_tag(repo, "v1.0.0").prerelease is False
    assert _published_release_for_tag(repo, "v1.0.0", include_prereleases=True).prerelease is True
    assert _published_release_for_tag(repo, "v3.0.0") is None

    repo.get_releases.side_effect = RuntimeError("api down")
    assert _published_release_for_tag(repo, "v1.0.0") is None


def test_latest_tag_name_returns_first_tag_or_empty():
    from repokeeper.release import _latest_tag_name

    repo = MagicMock()
    repo.get_tags.return_value = [SimpleNamespace(name="v4.0.0"), SimpleNamespace(name="v3.0.0")]
    assert _latest_tag_name(repo) == "v4.0.0"

    repo.get_tags.return_value = []
    assert _latest_tag_name(repo) == ""

    repo.get_tags.side_effect = RuntimeError("api down")
    assert _latest_tag_name(repo) == ""


def test_release_search_date_falls_back_to_created_at():
    from repokeeper.release import _release_search_date

    assert _release_search_date(None) is None
    assert _release_search_date(_release("v1", created_at=datetime(2026, 3, 4))) == "2026-03-04"
    assert _release_search_date(_release("v1", published_at=datetime(2026, 5, 6))) == "2026-05-06"
    assert _release_search_date(_release("v1", published_at="2026-01-01")) is None


def test_normalize_pull_request_caps_files_and_tolerates_errors():
    from repokeeper.release import _normalize_pull_request

    pr = MagicMock()
    pr.number = 3
    pr.title = "t"
    pr.html_url = "u"
    pr.user = None
    pr.merged_at = "not-a-date"
    pr.labels = []
    pr.merge_commit_sha = None
    pr.get_files.return_value = [_file(f"f{i}.py") for i in range(40)]

    normalized = _normalize_pull_request(pr)
    assert len(normalized.changed_files) == 30
    assert normalized.author == "unknown"
    assert normalized.merged_at is None
    assert normalized.merge_commit_sha == ""

    pr.get_files.side_effect = RuntimeError("no files")
    assert _normalize_pull_request(pr).changed_files == []


def test_fetch_merged_pull_requests_dedupes_caps_and_handles_errors():
    from repokeeper.release import _fetch_merged_pull_requests

    repo = _repo_with_release()
    gh = MagicMock()
    gh.search_issues.return_value = [
        SimpleNamespace(number=5),
        SimpleNamespace(number=5),
        SimpleNamespace(number=6),
    ]

    assert _fetch_merged_pull_requests(gh, repo, "owner/repo", "main", None, 10) == []

    prs = _fetch_merged_pull_requests(gh, repo, "owner/repo", "main", "2026-01-01", 2)
    assert len(prs) == 2
    assert [call.args[0] for call in repo.get_pull.call_args_list] == [5, 6]

    repo.get_pull.reset_mock()
    prs = _fetch_merged_pull_requests(gh, repo, "owner/repo", "main", "2026-01-01", 1)
    assert len(prs) == 1
    assert repo.get_pull.call_count == 1

    gh.search_issues.side_effect = RuntimeError("search down")
    assert _fetch_merged_pull_requests(gh, repo, "owner/repo", "main", "2026-01-01", 10) == []


def test_collect_compare_data_caps_commits_and_handles_errors():
    from repokeeper.release import _collect_compare_data

    repo = MagicMock()
    repo.compare.return_value = SimpleNamespace(
        commits=[_commit("a" * 40, "one"), _commit("b" * 40, "two")],
        files=[_file("a.py")],
    )

    commits, files = _collect_compare_data(repo, "v1", "main", max_commits=1)
    assert [c.title for c in commits] == ["one"]
    assert [f.filename for f in files] == ["a.py"]

    repo.compare.side_effect = RuntimeError("compare down")
    assert _collect_compare_data(repo, "v1", "main", 10) == ([], [])


def test_normalize_commit_falls_back_to_commit_author_name():
    from repokeeper.release import _normalize_commit

    commit = SimpleNamespace(
        sha="",
        html_url="u",
        author=None,
        commit=SimpleNamespace(message="   ", author=SimpleNamespace(name="", date=None)),
    )

    normalized = _normalize_commit(commit)

    assert normalized.short_sha == ""
    assert normalized.title == ""
    assert normalized.author == "unknown"


def test_collect_release_context_without_releases_or_tags_uses_target():
    repo = MagicMock()
    repo.default_branch = None
    repo.get_releases.return_value = []
    repo.get_tags.return_value = []
    repo.compare.return_value = SimpleNamespace(commits=[], files=[])
    gh = MagicMock()
    gh.get_repo.return_value = repo

    context = collect_release_context(gh, "owner/repo")

    assert context.base_ref == "main"
    assert context.target_ref == "main"
    assert context.tag_name == "v0.1.0"
    assert context.pull_requests == []
    gh.search_issues.assert_not_called()


# ── Evidence and fallback formatting ─────────────────────────────────────────


def _rich_context():
    from repokeeper.release import ReleaseCommit, ReleaseFileChange

    return ReleaseContext(
        repo="owner/repo",
        base_ref="v1",
        target_ref="main",
        tag_name="v2",
        previous_release_url="https://github.com/owner/repo/releases/tag/v1",
        pull_requests=[ReleasePullRequest(number=7, title="fix bug", url="u", author="alice")],
        direct_commits=[ReleaseCommit(sha="a" * 40, short_sha="aaaaaaa", title="docs", url="c", author="bob")],
        files=[ReleaseFileChange(filename="a.py", status="modified", additions=2, deletions=1)],
    )


def test_format_release_evidence_lists_all_sections():
    from repokeeper.release import _format_release_evidence

    evidence = _format_release_evidence(_rich_context())

    assert "- PR #7: fix bug by @alice (u); labels: none; files: not fetched" in evidence
    assert "- aaaaaaa: docs by bob (c)" in evidence
    assert "- a.py (modified, +2/-1)" in evidence

    empty = _format_release_evidence(
        ReleaseContext(repo="o/r", base_ref="v1", target_ref="main", tag_name="v2")
    )
    assert empty.count("- none") == 3


def test_fallback_release_body_links_previous_release():
    body = _fallback_release_body(_rich_context())

    assert "- Previous release: https://github.com/owner/repo/releases/tag/v1" in body
    assert "No user-visible changes" not in body


def test_draft_release_notes_uses_fallback_body_when_llm_body_is_empty():
    context = _rich_context()
    llm = _FakeLLM({"name": "", "body": "   "})

    name, body = draft_release_notes(llm, context, {"agent": {"model": "deepseek-chat"}})

    assert name == "v2"
    assert "fix bug by @alice (#7)" in body


# ── Draft release lookup and run_release ─────────────────────────────────────


def test_find_matching_draft_release_ignores_other_tags_and_errors():
    from repokeeper.release import _find_matching_draft_release

    repo = MagicMock()
    repo.get_releases.return_value = [_release("v9", draft=True), _release("v1")]
    assert _find_matching_draft_release(repo, "v1") is None

    repo.get_releases.side_effect = RuntimeError("api down")
    assert _find_matching_draft_release(repo, "v1") is None


def test_run_release_creates_draft_and_summary_links_it(monkeypatch):
    context = ReleaseContext(repo="owner/repo", base_ref="v1", target_ref="main", tag_name="v2")
    monkeypatch.setattr("repokeeper.release.collect_release_context", lambda *a, **kw: context)
    monkeypatch.setattr("repokeeper.release.draft_release_notes", lambda *a, **kw: ("v2", "body"))
    repo = MagicMock()
    repo.get_releases.return_value = []
    repo.create_git_release.return_value = SimpleNamespace(
        html_url="https://github.com/owner/repo/releases/tag/v2"
    )
    gh = MagicMock()
    gh.get_repo.return_value = repo

    result = run_release(gh, MagicMock(), "owner/repo", profile={"release": {"prerelease": True}})

    assert result.action == "created"
    assert result.html_url.endswith("/v2")
    assert repo.create_git_release.call_args.kwargs["prerelease"] is True
    summary = generate_release_summary(result)
    assert "- Draft release: https://github.com/owner/repo/releases/tag/v2" in summary
