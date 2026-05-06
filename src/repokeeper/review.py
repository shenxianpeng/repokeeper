"""
Module 5: Code Review Agent

Triggered when a PR is labeled ``agent-review`` or when a maintainer
comments ``@repokeeper review``. Reads the PR diff and relevant codebase
context, then posts a structured code review comment.

Uses the Maintainer Profile (Module 4) to check:
- Code style compliance
- Tech stack preferences
- PR standards (file count, test coverage)
- Overall code quality

Safety: RepoKeeper **never** approves or merges PRs.  It only provides
review suggestions for the human maintainer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from github import Github

from repokeeper.exceptions import ConfigError, LLMParseError
from repokeeper.llm_client import LLMClient, TokenUsage, parse_llm_json
from repokeeper.logs import get_logger
from repokeeper.profile import load_profile
from repokeeper.repo_context import (
    MAX_FILE_SIZE,
    build_context_string,
    collect_repo_files,
    estimate_tokens,
)

logger = get_logger("review")

# ─── GitHub helpers ──────────────────────────────────────────────────────────


def get_pr_data(repo: Any, pr_number: int) -> dict[str, Any]:
    """Extract structured data from a GitHub pull request.

    Args:
        repo: PyGithub Repository object.
        pr_number: PR number.

    Returns:
        Dict with ``number``, ``title``, ``body``, ``author``, ``base_branch``,
        ``head_branch``, ``files``, ``additions``, ``deletions``, ``changed_files``,
        and ``comments``.
    """
    pr = repo.get_pull(pr_number)
    files = list(pr.get_files())
    recent_comments = list(pr.get_issue_comments())[-5:]
    return {
        "number": pr.number,
        "title": pr.title,
        "body": pr.body or "(no description)",
        "author": pr.user.login if pr.user else "unknown",
        "base_branch": pr.base.ref,
        "head_branch": pr.head.ref,
        "files": [
            {
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "changes": f.changes,
                "patch": f.patch[:MAX_FILE_SIZE] if f.patch else "(binary or too large)",
            }
            for f in files
        ],
        "changed_files": [f.filename for f in files],
        "comments": [
            {"author": c.user.login, "body": c.body}
            for c in recent_comments
        ],
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files_count": pr.changed_files,
    }


def post_review_comment(
    pr_obj: Any, message: str, suggestions: list[dict[str, Any]] | None = None
) -> None:
    """Post a review comment on a GitHub PR.

    Uses the issue comment API for the summary, and optionally
    adds an inline review for file-specific suggestions.

    Args:
        pr_obj: PyGithub PullRequest object.
        message: Markdown summary message.
        suggestions: Optional list of line-level suggestions.
    """
    pr_obj.create_issue_comment(message)


# ─── Skip keyword check ─────────────────────────────────────────────────────


def check_review_skip_keywords(pr_data: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """Check if the PR matches any skip keywords from the profile.

    Args:
        pr_data: PR data dict from :func:`get_pr_data`.
        profile: Maintainer profile dict.

    Returns:
        Matched keyword if found, ``None`` otherwise.
    """
    skip_keywords = profile.get("agent", {}).get("skip_keywords", [])
    if not skip_keywords:
        return None

    combined = f"{pr_data['title']} {pr_data['body']}".lower()
    for kw in skip_keywords:
        if kw.lower() in combined:
            return kw  # type: ignore[no-any-return]
    return None


# ─── Collect review context ──────────────────────────────────────────────────


def collect_review_context(
    pr_data: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, str]:
    """Collect relevant source files for review context.

    Gathers files that are either changed by the PR or adjacent files
    that provide context for understanding the changes.

    Args:
        pr_data: PR data from :func:`get_pr_data`.
        profile: Maintainer profile dict.

    Returns:
        Dict mapping file paths to their contents.
    """
    agent_config = profile.get("agent", {})
    max_context = agent_config.get("max_context_files", 60)

    changed = set(pr_data["changed_files"])

    # Collect all repo files, prioritizing:
    # 1. Changed files (highest priority)
    # 2. Test files corresponding to changed source files
    # 3. Config / build files
    # 4. Other source files
    all_files = collect_repo_files(max_files=max_context + len(changed))

    # Start with changed files that exist in our collection
    context: dict[str, str] = {}
    for path in changed:
        if path in all_files:
            context[path] = all_files[path]

    # Add non-changed files as additional context (up to max_context)
    remaining = max_context - len(context)
    for path, content in all_files.items():
        if path not in context and remaining > 0:
            context[path] = content
            remaining -= 1

    return context


def build_review_context_string(
    pr_data: dict[str, Any],
    files: dict[str, str],
) -> str:
    """Build a markdown context string for the LLM review prompt.

    Includes the PR diff first, then relevant source files.

    Args:
        pr_data: PR data dict.
        files: Collected source files.

    Returns:
        Markdown string with diff blocks and file contents.
    """
    parts = []

    # ── PR diff section ──
    parts.append("## Pull Request Diff")
    parts.append(f"**{pr_data['number']} files changed, "
                 f"+{pr_data['additions']} / -{pr_data['deletions']}**")
    parts.append("")

    for f in pr_data["files"]:
        parts.append(f"### {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})")
        parts.append(f"```diff\n{f['patch']}\n```")
        parts.append("")

    # ── Full file contents for context ──
    # Only include files NOT already shown in the diff (avoid duplication)
    changed_paths = {f["filename"] for f in pr_data["files"]}
    context_files = {k: v for k, v in files.items() if k not in changed_paths}

    if context_files:
        parts.append("## Repository Context (Unchanged Files)")
        parts.append(build_context_string(context_files))

    return "\n".join(parts)


# ─── LLM interaction ────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """\
You are an expert software engineer conducting a thorough code review.
Your job is to analyze a GitHub pull request and provide a structured,
actionable review for the project maintainer.

Review criteria:
- Code correctness: Does the code do what it claims? Are there bugs?
- Code style: Does it follow the maintainer's preferences?
- Tech stack: Does it avoid disallowed technologies?
- PR standards: Does it meet file count, test coverage requirements?
- Security: Are there any obvious security issues (SQL injection, XSS, secrets, etc.)?
- Performance: Are there obvious performance problems?
- Maintainability: Is the code clear, well-structured, well-named?

Respond with a single valid JSON object — no markdown fences, no explanation:

{
  "approval_recommendation": "approve | request_changes | comment",
  "summary": "One paragraph overall assessment.",
  "issues": [
    {
      "severity": "critical | major | minor | nit",
      "file": "path/to/file.py",
      "line": 42,
      "message": "Clear description of the problem.",
      "suggestion": "How to fix or improve (code example if helpful)."
    }
  ],
  "style_violations": [
    {
      "file": "path/to/file.py",
      "description": "Style issue found."
    }
  ],
  "positive_notes": [
    "What the PR does well."
  ],
  "test_recommendation": "Suggested tests that should be added or none if adequate.",
  "security_concerns": [
    "Any security issues found."
  ]
}

Approval guidelines:
- "approve": Code is correct, well-tested, follows style, no issues.
- "request_changes": Has bugs, security issues, or significant style violations.
- "comment": No blocking issues, but has suggestions for improvement.

Be concise and actionable. Focus on what matters most to the maintainer.
"""


def call_llm_for_review(
    pr_data: dict[str, Any],
    context_str: str,
    profile: dict[str, Any],
    llm_client: LLMClient,
) -> tuple[dict[str, Any], TokenUsage]:
    """Call the LLM to generate a code review.

    Args:
        pr_data: PR data from :func:`get_pr_data`.
        context_str: Formatted review context string.
        profile: Maintainer profile dict.
        llm_client: Unified LLM client.

    Returns:
        Tuple of (parsed JSON response, token usage info).
    """
    style_config = profile.get("style", {})
    code_style = style_config.get("code_style", "follow existing patterns")
    tech_config = profile.get("tech", {})
    preferred = tech_config.get("preferred", [])
    avoided = tech_config.get("avoid", [])
    pr_config = profile.get("pr", {})

    tech_note = ""
    if preferred:
        tech_note += f"\n- Preferred tech stack: {', '.join(preferred)}"
    if avoided:
        tech_note += f"\n- Tech stack to avoid (flag if introduced): {', '.join(avoided)}"

    pr_standards = ""
    if pr_config.get("min_tests"):
        pr_standards += "\n- Tests are required for new code."
    if pr_config.get("max_files_per_pr"):
        pr_standards += f"\n- Max files per PR: {pr_config['max_files_per_pr']}"
    if pr_config.get("require_changelog"):
        pr_standards += "\n- Changelog entry is required."

    user_prompt = f"""\
## PR #{pr_data['number']}: {pr_data['title']}

**Author:** {pr_data['author']}
**Base:** {pr_data['base_branch']} ← **Head:** {pr_data['head_branch']}
**Changes:** {pr_data['changed_files_count']} files, +{pr_data['additions']}/-{pr_data['deletions']}

### PR Description
{pr_data['body']}

### Recent discussion
{', '.join(f"@{c['author']}" for c in pr_data.get('comments', [])) or 'None'}

## Maintainer Preferences
- Code style: {code_style}
{tech_note}
{pr_standards}

## Code to Review
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
        use_stream = stream and attempt == 0

        response = llm_client.chat(
            system=REVIEW_SYSTEM_PROMPT,
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
                        "JSON object."
                    ),
                })
            else:
                raise LLMParseError(
                    f"LLM JSON parsing failed after {max_retries + 1} attempts. "
                    f"Last error: {err}"
                ) from err

    raise LLMParseError("LLM JSON parsing failed")


# ─── Format review comment ───────────────────────────────────────────────────


def format_review_comment(
    pr_data: dict[str, Any],
    review: dict[str, Any],
    usage: TokenUsage,
    profile: dict[str, Any],
) -> str:
    """Format the LLM review response into a beautiful markdown comment.

    Args:
        pr_data: PR data dict.
        review: LLM review response.
        usage: Token usage info.
        profile: Maintainer profile.

    Returns:
        Markdown string for the PR comment.
    """
    recommendation = review.get("approval_recommendation", "comment")
    emoji_map = {
        "approve": "✅",
        "request_changes": "🔴",
        "comment": "💬",
    }
    emoji = emoji_map.get(recommendation, "💬")

    lines = [
        "## 🤖 RepoKeeper Code Review",
        "",
        f"**PR:** #{pr_data['number']} — {pr_data['title']}",
        f"**Author:** @{pr_data['author']} · "
        f"{pr_data['changed_files_count']} files · +{pr_data['additions']}/-{pr_data['deletions']}",
        "",
        f"### {emoji} Recommendation: **{recommendation.replace('_', ' ').title()}**",
        "",
        review.get("summary", "(No summary provided)"),
        "",
    ]

    # ── Issues ──
    issues = review.get("issues", [])
    if issues:
        lines.append("### 🔍 Issues Found")
        lines.append("")
        lines.append("| Severity | File | Line | Issue |")
        lines.append("|----------|------|------|-------|")
        for issue in issues[:30]:
            sev_emoji = {"critical": "🔴", "major": "🟠", "minor": "🟡", "nit": "⚪"}.get(
                issue.get("severity", "minor"), "⚪"
            )
            msg = issue.get("message", "").replace("|", "\\|")[:120]
            suggestion = issue.get("suggestion", "")
            display = f"{msg}"
            if suggestion:
                display += f" — *{suggestion[:200]}*"
            lines.append(
                f"| {sev_emoji} {issue.get('severity', 'minor')} "
                f"| `{issue.get('file', '?')}` "
                f"| {issue.get('line', '-')} "
                f"| {display} |"
            )
        lines.append("")

        # Detailed issues
        for i, issue in enumerate(issues[:15], 1):
            lines.append(f"#### Issue {i}: [{issue.get('severity', 'minor')}] {issue.get('file', '?')}:{issue.get('line', '-')}")
            lines.append(f"**Problem:** {issue.get('message', '')}")
            if issue.get("suggestion"):
                lines.append(f"**Suggestion:** {issue.get('suggestion')}")
            lines.append("")
    else:
        lines.append("### ✨ No Issues Found")
        lines.append("")
        lines.append("No code issues detected by automated review.")
        lines.append("")

    # ── Style violations ──
    style_violations = review.get("style_violations", [])
    if style_violations:
        lines.append("### 🎨 Style Violations")
        lines.append("")
        for sv in style_violations[:10]:
            lines.append(f"- **`{sv.get('file', '?')}`**: {sv.get('description', '')}")
        lines.append("")

    # ── Security ──
    security = review.get("security_concerns", [])
    if security:
        lines.append("### 🔒 Security Concerns")
        lines.append("")
        for s in security[:5]:
            lines.append(f"- ⚠️ {s}")
        lines.append("")

    # ── Test recommendation ──
    test_rec = review.get("test_recommendation", "")
    if test_rec:
        lines.append("### 🧪 Test Recommendation")
        lines.append("")
        lines.append(test_rec)
        lines.append("")

    # ── Positive notes ──
    positive = review.get("positive_notes", [])
    if positive:
        lines.append("### 👏 Positive Highlights")
        lines.append("")
        for note in positive[:5]:
            lines.append(f"- ✅ {note}")
        lines.append("")

    # ── Cost note ──
    if usage.total_tokens > 0:
        lines.append(
            f"---\n"
            f"**Estimated cost:** ~${usage.cost_usd:.6f} "
            f"({usage.total_tokens} tokens, {usage.model})"
        )

    lines.append("")
    lines.append(
        "*🤖 Generated by [RepoKeeper](https://github.com/shenxianpeng/repokeeper) "
        "— AI-powered open source maintenance. "
        "This is an automated suggestion, not a substitute for human review.*"
    )

    return "\n".join(lines)


# ─── Main entry point ────────────────────────────────────────────────────────


def run_review(
    gh_token: str | None = None,
    repository: str | None = None,
    pr_number: int | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the Code Review Agent end-to-end.

    Args:
        gh_token: GitHub personal access token.
        repository: Repository slug (``owner/repo``).
        pr_number: Pull request number.
        llm_api_key: LLM API key.
        llm_base_url: OpenAI-compatible API base URL.
        profile_path: Path to repokeeper.yml.

    Returns:
        Dict with result info (``review_posted``, ``approval_recommendation``,
        ``issues_count``, ``error``).
    """
    gh_token = (
        gh_token
        or os.environ.get("REPOKEEPER_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    repository = repository or os.environ.get("GITHUB_REPOSITORY")
    pr_number = pr_number or int(os.environ.get("PR_NUMBER", "0"))
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
    if not pr_number:
        missing.append("PR_NUMBER or --pr")
    if not llm_api_key:
        missing.append("DEEPSEEK_API_KEY or OPENAI_API_KEY")
    if missing:
        raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

    profile = load_profile(profile_path)

    # Check if agent is enabled (reuse agent.implement for review gating)
    if not profile.get("agent", {}).get("implement", True):
        return {
            "review_posted": False,
            "reason": "Agent implementation disabled in profile.",
        }

    gh = Github(gh_token)
    llm = LLMClient(api_key=llm_api_key, base_url=llm_base_url)
    assert repository is not None

    try:
        repo = gh.get_repo(repository)
    except Exception as exc:
        logger.error("Failed to access repository: %s", exc)
        return {"review_posted": False, "reason": str(exc)}

    try:
        pr_obj = repo.get_pull(pr_number)
    except Exception as exc:
        logger.error("Failed to access PR #%d: %s", pr_number, exc)
        return {"review_posted": False, "reason": f"PR #{pr_number} not found: {exc}"}

    pr_data = get_pr_data(repo, pr_number)

    logger.info("PR #%d: %s by @%s", pr_number, pr_data["title"], pr_data["author"])

    # Check skip keywords
    skip_kw = check_review_skip_keywords(pr_data, profile)
    if skip_kw:
        logger.info("Skipping review: matched skip keyword '%s'", skip_kw)
        pr_obj.create_issue_comment(
            f"🤖 **RepoKeeper** skipped review: matched skip keyword `{skip_kw}`.\n\n"
            f"Remove the keyword or request a manual review."
        )
        return {
            "review_posted": False,
            "reason": f"Skip keyword: {skip_kw}",
        }

    # Acknowledge
    pr_obj.create_issue_comment(
        "🤖 **RepoKeeper** is reviewing this PR — analyzing changes..."
    )

    try:
        # Collect review context
        logger.info("Collecting review context...")
        files = collect_review_context(pr_data, profile)
        context_str = build_review_context_string(pr_data, files)
        logger.info(
            "Review context: %d files, ~%d tokens",
            len(files) + len(pr_data["files"]),
            estimate_tokens(files),
        )

        # Call LLM
        model = profile.get("agent", {}).get("model", "deepseek-chat")
        logger.info("Calling LLM for review (%s)...", model)
        review, usage = call_llm_for_review(pr_data, context_str, profile, llm)

        if usage.total_tokens > 0:
            logger.info(
                "LLM usage: %d tokens · estimated $%.6f (model: %s)",
                usage.total_tokens, usage.cost_usd, usage.model,
            )

        # Format and post review
        comment = format_review_comment(pr_data, review, usage, profile)
        post_review_comment(pr_obj, comment)
        logger.info("Review posted for PR #%d", pr_number)

        return {
            "review_posted": True,
            "approval_recommendation": review.get("approval_recommendation", "comment"),
            "issues_count": len(review.get("issues", [])),
            "style_violations_count": len(review.get("style_violations", [])),
            "security_concerns_count": len(review.get("security_concerns", [])),
        }

    except LLMParseError as exc:
        logger.error("LLM parse error: %s", exc)
        pr_obj.create_issue_comment(
            f"🤖 **RepoKeeper** review failed: could not parse LLM response.\n\n"
            f"```\n{exc}\n```\n\n"
            f"Please review manually."
        )
        return {"review_posted": False, "reason": str(exc)}

    except Exception as exc:
        logger.error("Review error: %s", exc)
        pr_obj.create_issue_comment(
            f"🤖 **RepoKeeper** review encountered an error:\n\n"
            f"```\n{exc}\n```\n\n"
            f"Please review manually. Check the [workflow logs]"
            f"(https://github.com/{repository}/actions) for details."
        )
        raise


if __name__ == "__main__":
    run_review()
