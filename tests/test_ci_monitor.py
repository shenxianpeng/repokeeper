"""Tests for repokeeper.ci_monitor."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from repokeeper import ci_monitor as cm
from repokeeper.ci_monitor import (
    MAX_FIX_ATTEMPTS,
    PRCheckStatus,
    diagnose_and_fix_ci,
    find_agent_prs,
    get_pr_check_status,
    run_ci_monitor,
)

REPO = "owner/repo"
PR_NUMBER = 7
BRANCH = "repokeeper/issue-1-fix"
WORKFLOW_RUN_ID = 26253371337
JOB_ID = 77269938268
LOG_TEXT = "FAILED tests/test_app.py::test_value - assert 2 == 3"


# ── Helpers ─────────────────────────────────────────────────────────────


def _check_run(
    name: str = "test",
    status: str = "completed",
    conclusion: str | None = "failure",
    job_id: int = JOB_ID,
    url: str | None = None,
) -> SimpleNamespace:
    if url is None:
        url = f"https://github.com/{REPO}/actions/runs/{WORKFLOW_RUN_ID}/job/{job_id}"
    return SimpleNamespace(
        name=name, status=status, conclusion=conclusion,
        html_url=url, details_url=url, id=job_id, completed_at=None,
    )


def _make_pr(
    number: int = PR_NUMBER,
    ref: str = BRANCH,
    sha: str = "a" * 40,
    head_repo: str | None = REPO,
    base_repo: str = REPO,
    body: str = "",
    comments: tuple[str, ...] = (),
) -> MagicMock:
    pr = MagicMock()
    pr.number = number
    pr.body = body
    pr.head.ref = ref
    pr.head.sha = sha
    if head_repo is None:
        pr.head.repo = None
    else:
        pr.head.repo.full_name = head_repo
    pr.base.repo.full_name = base_repo
    pr.get_issue_comments.return_value = [SimpleNamespace(body=c) for c in comments]
    return pr


def _make_gh(pr: MagicMock, check_runs: tuple = (), default_branch: str = "main") -> MagicMock:
    gh = MagicMock()
    gh_repo = gh.get_repo.return_value
    gh_repo.default_branch = default_branch
    gh_repo.get_pull.return_value = pr
    gh_repo.get_pulls.return_value = [pr]
    gh_repo.get_commit.return_value.get_check_runs.return_value = list(check_runs)
    gh._Github__requester.auth.token = None  # push goes to the local "origin"
    return gh


def _make_llm(response: dict) -> MagicMock:
    llm = MagicMock()
    llm.api_key = "sk-llm-secret"
    llm.chat.return_value = SimpleNamespace(content=json.dumps(response))
    return llm


def _marker(sha: str) -> str:
    return f"<!-- repokeeper-ci-monitor: {sha} -->"


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    return result.stdout.rstrip("\n")


@pytest.fixture
def repos(tmp_path, monkeypatch) -> SimpleNamespace:
    """A bare "origin" with an agent PR branch, plus a local clone on main."""
    # Keep the developer's or runner's own git config out of the test.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(cm.tempfile, "tempdir", str(scratch))

    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    _git("init", "-b", "main", str(work), cwd=tmp_path)
    (work / "app.py").write_text("VALUE = 1\n")
    (work / "other.py").write_text("OTHER = 1\n")
    _git("add", "app.py", "other.py", cwd=work)
    _git("commit", "-m", "init", cwd=work)
    _git("checkout", "-b", BRANCH, cwd=work)
    (work / "app.py").write_text("VALUE = 2\n")
    _git("commit", "-am", "feat: agent change", cwd=work)
    head_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    _git("push", "origin", "main", BRANCH, cwd=work)
    # GitHub exposes every PR head as refs/pull/<n>/head.
    _git("update-ref", f"refs/pull/{PR_NUMBER}/head", head_sha, cwd=origin)
    _git("checkout", "main", cwd=work)
    _git("fetch", "origin", cwd=work)
    return SimpleNamespace(origin=origin, work=work, head_sha=head_sha, scratch=scratch)


@pytest.fixture
def log_calls(monkeypatch) -> list[dict]:
    """Replace the CI log download and record how it was called."""
    calls: list[dict] = []

    def _fake_fetch(gh_client, repo, **kwargs):
        calls.append(kwargs)
        return LOG_TEXT

    monkeypatch.setattr(cm, "_fetch_ci_log_snippet", _fake_fetch)
    return calls


GOOD_FIX = {
    "skip": False,
    "summary": "Set VALUE to the expected 3.",
    "commit_message": "fix: set VALUE to 3\n\nbody that must not reach the subject",
    "edits": [{"path": "app.py", "find": "VALUE = 2", "replace": "VALUE = 3"}],
}


def _assert_untouched(repos: SimpleNamespace) -> None:
    """Nothing was pushed and the local clone is exactly as it was."""
    assert _git("rev-parse", BRANCH, cwd=repos.origin) == repos.head_sha
    assert _git("branch", "--show-current", cwd=repos.work) == "main"
    assert _git("status", "--porcelain", cwd=repos.work) == ""
    assert len(_git("worktree", "list", cwd=repos.work).splitlines()) == 1
    assert list(repos.scratch.iterdir()) == []


# ── get_pr_check_status ─────────────────────────────────────────────────


class TestGetPrCheckStatus:
    def test_handles_error(self) -> None:
        """Should return pending status on API error."""
        mock_gh = MagicMock()
        mock_gh.get_repo.side_effect = RuntimeError("API error")
        status = get_pr_check_status(mock_gh, REPO, 42)
        assert status.pr_number == 42
        assert status.overall == "pending"
        assert status.total_checks == 0

    def test_no_checks_is_pending(self) -> None:
        status = get_pr_check_status(_make_gh(_make_pr()), REPO, PR_NUMBER)
        assert status.overall == "pending"
        assert status.head_sha == "a" * 40

    def test_skipped_and_neutral_count_as_passed(self) -> None:
        runs = (
            _check_run("test", conclusion="success"),
            _check_run("deploy", conclusion="skipped"),
            _check_run("lint", conclusion="neutral"),
        )
        status = get_pr_check_status(_make_gh(_make_pr(), runs), REPO, PR_NUMBER)
        assert status.overall == "success"
        assert (status.total_checks, status.completed, status.passed) == (3, 3, 3)

    def test_in_progress_is_pending_even_with_a_failure(self) -> None:
        runs = (
            _check_run("test", conclusion="failure"),
            _check_run("docs", status="in_progress", conclusion=None),
        )
        status = get_pr_check_status(_make_gh(_make_pr(), runs), REPO, PR_NUMBER)
        assert status.overall == "pending"
        assert [c.name for c in status.in_progress] == ["docs"]

    def test_failure_records_workflow_run_id_not_job_id(self) -> None:
        runs = (_check_run("lint", conclusion="success"), _check_run("test"))
        status = get_pr_check_status(_make_gh(_make_pr(), runs), REPO, PR_NUMBER)
        assert status.overall == "failure"
        failed = status.failed[0]
        assert failed.run_id == JOB_ID
        assert failed.workflow_run_id == WORKFLOW_RUN_ID

    def test_non_actions_check_has_no_workflow_run_id(self) -> None:
        runs = (_check_run("codecov/patch", url="https://app.codecov.io/gh/owner/repo/pull/7"),)
        status = get_pr_check_status(_make_gh(_make_pr(), runs), REPO, PR_NUMBER)
        assert status.failed[0].workflow_run_id == 0


# ── find_agent_prs ──────────────────────────────────────────────────────


class TestFindAgentPrs:
    def test_handles_error(self) -> None:
        """Should return empty list on API error."""
        mock_gh = MagicMock()
        mock_gh.get_repo.side_effect = RuntimeError("API error")
        assert find_agent_prs(mock_gh, REPO) == []

    def test_only_same_repo_agent_branches(self) -> None:
        marker_body = "## 🤖 RepoKeeper Implementation\n*Generated by RepoKeeper*"
        pulls = [
            _make_pr(number=1),
            # A fork controls its branch name and PR body; neither proves anything.
            _make_pr(number=2, head_repo="attacker/repo"),
            _make_pr(number=3, ref="main", head_repo="attacker/repo", body=marker_body),
            _make_pr(number=4, ref="feature/x", body=marker_body),
            _make_pr(number=5, head_repo=None),  # fork was deleted
            _make_pr(number=6, ref="repokeeper/deps-upgrade"),
        ]
        gh = _make_gh(pulls[0])
        gh.get_repo.return_value.get_pulls.return_value = pulls
        assert find_agent_prs(gh, REPO) == [1, 6]

    def test_never_returns_the_default_branch(self) -> None:
        gh = _make_gh(_make_pr(ref="repokeeper/main"), default_branch="repokeeper/main")
        assert find_agent_prs(gh, REPO) == []

    def test_respects_max_prs(self) -> None:
        pulls = [_make_pr(number=n) for n in range(1, 6)]
        gh = _make_gh(pulls[0])
        gh.get_repo.return_value.get_pulls.return_value = pulls
        assert find_agent_prs(gh, REPO, max_prs=2) == [1, 2]


# ── diagnose_and_fix_ci: pushing a fix ──────────────────────────────────


class TestDiagnoseAndFixCi:
    def test_pushes_fix_to_pr_branch(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha)
        gh = _make_gh(pr, (_check_run(),))
        llm = _make_llm(GOOD_FIX)

        result = diagnose_and_fix_ci(gh, llm, REPO, PR_NUMBER, {}, repos.work)

        assert result == {
            "fixed": True,
            "summary": "Set VALUE to the expected 3.",
            "fixes_applied": ["app.py"],
        }
        # One new commit on the PR branch, authored by the bot, on top of the old head.
        assert _git("show", f"{BRANCH}:app.py", cwd=repos.origin) == "VALUE = 3"
        assert _git("rev-parse", f"{BRANCH}~1", cwd=repos.origin) == repos.head_sha
        assert _git("log", "-1", "--format=%an|%ae|%B", BRANCH, cwd=repos.origin) == (
            "repokeeper[bot]|repokeeper[bot]@users.noreply.github.com|fix: set VALUE to 3"
        )
        assert _git("diff-tree", "--no-commit-id", "--name-only", "-r", BRANCH, cwd=repos.origin) == "app.py"
        # The default branch is untouched.
        assert _git("show", "main:app.py", cwd=repos.origin) == "VALUE = 1"

        # Logs were requested for the workflow run, not the check-run (job) ID.
        assert log_calls[0]["run_id"] == WORKFLOW_RUN_ID
        # The LLM saw the log and the PR diff.
        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert LOG_TEXT in prompt
        assert "+VALUE = 2" in prompt

        # The PR comment records the handled commit.
        comment = pr.create_issue_comment.call_args.args[0]
        assert "pushed a fix" in comment
        assert "`fix: set VALUE to 3`" in comment
        assert _marker(repos.head_sha) in comment

    def test_leaves_local_checkout_alone(self, repos, log_calls) -> None:
        """The caller's branch, uncommitted work, config and remote stay as they were."""
        (repos.work / "other.py").write_text("OTHER = 'uncommitted'\n")
        (repos.work / "notes.txt").write_text("untracked\n")
        gh = _make_gh(_make_pr(sha=repos.head_sha), (_check_run(),))

        result = diagnose_and_fix_ci(gh, _make_llm(GOOD_FIX), REPO, PR_NUMBER, {}, repos.work)

        assert result["fixed"] is True
        # Neither the uncommitted edit nor the untracked file ended up in the push.
        assert _git("diff-tree", "--no-commit-id", "--name-only", "-r", BRANCH, cwd=repos.origin) == "app.py"
        assert _git("show", f"{BRANCH}:other.py", cwd=repos.origin) == "OTHER = 1"
        assert _git("branch", "--show-current", cwd=repos.work) == "main"
        assert _git("status", "--porcelain", cwd=repos.work).splitlines() == [
            " M other.py", "?? notes.txt",
        ]
        assert (repos.work / "app.py").read_text() == "VALUE = 1\n"
        assert _git("config", "--get", "remote.origin.url", cwd=repos.work) == str(repos.origin)
        assert _git("config", "--local", "--list", cwd=repos.work).count("user.") == 0
        assert len(_git("worktree", "list", cwd=repos.work).splitlines()) == 1
        assert list(repos.scratch.iterdir()) == []

    def test_not_failing_does_nothing(self) -> None:
        gh = _make_gh(_make_pr(), (_check_run(conclusion="success"),))
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(gh, llm, REPO, PR_NUMBER, {})
        assert result["fixed"] is False
        assert "not failing" in result["summary"]
        llm.chat.assert_not_called()

    @pytest.mark.parametrize(
        "check_run",
        [
            _check_run(conclusion="cancelled"),
            _check_run(conclusion="action_required"),
            _check_run("codecov/patch", url="https://app.codecov.io/gh/owner/repo/pull/7"),
        ],
    )
    def test_skips_failures_a_commit_cannot_fix(self, repos, log_calls, check_run) -> None:
        pr = _make_pr(sha=repos.head_sha)
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(_make_gh(pr, (check_run,)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "no failed GitHub Actions check" in result["summary"]
        llm.chat.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        _assert_untouched(repos)

    @pytest.mark.parametrize(
        ("ref", "head_repo"),
        [
            (BRANCH, "attacker/repo"),      # fork using the agent's branch prefix
            ("main", "attacker/repo"),      # fork whose branch is named like the default branch
            ("main", REPO),                 # the default branch itself
            ("feature/human-work", REPO),   # a maintainer's own branch
        ],
    )
    def test_refuses_prs_it_does_not_own(self, repos, log_calls, ref, head_repo) -> None:
        pr = _make_pr(ref=ref, sha=repos.head_sha, head_repo=head_repo,
                      body="*Generated by RepoKeeper*")
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "refusing to push" in result["summary"]
        llm.chat.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        assert log_calls == []
        assert _git("show", "main:app.py", cwd=repos.origin) == "VALUE = 1"
        _assert_untouched(repos)

    def test_one_attempt_per_head_commit(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha, comments=(f"earlier attempt\n\n{_marker(repos.head_sha)}",))
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "already attempted" in result["summary"]
        llm.chat.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        _assert_untouched(repos)

    def test_stops_at_attempt_limit(self, repos, log_calls) -> None:
        earlier = tuple(_marker(f"{n}" * 40) for n in range(MAX_FIX_ATTEMPTS))
        pr = _make_pr(sha=repos.head_sha, comments=("unrelated comment", *earlier))
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert f"limit of {MAX_FIX_ATTEMPTS}" in result["summary"]
        llm.chat.assert_not_called()
        _assert_untouched(repos)

    def test_skips_when_pr_head_moved(self, repos, log_calls) -> None:
        """The checks belong to an older commit than the one git just fetched."""
        pr = _make_pr(sha="b" * 40)
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "head moved" in result["summary"]
        llm.chat.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        _assert_untouched(repos)

    def test_llm_skip_is_reported_once(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha)
        llm = _make_llm({"skip": True, "reason": "The runner is out of disk space."})
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "out of disk space" in result["summary"]
        comment = pr.create_issue_comment.call_args.args[0]
        assert "did not push a fix" in comment
        assert _marker(repos.head_sha) in comment
        _assert_untouched(repos)

    @pytest.mark.parametrize(
        "fix",
        [
            {"new_files": {".github/workflows/ci.yml": "on: push\n"}},
            {"new_files": {".git/config": "[core]\n\tfsmonitor = touch pwned\n"}},
            {"new_files": {"../outside.py": "x = 1\n"}},
            {"edits": [{"path": "app.py", "find": "VALUE = 2", "replace": "VALUE = 3"}],
             "new_files": {"sub/.git/hooks/pre-commit": "#!/bin/sh\n"}},
        ],
    )
    def test_rejects_unsafe_paths(self, repos, log_calls, fix) -> None:
        pr = _make_pr(sha=repos.head_sha)
        result = diagnose_and_fix_ci(
            _make_gh(pr, (_check_run(),)), _make_llm(fix), REPO, PR_NUMBER, {}, repos.work,
        )
        assert result["fixed"] is False
        assert "Refusing" in result["summary"]
        comment = pr.create_issue_comment.call_args.args[0]
        assert "could not fix it automatically" in comment
        assert _marker(repos.head_sha) in comment
        _assert_untouched(repos)

    def test_no_file_changes_is_not_a_fix(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha)
        llm = _make_llm({"summary": "nothing", "changes": {"app.py": "VALUE = 2\n"}})
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result == {
            "fixed": False, "summary": "CI fix produced no file changes.", "fixes_applied": [],
        }
        assert "changed no files" in pr.create_issue_comment.call_args.args[0]
        _assert_untouched(repos)

    def test_edit_that_does_not_apply_is_reported(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha)
        llm = _make_llm({"edits": [{"path": "app.py", "find": "NOT THERE", "replace": "x"}]})
        result = diagnose_and_fix_ci(_make_gh(pr, (_check_run(),)), llm, REPO, PR_NUMBER, {}, repos.work)
        assert result["fixed"] is False
        assert "Edit target not found" in result["summary"]
        assert "could not fix it automatically" in pr.create_issue_comment.call_args.args[0]
        _assert_untouched(repos)

    def test_failed_push_is_not_reported_as_fixed(self, repos, log_calls, monkeypatch) -> None:
        """A push error must surface, with the token kept out of the report."""
        token = "ghs_supersecrettoken"
        monkeypatch.setattr(
            cm, "_push_target", lambda repo, tok: str(repos.scratch.parent / tok / "missing.git"),
        )
        pr = _make_pr(sha=repos.head_sha)
        result = diagnose_and_fix_ci(
            _make_gh(pr, (_check_run(),)), _make_llm(GOOD_FIX), REPO, PR_NUMBER, {},
            repos.work, gh_token=token,
        )
        assert result["fixed"] is False
        assert "git push failed" in result["summary"]
        comment = pr.create_issue_comment.call_args.args[0]
        assert "could not fix it automatically" in comment
        assert token not in result["summary"]
        assert token not in comment
        assert "***" in comment
        _assert_untouched(repos)

    def test_fix_stands_when_the_comment_fails(self, repos, log_calls) -> None:
        pr = _make_pr(sha=repos.head_sha)
        pr.create_issue_comment.side_effect = RuntimeError("403 Resource not accessible")
        result = diagnose_and_fix_ci(
            _make_gh(pr, (_check_run(),)), _make_llm(GOOD_FIX), REPO, PR_NUMBER, {}, repos.work,
        )
        assert result["fixed"] is True
        assert _git("show", f"{BRANCH}:app.py", cwd=repos.origin) == "VALUE = 3"

    def test_api_error_while_inspecting_pr(self) -> None:
        gh = _make_gh(_make_pr(), (_check_run(),))
        gh.get_repo.return_value.get_pull.side_effect = [_make_pr(), RuntimeError("502")]
        llm = _make_llm(GOOD_FIX)
        result = diagnose_and_fix_ci(gh, llm, REPO, PR_NUMBER, {})
        assert result == {
            "fixed": False, "summary": "CI monitoring error: 502", "fixes_applied": [],
        }
        llm.chat.assert_not_called()


# ── Small helpers ───────────────────────────────────────────────────────


def test_push_target_uses_token_url_without_touching_remote() -> None:
    assert cm._push_target(REPO, None) == "origin"
    assert cm._push_target(REPO, "tok") == "https://x-access-token:tok@github.com/owner/repo.git"


def test_redact_removes_every_known_secret() -> None:
    text = "push to https://x-access-token:ghs_abc@github.com failed (key sk-1)"
    assert cm._redact(text, "ghs_abc", None, "", "sk-1") == (
        "push to https://x-access-token:***@github.com failed (key ***)"
    )


def test_commit_message_falls_back_and_stays_single_line() -> None:
    assert cm._commit_message({}, 7) == "fix: CI failure on PR #7"
    assert cm._commit_message({"commit_message": "  "}, 7) == "fix: CI failure on PR #7"
    assert cm._commit_message({"commit_message": "fix: a\n\nlong body"}, 7) == "fix: a"


def test_clip_marks_truncation() -> None:
    assert cm._clip("short", 10) == "short"
    assert cm._clip("x" * 20, 10) == "x" * 10 + "\n...(truncated)..."


# ── run_ci_monitor ──────────────────────────────────────────────────────


class TestRunCiMonitor:
    def test_returns_early_when_disabled(self, monkeypatch) -> None:
        """ci_auto_fix=False should skip."""
        profile = {"patrol": {"ci_auto_fix": False}}
        monkeypatch.setattr(cm, "load_profile", lambda _p=None: profile)

        mock_gh = MagicMock()
        result = run_ci_monitor(mock_gh, MagicMock(), REPO)
        assert result["prs_checked"] == 0
        assert result["reason"] == "ci_auto_fix disabled"
        mock_gh.get_repo.assert_not_called()

    def test_skips_when_no_agent_prs(self, monkeypatch) -> None:
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [])
        result = run_ci_monitor(MagicMock(), MagicMock(), REPO, profile={}, max_prs=5)
        assert result == {"prs_checked": 0, "prs_fixed": 0, "details": []}

    def test_skips_passing_prs(self, monkeypatch) -> None:
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [42])
        monkeypatch.setattr(
            cm, "get_pr_check_status",
            lambda gh, repo, pr: PRCheckStatus(pr_number=42, overall="success"),
        )

        def _must_not_run(*args, **kwargs):
            raise AssertionError("a passing PR must not be fixed")

        monkeypatch.setattr(cm, "diagnose_and_fix_ci", _must_not_run)
        result = run_ci_monitor(MagicMock(), MagicMock(), REPO, profile={}, max_prs=5)
        assert result["prs_checked"] == 1
        assert result["prs_fixed"] == 0
        assert result["details"][0]["fix_applied"] is False

    def test_attempts_fix_on_failing_pr(self, monkeypatch) -> None:
        monkeypatch.setattr(cm, "find_agent_prs", lambda gh, repo, **kw: [99])
        monkeypatch.setattr(
            cm, "get_pr_check_status",
            lambda gh, repo, pr: PRCheckStatus(pr_number=99, overall="failure"),
        )
        seen: dict = {}

        def _fake_diagnose(gh, llm, repo, pr, profile, repo_path, gh_token=None):
            seen.update(pr=pr, repo_path=repo_path, gh_token=gh_token)
            return {"fixed": True, "summary": "CI fix applied.", "fixes_applied": ["test.py"]}

        monkeypatch.setattr(cm, "diagnose_and_fix_ci", _fake_diagnose)
        result = run_ci_monitor(
            MagicMock(), MagicMock(), REPO, profile={}, max_prs=5,
            repo_path=Path("/repo"), gh_token="tok",
        )
        assert seen == {"pr": 99, "repo_path": Path("/repo"), "gh_token": "tok"}
        assert result["prs_checked"] == 1
        assert result["prs_fixed"] == 1
        assert result["details"][0]["fix_applied"] is True
        assert result["details"][0]["fix_summary"] == "CI fix applied."

    def test_end_to_end_with_real_git(self, repos, log_calls) -> None:
        """find → status → fix → push, with only GitHub and the LLM mocked."""
        pr = _make_pr(sha=repos.head_sha)
        gh = _make_gh(pr, (_check_run(),))
        result = run_ci_monitor(gh, _make_llm(GOOD_FIX), REPO, profile={}, repo_path=repos.work)
        assert result["prs_checked"] == 1
        assert result["prs_fixed"] == 1
        assert result["details"][0] == {
            "pr_number": PR_NUMBER, "overall": "failure", "total_checks": 1,
            "failed_count": 1, "fix_applied": True,
            "fix_summary": "Set VALUE to the expected 3.",
        }
        assert _git("show", f"{BRANCH}:app.py", cwd=repos.origin) == "VALUE = 3"
