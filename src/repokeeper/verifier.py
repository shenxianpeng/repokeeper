"""Pre-commit verification for the Implementation Agent.

Runs configured or discovered verification commands (linter, tests)
before the agent opens a pull request, so broken code never leaves
the branch.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repokeeper.repo_context import SKIP_DIRS

# Seconds before a verification command is killed
VERIFICATION_TIMEOUT = 600
OUTPUT_EXCERPT_CHARS = 3000


@dataclass
class VerificationResult:
    """Result from a command run before committing agent changes."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def display_command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def _has_python_files(root: Path) -> bool:
    """Check if the repo contains Python source files."""
    return any(
        p.suffix == ".py" and not any(part in SKIP_DIRS for part in p.parts)
        for p in root.rglob("*.py")
    )


def _normalize_command(command: object) -> list[str] | None:
    """Normalize a verify_command entry to a list of strings.

    Accepts a space-separated string or a list of strings.
    Returns ``None`` for unsupported types.
    """
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return command
    return None


def discover_verification_commands(
    profile: dict,
    repo_path: Path = Path("."),
) -> list[list[str]]:
    """Discover commands that should pass before the agent opens a PR.

    Reads ``agent.verify_commands`` from the profile.  When not
    explicitly configured, auto-detects based on the project ecosystem.

    Args:
        profile: Maintainer profile dict.
        repo_path: Repository root path.

    Returns:
        List of command argument lists, e.g. ``[["ruff", "check", "."]]``.
    """
    agent_config = profile.get("agent", {})
    configured = agent_config.get("verify_commands")
    if configured is not None:
        if configured is False:
            return []
        if not isinstance(configured, list):
            return []
        normalized = [_normalize_command(command) for command in configured]
        return [cmd for cmd in normalized if cmd]

    style = profile.get("style", {})
    commands: list[list[str]] = []
    has_python = _has_python_files(repo_path)

    if style.get("linting", True) and has_python and shutil.which("ruff"):
        commands.append(["ruff", "check", "."])

    testing = style.get("testing", "pytest")
    if testing == "pytest" and (repo_path / "tests").exists() and shutil.which("pytest"):
        commands.append(["pytest", "tests"])
    elif testing == "go test" and (repo_path / "go.mod").exists() and shutil.which("go"):
        commands.append(["go", "test", "./..."])
    elif testing == "jest" and (repo_path / "package.json").exists() and shutil.which("npm"):
        commands.append(["npm", "test"])

    return commands


def run_verification_commands(
    profile: dict,
    repo_path: Path = Path("."),
) -> list[VerificationResult]:
    """Run configured or discovered verification commands.

    Args:
        profile: Maintainer profile dict.
        repo_path: Repository root path.

    Returns:
        List of VerificationResult objects, one per command.
    """
    results: list[VerificationResult] = []
    for command in discover_verification_commands(profile, repo_path):
        print(f"[repokeeper] Verifying: {' '.join(command)}", flush=True)
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=VERIFICATION_TIMEOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            results.append(
                VerificationResult(
                    command=command,
                    returncode=127,
                    stdout="",
                    stderr=f"Command not found: {exc.filename}",
                )
            )
            continue
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            results.append(
                VerificationResult(
                    command=command,
                    returncode=124,
                    stdout=out,
                    stderr=err + "\nCommand timed out after 600 seconds.",
                )
            )
            continue
        results.append(
            VerificationResult(
                command=command,
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        )
    return results


def _output_excerpt(result: VerificationResult, max_chars: int = OUTPUT_EXCERPT_CHARS) -> str:
    """Return a compact stdout/stderr excerpt for one verification result."""
    output = (result.stdout + "\n" + result.stderr).strip()
    if not output:
        return "(no output)"
    if len(output) <= max_chars:
        return output
    return f"[truncated to last {max_chars} chars]\n{output[-max_chars:]}"


def summarize_verification_results(results: list[VerificationResult]) -> list[dict[str, object]]:
    """Return structured verification evidence for PR bodies and comments."""
    return [
        {
            "command": result.display_command,
            "passed": result.passed,
            "returncode": result.returncode,
            "output_excerpt": _output_excerpt(result, max_chars=1200),
        }
        for result in results
    ]


def format_verification_report(results: list[VerificationResult]) -> str:
    """Format all verification results as a compact Markdown report."""
    if not results:
        return "Verification was not run."

    rows = ["| Command | Status | Exit |", "|---|---:|---:|"]
    for result in results:
        status = "passed" if result.passed else "failed"
        rows.append(f"| `{result.display_command}` | {status} | {result.returncode} |")

    failed = [result for result in results if not result.passed]
    if failed:
        rows.append("")
        rows.append("<details><summary>Failure output</summary>")
        for result in failed:
            rows.append("")
            rows.append(f"#### `{result.display_command}`")
            rows.append("")
            rows.append(f"```text\n{_output_excerpt(result)}\n```")
        rows.append("</details>")

    return "\n".join(rows)


def format_verification_failures(results: list[VerificationResult]) -> str:
    """Format failed verification results for workflow logs and issue comments.

    Args:
        results: List of VerificationResult objects.

    Returns:
        Formatted failure message, or empty string if all passed.
    """
    failed = [result for result in results if not result.passed]
    if not failed:
        return ""

    parts = ["Verification failed before creating a pull request."]
    for result in failed:
        parts.append(
            f"\nCommand: `{result.display_command}`\n"
            f"Exit code: {result.returncode}\n"
            f"Output:\n```\n{_output_excerpt(result)}\n```"
        )
    return "\n".join(parts)
