"""Tests for git operations used by the Implementation Agent (git_ops.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repokeeper import git_ops
from repokeeper.exceptions import GitOperationError, VerificationError
from repokeeper.git_ops import (
    _apply_structured_edits,
    _implementation_patch,
    _strip_diff_prefix,
    _write_full_file_sections,
    apply_and_push,
    apply_implementation_changes,
    apply_unified_patch,
    extract_patch_paths,
    fix_and_push,
    git,
    implementation_file_paths,
    safe_repo_path,
)


def _init_repo(workdir: Path, origin: str = "https://example.test/repo.git") -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(workdir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(workdir), "config", "user.email", "test@test.test"], check=True
    )
    subprocess.run(["git", "-C", str(workdir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(workdir), "remote", "add", "origin", origin], check=True)
    (workdir / "existing.py").write_text("old\n")
    subprocess.run(["git", "-C", str(workdir), "add", "existing.py"], check=True)
    subprocess.run(
        ["git", "-C", str(workdir), "commit", "-m", "init"], check=True, capture_output=True
    )
    return workdir


def _intercept_push(monkeypatch):
    """Let real git run but record ``push`` instead of reaching the network."""
    real_git = git_ops.git
    pushes: list[tuple[str, ...]] = []

    def fake_git(*args, **kwargs):
        if args and args[0] == "push":
            pushes.append(args)
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", fake_git)
    return pushes


# ── safe_repo_path ───────────────────────────────────────────────────────────


def test_safe_repo_path_strips_leading_dot_segments(tmp_path):
    resolved = safe_repo_path("././src/app.py", repo_root=tmp_path)

    assert resolved == tmp_path.resolve() / "src" / "app.py"


def test_safe_repo_path_rejects_blocked_prefix_after_normalization(tmp_path):
    with pytest.raises(ValueError, match="blocked path"):
        safe_repo_path("./.github/workflows/ci.yml", repo_root=tmp_path)
    with pytest.raises(ValueError, match="blocked path"):
        safe_repo_path(".github/workflows", repo_root=tmp_path)


def test_safe_repo_path_rejects_absolute_traversal_and_symlink_escape(tmp_path):
    with pytest.raises(ValueError, match="absolute path"):
        safe_repo_path("/etc/passwd", repo_root=tmp_path)
    with pytest.raises(ValueError, match="path traversal"):
        safe_repo_path("../outside.py", repo_root=tmp_path)

    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside repository"):
        safe_repo_path("link/file.py", repo_root=repo)


# ── git ──────────────────────────────────────────────────────────────────────


def test_git_runs_in_cwd_and_captures_output(tmp_path):
    _init_repo(tmp_path / "repo")

    result = git("rev-parse", "--abbrev-ref", "HEAD", capture=True, cwd=tmp_path / "repo")

    assert result.stdout.strip() == "main"
    assert git("status", "--porcelain", check=False, cwd=tmp_path / "repo").returncode == 0


# ── Patch helpers ────────────────────────────────────────────────────────────


def test_implementation_patch_prefers_first_non_empty_field():
    assert _implementation_patch({"patch": "  ", "unified_diff": "diff --git a/x b/x"}) == (
        "diff --git a/x b/x\n"
    )
    assert _implementation_patch({"patch": 12}) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/dev/null", "/dev/null"),
        ("a/src/app.py\t2024-01-01", "src/app.py"),
        ("b/README.md", "README.md"),
        ("plain.txt", "plain.txt"),
    ],
)
def test_strip_diff_prefix(raw, expected):
    assert _strip_diff_prefix(raw) == expected


def test_extract_patch_paths_handles_headers_and_dev_null():
    patch = "\n".join(
        [
            "diff --git a/new.py /dev/null",
            "diff --git a/short",
            "--- /dev/null",
            "+++ b/added.py",
            "--- a/changed.py",
            "+++ b/changed.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ]
    )

    assert extract_patch_paths(patch) == ["added.py", "changed.py", "new.py"]


def test_implementation_file_paths_collects_all_sections():
    paths = implementation_file_paths(
        {
            "changes": {"a.py": "x"},
            "new_files": "not-a-dict",
            "edits": [{"path": "b.py", "find": "x", "replace": "y"}, {"path": 3}, "junk"],
            "patch": "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1 +1 @@\n-1\n+2\n",
        }
    )

    assert paths == ["a.py", "b.py", "c.py"]


def test_implementation_file_paths_ignores_non_list_edits():
    assert implementation_file_paths({"edits": {"path": "x.py"}}) == []


# ── apply_unified_patch ──────────────────────────────────────────────────────


def test_apply_unified_patch_empty_patch_returns_nothing(tmp_path):
    assert apply_unified_patch("   \n", repo_root=tmp_path) == []


def test_apply_unified_patch_rejects_patch_that_does_not_apply(tmp_path):
    _init_repo(tmp_path / "repo")
    patch = (
        "diff --git a/existing.py b/existing.py\n"
        "--- a/existing.py\n"
        "+++ b/existing.py\n"
        "@@ -1 +1 @@\n"
        "-does-not-match\n"
        "+new\n"
    )

    with pytest.raises(GitOperationError, match="Patch could not be applied"):
        apply_unified_patch(patch, repo_root=tmp_path / "repo")


def test_apply_unified_patch_reports_apply_failure_after_check(tmp_path, monkeypatch):
    _init_repo(tmp_path / "repo")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        code = 0 if "--check" in cmd else 1
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr="apply exploded")

    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    patch = "diff --git a/existing.py b/existing.py\n--- a/existing.py\n+++ b/existing.py\n"

    with pytest.raises(GitOperationError, match="Patch apply failed:\napply exploded"):
        apply_unified_patch(patch, repo_root=tmp_path / "repo")

    assert len(calls) == 2


def test_apply_unified_patch_rejects_blocked_paths(tmp_path):
    patch = "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n"

    with pytest.raises(ValueError, match="blocked path"):
        apply_unified_patch(patch, repo_root=tmp_path)


# ── _apply_structured_edits ──────────────────────────────────────────────────


def test_apply_structured_edits_skips_malformed_entries(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")

    changed = _apply_structured_edits(
        [
            "junk",
            {"path": 1, "find": "x"},
            {"path": "a.py", "find": 2},
            {"path": "a.py", "find": "x = 1", "replace": None},
        ],
        repo_root=tmp_path,
    )

    assert changed == ["a.py"]
    assert (tmp_path / "a.py").read_text() == "\n"
    assert _apply_structured_edits({"path": "a.py"}, repo_root=tmp_path) == []


def test_apply_structured_edits_errors(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")

    with pytest.raises(GitOperationError, match="Cannot edit missing file: missing.py"):
        _apply_structured_edits([{"path": "missing.py", "find": "x"}], repo_root=tmp_path)
    with pytest.raises(GitOperationError, match="Edit target not found in a.py"):
        _apply_structured_edits([{"path": "a.py", "find": "nope"}], repo_root=tmp_path)
    with pytest.raises(GitOperationError, match="ambiguous"):
        _apply_structured_edits([{"path": "a.py", "find": "x = 1"}], repo_root=tmp_path)


def test_apply_structured_edits_replace_all(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")

    changed = _apply_structured_edits(
        [{"path": "a.py", "find": "x = 1", "replace": "y = 2", "replace_all": True}],
        repo_root=tmp_path,
    )

    assert changed == ["a.py"]
    assert (tmp_path / "a.py").read_text() == "y = 2\ny = 2\n"


# ── _write_full_file_sections ────────────────────────────────────────────────


def test_write_full_file_sections_skips_invalid_entries(tmp_path, capsys):
    changed = _write_full_file_sections(
        {
            "changes": "not-a-dict",
            "new_files": {
                "ok.py": "content",
                3: "ignored",
                "bad.py": None,
                "../escape.py": "nope",
            },
        },
        repo_root=tmp_path,
    )

    assert changed == ["ok.py"]
    assert (tmp_path / "ok.py").read_text() == "content"
    assert "Skipping unsafe path" in capsys.readouterr().err


# ── apply_implementation_changes ─────────────────────────────────────────────


def test_apply_implementation_changes_combines_all_strategies(tmp_path):
    _init_repo(tmp_path / "repo")
    repo = tmp_path / "repo"
    (repo / "edit.py").write_text("value = 1\n")
    patch = (
        "diff --git a/existing.py b/existing.py\n"
        "--- a/existing.py\n"
        "+++ b/existing.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    changed = apply_implementation_changes(
        {
            "patch": patch,
            "edits": [{"path": "edit.py", "find": "1", "replace": "2"}],
            "new_files": {"fresh.py": "print('hi')\n"},
        },
        repo_root=repo,
    )

    assert changed == ["edit.py", "existing.py", "fresh.py"]
    assert (repo / "existing.py").read_text() == "new\n"
    assert (repo / "edit.py").read_text() == "value = 2\n"


# ── apply_and_push ───────────────────────────────────────────────────────────


def test_apply_and_push_stages_untracked_files_and_runs_verification(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    (repo / "stray.txt").write_text("created by a tool\n")
    pushes = _intercept_push(monkeypatch)
    ran: list[dict] = []

    def fake_run(profile, repo_path=Path(".")):
        ran.append(profile)
        return []

    monkeypatch.setattr("repokeeper.verifier.run_verification_commands", fake_run)
    impl = {
        "branch_name": "repokeeper/issue-9",
        "commit_message": "fix: thing",
        "changes": {"existing.py": "new\n"},
        "new_files": {"../escape.py": "never written"},
    }

    branch, files = apply_and_push(impl, "token", "owner/repo", profile={"agent": {}})

    assert branch == "repokeeper/issue-9"
    assert files == ["existing.py", "stray.txt"]
    assert ran == [{"agent": {}}]
    assert pushes == [("push", "origin", "repokeeper/issue-9")]
    remote = git("remote", "get-url", "origin", capture=True).stdout.strip()
    assert remote == "https://x-access-token:token@github.com/owner/repo.git"
    log = git("log", "-1", "--pretty=%s", capture=True).stdout.strip()
    assert log == "fix: thing"


def test_apply_and_push_raises_when_nothing_changed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    _intercept_push(monkeypatch)

    with pytest.raises(GitOperationError, match="no file changes"):
        apply_and_push({"branch_name": "b", "commit_message": "m"}, "token", "owner/repo")


def test_apply_and_push_already_applied_skips_verification(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    (repo / "existing.py").write_text("changed\n")
    pushes = _intercept_push(monkeypatch)

    def fail(profile, repo_path=Path(".")):
        raise AssertionError("verification must not run when verify=False")

    monkeypatch.setattr("repokeeper.verifier.run_verification_commands", fail)

    branch, files = apply_and_push(
        {"branch_name": "b", "commit_message": "m"},
        "token",
        "owner/repo",
        profile={},
        already_applied=True,
        verify=False,
    )

    assert (branch, files) == ("b", ["existing.py"])
    assert len(pushes) == 1


def test_apply_and_push_raises_verification_error(tmp_path, monkeypatch):
    from repokeeper.verifier import VerificationResult

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    (repo / "existing.py").write_text("changed\n")
    pushes = _intercept_push(monkeypatch)
    monkeypatch.setattr(
        "repokeeper.verifier.run_verification_commands",
        lambda profile, repo_path=Path("."): [VerificationResult(["pytest"], 1, "", "boom")],
    )

    with pytest.raises(VerificationError, match="Verification failed"):
        apply_and_push(
            {"branch_name": "b", "commit_message": "m"},
            "token",
            "owner/repo",
            profile={},
            already_applied=True,
        )

    assert pushes == []


# ── fix_and_push ─────────────────────────────────────────────────────────────


def test_fix_and_push_fetches_pr_head_and_pushes_to_head_branch(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    repo = _init_repo(tmp_path / "repo", origin=str(origin))
    subprocess.run(
        ["git", "-C", str(repo), "push", "origin", "main"], check=True, capture_output=True
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(origin), "update-ref", "refs/pull/7/head", head], check=True)
    monkeypatch.chdir(repo)
    (repo / "stray.txt").write_text("tool output\n")
    pushes = _intercept_push(monkeypatch)

    branch, files = fix_and_push(
        {
            "commit_message": "fix: ci",
            "changes": {"existing.py": "fixed\n"},
            "new_files": {"../escape.py": "never written"},
        },
        "token",
        "owner/repo",
        "feature/pr-branch",
        7,
    )

    assert branch == "feature/pr-branch"
    assert files == ["existing.py", "stray.txt"]
    assert pushes == [("push", "origin", "HEAD:feature/pr-branch")]
    current = git("rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip()
    assert current == "repokeeper-fix-7"
    remote = git("remote", "get-url", "origin", capture=True).stdout.strip()
    assert remote == "https://x-access-token:token@github.com/owner/repo.git"


def test_fix_and_push_already_checked_out_raises_without_changes(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    pushes = _intercept_push(monkeypatch)

    with pytest.raises(GitOperationError, match="Fix produced no file changes"):
        fix_and_push(
            {"commit_message": "fix: nothing"},
            "token",
            "owner/repo",
            "feature",
            3,
            already_checked_out=True,
        )

    assert pushes == []
    assert git("rev-parse", "--abbrev-ref", "HEAD", capture=True).stdout.strip() == "main"
