"""Git operations for the Implementation Agent.

Handles branch creation, file staging, committing, and pushing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from repokeeper.exceptions import GitOperationError, VerificationError

# Paths the implementation agent must never modify (GitHub Actions security)
BLOCKED_PREFIXES = (".github/workflows/",)


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


def apply_and_push(
    implementation: dict,
    gh_token: str,
    repository: str,
    profile: dict | None = None,
) -> tuple[str, list[str]]:
    """Create a branch, apply changes from the LLM response, commit and push.

    Args:
        implementation: LLM response dict with ``branch_name``, ``commit_message``,
                        ``changes``, and optionally ``new_files``.
        gh_token: GitHub token for authentication.
        repository: Repository slug (``owner/repo``).
        profile: Maintainer profile (used for pre-push verification).

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

    # Write modified files (filter blocked paths)
    for filepath, content in implementation.get("changes", {}).items():
        try:
            p = safe_repo_path(filepath)
        except ValueError as exc:
            print(f"[repokeeper] Skipping unsafe path: {exc}", file=sys.stderr)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Write new files (filter blocked paths)
    for filepath, content in implementation.get("new_files", {}).items():
        try:
            p = safe_repo_path(filepath)
        except ValueError as exc:
            print(f"[repokeeper] Skipping unsafe path: {exc}", file=sys.stderr)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    git("add", "-A")

    # Verify something changed
    diff = git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise GitOperationError("Agent produced no file changes.")

    if profile is not None:
        verification_results = run_verification_commands(profile)
        failure_message = format_verification_failures(verification_results)
        if failure_message:
            raise VerificationError(failure_message)

    git("commit", "-m", implementation["commit_message"])

    remote_url = f"https://x-access-token:{gh_token}@github.com/{repository}.git"
    git("remote", "set-url", "origin", remote_url)
    git("push", "origin", branch)

    return branch, diff.splitlines()
