"""Git operations for the Implementation Agent.

Handles branch creation, file staging, committing, and pushing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from repokeeper.exceptions import GitOperationError, VerificationError

# Paths the implementation agent must never modify (GitHub Actions security)
BLOCKED_PREFIXES = (".github/workflows/",)
PATCH_FIELDS = ("patch", "unified_diff")


def safe_repo_path(
    filepath: str,
    repo_root: str | Path = ".",
    blocked_prefixes: tuple[str, ...] = BLOCKED_PREFIXES,
) -> Path:
    """Resolve a user/LLM-provided path and ensure it stays inside the repo.

    Args:
        filepath: Repository-relative path from an LLM response.
        repo_root: Repository root directory.
        blocked_prefixes: Repository-relative prefixes to reject.

    Returns:
        Absolute path under ``repo_root``.

    Raises:
        ValueError: If the path is absolute, escapes the repo, or is blocked.
    """
    raw_path = Path(filepath)
    if raw_path.is_absolute():
        raise ValueError(f"Refusing absolute path: {filepath}")
    if any(part == ".." for part in raw_path.parts):
        raise ValueError(f"Refusing path traversal: {filepath}")

    normalized = raw_path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix)
           for prefix in blocked_prefixes):
        raise ValueError(f"Refusing blocked path: {filepath}")

    root = Path(repo_root).resolve()
    candidate = root / raw_path
    if candidate.exists():
        resolved = candidate.resolve()
    else:
        resolved = candidate.parent.resolve() / candidate.name

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing path outside repository: {filepath}") from exc

    return resolved


def git(*args: str, check: bool = True, capture: bool = False, cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process.

    Args:
        *args: Git subcommand and arguments, e.g. ``git("checkout", "-b", "my-branch")``.
        check: If True, raise on non-zero exit.
        capture: If True, capture stdout/stderr as text.
        cwd: Working directory for the git command.  Defaults to the current directory.

    Returns:
        CompletedProcess with ``stdout`` and ``stderr`` attributes.
    """
    kwargs: dict = {"check": check}
    if capture:
        kwargs.update({"capture_output": True, "text": True})
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    return subprocess.run(["git", *args], **kwargs)


def _implementation_patch(implementation: dict[str, Any]) -> str:
    """Return the first non-empty unified diff field from an implementation."""
    for field in PATCH_FIELDS:
        patch = implementation.get(field)
        if isinstance(patch, str) and patch.strip():
            return patch.strip() + "\n"
    return ""


def _strip_diff_prefix(path: str) -> str:
    """Normalize a path from a unified diff header."""
    path = path.strip()
    if path == "/dev/null":
        return path
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def extract_patch_paths(patch: str) -> list[str]:
    """Extract touched repository paths from a unified diff."""
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                for raw in parts[2:4]:
                    path = _strip_diff_prefix(raw)
                    if path != "/dev/null":
                        paths.add(path)
        elif line.startswith(("--- ", "+++ ")):
            path = _strip_diff_prefix(line[4:])
            if path != "/dev/null":
                paths.add(path)
    return sorted(paths)


def implementation_file_paths(implementation: dict[str, Any]) -> list[str]:
    """Return all file paths referenced by an implementation response."""
    paths: set[str] = set()
    for section in ("changes", "new_files"):
        value = implementation.get(section, {})
        if isinstance(value, dict):
            paths.update(str(path) for path in value)

    edits = implementation.get("edits", [])
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("path"), str):
                paths.add(edit["path"])

    patch = _implementation_patch(implementation)
    if patch:
        paths.update(extract_patch_paths(patch))

    return sorted(paths)


def _validate_patch_paths(patch: str, repo_root: str | Path = ".") -> None:
    """Reject unified diffs that touch unsafe or blocked paths."""
    for path in extract_patch_paths(patch):
        safe_repo_path(path, repo_root=repo_root)


def apply_unified_patch(patch: str, repo_root: str | Path = ".") -> list[str]:
    """Apply a unified diff after path and patch validation."""
    if not patch.strip():
        return []

    _validate_patch_paths(patch, repo_root)
    root = Path(repo_root)

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        output = (check.stdout + "\n" + check.stderr).strip()
        raise GitOperationError(f"Patch could not be applied:\n{output}")

    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    if applied.returncode != 0:
        output = (applied.stdout + "\n" + applied.stderr).strip()
        raise GitOperationError(f"Patch apply failed:\n{output}")

    return extract_patch_paths(patch)


def _apply_structured_edits(
    edits: object,
    repo_root: str | Path = ".",
) -> list[str]:
    """Apply exact find/replace edit operations from an implementation."""
    if not isinstance(edits, list):
        return []

    changed: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        filepath = edit.get("path")
        find = edit.get("find")
        replace = edit.get("replace")
        if not isinstance(filepath, str) or not isinstance(find, str):
            continue
        if not isinstance(replace, str):
            replace = ""

        p = safe_repo_path(filepath, repo_root=repo_root)
        if not p.exists():
            raise GitOperationError(f"Cannot edit missing file: {filepath}")

        content = p.read_text(encoding="utf-8")
        count = content.count(find)
        if count == 0:
            raise GitOperationError(f"Edit target not found in {filepath}")
        if count > 1 and not bool(edit.get("replace_all", False)):
            raise GitOperationError(
                f"Edit target is ambiguous in {filepath}; set replace_all=true"
            )

        new_content = content.replace(find, replace) if edit.get("replace_all") else content.replace(find, replace, 1)
        p.write_text(new_content, encoding="utf-8")
        changed.append(filepath)

    return changed


def _write_full_file_sections(
    implementation: dict[str, Any],
    repo_root: str | Path = ".",
) -> list[str]:
    """Write legacy full-file change sections from an implementation."""
    changed: list[str] = []
    for section in ("changes", "new_files"):
        files = implementation.get(section, {})
        if not isinstance(files, dict):
            continue
        for filepath, content in files.items():
            if not isinstance(filepath, str) or not isinstance(content, str):
                continue
            try:
                p = safe_repo_path(filepath, repo_root=repo_root)
            except ValueError as exc:
                print(f"[repokeeper] Skipping unsafe path: {exc}", file=sys.stderr)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            changed.append(filepath)
    return changed


def apply_implementation_changes(
    implementation: dict[str, Any],
    repo_root: str | Path = ".",
) -> list[str]:
    """Apply patch/tool/full-file changes from an implementation response.

    Preferred order is unified patch, then exact edit operations, then legacy
    full-file writes.  Keeping the full-file path makes existing workflows and
    tests compatible while allowing newer prompts to avoid rewriting big files.
    """
    changed: list[str] = []

    patch = _implementation_patch(implementation)
    if patch:
        changed.extend(apply_unified_patch(patch, repo_root=repo_root))

    changed.extend(_apply_structured_edits(implementation.get("edits", []), repo_root=repo_root))
    changed.extend(_write_full_file_sections(implementation, repo_root=repo_root))

    return sorted(set(changed))


def apply_and_push(
    implementation: dict,
    gh_token: str,
    repository: str,
    profile: dict | None = None,
    *,
    already_applied: bool = False,
    verify: bool = True,
) -> tuple[str, list[str]]:
    """Create a branch, apply changes from the LLM response, commit and push.

    Args:
        implementation: LLM response dict with ``branch_name``, ``commit_message``,
                        ``changes``, and optionally ``new_files``.
        gh_token: GitHub token for authentication.
        repository: Repository slug (``owner/repo``).
        profile: Maintainer profile (used for pre-push verification).
        already_applied: When True, only stage/commit/push current worktree changes.
        verify: Run verification commands before committing when ``profile`` is set.

    Returns:
        Tuple of ``(branch_name, list_of_changed_files)``.

    Raises:
        GitOperationError: If no file changes were produced.
        VerificationError: If pre-push verification fails.
    """
    from repokeeper.verifier import (  # local import to avoid circular dependency
        format_verification_failures,
        run_verification_commands,
    )

    branch = implementation["branch_name"]

    git("config", "user.email", "repokeeper[bot]@users.noreply.github.com")
    git("config", "user.name", "repokeeper[bot]")
    git("checkout", "-b", branch)

    if not already_applied:
        apply_implementation_changes(implementation)

    git("add", "-A")

    # Verify something changed
    diff = git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise GitOperationError("Agent produced no file changes.")

    if profile is not None and verify:
        verification_results = run_verification_commands(profile)
        failure_message = format_verification_failures(verification_results)
        if failure_message:
            raise VerificationError(failure_message)

    git("commit", "-m", implementation["commit_message"])

    remote_url = f"https://x-access-token:{gh_token}@github.com/{repository}.git"
    git("remote", "set-url", "origin", remote_url)
    git("push", "origin", branch)

    return branch, diff.splitlines()


def fix_and_push(
    implementation: dict,
    gh_token: str,
    repository: str,
    head_branch: str,
    pr_number: int,
) -> tuple[str, list[str]]:
    """Apply fixes to an existing PR branch and push.

    Fetches the PR head branch, applies changes, commits, and force-pushes
    back.  Unlike :func:`apply_and_push`, this does NOT create a new branch
    or a new PR — it modifies the existing PR in-place.

    Args:
        implementation: LLM response dict with ``commit_message``,
                        ``changes``, ``edits``, optionally ``new_files``.
        gh_token: GitHub token for authentication.
        repository: Repository slug (``owner/repo``).
        head_branch: The PR's head branch name (from PR data).
        pr_number: PR number (used for local temp branch name).

    Returns:
        Tuple of ``(branch_name, list_of_changed_files)``.

    Raises:
        GitOperationError: If no file changes were produced.
    """
    local_branch = f"repokeeper-fix-{pr_number}"

    git("config", "user.email", "repokeeper[bot]@users.noreply.github.com")
    git("config", "user.name", "repokeeper[bot]")

    # Fetch PR head for the target branch.
    git("fetch", "origin", "pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}")
    git("checkout", "-b", local_branch, "origin/pr/{pr_number}")

    apply_implementation_changes(implementation)

    git("add", "-A")
    diff = git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise GitOperationError("Fix produced no file changes.")

    git("commit", "-m", implementation["commit_message"])

    remote_url = f"https://x-access-token:{gh_token}@github.com/{repository}.git"
    git("remote", "set-url", "origin", remote_url)
    git("push", "origin", f"HEAD:{head_branch}")

    return head_branch, diff.splitlines()
