#!/usr/bin/env python3
"""
RepoKeeper Agent
AI-powered open source maintainer — reads an issue, implements code, opens a PR.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml
from github import Github
from openai import OpenAI

# ─── Environment ─────────────────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]
ISSUE_NUMBER = int(os.environ["ISSUE_NUMBER"])

# ─── Clients ─────────────────────────────────────────────────────────────────

gh = Github(GITHUB_TOKEN)
llm = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)


# ─── Profile ─────────────────────────────────────────────────────────────────

def load_profile() -> dict:
    """Load repokeeper.yml from the repo root."""
    path = Path("repokeeper.yml")
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


# ─── GitHub helpers ──────────────────────────────────────────────────────────

def get_issue_data(repo, number: int) -> dict:
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
    issue_obj.create_comment(message)


# ─── Repo context ────────────────────────────────────────────────────────────

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".go", ".java", ".rb", ".sh",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".md", ".txt",
    ".json", ".rst",
}
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".tox",
    "venv", ".venv", "dist", "build", ".eggs", ".mypy_cache",
}
MAX_FILE_SIZE = 40_000   # chars — skip minified / generated files
MAX_FILES = 40


def collect_repo_files() -> dict[str, str]:
    """Walk the repo and return {path: content} for source files."""
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

    # If too many files, keep config/docs first, then source
    if len(files) > MAX_FILES:
        priority = {
            k: v for k, v in files.items()
            if "readme" in k.lower() or k.endswith((".yml", ".toml", ".cfg", ".ini"))
        }
        source = {k: v for k, v in files.items() if k not in priority}
        remaining = MAX_FILES - len(priority)
        files = {**priority, **dict(list(source.items())[:remaining])}

    return files


def build_context_string(files: dict[str, str]) -> str:
    parts = []
    for path, content in files.items():
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


# ─── LLM ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert software engineer acting as an open source maintainer's AI deputy.
Your job is to implement a GitHub issue by making precise, minimal code changes.

Rules:
- Follow the existing code style exactly.
- Only change what is necessary to close the issue.
- Do not add unrequested features, refactors, or comments.
- If the issue is unclear or unsafe to implement autonomously, set "skip": true and explain why.

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


def call_llm(issue_data: dict, context_str: str, profile: dict) -> dict:
    style_note = profile.get("style", {}).get("code_style", "follow existing patterns")

    user_prompt = f"""\
## Issue #{issue_data['number']}: {issue_data['title']}

{issue_data['body']}

## Recent discussion
{json.dumps(issue_data['comments'], indent=2)}

## Maintainer style preference
{style_note}

## Repository source files
{context_str}
"""

    response = llm.chat.completions.create(
        model=profile.get("agent", {}).get("model", "deepseek-chat"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=8000,
    )

    raw = response.choices[0].message.content.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw.strip())


# ─── Git + PR ─────────────────────────────────────────────────────────────────

def git(*args, check=True, capture=False):
    kwargs = {"check": check}
    if capture:
        kwargs.update({"capture_output": True, "text": True})
    return subprocess.run(["git", *args], **kwargs)


def apply_and_push(implementation: dict, issue_data: dict, base_branch: str):
    """Create a branch, apply changes, push, return branch name."""
    branch = implementation["branch_name"]

    git("config", "user.email", "repokeeper[bot]@users.noreply.github.com")
    git("config", "user.name", "repokeeper[bot]")
    git("checkout", "-b", branch)

    # Write modified files
    for filepath, content in implementation.get("changes", {}).items():
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # Write new files
    for filepath, content in implementation.get("new_files", {}).items():
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    git("add", "-A")

    # Verify something changed
    diff = git("diff", "--cached", "--name-only", capture=True).stdout.strip()
    if not diff:
        raise RuntimeError("Agent produced no file changes.")

    git("commit", "-m", implementation["commit_message"])

    remote_url = (
        f"https://x-access-token:{GITHUB_TOKEN}"
        f"@github.com/{GITHUB_REPOSITORY}.git"
    )
    git("remote", "set-url", "origin", remote_url)
    git("push", "origin", branch)

    return branch, diff.splitlines()


def create_pr(repo, issue_data: dict, implementation: dict, branch: str, changed_files: list) -> str:
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
    pr = repo.create_pull(
        title=f"{implementation['commit_message']} (#{issue_data['number']})",
        body=body,
        head=branch,
        base=repo.default_branch,
    )
    return pr.html_url


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    profile = load_profile()
    repo = gh.get_repo(GITHUB_REPOSITORY)
    issue_obj = repo.get_issue(ISSUE_NUMBER)
    issue_data = get_issue_data(repo, ISSUE_NUMBER)

    print(f"[repokeeper] Issue #{ISSUE_NUMBER}: {issue_data['title']}")
    post_comment(issue_obj, "🤖 **RepoKeeper** is analyzing this issue — working on an implementation...")

    try:
        # Collect repo context
        print("[repokeeper] Collecting repository context...")
        files = collect_repo_files()
        print(f"[repokeeper] Loaded {len(files)} files")
        context_str = build_context_string(files)

        # Call LLM
        print("[repokeeper] Calling DeepSeek API...")
        result = call_llm(issue_data, context_str, profile)

        # Agent decided to skip
        if result.get("skip"):
            reason = result.get("reason", "No reason provided.")
            print(f"[repokeeper] Skipping: {reason}")
            post_comment(
                issue_obj,
                f"🤖 **RepoKeeper** decided not to implement this automatically:\n\n> {reason}\n\n"
                f"Please implement manually or clarify the issue.",
            )
            return

        print(f"[repokeeper] Plan: {result['summary']}")

        # Apply changes and push
        branch, changed_files = apply_and_push(result, issue_data, repo.default_branch)

        # Create PR
        pr_url = create_pr(repo, issue_data, result, branch, changed_files)

        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** finished implementation.\n\n"
            f"**PR:** {pr_url}\n\n"
            f"**Summary:** {result['summary']}\n\n"
            f"Please review the changes before merging.",
        )
        print(f"[repokeeper] Done — PR: {pr_url}")

    except Exception as exc:
        print(f"[repokeeper] ERROR: {exc}", file=sys.stderr)
        post_comment(
            issue_obj,
            f"🤖 **RepoKeeper** encountered an error:\n\n```\n{exc}\n```\n\n"
            f"Check the [workflow logs]({repo.html_url}/actions) for details.",
        )
        raise


if __name__ == "__main__":
    main()
