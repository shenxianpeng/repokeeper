#!/usr/bin/env python3
"""
Module 3: Implementation Agent

Triggered when an issue is labeled ``agent-todo`` or when a maintainer
comments ``@repokeeper go``. Reads the codebase + issue description,
generates an implementation plan, submits a PR with a summary.

Uses the Maintainer Profile (Module 4) for code style, tone, PR standards,
tech stack preferences, and skip keywords.

Supports three context collection strategies (configured via profile):

- **Two-step smart selection** (default): first LLM call lists available
  files and picks relevant ones; second call implements.
- **Direct collection**: walks the repo and sends the best N files.
- **Token-budgeted collection**: same as direct but stops at a token limit.

Also supports a verification fix loop: when pre-push verification fails,
the errors are fed back to the LLM for up to ``max_fix_attempts`` retries.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from github import Github
from github.GithubException import GithubException, UnknownObjectException

from repokeeper.exceptions import (
    ConfigError,
    LLMParseError,
    PermissionDeniedError,
)
from repokeeper.git_ops import (
    BLOCKED_PREFIXES,  # noqa: F401  # re-export
    apply_and_push,  # noqa: F401  # re-export
)
from repokeeper.git_ops import git as _git  # noqa: F401  # re-export
from repokeeper.llm_client import LLMClient, TokenUsage, parse_llm_json
from repokeeper.logs import get_logger
from repokeeper.profile import load_profile
from repokeeper.repo_context import (  # noqa: F401  # re-export
    MAX_FILE_SIZE,
    MAX_FILES,
    SKIP_DIRS,
    SOURCE_EXTENSIONS,
    build_context_string,
    collect_repo_files,
    collect_specific_files,
    estimate_tokens,
    list_repo_files,
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

SYSTEM_PROMPT_FILE_SELECTION = """\
You are an expert software engineer. Your task is to identify which files in a
repository are relevant to a GitHub issue, so that another agent can implement
the fix using only those files.

Given:
- A GitHub issue (title + body)
- A list of all source files in the repository (paths + sizes + extensions)

Select the files that are MOST LIKELY to need changes. Be precise and minimal:
- Config files (pyproject.toml, package.json, etc.) if the issue is about deps/tooling.
- Source files that match the module/feature described in the issue.
- Test files that correspond to the affected source files.
- README/docs only if the issue is about documentation.

Respond with a single valid JSON object — no markdown fences, no explanation:

{
  "files": ["path/to/file1.py", "path/to/file2.py"],
  "reasoning": "One sentence explaining your selection."
}

- List at most 30 files.
- Only include files from the provided file list — do not invent paths.
"""


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
        LLMParseError: If JSON parsing fails after retries.
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
            return parse_llm_json(raw), total_usage
        except (ValueError, LLMParseError) as err:
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
                raise LLMParseError(
                    f"LLM JSON parsing failed after {max_retries + 1} attempts. "
                    f"Last error: {err}"
                ) from err

    # Unreachable — satisfy type checker
    raise LLMParseError("LLM JSON parsing failed")


# ─── Two-step file selection ────────────────────────────────────────────────


def smart_select_files(
    issue_data: dict[str, Any],
    profile: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[dict[str, str], TokenUsage]:
    """Two-step file selection: list files → LLM picks → read selected.

    Step 1: Send the issue + file listing to the LLM, ask it to pick
    relevant files.
    Step 2: Read only the selected files and return their contents.

    Falls back to direct collection if the LLM call fails.

    Args:
        issue_data: Structured issue data.
        profile: Maintainer profile dict.
        llm_client: Unified LLM client.

    Returns:
        Tuple of (``{path: content}`` dict, token usage).
    """
    model = profile.get("agent", {}).get("model", "deepseek-chat")
    max_context = profile.get("agent", {}).get("max_context_files", 60)

    all_files = list_repo_files()
    if not all_files:
        logger.warning("No source files found; falling back to direct collection")
        return collect_repo_files(max_files=max_context), TokenUsage(model=model)

    logger.info("Smart file selection: %d candidates available", len(all_files))

    # Build a compact file listing (path + size only)
    listing = "\n".join(
        f"  {f['path']} ({f['size']} bytes)"
        for f in all_files
    )

    selection_prompt = f"""\
## Issue #{issue_data['number']}: {issue_data['title']}

{issue_data['body']}

## Available files ({len(all_files)} total)
{listing}
"""

    total_usage = TokenUsage(model=model)

    try:
        response = llm_client.chat(
            system=SYSTEM_PROMPT_FILE_SELECTION,
            messages=[{"role": "user", "content": selection_prompt}],
            model=model,
            temperature=0.0,
            max_tokens=2000,
            stream=False,
        )

        total_usage.prompt_tokens += response.usage.prompt_tokens
        total_usage.completion_tokens += response.usage.completion_tokens
        total_usage.total_tokens += response.usage.total_tokens
        total_usage.cost_usd += response.usage.cost_usd

        selection = parse_llm_json(response.content.strip())
        selected_paths: list[str] = selection.get("files", [])

        if not selected_paths:
            logger.warning("LLM selected no files; falling back to direct collection")
            return collect_repo_files(max_files=max_context), total_usage

        # Limit to max_context_files
        selected_paths = selected_paths[:max_context]
        logger.info(
            "LLM selected %d files (%s), reading content...",
            len(selected_paths),
            selection.get("reasoning", "no reasoning provided"),
        )

        files = collect_specific_files(selected_paths)
        logger.info("Read %d/%d selected files", len(files), len(selected_paths))

        if not files:
            logger.warning("None of the selected files could be read; falling back")
            return collect_repo_files(max_files=max_context), total_usage

        return files, total_usage

    except (LLMParseError, Exception) as exc:
        logger.warning("Smart file selection failed (%s); falling back to direct collection", exc)
        return collect_repo_files(max_files=max_context), total_usage


# ─── Verification fix loop ─────────────────────────────────────────────────

FIX_SYSTEM_PROMPT = """\
You are an expert software engineer fixing CI failures in an automated PR.
Your previous implementation caused verification failures (linter errors
or test failures). Fix the code so that all checks pass.

Rules:
- Only fix the actual failures — don't refactor or add features.
- If a test expectation is wrong (not your code), skip rather than modifying tests.
- Respond with the SAME JSON format as the original implementation.
- You may modify the same files again or touch additional files if needed.
"""


def verification_fix_loop(
    result: dict[str, Any],
    issue_data: dict[str, Any],
    profile: dict[str, Any],
    llm_client: LLMClient,
    workdir: str | Path = ".",
) -> tuple[dict[str, Any], TokenUsage, list[str]]:
    """Run verification and retry fixes up to ``max_fix_attempts`` times.

    After each attempt, verification commands are re-run.  If all pass,
    the loop exits early.  If ``max_fix_attempts`` is 0, verification is
    run once without retries (legacy behavior).

    Args:
        result: Current implementation plan (the ``changes``/``new_files``
            dicts are updated in-place on each fix attempt).
        issue_data: Structured issue data.
        profile: Maintainer profile dict.
        llm_client: Unified LLM client.
        workdir: Repository root (for running verification commands).

    Returns:
        Tuple of ``(updated_result, total_usage, failure_messages)``.
        ``failure_messages`` is empty on success.
    """
    max_attempts = profile.get("agent", {}).get("max_fix_attempts", 2)
    total_usage = TokenUsage(model=profile.get("agent", {}).get("model", "deepseek-chat"))
    failure_messages: list[str] = []

    for attempt in range(max_attempts + 1):
        results = run_verification_commands(profile, Path(workdir))
        failures = [r for r in results if not r.passed]

        if not failures:
            logger.info(
                "Verification passed%s",
                f" on fix attempt {attempt}" if attempt > 0 else "",
            )
            return result, total_usage, []

        failure_msg = format_verification_failures(results)
        failure_messages.append(failure_msg)

        if attempt >= max_attempts:
            logger.warning("Verification failed after %d fix attempt(s)", attempt)
            break

        logger.info(
            "Verification failed (attempt %d/%d), asking LLM to fix...",
            attempt + 1, max_attempts,
        )

        # Build a fix prompt with the failed output
        style_config = profile.get("style", {})
        code_style = style_config.get("code_style", "follow existing patterns")

        fix_prompt = f"""\
## Issue: #{issue_data['number']} - {issue_data['title']}

Your previous implementation caused these verification failures:

{failure_msg}

## Maintainer style preference
{code_style}

Please fix the implementation. Respond with a corrected JSON object
(same format as before: skip, reason, summary, branch_name,
commit_message, changes, new_files).
"""

        try:
            response = llm_client.chat(
                system=FIX_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": fix_prompt}],
                model=profile.get("agent", {}).get("model", "deepseek-chat"),
                temperature=profile.get("agent", {}).get("temperature", 0.1),
                max_tokens=8000,
                stream=False,
            )

            total_usage.prompt_tokens += response.usage.prompt_tokens
            total_usage.completion_tokens += response.usage.completion_tokens
            total_usage.total_tokens += response.usage.total_tokens
            total_usage.cost_usd += response.usage.cost_usd

            fixed = parse_llm_json(response.content.strip())

            if fixed.get("skip"):
                logger.warning("LLM gave up on fix: %s", fixed.get("reason", ""))
                break

            # Merge the fix into result
            result.update(fixed)
            logger.info("Fix attempt %d applied, re-running verification...", attempt + 1)

            # Re-apply changes to disk so verification runs against fixed code
            from repokeeper.git_ops import safe_repo_path

            for filepath, content in result.get("changes", {}).items():
                try:
                    p = safe_repo_path(filepath)
                except ValueError:
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            for filepath, content in result.get("new_files", {}).items():
                try:
                    p = safe_repo_path(filepath)
                except ValueError:
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

        except LLMParseError as exc:
            logger.warning("Fix LLM response could not be parsed: %s", exc)
            break

    return result, total_usage, failure_messages


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
        PermissionDeniedError: If GitHub refuses to create the PR (e.g. permissions).
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
            raise PermissionDeniedError(
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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the Implementation Agent end-to-end.

    Args:
        dry_run: If True, stop after generating the implementation plan
                 and return it without applying changes or creating a PR.

    Returns:
        Dict with result info (``pr_url``, ``skip`` reason, ``error``,
        and ``plan`` when dry_run=True).
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
        raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

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
        agent_config = profile.get("agent", {})
        use_smart_selection = agent_config.get("smart_file_selection", True)
        max_context = agent_config.get("max_context_files", 60)
        token_budget = agent_config.get("max_context_tokens")
        model = agent_config.get("model", "deepseek-chat")

        selection_usage = TokenUsage(model=model)

        if use_smart_selection:
            logger.info("Using two-step smart file selection...")
            files, selection_usage = smart_select_files(issue_data, profile, llm)
            logger.info("Smart selection: %d files loaded", len(files))
        else:
            logger.info("Collecting repository context (max %d files)...", max_context)
            files = collect_repo_files(
                max_files=max_context,
                target_tokens=token_budget,
            )
            logger.info("Loaded %d files", len(files))

        context_str = build_context_string(files)
        logger.info(
            "Context: %d files, ~%d tokens",
            len(files), estimate_tokens(files),
        )

        # Call LLM
        logger.info("Calling LLM (%s)...", model)
        result, impl_usage = call_llm(issue_data, context_str, profile, llm)

        # Merge usage from both calls
        usage = TokenUsage(model=model)
        usage.prompt_tokens = selection_usage.prompt_tokens + impl_usage.prompt_tokens
        usage.completion_tokens = selection_usage.completion_tokens + impl_usage.completion_tokens
        usage.total_tokens = selection_usage.total_tokens + impl_usage.total_tokens
        usage.cost_usd = selection_usage.cost_usd + impl_usage.cost_usd

        # Log token usage
        if usage.total_tokens > 0:
            logger.info(
                "LLM usage: %d tokens · estimated $%.6f (model: %s)",
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

        if dry_run:
            logger.info("Dry-run mode — skipping apply and PR creation")
            plan_detail = {
                "branch_name": result.get("branch_name", "repokeeper/unknown"),
                "commit_message": result.get("commit_message", ""),
                "summary": result.get("summary", ""),
                "changes": list(result.get("changes", {}).keys()),
                "new_files": list(result.get("new_files", {}).keys()),
            }
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** dry-run plan:\n\n"
                f"**Branch:** `{plan_detail['branch_name']}`\n"
                f"**Commit:** {plan_detail['commit_message']}\n"
                f"**Summary:** {plan_detail['summary']}\n"
                f"**Files to modify:** {', '.join(plan_detail['changes']) or '(none)'}\n"
                f"**Files to create:** {', '.join(plan_detail['new_files']) or '(none)'}\n\n"
                f"*No changes were applied. Use `@repokeeper go` or `agent-todo` label to implement.*",
            )
            return {"skip": True, "reason": "dry-run", "pr_url": None, "plan": plan_detail}

        # Resolve branch name collisions — append timestamp if branch exists
        branch_name = result.get("branch_name", "repokeeper/unknown")
        result["branch_name"] = _resolve_branch_collision(branch_name, repo)

        # Apply changes to disk first (so verification can run against them)
        from repokeeper.git_ops import safe_repo_path

        for filepath, content in result.get("changes", {}).items():
            try:
                p = safe_repo_path(filepath)
            except ValueError:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        for filepath, content in result.get("new_files", {}).items():
            try:
                p = safe_repo_path(filepath)
            except ValueError:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        # ── Verification fix loop ──
        max_fix_attempts = agent_config.get("max_fix_attempts", 2)
        if max_fix_attempts >= 0:
            logger.info("Running verification (max %d fix attempts)...", max_fix_attempts)
            result, fix_usage, failures = verification_fix_loop(
                result, issue_data, profile, llm,
            )
            usage.prompt_tokens += fix_usage.prompt_tokens
            usage.completion_tokens += fix_usage.completion_tokens
            usage.total_tokens += fix_usage.total_tokens
            usage.cost_usd += fix_usage.cost_usd

            if failures:
                last_failure = failures[-1]
                post_comment(
                    issue_obj,
                    f"🤖 **RepoKeeper** implemented the changes but verification failed "
                    f"after {len(failures)} attempt(s):\n\n{last_failure}\n\n"
                    f"Please review and fix manually.",
                )
                return {
                    "skip": True,
                    "reason": f"Verification failed after {len(failures)} attempt(s)",
                    "pr_url": None,
                }

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
            cost_note = (
                f"\n**Estimated cost:** ~${usage.cost_usd:.6f} "
                f"({usage.total_tokens} tokens, {usage.model})"
            )

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
