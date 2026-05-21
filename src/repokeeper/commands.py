"""Dedicated ``/repokeeper`` comment command parser.

Centralises all comment-based command handling so every module uses the
same parser.  Workflows and the CLI both trigger via :func:`parse_commands`.

Commands supported::

    /repokeeper go          — implement the issue / fix the PR
    /repokeeper review      — run code review
    /repokeeper describe    — generate PR description
    /repokeeper label       — auto-label the issue or PR
    /repokeeper fix         — alias for ``go`` on a PR (conversational fix)
    /repokeeper status      — show what RepoKeeper has done on this item
    /repokeeper help        — list available commands
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_COMMAND_HELP = {
    "go": (
        "Ask RepoKeeper to implement this issue. "
        "The agent reads the codebase, writes code, runs verification, and opens a PR. "
        "On a PR, this acts as a fix request — RepoKeeper reads your feedback and pushes "
        "fixes to the same branch."
    ),
    "review": (
        "Request a code review. RepoKeeper reads the PR diff and posts inline "
        "line-level suggestions with severity indicators."
    ),
    "describe": (
        "Generate a structured PR description from the diff. Updates the PR title "
        "if the LLM suggests a better one."
    ),
    "label": (
        "Auto-label this issue or PR. RepoKeeper picks labels from the existing set "
        "and creates new ones only when needed, matching your naming convention."
    ),
    "fix": (
        "Fix an existing PR based on review feedback. Same as `/repokeeper go` on a PR. "
        "Repokeeper reads your comments, understands the requested changes, and pushes "
        "fixes to the same branch."
    ),
    "status": (
        "Show what RepoKeeper has done on this issue or PR — any comments posted, "
        "labels applied, reviews submitted, or PRs created."
    ),
    "help": "Show this help message.",
}

# Commands that require maintainer/owner permission (enforced by workflow YAML).
_RESTRICTED_COMMANDS = frozenset({"go", "fix", "review", "describe", "label", "help", "status"})


@dataclass
class ParsedCommand:
    """A single parsed ``/repokeeper`` command from a comment."""

    verb: str           # "go" | "review" | "describe" | "label" | "fix" | "status" | "help"
    args: list[str]     # positional arguments after the verb
    raw: str            # original command text
    line_index: int     # which line of the comment this command appeared on


@dataclass
class CommandResult:
    """All commands found in a single comment, plus metadata."""

    commands: list[ParsedCommand] = field(default_factory=list)
    has_any: bool = False
    has_restricted: bool = False   # True if any command needs maintainer permission
    first_verb: str = ""


def parse_commands(comment_body: str) -> CommandResult:
    """Parse all ``/repokeeper`` commands from a GitHub comment body.

    Recognises lines that begin with ``/repokeeper`` (case-insensitive,
    leading whitespace allowed).  Each such line is split into a verb
    and optional arguments.

    Args:
        comment_body: Full text of a GitHub comment.

    Returns:
        ``CommandResult`` with all commands found and metadata flags.
    """
    result = CommandResult()
    if not comment_body:
        return result

    for idx, raw_line in enumerate(comment_body.splitlines()):
        line = raw_line.strip()
        if not line.lower().startswith("/repokeeper"):
            continue

        # Remove the "/repokeeper" prefix
        rest = line[len("/repokeeper"):].strip()
        if not rest:
            continue

        parts = rest.split()
        verb = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if verb not in _RESTRICTED_COMMANDS:
            continue

        parsed = ParsedCommand(
            verb=verb,
            args=args,
            raw=line,
            line_index=idx,
        )
        result.commands.append(parsed)

        if not result.first_verb:
            result.first_verb = verb

    result.has_any = len(result.commands) > 0
    result.has_restricted = any(
        cmd.verb in _RESTRICTED_COMMANDS for cmd in result.commands
    )

    return result


def has_trigger(comment_body: str) -> bool:
    """Quick check: does this comment contain any ``/repokeeper`` command?

    Faster than :func:`parse_commands` and suitable for workflow conditionals.

    Args:
        comment_body: Full text of a GitHub comment.

    Returns:
        ``True`` if one or more commands are present.
    """
    for line in comment_body.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("/repokeeper") and len(stripped) > len("/repokeeper"):
            return True
    return False


def help_text() -> str:
    """Return a markdown help block listing all available commands.

    Returns:
        Markdown string suitable for posting as a comment.
    """
    lines = [
        "## 🤖 RepoKeeper Commands",
        "",
        "Comment on an issue or PR with any of these commands:",
        "",
        "| Command | Description |",
        "|---------|-------------|",
    ]
    for verb in ["go", "review", "describe", "label", "fix", "status", "help"]:
        desc = _COMMAND_HELP.get(verb, "")
        lines.append(f"| `/repokeeper {verb}` | {desc} |")
    lines.extend([
        "",
        "**Triggers:**",
        "- Issues: `/repokeeper go` / `agent-todo` label → implementation PR",
        "- PRs: `/repokeeper go` / `agent-fix` label → conversational PR fix",
        "- PRs: `/repokeeper review` / `agent-review` label → inline code review",
        "- PRs: `/repokeeper describe` → generated PR description",
        "- Issues/PRs: `/repokeeper label` → auto-labeling",
        "",
        "---",
        "*[RepoKeeper](https://github.com/shenxianpeng/repokeeper) — AI-powered OSS maintenance*",
    ])
    return "\n".join(lines)


def build_status_comment(
    issue_data: dict[str, Any],
    repo_activity: dict[str, Any],
) -> str:
    """Build a status comment summarising RepoKeeper's activity on an item.

    Args:
        issue_data: Issue/PR data from :func:`get_issue_data` or
                   :func:`get_pr_data`.
        repo_activity: Dict with optional keys:
            - ``agent_comments``: list of comment dicts (author, body)
            - ``prs_created``: list of PR URLs
            - ``reviews_posted``: list of review data
            - ``labels_applied``: list of label names
            - ``fixes_applied``: list of fix summaries

    Returns:
        Markdown status comment.
    """
    lines = [
        "## 🤖 RepoKeeper Status",
        "",
        f"**[#{issue_data.get('number', '?')} {issue_data.get('title', '')}]"
        f"(https://github.com/{issue_data.get('repo', '')}/issues/{issue_data.get('number', '')})**",
        "",
    ]

    agent_comments = repo_activity.get("agent_comments") or []
    if agent_comments:
        lines.append("### Activity")
        lines.append("")
        for c in agent_comments[-5:]:
            preview = c.get("body", "")[:150]
            lines.append(f"- 🤖 RepoKeeper commented: _{preview}..._")
        lines.append("")

    prs_created = repo_activity.get("prs_created") or []
    if prs_created:
        lines.append("### PRs Created")
        for pr_url in prs_created:
            lines.append(f"- {pr_url}")
        lines.append("")

    reviews_posted = repo_activity.get("reviews_posted") or []
    if reviews_posted:
        lines.append("### Reviews")
        for review in reviews_posted:
            lines.append(f"- {review}")
        lines.append("")

    labels_applied = repo_activity.get("labels_applied") or []
    if labels_applied:
        lines.append("### Labels Applied")
        lines.append(f"- {', '.join(f'`{lb}`' for lb in labels_applied)}")
        lines.append("")

    fixes_applied = repo_activity.get("fixes_applied") or []
    if fixes_applied:
        lines.append("### Fixes Applied")
        for fix in fixes_applied:
            lines.append(f"- {fix}")
        lines.append("")

    if not any([agent_comments, prs_created, reviews_posted, labels_applied, fixes_applied]):
        lines.append("No RepoKeeper activity yet on this item.")
        lines.append("")
        lines.append(
            "Use `/repokeeper go` to request implementation, "
            "`/repokeeper review` for code review, or "
            "`/repokeeper label` for auto-labeling."
        )
        lines.append("")

    lines.extend([
        "---",
        "*Use `/repokeeper help` for a list of all commands.*",
    ])
    return "\n".join(lines)


def handle_help_command(gh_obj: Any, target_type: str = "issue") -> bool:
    """Post the help text as a comment on a GitHub issue or PR.

    Args:
        gh_obj: PyGithub Issue or PullRequest object.
        target_type: ``"issue"`` or ``"pr"``.

    Returns:
        True if the comment was posted.
    """
    try:
        gh_obj.create_comment(help_text())
        return True
    except Exception:
        return False
