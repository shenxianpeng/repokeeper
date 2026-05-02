#!/usr/bin/env python3
"""
Module 3: Implementation Agent

Triggered when an issue is labeled `agent-todo` or when a maintainer
comments `@repokeeper go`. Reads the codebase + issue description,
generates an implementation plan, submits a PR with a summary.

Uses the Maintainer Profile (Module 4) for code style, tone, PR standards,
tech stack preferences, and skip keywords.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from github import Github
from github.GithubException import GithubException, UnknownObjectException
from openai import OpenAI

from .profile import load_profile

# ─── Repo context collection ─────────────────────────────────────────────────

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".go", ".java", ".rb", ".sh",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".md", ".txt",
    ".json", ".rst",
}
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".tox",
    "venv", ".venv", "dist", "build", ".eggs", ".mypy_cache",
}
MAX_FILE_SIZE = 40_000
MAX_FILES = 40

# Paths the agent must never modify (GitHub security: no workflows permission)
BLOCKED_PREFIXES = (".github/workflows/",)


def collect_repo_files(max_files: int = MAX_FILES) -> dict[str, str]:
    """Walk the repo and return {path: content} for source files.

    Args:
        max_files: Maximum number of files to include.

    Returns:
        Dict mapping file paths to their contents.
    """
    files: dict[str, str] = {}
    for p in sorted(Path(".").rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        if p.suffix not in SOURCE_EXTENSIONS:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(content) > MAX_FILE_SIZE:
            continue
        files[str(p)] = content

    if len(files) > max_files:
        priority = {
            k: v for k, v in files.items()
            if "readme" in k.lower() or k.endswith((".yml", ".toml", ".cfg", ".ini"))
        }
        source = {k: v for k, v in files.items() if k not in priority}
        remaining = max_files - len(priority)
        files = {**priority, **dict(list(source.items())[:remaining])}

    return files


def build_context_string(files: dict[str, str]) -> str:
    """Build a markdown context string from collected files."""
    parts = []
    for path, content in files.items():
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


# ─── GitHub helpers ──────────────────────────────────────────────────────────

def get_issue_data(repo, number: int) -> dict:
    """Extract structured data from a GitHub issue."""
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


def post_comment(issue_obj, message: str):
    """Post a comment on the issue."""
    issue_obj.create_comment(message)


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


def check_skip_keywords(issue_data: dict, profile: dict) -> str | None:
    """Check if the issue matches any skip keywords from the profile.

    Args:
        issue_data: Issue data dict.
        profile: Maintainer profile.

    Returns:
        Matched keyword if found, None otherwise.
    """
    skip_keywords = profile.get("agent", {}).get("skip_keywords", [])
    if not skip_keywords:
        return None

    combined = f"{issue_data['title']} {issue_data['body']}".lower()
    for kw in skip_keywords:
        if kw.lower() in combined:
            return kw
    return None


def _parse_llm_json(raw: str) -> dict:
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
    # Pattern: ```json ... ``` or ``` ... ```
    fence_pattern = r"```(?:json)?\s*\n(.*?)\n```"
    m = re.search(fence_pattern, text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    elif text.startswith("```"):
        # Partial fence: ```json\n... or ```\n...
        inner = text.split("```", 2)
        if len(inner) >= 2:
            candidate = inner[1]
            if candidate.startswith("json"):
                candidate = candidate[4:]
            text = candidate.strip()

    # ── Try direct parse ──
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        first_error = err

    # ── Try extracting the outermost JSON object ──
    # Some LLMs add explanatory text before/after the JSON.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        extracted = text[start:end + 1]
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    # ── Try fixing common unterminated string by adding missing quote + closing ──
    # This handles the most frequent LLM error: an unescaped quote inside a file
    # content string, which breaks the JSON. We try to find a valid closure.
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # ── Give up with a clear error ──
    raise ValueError(
        f"Failed to parse LLM JSON response. First error: {first_error}\n"
        f"Raw response (last 2000 chars): ...{text[-2000:]}"
    )


def _repair_truncated_json(text: str) -> str | None:
    """Attempt to repair a truncated/incomplete JSON string.

    Tries to find the last complete key-value pair and close the object.
    Returns the repaired string or None if repair is not possible.
    """
    # Find the last complete "key": "value" or "key": value pair
    # Look for a pattern like "changes": { ... }
    # If the string is unterminated, try adding closing quotes/braces.

    # Strategy: walk backwards, tracking depth, to find where the string
    # got cut off. Then try to close it.
    depth = 0
    in_string = False
    escape_next = False
    last_key_end = -1

    for i, ch in enumerate(text):
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
            depth += 1
        elif ch in "}]":
            depth -= 1

    if depth <= 0:
        return None  # Not a truncation issue

    # Try adding enough closing quotes and braces
    repaired = text.rstrip()
    # If we're inside a string, close it first
    if in_string:
        repaired += '"'
    # Close remaining brackets
    # We need to figure out what's open - simplified: just try closing braces
    # Walk backwards to determine open structure
    close_chars = []
    escape = False
    in_str = False
    for ch in reversed(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "}":
            close_chars.append("{")
        elif ch == "]":
            close_chars.append("[")
        elif ch == "{":
            if close_chars and close_chars[-1] == "{":
                close_chars.pop()
        elif ch == "[":
            if close_chars and close_chars[-1] == "[":
                close_chars.pop()

    # close_chars now contains the OPEN characters that haven't been closed,
    # in order from innermost to outermost.
    closing = ""
    for open_ch in close_chars:
        if open_ch == "{":
            closing += "}"
        elif open_ch == "[":
            closing += "]"

    if closing:
        repaired += closing

    return repaired if repaired != text else None


def call_llm(
    issue_data: dict,
    context_str: str,
    profile: dict,
    llm_client: OpenAI,
) -> dict:
    """Call the LLM to generate an implementation plan.

    Args:
        issue_data: Structured issue data.
        context_str: Repository source context string.
        profile: Maintainer profile with style preferences.
        llm_client: OpenAI-compatible client.

    Returns:
        Parsed JSON response from the LLM.
    """
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
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    max_retries = 2
    last_error = None

    for attempt in range(max_retries + 1):
        response = llm_client.chat.completions.create(
            model=profile.get("agent", {}).get("model", "deepseek-chat"),
            messages=messages,
            temperature=profile.get("agent", {}).get("temperature", 0.1),
            max_tokens=8000,
        )

        raw = response.choices[0].message.content.strip()

        try:
            return _parse_llm_json(raw)
        except ValueError as err:
            last_error = err
            if attempt < max_retries:
                print(
                    f"[repokeeper] JSON parse failed (attempt {attempt + 1}), retrying...",
                    flush=True,
                )
                # Append error feedback to messages for the retry
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response contained invalid JSON. "
                        f"Error: {err}\n\n"
                        f"Please fix the JSON and respond with ONLY the corrected "
                        f"JSON object. Ensure all strings are properly escaped."
                    ),
                })
            else:
                raise RuntimeError(
                    f"LLM JSON parsing failed after {max_retries + 1} attempts. "
                    f"Last error: {err}"
                ) from err


# ─── Git + PR operations ─────────────────────────────────────────────────────

def _git(*args, check=True, capture=False):
    kwargs = {"check": check}
    if capture:
        kwargs.update({"capture_output": True, "text": True})
    return subprocess.run(["git", *args], **kwargs)


def apply_and_push(
    implementation: dict,
    gh_token: str,
    repository: str,
) -> tuple[str, list[str]]:
    """Create a branch, apply changes, push.

    Args:
        implementation: LLM response with changes/new_files.
        gh_token: GitHub token for authentication.
        repository: Repository slug (owner/name).

    Returns:
        Tuple of (branch_name, list_of_changed_files).
    """
    branch = implementation["branch_name"]

    _git("config", "user.email", "repokeeper[bot]@users.noreply.github.com")
    _git("config", "user.name", "repokeeper[bot]")
    _git("checkout", "-b", branch)

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

    _git("add", "-A")

    # Verify something changed
    diff = _git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise RuntimeError("Agent produced no file changes.")

    _git("commit", "-m", implementation["commit_message"])

    remote_url = f"https://x-access-token:{gh_token}@github.com/{repository}.git"
    _git("remote", "set-url", "origin", remote_url)
    _git("push", "origin", branch)

    return branch, diff.splitlines()


def create_pr(
    repo,
    issue_data: dict,
    implementation: dict,
    branch: str,
    changed_files: list[str],
    profile: dict,
) -> str:
    """Create a GitHub pull request.

    Args:
        repo: PyGithub Repository object.
        issue_data: Issue data dict.
        implementation: LLM response.
        branch: Branch name.
        changed_files: List of changed file paths.
        profile: Maintainer profile.

    Returns:
        PR URL.
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
                "REPOKEEPER_GITHUB_TOKEN to a token with contents and pull request write access."
            ) from exc
        raise
    return pr.html_url


def strip_blocked_paths(implementation: dict) -> list[str]:
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


def validate_implementation(implementation: dict, profile: dict) -> list[str]:
    """Validate an implementation against profile constraints.

    Args:
        implementation: LLM response dict.
        profile: Maintainer profile.

    Returns:
        List of validation issues (empty = valid).
    """
    issues: list[str] = []

    pr_config = profile.get("pr", {})

    # Check max files
    max_files = pr_config.get("max_files_per_pr", 15)
    total_files = len(implementation.get("changes", {})) + len(implementation.get("new_files", {}))
    if total_files > max_files:
        issues.append(f"Implementation touches {total_files} files (max: {max_files})")

    # Check branch naming
    branch = implementation.get("branch_name", "")
    if not branch.startswith("repokeeper/"):
        issues.append("branch_name must start with 'repokeeper/'")

    return issues


# ─── Main entry point ────────────────────────────────────────────────────────

def run_agent(
    gh_token: str | None = None,
    repository: str | None = None,
    issue_number: int | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    profile_path: str | Path | None = None,
) -> dict:
    """Run the Implementation Agent end-to-end.

    Returns:
        Dict with result info (pr_url, skip_reason, error).
    """
    gh_token = gh_token or os.environ.get("REPOKEEPER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repository = repository or os.environ.get("GITHUB_REPOSITORY")
    issue_number = issue_number or int(os.environ.get("ISSUE_NUMBER", "0"))
    llm_api_key = llm_api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
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
    llm = OpenAI(api_key=llm_api_key, base_url=llm_base_url)
    try:
        repo = gh.get_repo(repository)
    except UnknownObjectException:
        # Token doesn't have access to this repo — try GITHUB_TOKEN as fallback
        fallback = os.environ.get("GITHUB_TOKEN")
        if fallback and fallback != gh_token:
            print("[repokeeper] Primary token unauthorized, falling back to GITHUB_TOKEN", flush=True)
            gh = Github(fallback)
            repo = gh.get_repo(repository)
            gh_token = fallback
        else:
            raise
    issue_obj = repo.get_issue(issue_number)
    issue_data = get_issue_data(repo, issue_number)

    print(f"[repokeeper] Issue #{issue_number}: {issue_data['title']}", flush=True)

    # Check skip keywords early
    skip_kw = check_skip_keywords(issue_data, profile)
    if skip_kw:
        print(f"[repokeeper] Skipping: matched skip keyword '{skip_kw}'", flush=True)
        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** skipped this issue: matched skip keyword `{skip_kw}`.\n\n"
            f"Remove the keyword or clarify if you still want automatic implementation.",
        )
        return {"skip": True, "reason": f"Skip keyword: {skip_kw}", "pr_url": None}

    post_comment(issue_obj, "🤖 **RepoKeeper** is analyzing this issue — working on an implementation...")

    try:
        # Collect repo context
        print("[repokeeper] Collecting repository context...", flush=True)
        max_context = profile.get("agent", {}).get("max_context_files", 40)
        files = collect_repo_files(max_files=max_context)
        print(f"[repokeeper] Loaded {len(files)} files", flush=True)
        context_str = build_context_string(files)

        # Call LLM
        print(f"[repokeeper] Calling LLM ({profile.get('agent', {}).get('model', 'deepseek-chat')})...", flush=True)
        result = call_llm(issue_data, context_str, profile, llm)

        # Agent decided to skip
        if result.get("skip"):
            reason = result.get("reason", "No reason provided.")
            print(f"[repokeeper] Skipping: {reason}", flush=True)
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** decided not to implement this automatically:\n\n> {reason}\n\n"
                f"Please implement manually or clarify the issue.",
            )
            return {"skip": True, "reason": reason, "pr_url": None}

        # Strip blocked paths (auto-fix, warn but don't skip)
        stripped = strip_blocked_paths(result)
        if stripped:
            print(f"[repokeeper] Stripped blocked paths: {', '.join(stripped)}", flush=True)
            if not result.get("changes") and not result.get("new_files"):
                post_comment(
                    issue_obj,
                    "🤖 **RepoKeeper** skipped: all planned changes were in blocked paths"
                    f" ({', '.join(stripped)}).\n\n"
                    "Please implement manually or clarify the issue.",
                )
                return {"skip": True, "reason": f"All changes in blocked paths: {', '.join(stripped)}", "pr_url": None}

        # Validate against profile constraints
        validation_issues = validate_implementation(result, profile)
        if validation_issues:
            issues_str = "\n".join(f"- {v}" for v in validation_issues)
            print(f"[repokeeper] Validation issues:\n{issues_str}", flush=True)
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** found issues with the implementation plan:\n\n{issues_str}\n\n"
                f"Please review and adjust the profile constraints or the issue scope.",
            )
            return {"skip": True, "reason": f"Validation failed: {issues_str}", "pr_url": None}

        print(f"[repokeeper] Plan: {result['summary']}", flush=True)

        # Apply changes and push
        branch, changed_files = apply_and_push(result, gh_token, repository)

        # Create PR
        pr_url = create_pr(repo, issue_data, result, branch, changed_files, profile)

        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** finished implementation.\n\n"
            f"**PR:** {pr_url}\n\n"
            f"**Summary:** {result['summary']}\n\n"
            f"Please review the changes before merging.",
        )
        print(f"[repokeeper] Done — PR: {pr_url}", flush=True)
        return {"skip": False, "reason": "", "pr_url": pr_url}

    except Exception as exc:
        print(f"[repokeeper] ERROR: {exc}", file=sys.stderr, flush=True)
        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** encountered an error:\n\n```\n{exc}\n```\n\n"
            f"Check the [workflow logs]({repo.html_url}/actions) for details.",
        )
        raise


# ─── CLI entry point (backwards-compatible) ──────────────────────────────────

if __name__ == "__main__":
    # When run as a script from GitHub Actions
    run_agent()
