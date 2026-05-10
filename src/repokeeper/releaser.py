"""
Module 6: Draft Release Generator

Generates release notes from commit history using AI and creates draft
GitHub releases.  Supports both PR-based commits and direct pushes to
the main branch.

Workflow:
  1. Find the latest tag (or the first commit if no tags exist).
  2. Collect commits since that tag.
  3. Use AI to generate structured release notes.
  4. Create a draft GitHub release with the notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .llm_client import parse_llm_json
from .logs import get_logger
from .profile import get_module_model, load_profile

logger = get_logger("releaser")


# ─── Data models ─────────────────────────────────────────────────────────────


@dataclass
class CommitEntry:
    """A single commit in the release window."""

    sha: str
    author: str
    date: datetime
    message: str
    is_pr_merge: bool = False
    pr_title: str = ""
    pr_number: int = 0


@dataclass
class ReleaseDraft:
    """A generated draft release."""

    tag_name: str
    target_commitish: str
    title: str
    notes: str
    is_prerelease: bool = False


@dataclass
class ReleaseReport:
    """Result of a release generation run."""

    repo: str
    generated_at: datetime
    commits_scanned: int = 0
    since_tag: str = ""
    draft: ReleaseDraft | None = None
    release_url: str = ""
    error: str = ""


# ─── Commit collection ──────────────────────────────────────────────────────


def _find_latest_tag(gh_repo: Any) -> tuple[str, str]:
    """Find the latest tag on the default branch.

    Returns:
        Tuple of ``(tag_name, tag_sha)``.
        If no tags exist, returns ``("", "")``.
    """
    try:
        tags = gh_repo.get_tags()
        for tag in tags:
            # Return the first (most recent) tag
            return tag.name, tag.commit.sha  # type: ignore[no-any-return]
    except Exception as e:
        logger.warning(f"Failed to list tags: {e}")

    return "", ""


def _collect_commits_since(
    gh_repo: Any,
    since_sha: str | None = None,
    max_commits: int = 200,
) -> tuple[str, list[CommitEntry]]:
    """Collect commits since a given SHA (or from the beginning).

    Args:
        gh_repo: PyGithub Repository object.
        since_sha: SHA to start from (exclusive).  None = from the first commit.
        max_commits: Maximum commits to return.

    Returns:
        Tuple of ``(base_sha, commits)`` where ``base_sha`` is the SHA
        from the latest tag (or empty string if none).
    """
    commits: list[CommitEntry] = []
    base_sha = ""

    try:
        comparison = gh_repo.compare(
            base=since_sha or gh_repo.get_commits()[0].sha,
            head=gh_repo.default_branch,
        )

        base_sha = comparison.base_commit.sha

        for c in comparison.commits[:max_commits]:
            message = c.commit.message or ""
            # Detect PR merge commits
            is_pr = False
            pr_number = 0
            pr_title = ""

            lines = message.splitlines()
            first_line = lines[0].strip() if lines else ""

            # GitHub merge commits: "Merge pull request #N from ..."
            if first_line.startswith("Merge pull request #"):
                is_pr = True
                import re as _re
                match = _re.match(r"Merge pull request #(\d+)", first_line)
                if match:
                    pr_number = int(match.group(1))
                # The PR title is usually the commit body
                if len(lines) > 2:
                    pr_title = lines[2].strip()
            # Squash merge commits: "Title (#N)"
            elif " (#" in first_line and first_line.endswith(")"):
                is_pr = True
                import re as _re
                match = _re.search(r"\(#(\d+)\)", first_line)
                if match:
                    pr_number = int(match.group(1))
                    pr_title = first_line[: first_line.rfind(" (#")].strip()

            commits.append(CommitEntry(
                sha=c.sha,
                author=c.commit.author.name or c.commit.author.email or "unknown",
                date=c.commit.author.date,
                message=message,
                is_pr_merge=is_pr,
                pr_title=pr_title,
                pr_number=pr_number,
            ))

    except Exception as e:
        logger.warning(f"Failed to collect commits: {e}")

    return base_sha, commits


# ─── AI Release Notes Generation ────────────────────────────────────────────

RELEASE_NOTES_SYSTEM_PROMPT = """\
You are an assistant that generates well-structured GitHub release notes
from a list of commits.

Given the commit history since the last release tag, produce a release notes
document in Markdown.  Group changes into logical categories.

Respond with a single JSON object (no markdown fences):

{
  "title": "Release title (e.g. 'v1.2.3 - New Feature Summary')",
  "notes": "Full release notes in markdown",
  "is_prerelease": false
}

The notes should include sections like:
- ## What's Changed (list of notable changes with commit SHAs)
- ## New Contributors (if any first-time contributors appear)
- ## Full Changelog (link to compare view)

Format each change as: `* description by @author in <commit-sha>`

Be concise but informative.  Omit trivial/automated commits like
dependabot bumps unless they are significant.
"""


def format_commit_list(commits: list[CommitEntry], repo: str, since_tag: str) -> str:
    """Format the commit list for the LLM prompt.

    Args:
        commits: List of commits since the last tag.
        repo: Repository slug (owner/repo).
        since_tag: The previous tag name (or latest tag).

    Returns:
        Formatted string.
    """
    parts = [
        f"Repository: {repo}",
        f"Previous tag: {since_tag or '(no previous tag)'}",
        f"Commits since tag ({len(commits)} total):",
        "",
    ]

    for i, c in enumerate(commits, 1):
        sha_short = c.sha[:7] if len(c.sha) > 7 else c.sha
        date_str = c.date.strftime("%Y-%m-%d") if c.date else "?"
        first_line = c.message.split("\n")[0].strip() if c.message else "(empty)"
        pr_info = ""
        if c.is_pr_merge and c.pr_number:
            pr_info = f" (#{c.pr_number})"
        parts.append(f"  {i}. [{date_str}] {sha_short} - {c.author}: {first_line}{pr_info}")

    return "\n".join(parts)


def generate_release_notes(
    commits: list[CommitEntry],
    repo: str,
    since_tag: str,
    llm_client: Any,
    model: str = "deepseek-chat",
) -> ReleaseDraft:
    """Use AI to generate release notes from commit history.

    Args:
        commits: List of commits since the last tag.
        repo: Repository slug (owner/repo).
        since_tag: Previous tag name.
        llm_client: OpenAI-compatible LLM client.
        model: LLM model name.

    Returns:
        ReleaseDraft with title, notes, and prerelease flag.
    """
    commit_list = format_commit_list(commits, repo, since_tag)

    # Determine the next version from the latest tag
    next_tag = _bump_version(since_tag) if since_tag else "v0.1.0"

    user_prompt = f"""\
Generate release notes for {repo}.

{commit_list}

The previous tag was: {since_tag or '(none)'}
Suggest a next version tag (e.g. {next_tag}).

Respond with JSON only.
"""

    try:
        response = llm_client.chat(
            system=RELEASE_NOTES_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.2,
            max_tokens=3000,
        )

        result = parse_llm_json(response.content)
        title = result.get("title", "") or f"Release {next_tag}"
        notes = result.get("notes", "")
        is_prerelease = bool(result.get("is_prerelease", False))

        return ReleaseDraft(
            tag_name=next_tag,
            target_commitish="",
            title=title,
            notes=notes,
            is_prerelease=is_prerelease,
        )

    except Exception as e:
        logger.error(f"Release notes generation failed: {e}")
        # Fallback: simple changelog from commits
        fallback_notes = _generate_fallback_notes(commits, repo, since_tag)
        return ReleaseDraft(
            tag_name=next_tag,
            target_commitish="",
            title=f"Release {next_tag}",
            notes=fallback_notes,
            is_prerelease=False,
        )


def _bump_version(tag: str) -> str:
    """Bump the patch version of a semver tag.

    Examples:
        v1.2.3 -> v1.2.4
        1.2.3 -> 1.2.4
        v1.2 -> v1.2.1
    """
    import re as _re

    tag = tag.strip()
    match = _re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$", tag)
    if match:
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3)) if match.group(3) else 0
        prefix = "v" if tag.startswith("v") else ""
        return f"{prefix}{major}.{minor}.{patch + 1}"
    return f"{tag}.1" if tag else "v0.1.0"


def _generate_fallback_notes(
    commits: list[CommitEntry],
    repo: str,
    since_tag: str,
) -> str:
    """Generate plain markdown release notes from commits (no AI).

    Used as fallback when the LLM call fails.
    """
    parts = [
        "## What's Changed",
        "",
    ]
    for c in commits:
        first_line = c.message.split("\n")[0].strip() if c.message else "(empty)"
        sha_short = c.sha[:7] if len(c.sha) > 7 else c.sha
        parts.append(f"- {first_line} by @{c.author} in {sha_short}")

    parts.extend([
        "",
        "---",
        "_Generated by RepoKeeper Releaser_",
    ])

    return "\n".join(parts)


# ─── GitHub Release Creation ────────────────────────────────────────────────


def create_draft_release(
    gh_repo: Any,
    draft: ReleaseDraft,
) -> str:
    """Create a draft release on GitHub.

    Args:
        gh_repo: PyGithub Repository object.
        draft: ReleaseDraft with tag, title, notes.

    Returns:
        URL of the created release, or empty string on failure.
    """
    try:
        release = gh_repo.create_git_release(
            tag=draft.tag_name,
            name=draft.title,
            message=draft.notes,
            draft=True,
            prerelease=draft.is_prerelease,
            target_commitish=draft.target_commitish or gh_repo.default_branch,
        )
        logger.info(
            f"Created draft release: {draft.tag_name} "
            f"({'prerelease' if draft.is_prerelease else 'release'})"
        )
        return release.html_url  # type: ignore[no-any-return]
    except Exception as e:
        logger.error(f"Failed to create draft release: {e}")
        return ""


# ─── Main pipeline ──────────────────────────────────────────────────────────


def run_releaser(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict | None = None,
) -> ReleaseReport:
    """Run the Draft Release Generator.

    Pipeline:
        1. Find the latest tag on the default branch.
        2. Collect commits since that tag.
        3. Generate AI release notes.
        4. Create a draft GitHub release.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        profile: Maintainer profile (loaded if None).

    Returns:
        ReleaseReport with full results.
    """
    if profile is None:
        profile = load_profile()

    releaser_config = profile.get("releaser", {})
    if not releaser_config.get("enabled", True):
        logger.info(f"Releaser disabled for {repo}")
        return ReleaseReport(repo=repo, generated_at=datetime.now(), commits_scanned=0)

    model = get_module_model(profile, "releaser")
    report = ReleaseReport(repo=repo, generated_at=datetime.now())

    try:
        gh_repo = gh_client.get_repo(repo)

        # Step 1: Find latest tag
        tag_name, tag_sha = _find_latest_tag(gh_repo)
        report.since_tag = tag_name

        logger.info(
            f"📝 Releaser: generating release notes for {repo} "
            f"(since tag: {tag_name or 'beginning'})"
        )

        # Step 2: Collect commits since tag
        base_sha, commits = _collect_commits_since(
            gh_repo,
            since_sha=tag_sha if tag_sha else None,
            max_commits=releaser_config.get("max_commits", 200),
        )
        report.commits_scanned = len(commits)

        if not commits:
            logger.info("  No new commits since last tag.")
            report.error = "No new commits since last tag."
            return report

        logger.info(f"  Found {len(commits)} commits since '{tag_name or 'beginning'}'")

        # Step 3: Generate release notes
        draft = generate_release_notes(
            commits,
            repo,
            tag_name,
            llm_client,
            model=model,
        )

        # Set target commitish to the latest commit
        draft.target_commitish = commits[0].sha

        report.draft = draft

        # Step 4: Create draft release
        dry_run = releaser_config.get("dry_run", False)
        if not dry_run:
            url = create_draft_release(gh_repo, draft)
            report.release_url = url
            if url:
                logger.info(f"  Draft release created: {url}")
            else:
                logger.warning("  Failed to create draft release")
        else:
            logger.info("  Dry-run mode: skipping draft release creation")

        logger.info(
            f"  Draft: {draft.tag_name} — {draft.title}"
        )

    except Exception as e:
        logger.error(f"Releaser failed for {repo}: {e}")
        report.error = str(e)

    return report


# ─── Summary ────────────────────────────────────────────────────────────────


def generate_release_summary(report: ReleaseReport) -> str:
    """Generate a markdown summary of the release generation.

    Args:
        report: Filled ReleaseReport.

    Returns:
        Markdown string.
    """
    lines = [
        f"# 📝 Draft Release Report — [{report.repo}](https://github.com/{report.repo})",
        "",
        f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Commits scanned:** {report.commits_scanned}",
    ]

    if report.since_tag:
        lines.append(f"**Since tag:** {report.since_tag}")
    lines.append("")

    if report.error:
        lines.append(f"**Error:** {report.error}")
        lines.append("")
        return "\n".join(lines)

    if report.draft is None:
        lines.append("✅ No new commits to release.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"## {report.draft.title}")
    lines.append("")
    lines.append(f"- **Tag:** `{report.draft.tag_name}`")
    lines.append(f"- **Prerelease:** {'Yes' if report.draft.is_prerelease else 'No'}")
    if report.release_url:
        lines.append(f"- **Draft release:** [View on GitHub]({report.release_url})")
    lines.append("")

    # Include a preview of the notes (first 20 lines)
    notes_lines = report.draft.notes.strip().splitlines()
    preview = notes_lines[:20]
    if len(notes_lines) > 20:
        preview.append("")
        preview.append(f"*... and {len(notes_lines) - 20} more lines*")

    lines.append("### Release Notes Preview")
    lines.append("")
    lines.extend(preview)
    lines.append("")

    return "\n".join(lines)
