"""Git operations for the Implementation Agent.

Handles branch creation, file staging, committing, and pushing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Paths the agent must never modify (GitHub Actions security)
BLOCKED_PREFIXES = (".github/workflows/",)


def git(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process.

    Args:
        *args: Git subcommand and arguments, e.g. ``git("checkout", "-b", "my-branch")``.
        check: If True, raise on non-zero exit.
        capture: If True, capture stdout/stderr as text.

    Returns:
        CompletedProcess with ``stdout`` and ``stderr`` attributes.
    """
    kwargs: dict = {"check": check}
    if capture:
        kwargs.update({"capture_output": True, "text": True})
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
        RuntimeError: If no file changes were produced or verification fails.
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
        if filepath.startswith(BLOCKED_PREFIXES):
            print(f"[repokeeper] Skipping blocked path: {filepath}", file=sys.stderr)
            continue
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Write new files (filter blocked paths)
    for filepath, content in implementation.get("new_files", {}).items():
        if filepath.startswith(BLOCKED_PREFIXES):
            print(f"[repokeeper] Skipping blocked path: {filepath}", file=sys.stderr)
            continue
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    git("add", "-A")

    # Verify something changed
    diff = git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise RuntimeError("Agent produced no file changes.")

    if profile is not None:
        verification_results = run_verification_commands(profile)
        failure_message = format_verification_failures(verification_results)
        if failure_message:
            raise RuntimeError(failure_message)

    git("commit", "-m", implementation["commit_message"])

    remote_url = f"https://x-access-token:{gh_token}@github.com/{repository}.git"
    git("remote", "set-url", "origin", remote_url)
    git("push", "origin", branch)

    return branch, diff.splitlines()
