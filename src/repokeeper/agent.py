#!/usr/bin/env python3
"""
Module 3: Implementation Agent

Triggered when an issue is labeled ``agent-todo`` or when a maintainer
comments ``/repokeeper go``. Reads the codebase + issue description,
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
import subprocess
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
    apply_implementation_changes,
    extract_patch_paths,
    fix_and_push,  # noqa: F401  # re-export
    implementation_file_paths,
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
    collect_specific_files,  # noqa: F401  # re-export for tests/API compatibility
    estimate_tokens,
    expand_context_paths,
    list_repo_files,
)
from repokeeper.verifier import (  # noqa: F401  # re-export
    VerificationResult,
    discover_verification_commands,
    format_verification_failures,
    format_verification_report,
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
        if not isinstance(section_dict, dict):
            continue
        blocked = [k for k in section_dict if k.startswith(BLOCKED_PREFIXES)]
        for k in blocked:
            del section_dict[k]
            stripped.append(k)

    edits = implementation.get("edits", [])
    if isinstance(edits, list):
        kept_edits = []
        for edit in edits:
            path = edit.get("path") if isinstance(edit, dict) else None
            if isinstance(path, str) and path.startswith(BLOCKED_PREFIXES):
                stripped.append(path)
            else:
                kept_edits.append(edit)
        implementation["edits"] = kept_edits

    patch = implementation.get("patch") or implementation.get("unified_diff")
    if isinstance(patch, str) and patch.strip():
        blocked_patch_paths = [
            path for path in extract_patch_paths(patch)
            if path.startswith(BLOCKED_PREFIXES)
        ]
        if blocked_patch_paths:
            stripped.extend(blocked_patch_paths)
            implementation["patch"] = ""
            implementation["unified_diff"] = ""
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
    total_files = len(implementation_file_paths(implementation))
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
- Prefer exact edit operations or a unified diff over full-file rewrites.
- Use full-file "changes" only when an exact edit or patch would be unreliable.

Respond with a single valid JSON object — no markdown fences, no explanation outside JSON:

{
  "skip": false,
  "reason": "",
  "summary": "One sentence describing what was implemented.",
  "branch_name": "repokeeper/issue-<number>-short-slug",
  "commit_message": "type: short imperative message",
  "edits": [
    {
      "path": "path/to/existing/file.py",
      "find": "exact existing text to replace",
      "replace": "replacement text",
      "replace_all": false
    }
  ],
  "patch": "optional unified diff when exact edits are awkward",
  "changes": {
    "path/to/existing/file.py": "<complete new file content, fallback only>"
  },
  "new_files": {
    "path/to/new/file.py": "<complete new file content>"
  }
}

- branch_name must start with "repokeeper/".
- "edits" = exact find/replace edits for existing files. The find text must match exactly.
- "patch" = unified diff text. Leave it empty if you use edits.
- "changes" = legacy full-file fallback for existing files. Avoid unless necessary.
- "new_files" = files to create.
- Empty sections must be represented as [] or {} rather than omitted.
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
- Use the kind, related tests, and local dependency hints when available.

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
    change_mode = profile.get("agent", {}).get("change_mode", "edits")

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

## Preferred change format
{change_mode}

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
    token_budget = profile.get("agent", {}).get("max_context_tokens")
    expand_context = profile.get("agent", {}).get("context_expansion", True)

    all_files = list_repo_files()
    if not all_files:
        logger.warning("No source files found; falling back to direct collection")
        return collect_repo_files(max_files=max_context, target_tokens=token_budget), TokenUsage(model=model)

    logger.info("Smart file selection: %d candidates available", len(all_files))

    # Build a compact file listing with relationship hints.
    listing_parts = []
    for file_info in all_files:
        bits = [
            f"{file_info['path']}",
            f"kind={file_info.get('kind', 'unknown')}",
            f"size={file_info['size']}",
        ]
        related_tests = file_info.get("related_tests") or []
        related_sources = file_info.get("related_sources") or []
        deps = file_info.get("local_dependencies") or []
        if related_tests:
            bits.append(f"tests={','.join(str(item) for item in related_tests)}")
        if related_sources:
            bits.append(f"sources={','.join(str(item) for item in related_sources)}")
        if deps:
            bits.append(f"deps={','.join(str(item) for item in deps)}")
        listing_parts.append("  - " + " | ".join(bits))
    listing = "\n".join(listing_parts)

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
            return collect_repo_files(max_files=max_context, target_tokens=token_budget), total_usage

        # Limit to max_context_files; relationship expansion below may add tests/deps.
        selected_paths = selected_paths[:max_context]
        logger.info(
            "LLM selected %d files (%s), reading content...",
            len(selected_paths),
            selection.get("reasoning", "no reasoning provided"),
        )

        if expand_context:
            files = expand_context_paths(
                selected_paths,
                max_files=max_context,
                target_tokens=token_budget,
            )
            logger.info("Read %d files after test/dependency expansion", len(files))
        else:
            files = collect_specific_files(
                selected_paths,
                max_files=max_context,
                target_tokens=token_budget,
            )
            logger.info("Read %d selected files", len(files))

        if not files:
            logger.warning("None of the selected files could be read; falling back")
            return collect_repo_files(max_files=max_context, target_tokens=token_budget), total_usage

        return files, total_usage

    except (LLMParseError, Exception) as exc:
        logger.warning("Smart file selection failed (%s); falling back to direct collection", exc)
        return collect_repo_files(max_files=max_context, target_tokens=token_budget), total_usage


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
- Prefer exact edits or a unified diff; use full-file changes only as fallback.
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
            result["_verification_results"] = results
            logger.info(
                "Verification passed%s",
                f" on fix attempt {attempt}" if attempt > 0 else "",
            )
            return result, total_usage, []

        failure_msg = format_verification_failures(results)
        result["_verification_results"] = results
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
(same format as before: skip, reason, summary, branch_name, commit_message,
edits, patch, changes, new_files). Use empty lists/objects for unused sections.
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
            for key, empty in (("edits", []), ("changes", {}), ("new_files", {})):
                fixed.setdefault(key, empty)
            if "patch" not in fixed and "unified_diff" not in fixed:
                fixed["patch"] = ""
            result.update(fixed)
            logger.info("Fix attempt %d applied, re-running verification...", attempt + 1)

            # Re-apply changes to disk so verification runs against fixed code
            apply_implementation_changes(result, repo_root=workdir)

        except LLMParseError as exc:
            logger.warning("Fix LLM response could not be parsed: %s", exc)
            break

    return result, total_usage, failure_messages


# ─── PR Fix Agent ────────────────────────────────────────────────────────────

FIX_PR_SYSTEM_PROMPT = """\
You are an expert software engineer fixing issues in a pull request
based on the maintainer's review feedback.

Your job:
1. Read the maintainer's comments carefully — they pointed out specific
   problems in the PR.
2. Read the PR diff to understand the original changes.
3. Read the relevant source files for context.
4. Produce precise, minimal fixes that address ONLY the issues raised.

Rules:
- Fix ONLY what the maintainer asked for. Do not add features or refactor.
- If the maintainer's feedback is unclear or contradictory, set "skip": true.
- Respect the maintainer's code style and tech stack preferences.
- Prefer exact edit operations or a unified diff over full-file rewrites.
- Each fix should correspond to a specific piece of feedback.

Respond with a single valid JSON object — no markdown fences, no explanation:

{
  "skip": false,
  "reason": "",
  "summary": "One sentence describing what was fixed.",
  "commit_message": "fix: short imperative message",
  "edits": [
    {
      "path": "path/to/existing/file.py",
      "find": "exact existing text to replace",
      "replace": "replacement text",
      "replace_all": false
    }
  ],
  "patch": "optional unified diff when exact edits are awkward",
  "changes": {
    "path/to/existing/file.py": "<complete new file content, fallback only>"
  },
  "new_files": {
    "path/to/new/file.py": "<complete new file content>"
  }
}

Empty sections must be represented as [] or {} rather than omitted.
"""


# ─── Pi Backend ──────────────────────────────────────────────────────────────

PI_TASK_TEMPLATE = """\
You are an expert software engineer acting as an AI deputy for an open source
maintainer.  Your job is to implement a GitHub issue by reading the codebase,
making precise, minimal changes, and verifying your work.

## Issue #{number}: {title}

{body}

## Recent Discussion
{discussion}

## Maintainer Preferences
- Code style: {code_style}
{tech_note}

## Rules
1. Read the relevant source files to understand the codebase before changing anything.
2. Make the MINIMAL changes needed to close the issue.
3. Do NOT add unrequested features, refactors, or comments.
4. Never modify files under .github/workflows/.
5. Run linters and tests if available to verify your changes.
6. When ALL work is complete, output EXACTLY this JSON on a single line with no
   markdown fences, no extra text — this is how the system knows you finished:

   {{"success": true, "summary": "one sentence", "commit_message": "type: message"}}

   If you cannot implement the issue, set success to false and explain why.

The repository files are on disk.  Start by exploring the codebase.
"""


def _build_pi_prompt(
    issue_data: dict[str, Any],
    profile: dict[str, Any],
) -> str:
    """Build a task prompt for the Pi coding agent.

    Args:
        issue_data: Structured issue data.
        profile: Maintainer profile dict.

    Returns:
        Markdown prompt string for Pi.
    """
    style_config = profile.get("style", {})
    code_style = style_config.get("code_style", "follow existing patterns")
    tech_config = profile.get("tech", {})
    preferred = tech_config.get("preferred", [])
    avoided = tech_config.get("avoid", [])

    tech_note = ""
    if preferred:
        tech_note += f"- Preferred tech stack: {', '.join(preferred)}\n"
    if avoided:
        tech_note += f"- Tech stack to avoid: {', '.join(avoided)}\n"

    discussion = "\n".join(
        f"@{c.get('author', '?')}: {c.get('body', '')}"
        for c in issue_data.get("comments", [])[-5:]
    ) or "(no recent discussion)"

    return PI_TASK_TEMPLATE.format(
        number=issue_data["number"],
        title=issue_data["title"],
        body=issue_data["body"],
        discussion=discussion,
        code_style=code_style,
        tech_note=tech_note,
    )


def _pi_model_arg(model: str) -> str:
    """Convert a profile model name to a Pi --model argument.

    Pi expects ``provider/model`` format.  Short names are mapped to known
    providers; unknown names are passed through as-is.
    """
    mapping: dict[str, str] = {
        "deepseek-chat": "deepseek/deepseek-chat",
        "deepseek-reasoner": "deepseek/deepseek-reasoner",
        "gpt-4o": "openai/gpt-4o",
        "gpt-4o-mini": "openai/gpt-4o-mini",
    }
    if "/" in model:
        return model
    return mapping.get(model, f"deepseek/{model}")


def _run_pi(
    prompt: str,
    model: str,
    api_key: str,
    workdir: str | Path = ".",
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    """Run Pi coding agent as a subprocess.

    Writes the prompt to a temp file and invokes Pi with ``@file`` syntax.
    Returns the completed process; caller inspects ``returncode``, ``stdout``,
    and ``stderr``.

    Args:
        prompt: Markdown prompt for Pi.
        model: Model name (resolved via :func:`_pi_model_arg`).
        api_key: API key for the provider.
        workdir: Working directory for Pi (should be the repo root).
        timeout: Maximum runtime in seconds.

    Returns:
        Completed subprocess.
    """
    prompt_file = Path(workdir) / ".repokeeper-pi-prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")

    try:
        result = subprocess.run(  # noqa: S603
            [
                "pi",
                "--model", _pi_model_arg(model),
                "--api-key", api_key,
                f"@{prompt_file}",
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result
    finally:
        prompt_file.unlink(missing_ok=True)


def _parse_pi_result(stdout: str) -> dict[str, Any]:
    """Parse the JSON result from Pi's output.

    Pi is instructed to emit a JSON object with ``success``, ``summary``,
    and ``commit_message``.  This function searches for it in several ways
    since Pi may embed the JSON in text or code fences.

    Args:
        stdout: Pi's captured stdout.

    Returns:
        Dict with at least ``skip``, ``summary``, and ``commit_message``.
    """
    import re as _re

    # 1. Search for {"success"...} anywhere in the output (handles inline JSON).
    match = _re.search(r'\{"success"\s*:\s*(?:true|false)[^}]*\}', stdout)
    if match:
        try:
            data = json.loads(match.group())
            return _pi_result_from_data(data)
        except json.JSONDecodeError:
            pass

    # 2. Search inside ```json fences.
    match = _re.search(r'```(?:json)?\s*\n(\{[^`]+\})\s*\n```', stdout, _re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return _pi_result_from_data(data)
        except json.JSONDecodeError:
            pass

    # 3. Fallback: scan lines in reverse (original behaviour).
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and '"success"' in line:
            try:
                data = json.loads(line)
                return _pi_result_from_data(data)
            except json.JSONDecodeError:
                continue

    # 4. Last resort: if Pi produced any output at all, assume it tried.
    #    The caller should check git diff separately for actual changes.
    return {
        "skip": False,
        "reason": "",
        "summary": "Pi made changes (no JSON confirmation).",
        "commit_message": "fix: changes from Pi",
        "edits": [],
        "changes": {},
        "new_files": {},
        "patch": "",
    }


def _pi_result_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a parsed Pi JSON blob to the standard result dict."""
    return {
        "skip": not data.get("success", False),
        "reason": data.get("reason", ""),
        "summary": data.get("summary", "Pi made changes."),
        "commit_message": data.get("commit_message", "fix: changes from Pi"),
        "edits": [],
        "changes": {},
        "new_files": {},
        "patch": "",
    }


# ─── PR Fix Context ─────────────────────────────────────────────────────────


def _get_pr_fix_context(
    pr_obj: Any,
    pr_number: int,
    profile: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[str, TokenUsage, dict[str, str]]:
    """Build the LLM context for a PR fix: diff + maintainer feedback + codebase.

    Args:
        pr_obj: PyGithub PullRequest object.
        pr_number: PR number.
        profile: Maintainer profile.
        llm_client: LLM client (for smart file selection usage tracking).

    Returns:
        Tuple of (context_string, token_usage, collected_files).
    """
    from repokeeper.review import build_review_context_string as build_pr_context
    from repokeeper.review import get_pr_data

    model = profile.get("agent", {}).get("model", "deepseek-chat")
    pr_data = get_pr_data(pr_obj._repo if hasattr(pr_obj, '_repo') else pr_obj.head.repo, pr_number)  # type: ignore[union-attr]

    # Collect files for context — prioritize changed files
    agent_config = profile.get("agent", {})
    max_context = agent_config.get("max_context_files", 60)
    files = collect_repo_files(max_files=max_context)

    context_str = build_pr_context(pr_data, files)

    # Add maintainer feedback as a dedicated section.
    # Bot comments (from repokeeper[bot]) are labelled as previous fix attempts
    # so the LLM understands what was already tried.
    comments = list(pr_obj.get_issue_comments())
    if comments:
        feedback_parts = ["## PR Conversation History"]
        for c in comments:
            author = c.user.login if c.user else "unknown"
            if author == "repokeeper[bot]":
                feedback_parts.append(f"**🤖 RepoKeeper (previous fix attempt):** {c.body}")
            else:
                feedback_parts.append(f"**@{author} (reviewer feedback):** {c.body}")
        context_str = "\n\n".join(feedback_parts) + "\n\n" + context_str

    usage = TokenUsage(model=model)
    usage.prompt_tokens = estimate_tokens(files)
    usage.total_tokens = usage.prompt_tokens

    return context_str, usage, files


def _call_llm_for_fix(
    context_str: str,
    profile: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[dict[str, Any], TokenUsage]:
    """Call the LLM to generate PR fixes.

    Args:
        context_str: Formatted fix context string.
        profile: Maintainer profile.
        llm_client: Unified LLM client.

    Returns:
        Tuple of (parsed JSON, token usage).
    """
    model = profile.get("agent", {}).get("model", "deepseek-chat")
    style_config = profile.get("style", {})
    code_style = style_config.get("code_style", "follow existing patterns")

    user_prompt = f"""\
## PR Fix Request

The maintainer has reviewed this PR and requested changes.
Read the feedback below and the PR diff, then produce fixes.

## Maintainer style preference
{code_style}

## Context
{context_str}
"""

    messages = [{"role": "user", "content": user_prompt}]
    max_retries = 2
    total_usage = TokenUsage(model=model)

    for attempt in range(max_retries + 1):
        response = llm_client.chat(
            system=FIX_PR_SYSTEM_PROMPT,
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=8000,
            stream=False,
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
                        "JSON object."
                    ),
                })
            else:
                raise LLMParseError(
                    f"LLM JSON parsing failed after {max_retries + 1} attempts. "
                    f"Last error: {err}"
                ) from err

    raise LLMParseError("LLM JSON parsing failed")


def run_fix_pr(
    gh_token: str,
    repository: str,
    pr_number: int,
    llm: LLMClient,
    repo: Any,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Fix issues in an existing PR based on maintainer feedback.

    1. Reads PR diff, comments, and codebase context.
    2. Calls the LLM to generate targeted fixes.
    3. Applies fixes and pushes to the PR's head branch.
    4. Posts a comment summarizing the fix.

    Args:
        gh_token: GitHub token.
        repository: Repository slug.
        pr_number: PR number.
        llm: LLM client.
        repo: PyGithub Repository object.
        profile: Maintainer profile.

    Returns:
        Dict with result info.
    """
    pr_obj = repo.get_pull(pr_number)
    pr_data_from_gh = {
        "title": pr_obj.title,
        "head_branch": pr_obj.head.ref,
        "author": pr_obj.user.login if pr_obj.user else "unknown",
    }

    logger.info("PR #%d (%s) by @%s — running fix mode",
                pr_number, pr_data_from_gh["title"], pr_data_from_gh["author"])

    pr_obj.create_issue_comment(
        "🤖 **RepoKeeper** is reviewing your feedback and working on fixes..."
    )

    try:
        context_str, context_usage, files = _get_pr_fix_context(
            pr_obj, pr_number, profile, llm,
        )
        logger.info("Fix context: %d files, ~%d tokens", len(files), estimate_tokens(files))

        # ── Backend: Pi or native ──
        backend = profile.get("agent", {}).get("backend", "native")
        if backend == "pi":
            model = profile.get("agent", {}).get("model", "deepseek-chat")
            pi_prompt = (
                "You are an expert software engineer fixing issues in a PR "
                "based on the maintainer's review feedback.\n\n"
                + context_str
                + "\n\nRead the feedback above. Make targeted, minimal fixes "
                "that address ONLY the issues raised.\n\n"
                "When ALL work is complete, output EXACTLY this JSON on a "
                "single line with no markdown fences:\n"
                '{"success": true, "summary": "one sentence", '
                '"commit_message": "fix: short message"}'
            )

            # Checkout the PR branch so Pi modifies the right code.
            head_branch = pr_data_from_gh["head_branch"]
            local_branch = f"repokeeper-fix-{pr_number}"
            _git("fetch", "origin", f"pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}")
            _git("checkout", "-b", local_branch, f"origin/pr/{pr_number}")

            logger.info("Running Pi for PR fix on branch %s (%s)...", local_branch, model)
            pi_result = _run_pi(pi_prompt, model, llm.api_key, workdir=".")
            if pi_result.returncode != 0:
                logger.warning("Pi exited with code %d", pi_result.returncode)
                logger.debug("Pi stderr: %s", pi_result.stderr[-500:])
            logger.info("Pi stdout (last 300): %s", pi_result.stdout[-300:])

            result = _parse_pi_result(pi_result.stdout)
            if result.get("skip"):
                reason = result.get("reason", "Pi could not fix this PR.")
                pr_obj.create_issue_comment(
                    f"🤖 **RepoKeeper** (Pi) could not fix this automatically:\n\n> {reason}\n\n"
                    f"Please fix manually or provide more specific feedback."
                )
                return {"skip": True, "reason": reason}

            # Detect changes made by Pi
            changed = _git("diff", "--name-only", capture=True, check=False).stdout.strip()
            changed_files_list = changed.splitlines() if changed else []
            if not changed_files_list:
                logger.warning("Pi produced no file changes")
                pr_obj.create_issue_comment(
                    "🤖 **RepoKeeper** (Pi) finished but produced no file changes.\n\n"
                    "Please review the feedback or fix manually."
                )
                return {"skip": True, "reason": "No file changes produced by Pi"}

            # Pi changes are already on disk; let fix_and_push pick them up.
            result["branch_name"] = pr_data_from_gh["head_branch"]
            head_branch = pr_data_from_gh["head_branch"]
            assert gh_token is not None
            assert repository is not None

            branch, changed_files = fix_and_push(
                result, gh_token, repository, head_branch, pr_number,
                already_checked_out=True,
            )

            pr_obj.create_issue_comment(
                f"🤖 **RepoKeeper** (Pi) applied fixes based on your feedback.\n\n"
                f"**Summary:** {result['summary']}\n"
                f"**Changed files:** {', '.join(f'`{f}`' for f in changed_files) or '(none)'}\n\n"
                f"Please re-review the changes."
            )

            logger.info("Pi fix pushed to %s (%d files)", branch, len(changed_files))
            return {"skip": False, "pr_url": pr_obj.html_url, "fix_applied": True}

        # Native backend
        result, fix_usage = _call_llm_for_fix(context_str, profile, llm)

        usage = TokenUsage(model=fix_usage.model)
        usage.prompt_tokens = context_usage.prompt_tokens + fix_usage.prompt_tokens
        usage.completion_tokens = fix_usage.completion_tokens
        usage.total_tokens = context_usage.prompt_tokens + fix_usage.total_tokens
        usage.cost_usd = fix_usage.cost_usd

        if usage.total_tokens > 0:
            logger.info(
                "LLM usage: %d tokens · estimated $%.6f (model: %s)",
                usage.total_tokens, usage.cost_usd, usage.model,
            )

        if result.get("skip"):
            reason = result.get("reason", "No reason provided.")
            pr_obj.create_issue_comment(
                f"🤖 **RepoKeeper** could not fix this automatically:\n\n> {reason}\n\n"
                f"Please fix manually or provide more specific feedback."
            )
            return {"skip": True, "reason": reason}

        # Strip blocked paths
        stripped = strip_blocked_paths(result)
        if stripped:
            logger.info("Stripped blocked paths: %s", ", ".join(stripped))

        # Validate (skip branch_name check — not applicable to fix mode)
        validation_issues = [
            v for v in validate_implementation(result, profile)
            if "branch_name" not in v
        ]
        if validation_issues:
            issues_str = "\n".join(f"- {v}" for v in validation_issues)
            pr_obj.create_issue_comment(
                f"🤖 **RepoKeeper** fix validation failed:\n\n{issues_str}\n\n"
                f"Please review manually."
            )
            return {"skip": True, "reason": f"Validation: {issues_str}"}

        logger.info("Fix plan: %s", result["summary"])

        # Apply changes and push to existing branch
        apply_implementation_changes(result)

        head_branch = pr_data_from_gh["head_branch"]
        assert gh_token is not None
        assert repository is not None

        branch, changed_files = fix_and_push(
            result, gh_token, repository, head_branch, pr_number,
        )

        cost_note = ""
        if usage.cost_usd > 0:
            cost_note = (
                f"\n**Estimated cost:** ~${usage.cost_usd:.6f} "
                f"({usage.total_tokens} tokens, {usage.model})"
            )

        pr_obj.create_issue_comment(
            f"🤖 **RepoKeeper** applied fixes based on your feedback.\n\n"
            f"**Summary:** {result['summary']}\n"
            f"**Changed files:** {', '.join(f'`{f}`' for f in changed_files) or '(none)'}"
            f"{cost_note}\n\n"
            f"Please re-review the changes."
        )

        logger.info("Fix pushed to %s (%d files)", branch, len(changed_files))
        return {"skip": False, "pr_url": pr_obj.html_url, "fix_applied": True}

    except Exception as exc:
        logger.error("Fix PR error: %s", exc)
        pr_obj.create_issue_comment(
            f"🤖 **RepoKeeper** fix encountered an error:\n\n```\n{exc}\n```\n\n"
            f"Check the [workflow logs]({repo.html_url}/actions) for details."
        )
        raise


# ─── PR creation ─────────────────────────────────────────────────────────────


def create_pr(
    repo: Any,
    issue_data: dict[str, Any],
    implementation: dict[str, Any],
    branch: str,
    changed_files: list[str],
    profile: dict[str, Any],
    verification_results: list[VerificationResult] | None = None,
    usage: TokenUsage | None = None,
    context_file_count: int | None = None,
    context_token_estimate: int | None = None,
) -> str:
    """Create a GitHub pull request for the agent's implementation.

    Args:
        repo: PyGithub Repository object.
        issue_data: Issue data dict.
        implementation: LLM response dict.
        branch: Branch name.
        changed_files: List of changed file paths.
        profile: Maintainer profile.
        verification_results: Verification command evidence for the PR body.
        usage: Optional aggregate token/cost usage.
        context_file_count: Number of files sent as LLM context.
        context_token_estimate: Estimated context tokens.

    Returns:
        PR URL.

    Raises:
        PermissionDeniedError: If GitHub refuses to create the PR (e.g. permissions).
    """
    files_list = "\n".join(f"- `{f}`" for f in changed_files)
    verification = format_verification_report(verification_results or [])
    cost_note = "Not available."
    if usage and usage.total_tokens:
        cost_note = f"{usage.total_tokens} tokens"
        if usage.cost_usd > 0:
            cost_note += f", ~${usage.cost_usd:.6f}"
        if usage.model:
            cost_note += f", {usage.model}"

    context_note = "Not recorded."
    if context_file_count is not None:
        context_note = f"{context_file_count} files"
        if context_token_estimate is not None:
            context_note += f", ~{context_token_estimate} context tokens"

    risk_level = "low"
    if len(changed_files) > 5:
        risk_level = "medium"
    if any(path.startswith(("auth/", "security/", ".github/")) for path in changed_files):
        risk_level = "high"
    tests_changed = [path for path in changed_files if "test" in path.lower()]
    test_note = ", ".join(f"`{path}`" for path in tests_changed) or "No test files changed."

    body = f"""\
## 🤖 RepoKeeper Implementation

Closes #{issue_data['number']}

### Issue
{issue_data.get('title', f"#{issue_data['number']}")}

### Plan
{implementation['summary']}

### Changed files
{files_list}

### Verification
{verification}

### Risk
- Estimated risk: **{risk_level}**
- Test coverage touched: {test_note}
- Human review is still required before merging.

### Cost and context
- LLM usage: {cost_note}
- Context: {context_note}

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


# ─── Similar issue detection ────────────────────────────────────────────────


def find_similar_issues(
    repo: Any,
    issue_data: dict[str, Any],
    profile: dict[str, Any] | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Search for open issues similar to the given issue.

    Uses GitHub's search API to find issues with overlapping keywords
    in the title or body.  Excludes the current issue from results.

    Call this before implementing an issue to avoid duplicating work.

    Args:
        repo: PyGithub Repository object.
        issue_data: Issue data dict from :func:`get_issue_data`.
        profile: Maintainer profile dict (unused currently, for future
                 similarity tuning).
        max_results: Maximum number of similar issues to return.

    Returns:
        List of dicts with ``number``, ``title``, ``url``, ``state``,
        ``created_at``, ``author``.
    """
    title = issue_data.get("title", "")
    # Extract significant words (3+ chars) from title for search
    words = [w for w in title.split() if len(w) >= 3 and w.lower() not in {
        "the", "and", "for", "with", "that", "this", "from", "when", "should",
    }]
    if not words:
        return []

    results: list[dict[str, Any]] = []
    current_number = issue_data.get("number")

    try:
        # Use GitHub's issue listing with keyword overlap
        issues = repo.get_issues(state="open", sort="created", direction="desc")
        for issue in issues:
            if len(results) >= max_results * 2:  # over-fetch to allow filtering
                break
            if issue.pull_request is not None:
                continue
            if issue.number == current_number:
                continue

            issue_title = (issue.title or "").lower()
            issue_body = (issue.body or "").lower()

            # Simple similarity: any significant word overlap
            overlap = sum(1 for w in words if w.lower() in issue_title or w.lower() in issue_body)
            # At least 2 word overlaps or more than 40% of words match
            threshold = max(2, len(words) * 0.4)
            if overlap >= threshold:
                results.append({
                    "number": issue.number,
                    "title": issue.title,
                    "url": issue.html_url,
                    "state": issue.state,
                    "created_at": str(issue.created_at),
                    "author": issue.user.login if issue.user else "unknown",
                })

    except Exception as exc:
        logger.warning("Similar issue search failed: %s", exc)

    return results[:max_results]


def _format_similar_issues_comment(
    issue_data: dict[str, Any],
    similar: list[dict[str, Any]],
) -> str:
    """Format a comment listing similar existing issues.

    Args:
        issue_data: The original issue data.
        similar: List of similar issue dicts from :func:`find_similar_issues`.

    Returns:
        Markdown comment string.
    """
    lines = [
        "🤖 **RepoKeeper** found potentially related issues. "
        "Please review before implementing:",
        "",
    ]
    for s in similar:
        lines.append(
            f"- [#{s['number']} {s['title']}]({s['url']}) "
            f"(opened {s['created_at'][:10]} by @{s['author']})"
        )
    lines.append("")
    lines.append(
        "If this issue is a duplicate, close it with a link to the original. "
        "Remove the `agent-todo` label or comment `/repokeeper go` again to "
        "proceed anyway."
    )
    return "\n".join(lines)


# ─── Main entry point ────────────────────────────────────────────────────────


def run_agent(
    gh_token: str | None = None,
    repository: str | None = None,
    issue_number: int | None = None,
    pr_number: int | None = None,
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

    # ── PR context detection ──
    # When triggered by a PR comment or label, switch to fix mode.
    # pr_number may come from CLI --pr flag or PR_NUMBER env.
    pr_number = pr_number or int(os.environ.get("PR_NUMBER") or "0")
    is_pr_context = False
    try:
        pr_check = repo.get_pull(issue_number)
        if pr_check is not None and pr_number == issue_number:
            is_pr_context = True
            # Get the original issue number linked to this PR
            import re as _re
            body = pr_check.body or ""
            match = _re.search(r"[Cc]loses?\s+#(\d+)", body)
            if match:
                issue_number = int(match.group(1))
                issue_obj = repo.get_issue(issue_number)
    except Exception:
        if pr_number and not is_pr_context:
            # pr_number set explicitly but get_pull failed — still try fix mode
            try:
                pr_check = repo.get_pull(pr_number)
                if pr_check is not None:
                    is_pr_context = True
                    import re as _re
                    body = pr_check.body or ""
                    match = _re.search(r"[Cc]loses?\s+#(\d+)", body)
                    if match:
                        issue_number = int(match.group(1))
                        issue_obj = repo.get_issue(issue_number)
            except Exception:
                pass

    issue_data = get_issue_data(repo, issue_number)

    # Branch to PR fix mode
    if is_pr_context:
        assert gh_token is not None and repository is not None
        logger.info("PR #%d detected — running fix mode (linked issue #%d)", pr_number, issue_number)
        return run_fix_pr(
            gh_token, repository, pr_number, llm, repo, profile,
        )

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

    # ── Similar issue detection ──
    if profile.get("agent", {}).get("similar_issue_check", True):
        logger.info("Checking for similar existing issues...")
        similar = find_similar_issues(repo, issue_data, profile)
        if similar:
            logger.info(
                "Found %d similar issue(s): %s",
                len(similar),
                ", ".join(f"#{s['number']}" for s in similar),
            )
            post_comment(issue_obj, _format_similar_issues_comment(issue_data, similar))
            return {
                "skip": True,
                "reason": (
                    f"Found {len(similar)} similar open issue(s): "
                    + ", ".join(f"#{s['number']}" for s in similar)
                    + ". Review duplicates and re-trigger if this is distinct."
                ),
                "similar_issues": similar,
                "pr_url": None,
            }
        logger.info("No similar issues found")

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

        # Call LLM or Pi agent
        backend = agent_config.get("backend", "native")
        usage = TokenUsage(model=model)
        if backend == "pi":
            logger.info("Running Pi coding agent (%s)...", model)
            pi_prompt = _build_pi_prompt(issue_data, profile)
            pi_result = _run_pi(pi_prompt, model, llm.api_key)
            if pi_result.returncode != 0:
                logger.warning("Pi exited with code %d", pi_result.returncode)
                logger.debug("Pi stderr: %s", pi_result.stderr[-500:])
            result = _parse_pi_result(pi_result.stdout)
            logger.info("Pi stdout (last 300 chars): %s", pi_result.stdout[-300:])

            # Detect changed files after Pi ran
            changed = _git("diff", "--name-only", capture=True, check=False).stdout.strip()
            changed_files_list = changed.splitlines() if changed else []

            # If Pi skipped or no files changed, return early
            if result.get("skip"):
                reason = result.get("reason", "Pi could not implement this issue.")
                logger.info("Pi skipped: %s", reason)
                post_comment(issue_obj,
                    f"🤖 **RepoKeeper** (Pi) could not implement this automatically:\n\n> {reason}\n\n"
                    f"Please implement manually or clarify the issue.")
                return {"skip": True, "reason": reason, "pr_url": None}

            if not changed_files_list:
                logger.warning("Pi produced no file changes")
                post_comment(issue_obj,
                    "🤖 **RepoKeeper** (Pi) finished but produced no file changes.\n\n"
                    "Please check the issue description or implement manually.")
                return {"skip": True, "reason": "No file changes produced by Pi", "pr_url": None}

            impl_usage = TokenUsage(model=model, total_tokens=0, cost_usd=0)
            verification_results: list[VerificationResult] = []

            # Directly commit and push — Pi changes are already on disk
            logger.info("Plan (Pi): %s", result.get("summary", ""))
            branch_name = f"repokeeper/issue-{issue_number}-pi"
            result["branch_name"] = _resolve_branch_collision(branch_name, repo)
            result["commit_message"] = result.get("commit_message",
                                                    f"fix: address issue #{issue_number} (Pi)")
            _git("add", "-A")
            _git("commit", "-m", result["commit_message"], check=False)

            assert gh_token is not None
            assert repository is not None
            branch, pushed_files = apply_and_push(
                result, gh_token, repository, profile,
                already_applied=True, verify=False,
            )

            pr_url = create_pr(repo, issue_data, result, branch, pushed_files, profile,
                               verification_results=verification_results, usage=usage,
                               context_file_count=len(files),
                               context_token_estimate=estimate_tokens(files))

            post_comment(issue_obj,
                f"🤖 **RepoKeeper** (Pi) finished implementation.\n\n"
                f"**PR:** {pr_url}\n\n"
                f"**Summary:** {result['summary']}\n"
                f"**Changed files:** {', '.join(f'`{f}`' for f in pushed_files) or '(none)'}\n\n"
                f"Please review the changes before merging.")
            logger.info("Done (Pi) — PR: %s", pr_url)
            return {"skip": False, "reason": "", "pr_url": pr_url}

        # Native backend: call LLM
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
            changed_paths = implementation_file_paths(result)
            patch_paths = extract_patch_paths(result.get("patch", "") or result.get("unified_diff", ""))
            plan_detail = {
                "branch_name": result.get("branch_name", "repokeeper/unknown"),
                "commit_message": result.get("commit_message", ""),
                "summary": result.get("summary", ""),
                "changes": list(result.get("changes", {}).keys()),
                "new_files": list(result.get("new_files", {}).keys()),
                "edits": [
                    edit.get("path")
                    for edit in result.get("edits", [])
                    if isinstance(edit, dict) and edit.get("path")
                ],
                "patch_files": patch_paths,
                "changed_files": changed_paths,
            }
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** dry-run plan:\n\n"
                f"**Branch:** `{plan_detail['branch_name']}`\n"
                f"**Commit:** {plan_detail['commit_message']}\n"
                f"**Summary:** {plan_detail['summary']}\n"
                f"**Files to edit:** {', '.join(plan_detail['changed_files']) or '(none)'}\n"
                f"**Files to create:** {', '.join(plan_detail['new_files']) or '(none)'}\n\n"
                f"*No changes were applied. Use `/repokeeper go` or `agent-todo` label to implement.*",
            )
            return {"skip": True, "reason": "dry-run", "pr_url": None, "plan": plan_detail}

        # Resolve branch name collisions — append timestamp if branch exists
        branch_name = result.get("branch_name", "repokeeper/unknown")
        result["branch_name"] = _resolve_branch_collision(branch_name, repo)

        # Apply changes to disk first (so verification can run against them).
        apply_implementation_changes(result)

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

        verification_results = result.get("_verification_results", [])

        # Stage, commit, and push the already-verified worktree.
        assert gh_token is not None
        assert repository is not None
        branch, changed_files = apply_and_push(
            result, gh_token, repository, profile,
            already_applied=True,
            verify=False,
        )

        # Create PR
        pr_url = create_pr(
            repo,
            issue_data,
            result,
            branch,
            changed_files,
            profile,
            verification_results=verification_results,
            usage=usage,
            context_file_count=len(files),
            context_token_estimate=estimate_tokens(files),
        )

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
            f"**Summary:** {result['summary']}\n"
            f"**Changed files:** {', '.join(f'`{f}`' for f in changed_files) or '(none)'}\n"
            f"**Verification:** {len(verification_results)} command(s) passed"
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
