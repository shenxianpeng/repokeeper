#!/usr/bin/env python3
"""
Module 3: Implementation Agent

Triggered when an issue is labeled ``agent-todo`` or when a maintainer
comments ``@repokeeper go``. Reads the codebase + issue description,
generates an implementation plan, submits a PR with a summary.

Uses the Maintainer Profile (Module 4) for code style, tone, PR standards,
tech stack preferences, and skip keywords.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from github import Github
from github.GithubException import GithubException, UnknownObjectException

from repokeeper.git_ops import (
    BLOCKED_PREFIXES,  # noqa: F401  # re-export
    apply_and_push,  # noqa: F401  # re-export
)
from repokeeper.git_ops import git as _git  # noqa: F401  # re-export
from repokeeper.llm_client import LLMClient, TokenUsage
from repokeeper.logs import get_logger
from repokeeper.profile import load_profile
from repokeeper.repo_context import (  # noqa: F401  # re-export
    MAX_FILE_SIZE,
    MAX_FILES,
    SKIP_DIRS,
    SOURCE_EXTENSIONS,
    build_context_string,
    collect_repo_files,
)
from repokeeper.verifier import (  # noqa: F401  # re-export
    VerificationResult,
    discover_verification_commands,
    format_verification_failures,
    run_verification_commands,
)

logger = get_logger("agent")

# ─── GitHub helpers ──────────────────────────────────────────────────────────


def get_issue_data(repo: Any, number: int) -> dict[str, Any]:
    """Extract structured data from a GitHub issue.

    Args:
        repo: PyGithub Repository object.
        number: Issue number.

    Returns:
        Dict with ``number``, ``title``, ``body``, ``labels``, and ``comments``.
    """
    issue = repo.get_issue(number)
    recent_comments = list(issue.get_comments())[-5:]
    return {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body or "(no description)",
        "labels": [lb.name for lb in issue.labels],
        "comments": [
            {"author": c.user.login, "body": c.body}
            for c in recent_comments
        ],
    }


def post_comment(issue_obj: Any, message: str) -> None:
    """Post a comment on a GitHub issue.

    Args:
        issue_obj: PyGithub Issue object.
        message: Markdown message body.
    """
    issue_obj.create_comment(message)


# ─── Skip keyword check ─────────────────────────────────────────────────────


def check_skip_keywords(issue_data: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """Check if the issue matches any skip keywords from the profile.

    Args:
        issue_data: Issue data dict from :func:`get_issue_data`.
        profile: Maintainer profile dict.

    Returns:
        Matched keyword if found, ``None`` otherwise.
    """
    skip_keywords = profile.get("agent", {}).get("skip_keywords", [])
    if not skip_keywords:
        return None

    combined = f"{issue_data['title']} {issue_data['body']}".lower()
    for kw in skip_keywords:
        if kw.lower() in combined:
            return kw  # type: ignore[no-any-return]
    return None


# ─── Implementation validation ──────────────────────────────────────────────


def strip_blocked_paths(implementation: dict[str, Any]) -> list[str]:
    """Remove blocked paths from implementation in-place.

    Args:
        implementation: LLM response dict (mutated).

    Returns:
        List of stripped path names (for warnings).
    """
    stripped: list[str] = []
    for section in ("changes", "new_files"):
        section_dict = implementation.get(section, {})
        blocked = [k for k in section_dict if k.startswith(BLOCKED_PREFIXES)]
        for k in blocked:
            del section_dict[k]
            stripped.append(k)
    return stripped


def validate_implementation(
    implementation: dict[str, Any], profile: dict[str, Any]
) -> list[str]:
    """Validate an implementation against profile constraints.

    Args:
        implementation: LLM response dict.
        profile: Maintainer profile dict.

    Returns:
        List of validation issues (empty = valid).
    """
    issues: list[str] = []

    pr_config = profile.get("pr", {})

    # Check max files
    max_files = pr_config.get("max_files_per_pr", 15)
    total_files = len(implementation.get("changes", {})) + len(
        implementation.get("new_files", {})
    )
    if total_files > max_files:
        issues.append(f"Implementation touches {total_files} files (max: {max_files})")

    # Check branch naming
    branch = implementation.get("branch_name", "")
    if not branch.startswith("repokeeper/"):
        issues.append("branch_name must start with 'repokeeper/'")

    return issues


# ─── LLM interaction ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert software engineer acting as an open source maintainer's AI deputy.
Your job is to implement a GitHub issue by making precise, minimal code changes.

Rules:
- Follow the existing code style exactly.
- Only change what is necessary to close the issue.
- Do not add unrequested features, refactors, or comments.
- Respect the maintainer's tech stack preferences (preferred/avoid lists).
- If the issue is unclear or unsafe to implement autonomously, set "skip": true and explain why.
- If the issue body or comments contain any skip keywords from the maintainer, skip.
- Never modify files under .github/workflows/ (blocked by GitHub security).

Respond with a single valid JSON object — no markdown fences, no explanation outside JSON:

{
  "skip": false,
  "reason": "",
  "summary": "One sentence describing what was implemented.",
  "branch_name": "repokeeper/issue-<number>-short-slug",
  "commit_message": "type: short imperative message",
  "changes": {
    "path/to/existing/file.py": "<complete new file content>"
  },
  "new_files": {
    "path/to/new/file.py": "<complete new file content>"
  }
}

- branch_name must start with "repokeeper/".
- "changes" = files to modify (provide FULL file content, not diffs).
- "new_files" = files to create.
- Both can be empty objects if nothing is needed on that side.
"""


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse LLM JSON output with resilience to common formatting issues.

    Handles markdown fences, code block markers, and attempts basic repair
    for unterminated strings.

    Args:
        raw: Raw text content from LLM response.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If the response could not be parsed as JSON.
    """
    text = raw.strip()

    # ── Strip markdown code fences ──
    fence_pattern = r"```(?:json)?\s*\n(.*?)\n```"
    m = re.search(fence_pattern, text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    elif text.startswith("```"):
        inner = text.split("```", 2)
        if len(inner) >= 2:
            candidate = inner[1]
            if candidate.startswith("json"):
                candidate = candidate[4:]
            text = candidate.strip()

    # ── Try direct parse ──
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError as err:
        first_error = err

    # ── Try extracting the outermost JSON object ──
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        extracted = text[start : end + 1]
        try:
            return json.loads(extracted)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    # ── Try fixing common unterminated string ──
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        try:
            return json.loads(repaired)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Failed to parse LLM JSON response. First error: {first_error}\n"
        f"Raw response (last 2000 chars): ...{text[-2000:]}"
    )


def _repair_truncated_json(text: str) -> str | None:
    """Attempt to repair a truncated/incomplete JSON string.

    Tries to find the last complete key-value pair and close the object.

    Args:
        text: Possibly truncated JSON string.

    Returns:
        Repaired string or ``None`` if repair is not possible.
    """
    bracket_stack: list[str] = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            bracket_stack.append(ch)
        elif ch == "}":
            if bracket_stack and bracket_stack[-1] == "{":
                bracket_stack.pop()
        elif ch == "]":
            if bracket_stack and bracket_stack[-1] == "[":
                bracket_stack.pop()

    if not bracket_stack and not in_string:
        return None

    repaired = text.rstrip()
    if in_string:
        repaired += '"'

    for open_ch in reversed(bracket_stack):
        if open_ch == "{":
            repaired += "}"
        elif open_ch == "[":
            repaired += "]"

    return repaired if repaired != text else None


def call_llm(
    issue_data: dict[str, Any],
    context_str: str,
    profile: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[dict[str, Any], TokenUsage]:
    """Call the LLM to generate an implementation plan.

    Args:
        issue_data: Structured issue data from :func:`get_issue_data`.
        context_str: Repository source context string.
        profile: Maintainer profile dict.
        llm_client: Unified LLM client.

    Returns:
        Tuple of (parsed JSON response, token usage info).

    Raises:
        RuntimeError: If JSON parsing fails after retries.
    """
    from repokeeper.llm_client import TokenUsage

    style_config = profile.get("style", {})
    code_style = style_config.get("code_style", "follow existing patterns")
    tech_config = profile.get("tech", {})
    preferred = tech_config.get("preferred", [])
    avoided = tech_config.get("avoid", [])

    tech_note = ""
    if preferred:
        tech_note += f"\n- Preferred tech stack: {', '.join(preferred)}"
    if avoided:
        tech_note += f"\n- Tech stack to avoid: {', '.join(avoided)}"

    user_prompt = f"""\
## Issue #{issue_data['number']}: {issue_data['title']}

{issue_data['body']}

## Recent discussion
{json.dumps(issue_data['comments'], indent=2)}

## Maintainer style preference
{code_style}
{tech_note}

## Repository source files
{context_str}
"""

    messages = [
        {"role": "user", "content": user_prompt},
    ]

    model = profile.get("agent", {}).get("model", "deepseek-chat")
    temperature = profile.get("agent", {}).get("temperature", 0.1)
    stream = profile.get("agent", {}).get("stream", os.environ.get("CI") is None)

    max_retries = 2
    total_usage = TokenUsage(model=model)

    for attempt in range(max_retries + 1):
        # Use stream=True on the first attempt so user sees progress;
        # disable on retries to reduce noise.
        use_stream = stream and attempt == 0

        response = llm_client.chat(
            system=SYSTEM_PROMPT,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=8000,
            stream=use_stream,
        )

        total_usage.prompt_tokens += response.usage.prompt_tokens
        total_usage.completion_tokens += response.usage.completion_tokens
        total_usage.total_tokens += response.usage.total_tokens
        total_usage.cost_usd += response.usage.cost_usd

        raw = response.content.strip()

        try:
            return _parse_llm_json(raw), total_usage
        except ValueError as err:
            if attempt < max_retries:
                logger.warning(
                    "JSON parse failed (attempt %d), retrying...", attempt + 1,
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response contained invalid JSON. "
                        f"Error: {err}\n\n"
                        "Please fix the JSON and respond with ONLY the corrected "
                        "JSON object. Ensure all strings are properly escaped."
                    ),
                })
            else:
                raise RuntimeError(
                    f"LLM JSON parsing failed after {max_retries + 1} attempts. "
                    f"Last error: {err}"
                ) from err

    # Unreachable — satisfy type checker
    raise RuntimeError("LLM JSON parsing failed")


# ─── PR creation ─────────────────────────────────────────────────────────────


def create_pr(
    repo: Any,
    issue_data: dict[str, Any],
    implementation: dict[str, Any],
    branch: str,
    changed_files: list[str],
    profile: dict[str, Any],
) -> str:
    """Create a GitHub pull request for the agent's implementation.

    Args:
        repo: PyGithub Repository object.
        issue_data: Issue data dict.
        implementation: LLM response dict.
        branch: Branch name.
        changed_files: List of changed file paths.
        profile: Maintainer profile.

    Returns:
        PR URL.

    Raises:
        RuntimeError: If GitHub refuses to create the PR (e.g. permissions).
    """
    files_list = "\n".join(f"- `{f}`" for f in changed_files)

    body = f"""\
## 🤖 RepoKeeper Implementation

Closes #{issue_data['number']}

### Summary
{implementation['summary']}

### Changed files
{files_list}

---
*Generated by RepoKeeper · Please review carefully before merging.*
"""
    try:
        pr = repo.create_pull(
            title=f"{implementation['commit_message']} (#{issue_data['number']})",
            body=body,
            head=branch,
            base=repo.default_branch,
        )
    except GithubException as exc:
        if exc.status == 403 and "not permitted to create" in str(exc):
            raise RuntimeError(
                "GitHub refused to create the pull request. Enable repository Actions "
                "'Allow GitHub Actions to create and approve pull requests', or set "
                "REPOKEEPER_GITHUB_TOKEN to a token with contents and pull request "
                "write access."
            ) from exc
        raise
    return str(pr.html_url)


# ─── Main entry point ────────────────────────────────────────────────────────


def run_agent(
    gh_token: str | None = None,
    repository: str | None = None,
    issue_number: int | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the Implementation Agent end-to-end.

    Returns:
        Dict with result info (``pr_url``, ``skip`` reason, ``error``).
    """
    gh_token = (
        gh_token
        or os.environ.get("REPOKEEPER_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    repository = repository or os.environ.get("GITHUB_REPOSITORY")
    issue_number = issue_number or int(os.environ.get("ISSUE_NUMBER", "0"))
    llm_api_key = (
        llm_api_key
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    llm_base_url = llm_base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    missing = []
    if not gh_token:
        missing.append("GITHUB_TOKEN or REPOKEEPER_GITHUB_TOKEN")
    if not repository:
        missing.append("GITHUB_REPOSITORY or --repo")
    if not issue_number:
        missing.append("ISSUE_NUMBER or --issue")
    if not llm_api_key:
        missing.append("DEEPSEEK_API_KEY or OPENAI_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")

    profile = load_profile(profile_path)

    # Check if agent is enabled
    if not profile.get("agent", {}).get("implement", True):
        return {"skip": True, "reason": "Agent implementation disabled in profile.", "pr_url": None}

    gh = Github(gh_token)
    llm = LLMClient(api_key=llm_api_key, base_url=llm_base_url)
    assert repository is not None  # validated above
    try:
        repo = gh.get_repo(repository)
    except UnknownObjectException:
        fallback = os.environ.get("GITHUB_TOKEN")
        if fallback and fallback != gh_token:
            logger.info("Primary token unauthorized, falling back to GITHUB_TOKEN")
            gh = Github(fallback)
            repo = gh.get_repo(repository)
            gh_token = fallback
        else:
            raise
    issue_obj = repo.get_issue(issue_number)
    issue_data = get_issue_data(repo, issue_number)

    logger.info("Issue #%d: %s", issue_number, issue_data["title"])

    # Check skip keywords early
    skip_kw = check_skip_keywords(issue_data, profile)
    if skip_kw:
        logger.info("Skipping: matched skip keyword '%s'", skip_kw)
        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** skipped this issue: matched skip keyword `{skip_kw}`.\n\n"
            f"Remove the keyword or clarify if you still want automatic implementation.",
        )
        return {"skip": True, "reason": f"Skip keyword: {skip_kw}", "pr_url": None}

    post_comment(issue_obj, "🤖 **RepoKeeper** is analyzing this issue — working on an implementation...")

    try:
        # Collect repo context
        logger.info("Collecting repository context...")
        max_context = profile.get("agent", {}).get("max_context_files", 40)
        files = collect_repo_files(max_files=max_context)
        logger.info("Loaded %d files", len(files))
        context_str = build_context_string(files)

        # Call LLM
        model = profile.get("agent", {}).get("model", "deepseek-chat")
        logger.info("Calling LLM (%s)...", model)
        result, usage = call_llm(issue_data, context_str, profile, llm)

        # Log token usage
        if usage.total_tokens > 0:
            logger.info(
                "LLM usage: %d tokens · $%.6f (model: %s)",
                usage.total_tokens, usage.cost_usd, usage.model,
            )

        # Agent decided to skip
        if result.get("skip"):
            reason = result.get("reason", "No reason provided.")
            logger.info("Skipping: %s", reason)
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** decided not to implement this automatically:\n\n> {reason}\n\n"
                f"Please implement manually or clarify the issue.",
            )
            return {"skip": True, "reason": reason, "pr_url": None}

        # Strip blocked paths (auto-fix, warn but don't skip)
        stripped = strip_blocked_paths(result)
        if stripped:
            logger.info("Stripped blocked paths: %s", ", ".join(stripped))
            if not result.get("changes") and not result.get("new_files"):
                post_comment(
                    issue_obj,
                    "🤖 **RepoKeeper** skipped: all planned changes were in blocked paths"
                    f" ({', '.join(stripped)}).\n\n"
                    "Please implement manually or clarify the issue.",
                )
                return {
                    "skip": True,
                    "reason": f"All changes in blocked paths: {', '.join(stripped)}",
                    "pr_url": None,
                }

        # Validate against profile constraints
        validation_issues = validate_implementation(result, profile)
        if validation_issues:
            issues_str = "\n".join(f"- {v}" for v in validation_issues)
            logger.warning("Validation issues:\n%s", issues_str)
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** found issues with the implementation plan:\n\n{issues_str}\n\n"
                f"Please review and adjust the profile constraints or the issue scope.",
            )
            return {
                "skip": True,
                "reason": f"Validation failed: {issues_str}",
                "pr_url": None,
            }

        logger.info("Plan: %s", result["summary"])

        # Resolve branch name collisions — append timestamp if branch exists
        branch_name = result.get("branch_name", "repokeeper/unknown")
        result["branch_name"] = _resolve_branch_collision(branch_name, repo)

        # Apply changes and push (gh_token and repository are guaranteed non-None
        # after the validation above, but mypy needs the hint).
        assert gh_token is not None
        assert repository is not None
        branch, changed_files = apply_and_push(
            result, gh_token, repository, profile
        )

        # Create PR
        pr_url = create_pr(repo, issue_data, result, branch, changed_files, profile)

        cost_note = ""
        if usage.cost_usd > 0:
            cost_note = f"\n**Cost:** ~${usage.cost_usd:.6f} ({usage.total_tokens} tokens, {usage.model})"

        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** finished implementation.\n\n"
            f"**PR:** {pr_url}\n\n"
            f"**Summary:** {result['summary']}"
            f"{cost_note}\n\n"
            f"Please review the changes before merging.",
        )
        logger.info("Done — PR: %s", pr_url)
        return {"skip": False, "reason": "", "pr_url": pr_url}

    except Exception as exc:
        logger.error("ERROR: %s", exc)
        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** encountered an error:\n\n```\n{exc}\n```\n\n"
            f"Check the [workflow logs]({repo.html_url}/actions) for details.",
        )
        raise


# ─── CLI entry point (backwards-compatible) ──────────────────────────────────


def _resolve_branch_collision(branch_name: str, repo: Any) -> str:
    """Ensure the branch name is unique by appending a timestamp if needed.

    Checks existing branches on the remote.  If a branch with the same name
    already exists, appends ``-YYYYMMDDHHMMSS`` to make it unique.

    Args:
        branch_name: Proposed branch name from the LLM.
        repo: PyGithub Repository object.

    Returns:
        A unique branch name.
    """
    try:
        existing = {b.name for b in repo.get_branches()}
    except Exception:
        # Can't list branches (e.g. token scope); append timestamp anyway
        from datetime import datetime as _dt

        ts = _dt.now().strftime("%Y%m%d%H%M%S")
        return f"{branch_name}-{ts}"

    if branch_name not in existing:
        return branch_name

    from datetime import datetime as _dt

    ts = _dt.now().strftime("%Y%m%d%H%M%S")
    resolved = f"{branch_name}-{ts}"
    logger.info("Branch '%s' exists, using '%s'", branch_name, resolved)
    return resolved


if __name__ == "__main__":
    run_agent()
