"""AI-assisted draft release note generation.

The release module collects pull requests and direct commits since the last
published release (or an explicit base ref), asks the LLM to classify and
summarize the evidence, then creates or updates a GitHub draft release.

Safety boundary: this module drafts release notes only. It does not publish a
release, move tags, bump package versions, or edit changelog files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from repokeeper.llm_client import parse_llm_json
from repokeeper.logs import get_logger
from repokeeper.profile import get_module_model, load_profile

logger = get_logger("release")


@dataclass
class ReleasePullRequest:
    """Merged pull request evidence for a draft release."""

    number: int
    title: str
    url: str
    author: str
    merged_at: datetime | None = None
    labels: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    merge_commit_sha: str = ""


@dataclass
class ReleaseCommit:
    """Direct commit evidence for a draft release."""

    sha: str
    short_sha: str
    title: str
    url: str
    author: str
    committed_at: datetime | None = None


@dataclass
class ReleaseFileChange:
    """Changed file summary from the compare range."""

    filename: str
    status: str
    additions: int = 0
    deletions: int = 0


@dataclass
class ReleaseContext:
    """Collected release evidence."""

    repo: str
    base_ref: str
    target_ref: str
    tag_name: str
    previous_release_url: str = ""
    pull_requests: list[ReleasePullRequest] = field(default_factory=list)
    direct_commits: list[ReleaseCommit] = field(default_factory=list)
    files: list[ReleaseFileChange] = field(default_factory=list)


@dataclass
class ReleaseDraftResult:
    """Result from a release draft run."""

    repo: str
    tag_name: str
    name: str
    body: str
    action: str
    html_url: str = ""
    pr_count: int = 0
    commit_count: int = 0
    dry_run: bool = False


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _short_sha(sha: str) -> str:
    return sha[:7] if sha else ""


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text and text.strip() else ""


def _next_patch_tag(tag: str) -> str:
    """Return a conservative next patch tag from a semver-like tag."""
    match = re.search(r"^(?P<prefix>v?)(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)", tag)
    if not match:
        return "v0.1.0"
    prefix = match.group("prefix")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch")) + 1
    return f"{prefix}{major}.{minor}.{patch}"


def _latest_published_release(repo_obj: Any, include_prereleases: bool = False) -> Any | None:
    """Return the newest non-draft release."""
    try:
        for release in repo_obj.get_releases():
            if getattr(release, "draft", False):
                continue
            if getattr(release, "prerelease", False) and not include_prereleases:
                continue
            return release
    except Exception as exc:
        logger.warning("Failed to read releases: %s", exc)
    return None


def _published_release_for_tag(
    repo_obj: Any,
    tag_name: str,
    include_prereleases: bool = False,
) -> Any | None:
    """Return a published release matching a tag name."""
    try:
        for release in repo_obj.get_releases():
            if getattr(release, "draft", False):
                continue
            if getattr(release, "prerelease", False) and not include_prereleases:
                continue
            if getattr(release, "tag_name", "") == tag_name:
                return release
    except Exception as exc:
        logger.warning("Failed to read release for tag %s: %s", tag_name, exc)
    return None


def _latest_tag_name(repo_obj: Any) -> str:
    try:
        for tag in repo_obj.get_tags():
            return str(tag.name)
    except Exception as exc:
        logger.warning("Failed to read tags: %s", exc)
    return ""


def _default_target_ref(repo_obj: Any, target_ref: str | None) -> str:
    return target_ref or str(getattr(repo_obj, "default_branch", "main") or "main")


def _release_search_date(release: Any | None) -> str | None:
    if release is None:
        return None
    date = _as_datetime(getattr(release, "published_at", None))
    if date is None:
        date = _as_datetime(getattr(release, "created_at", None))
    return date.date().isoformat() if date else None


def _normalize_pull_request(pr: Any) -> ReleasePullRequest:
    files: list[str] = []
    try:
        for item in pr.get_files():
            files.append(str(item.filename))
            if len(files) >= 30:
                break
    except Exception:
        files = []

    user = getattr(pr, "user", None)
    return ReleasePullRequest(
        number=int(getattr(pr, "number", 0)),
        title=str(getattr(pr, "title", "")),
        url=str(getattr(pr, "html_url", "")),
        author=str(getattr(user, "login", "unknown") if user else "unknown"),
        merged_at=_as_datetime(getattr(pr, "merged_at", None)),
        labels=[str(label.name) for label in getattr(pr, "labels", [])],
        changed_files=files,
        merge_commit_sha=str(getattr(pr, "merge_commit_sha", "") or ""),
    )


def _fetch_merged_pull_requests(
    gh_client: Any,
    repo_obj: Any,
    repo: str,
    target_ref: str,
    since_date: str | None,
    max_prs: int,
) -> list[ReleasePullRequest]:
    if since_date is None:
        return []

    query = f"repo:{repo} is:pr is:merged base:{target_ref} merged:>={since_date}"
    pull_requests: list[ReleasePullRequest] = []
    seen: set[int] = set()
    try:
        for item in gh_client.search_issues(query=query, sort="updated", order="desc"):
            number = int(getattr(item, "number", 0))
            if number in seen:
                continue
            seen.add(number)
            pr = repo_obj.get_pull(number)
            pull_requests.append(_normalize_pull_request(pr))
            if len(pull_requests) >= max_prs:
                break
    except Exception as exc:
        logger.warning("Failed to search merged pull requests: %s", exc)
    return sorted(pull_requests, key=lambda pr: pr.merged_at or datetime.min)


def _normalize_commit(commit: Any) -> ReleaseCommit:
    sha = str(getattr(commit, "sha", "") or "")
    commit_data = getattr(commit, "commit", None)
    author_data = getattr(commit_data, "author", None)
    user = getattr(commit, "author", None)
    message = str(getattr(commit_data, "message", "") or "")
    return ReleaseCommit(
        sha=sha,
        short_sha=_short_sha(sha),
        title=_first_line(message),
        url=str(getattr(commit, "html_url", "")),
        author=str(getattr(user, "login", "") or getattr(author_data, "name", "") or "unknown"),
        committed_at=_as_datetime(getattr(author_data, "date", None)),
    )


def _looks_like_merge_commit(title: str) -> bool:
    return title.startswith("Merge pull request ") or title.startswith("Merge branch ")


def _normalize_file_change(file_obj: Any) -> ReleaseFileChange:
    return ReleaseFileChange(
        filename=str(getattr(file_obj, "filename", "")),
        status=str(getattr(file_obj, "status", "")),
        additions=int(getattr(file_obj, "additions", 0) or 0),
        deletions=int(getattr(file_obj, "deletions", 0) or 0),
    )


def _collect_compare_data(
    repo_obj: Any,
    base_ref: str,
    target_ref: str,
    max_commits: int,
) -> tuple[list[ReleaseCommit], list[ReleaseFileChange]]:
    try:
        compare = repo_obj.compare(base_ref, target_ref)
    except Exception as exc:
        logger.warning("Failed to compare %s...%s: %s", base_ref, target_ref, exc)
        return [], []

    commits: list[ReleaseCommit] = []
    for item in getattr(compare, "commits", []):
        commits.append(_normalize_commit(item))
        if len(commits) >= max_commits:
            break

    files = [_normalize_file_change(item) for item in getattr(compare, "files", [])]
    return commits, files[:100]


def collect_release_context(
    gh_client: Any,
    repo: str,
    base_ref: str | None = None,
    target_ref: str | None = None,
    tag_name: str | None = None,
    include_prereleases: bool = False,
    max_prs: int = 75,
    max_commits: int = 150,
) -> ReleaseContext:
    """Collect merged PRs, direct commits, and changed files for release notes."""
    repo_obj = gh_client.get_repo(repo)
    target = _default_target_ref(repo_obj, target_ref)
    latest_release = _latest_published_release(repo_obj, include_prereleases)
    previous_release = (
        _published_release_for_tag(repo_obj, base_ref, include_prereleases)
        if base_ref
        else latest_release
    )
    previous_tag = str(getattr(previous_release, "tag_name", "") or "")
    latest_tag = str(getattr(latest_release, "tag_name", "") or "") or _latest_tag_name(repo_obj)
    base = base_ref or previous_tag or latest_tag
    if not base:
        base = target
    tag = tag_name or _next_patch_tag(base)

    prs = _fetch_merged_pull_requests(
        gh_client,
        repo_obj,
        repo,
        target,
        _release_search_date(previous_release),
        max_prs,
    )
    commits, files = _collect_compare_data(repo_obj, base, target, max_commits)
    pr_merge_shas = {pr.merge_commit_sha for pr in prs if pr.merge_commit_sha}
    direct_commits = [
        commit
        for commit in commits
        if commit.sha not in pr_merge_shas and not _looks_like_merge_commit(commit.title)
    ]

    return ReleaseContext(
        repo=repo,
        base_ref=base,
        target_ref=target,
        tag_name=tag,
        previous_release_url=str(getattr(previous_release, "html_url", "") or ""),
        pull_requests=prs,
        direct_commits=direct_commits,
        files=files,
    )


def _format_release_evidence(context: ReleaseContext) -> str:
    parts = [
        f"Repository: {context.repo}",
        f"Range: {context.base_ref}...{context.target_ref}",
        f"Draft tag: {context.tag_name}",
        "",
        "## Merged pull requests",
    ]
    if context.pull_requests:
        for pr in context.pull_requests:
            labels = ", ".join(pr.labels) if pr.labels else "none"
            files = ", ".join(pr.changed_files[:12]) if pr.changed_files else "not fetched"
            parts.append(
                f"- PR #{pr.number}: {pr.title} by @{pr.author} ({pr.url}); "
                f"labels: {labels}; files: {files}"
            )
    else:
        parts.append("- none")

    parts.append("")
    parts.append("## Direct commits")
    if context.direct_commits:
        for commit in context.direct_commits:
            parts.append(
                f"- {commit.short_sha}: {commit.title} by {commit.author} ({commit.url})"
            )
    else:
        parts.append("- none")

    parts.append("")
    parts.append("## Changed file summary")
    if context.files:
        for item in context.files[:80]:
            parts.append(
                f"- {item.filename} ({item.status}, +{item.additions}/-{item.deletions})"
            )
    else:
        parts.append("- none")
    return "\n".join(parts)


def _fallback_release_body(context: ReleaseContext) -> str:
    lines = ["## Changes", ""]
    if context.pull_requests:
        for pr in context.pull_requests:
            lines.append(f"- {pr.title} by @{pr.author} (#{pr.number})")
    if context.direct_commits:
        for commit in context.direct_commits:
            lines.append(f"- {commit.title} ({commit.short_sha})")
    if len(lines) == 2:
        lines.append("- No user-visible changes found in this range.")
    lines.extend([
        "",
        "## Evidence",
        f"- Range: `{context.base_ref}...{context.target_ref}`",
    ])
    if context.previous_release_url:
        lines.append(f"- Previous release: {context.previous_release_url}")
    return "\n".join(lines)


def draft_release_notes(
    llm_client: Any,
    context: ReleaseContext,
    profile: dict[str, Any],
) -> tuple[str, str]:
    """Generate a draft release name and body from collected evidence."""
    release_config = profile.get("release", {})
    model = get_module_model(profile, "release")
    temperature = float(release_config.get("temperature", 0.1))
    category_order = release_config.get(
        "categories",
        ["Breaking Changes", "Features", "Fixes", "Documentation", "Maintenance"],
    )
    audience = str(release_config.get("audience", "users and maintainers"))

    system = """\
You draft GitHub release notes from repository evidence.

Rules:
- Use only the supplied pull requests, commits, labels, and file summaries.
- Do not invent features, issue numbers, authors, or links.
- Every bullet must include a source reference such as "(#123)" or "(abc1234)".
- Group changes by likely user impact.
- Direct commits are valid release evidence.
- Keep wording concise and release-ready.

Respond with one JSON object:
{
  "name": "Release title",
  "body": "Markdown release notes"
}
"""
    user = f"""\
Audience: {audience}
Preferred category order: {", ".join(str(c) for c in category_order)}

Evidence:
{_format_release_evidence(context)}
"""
    try:
        response = llm_client.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            model=model,
            temperature=temperature,
            max_tokens=3000,
        )
        parsed = parse_llm_json(response.content)
        name = str(parsed.get("name") or context.tag_name)
        body = str(parsed.get("body") or "").strip()
        if not body:
            body = _fallback_release_body(context)
        return name, body
    except Exception as exc:
        logger.warning("LLM release draft failed, using fallback body: %s", exc)
        return context.tag_name, _fallback_release_body(context)


def _find_matching_draft_release(repo_obj: Any, tag_name: str) -> Any | None:
    try:
        for release in repo_obj.get_releases():
            if getattr(release, "draft", False) and getattr(release, "tag_name", "") == tag_name:
                return release
    except Exception as exc:
        logger.warning("Failed to find draft releases: %s", exc)
    return None


def create_or_update_draft_release(
    repo_obj: Any,
    tag_name: str,
    target_ref: str,
    name: str,
    body: str,
    prerelease: bool = False,
) -> tuple[str, str]:
    """Create or update a matching GitHub draft release."""
    existing = _find_matching_draft_release(repo_obj, tag_name)
    if existing is not None:
        existing.update_release(name=name, message=body, draft=True, prerelease=prerelease)
        return "updated", str(getattr(existing, "html_url", ""))

    release = repo_obj.create_git_release(
        tag=tag_name,
        name=name,
        message=body,
        draft=True,
        prerelease=prerelease,
        target_commitish=target_ref,
    )
    return "created", str(getattr(release, "html_url", ""))


def run_release(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict[str, Any] | None = None,
    base_ref: str | None = None,
    target_ref: str | None = None,
    tag_name: str | None = None,
    dry_run: bool = False,
) -> ReleaseDraftResult:
    """Generate release notes and optionally create/update a draft release."""
    profile = profile or load_profile()
    release_config = profile.get("release", {})
    if release_config.get("enabled", True) is False:
        return ReleaseDraftResult(
            repo=repo,
            tag_name=tag_name or "",
            name="",
            body="",
            action="disabled",
            dry_run=dry_run,
        )

    include_prereleases = bool(release_config.get("include_prereleases", False))
    prerelease = bool(release_config.get("prerelease", False))
    context = collect_release_context(
        gh_client,
        repo,
        base_ref=base_ref,
        target_ref=target_ref,
        tag_name=tag_name,
        include_prereleases=include_prereleases,
    )
    name, body = draft_release_notes(llm_client, context, profile)

    action = "dry-run"
    html_url = ""
    if not dry_run:
        repo_obj = gh_client.get_repo(repo)
        action, html_url = create_or_update_draft_release(
            repo_obj,
            context.tag_name,
            context.target_ref,
            name,
            body,
            prerelease=prerelease,
        )

    return ReleaseDraftResult(
        repo=repo,
        tag_name=context.tag_name,
        name=name,
        body=body,
        action=action,
        html_url=html_url,
        pr_count=len(context.pull_requests),
        commit_count=len(context.direct_commits),
        dry_run=dry_run,
    )


def generate_release_summary(result: ReleaseDraftResult) -> str:
    """Return a markdown summary suitable for GitHub step summaries."""
    lines = [
        "# RepoKeeper Draft Release",
        "",
        f"- Repository: `{result.repo}`",
        f"- Tag: `{result.tag_name}`",
        f"- Action: `{result.action}`",
        f"- Pull requests: {result.pr_count}",
        f"- Direct commits: {result.commit_count}",
    ]
    if result.html_url:
        lines.append(f"- Draft release: {result.html_url}")
    lines.extend(["", result.body])
    return "\n".join(lines)
