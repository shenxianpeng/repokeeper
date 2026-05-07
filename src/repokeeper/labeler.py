"""
Module 5: Auto-Labeler

Automatically labels GitHub issues and pull requests using AI classification.
Fetches repo labels first, then uses the LLM to pick from existing labels
(keeping naming style consistent). Only creates new labels when no suitable
existing label exists, matching the description pattern of existing labels.

Supports two modes:

- ``add`` — applies labels directly to the issue/PR.
- ``suggest`` — posts a comment with suggested labels for manual review.

Also supports batch mode: label all unlabeled open issues in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .collaboration import LABELER_LABEL
from .llm_client import parse_llm_json
from .logs import get_logger
from .profile import get_module_model, load_profile

logger = get_logger("labeler")


# ─── Data models ─────────────────────────────────────────────────────────────


@dataclass
class LabelerResult:
    """Result of labeling a single issue or PR."""

    issue_number: int
    issue_url: str
    title: str
    target_type: str = "issue"       # "issue" | "pr"
    category: str = ""
    confidence: float = 0.0
    suggested_labels: list[str] = field(default_factory=list)
    applied_labels: list[str] = field(default_factory=list)
    created_labels: list[str] = field(default_factory=list)
    action: str = ""                 # "labeled" | "commented" | "skipped"
    skipped_reason: str = ""
    error: str = ""


@dataclass
class LabelerReport:
    """Complete auto-labeler report for a repository."""

    repo: str
    scanned_at: datetime
    total_issues: int = 0
    results: list[LabelerResult] = field(default_factory=list)
    labeled: list[LabelerResult] = field(default_factory=list)
    commented: list[LabelerResult] = field(default_factory=list)
    skipped: list[LabelerResult] = field(default_factory=list)
    errors: list[LabelerResult] = field(default_factory=list)


# ─── Repo label fetching ─────────────────────────────────────────────────────


def fetch_repo_labels(gh_client: Any, repo: str) -> list[dict[str, str]]:
    """Fetch all labels from a GitHub repository.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).

    Returns:
        List of dicts with keys: name, description, color.
        Empty list on any error.
    """
    try:
        gh_repo = gh_client.get_repo(repo)
        labels = gh_repo.get_labels()

        result: list[dict[str, str]] = []
        for label in labels:
            desc = getattr(label, "description", "") or ""
            color = getattr(label, "color", "ededed")
            result.append({
                "name": label.name,
                "description": desc,
                "color": color,
            })

        logger.info(f"  Fetched {len(result)} existing labels from {repo}")
        return result

    except Exception as e:
        logger.warning(f"Failed to fetch labels for {repo}: {e}")
        return []


def _infer_label_naming_convention(labels: list[dict[str, str]]) -> str:
    """Infer the dominant naming convention from existing labels.

    Detects patterns like:
    - ``area/module`` slash-separated
    - ``type: bug`` colon-prefixed
    - ``Priority: High`` title-case colon
    - ``module-utils`` kebab-case
    - ``plain lowercase``
    - ``Title Case``

    Args:
        labels: List of label dicts from :func:`fetch_repo_labels`.

    Returns:
        A human-readable description of the naming convention.
    """
    if not labels:
        return "plain lowercase (e.g. 'bug', 'enhancement')"

    names = [lb["name"] for lb in labels if lb["name"]]

    slash_count = sum(1 for n in names if "/" in n)
    colon_count = sum(1 for n in names if ": " in n or ":" in n)
    kebab_count = sum(1 for n in names if "-" in n)
    title_case = sum(1 for n in names if " " in n and n[0].isupper())
    plain_lower = sum(1 for n in names if n.islower() and " " not in n and "/" not in n)

    total = len(names)
    patterns: list[tuple[int, str]] = [
        (slash_count, f"slash-separated (e.g. '{_pick_example(names, '/')}')"),
        (colon_count, f"colon-prefixed (e.g. '{_pick_example(names, ':')}')"),
        (kebab_count, f"kebab-case (e.g. '{_pick_example(names, '-')}')"),
        (title_case, f"Title Case with spaces (e.g. '{_pick_example(names, ' ')}')"),
        (plain_lower, f"plain lowercase (e.g. '{_pick_example(names, '')}')"),
    ]
    patterns.sort(reverse=True)

    if patterns[0][0] > total * 0.3:
        return patterns[0][1]

    return "plain lowercase (e.g. 'bug', 'enhancement')"


def _pick_example(names: list[str], separator: str) -> str:
    for n in names:
        if separator in n:
            return n
    return names[0] if names else "label"


def _format_labels_for_prompt(labels: list[dict[str, str]]) -> str:
    """Format the repo label list for inclusion in the LLM prompt.

    Args:
        labels: List of label dicts.

    Returns:
        Formatted string like:
        - `bug` — "Something isn't working" (color: d73a4a)
    """
    if not labels:
        return "(No existing labels in this repository.)"

    lines = []
    for lb in labels:
        name = lb["name"]
        desc = lb.get("description", "") or "(no description)"
        color = lb.get("color", "ededed")
        lines.append(f"- `{name}` — _{desc}_ (color: #{color})")

    return "\n".join(lines)


# ─── PR data fetching ────────────────────────────────────────────────────────


def fetch_pr_data(
    gh_client: Any,
    repo: str,
    pr_number: int,
) -> dict[str, Any] | None:
    """Fetch pull request metadata and changed file summary.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        pr_number: Pull request number.

    Returns:
        Dict with keys: number, title, body, url, labels, changed_files_summary,
        additions, deletions, changed_files_count. Returns None on error.
    """
    try:
        gh_repo = gh_client.get_repo(repo)
        pr = gh_repo.get_pull(pr_number)

        # Build a changed files summary
        files = pr.get_files()
        file_list: list[str] = []
        total_additions = 0
        total_deletions = 0

        for f in files:
            total_additions += f.additions
            total_deletions += f.deletions
            status = f.status  # "added" | "modified" | "removed" | "renamed"
            file_list.append(f"  [{status}] {f.filename} (+{f.additions} -{f.deletions})")

        changed_files_summary = "\n".join(file_list[:50])  # cap at 50 files
        if len(file_list) > 50:
            changed_files_summary += f"\n  ... and {len(file_list) - 50} more files"

        return {
            "number": pr.number,
            "title": pr.title,
            "body": pr.body or "",
            "url": pr.html_url,
            "labels": [lb.name for lb in pr.labels],
            "changed_files_summary": changed_files_summary,
            "additions": total_additions,
            "deletions": total_deletions,
            "changed_files_count": len(file_list),
        }

    except Exception as e:
        logger.error(f"Failed to fetch PR #{pr_number}: {e}")
        return None


# ─── AI Classification (context-aware) ───────────────────────────────────────


def _build_labeler_system_prompt(
    existing_labels: list[dict[str, str]],
    target_type: str = "issue",
) -> str:
    """Build a context-aware system prompt that includes repo labels.

    Args:
        existing_labels: Fetched repo labels with name/description/color.
        target_type: "issue" or "pr" — changes the classification instructions.

    Returns:
        System prompt string.
    """
    convention = _infer_label_naming_convention(existing_labels)
    label_list = _format_labels_for_prompt(existing_labels)
    has_descriptions = any(lb.get("description") for lb in existing_labels)

    if target_type == "pr":
        extra_rules = """\
**PR-specific rules:**
- Look at BOTH the PR description AND the changed files.
- The PRIMARY label MUST reflect the MAIN purpose, not side-effects.
  Example: a feature PR that also updates 2 docs files → "enhancement" (NOT "documentation").
  Example: a bugfix that also touches test files → "bug" (NOT "testing").
- Consider the proportion of changes: where is most of the diff?
- If the PR is very small (≤50 lines), it may be "good_first_issue" or a minor fix.
- If it touches CI workflows (`.github/workflows/`), consider "ci_cd" as appropriate.
"""
    else:
        extra_rules = """\
**Issue-specific rules:**
- Focus on the issue description. What is the user asking for or reporting?
- If the issue includes error messages, stack traces, or reproduction steps, it's probably a "bug".
- If it asks "how do I..." or seeks clarification, it's a "question".
- If the body is empty or the title is very vague, lower your confidence.
"""

    desc_rule = (
        "New labels MUST have a description that follows the same style "
        "(tone, length, format) as existing descriptions."
        if has_descriptions
        else "New labels may include a short description (1–2 sentences)."
    )

    return f"""\
You are an AI triage assistant that labels GitHub {target_type}s.
Your job is to pick the best-fitting labels from the existing repo labels.
ONLY suggest new labels when no existing label fits well.

## Existing labels in this repository

{label_list}

## Naming convention

The repository uses this naming style: **{convention}**

## Rules

1. **Prefer existing labels.** Always check the list above first.
   Choose the most specific matching existing label(s).
2. **Only suggest new labels if necessary.** If no existing label fits,
   suggest new labels that follow the naming convention above.
3. **New labels MUST match the naming style.** Copy the format
   (slashes, colons, case, prefixes) of existing labels.
4. **{desc_rule}**
5. **1-3 labels total** (existing + new combined).
6. If truly nothing fits and the item is unactionable, set category to "noise".
{extra_rules}
## Output format

Respond with a single JSON object:

{{
  "category": "bug | feature_request | question | documentation | performance | security | dependencies | duplicate | wontfix | good_first_issue | help_wanted | refactoring | testing | ci_cd | noise",
  "confidence": 0.0 to 1.0,
  "summary": "One sentence summarizing the {target_type}.",
  "existing_labels": ["label1", "label2"],
  "new_labels": [{{"name": "new-label", "description": "Why this label", "color": "hex"}}],
  "reasoning": "Why these labels were chosen, and why new labels were needed (if any)."
}}

- ``existing_labels``: names of labels from the list above (empty list if none fit).
- ``new_labels``: labels to create (empty list if not needed).
- ``color`` for new labels: pick a hex color that fits with the existing palette ({_describe_palette(existing_labels)}).
"""


def _describe_palette(labels: list[dict[str, str]]) -> str:
    """Describe the existing label color palette for the LLM."""
    if not labels:
        return "use standard GitHub label colors"
    colors = [lb.get("color", "") for lb in labels if lb.get("color")]
    unique = list(dict.fromkeys(colors))[:5]
    return ", ".join(f"#{c}" for c in unique) if unique else "use standard GitHub label colors"


def classify_with_context(
    title: str,
    body: str,
    existing_labels: list[dict[str, str]],
    llm_client: Any,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    target_type: str = "issue",
    changed_files_summary: str = "",
) -> dict[str, Any]:
    """Use AI to classify an issue/PR and suggest labels from the existing pool.

    Args:
        title: Issue/PR title.
        body: Issue/PR body.
        existing_labels: Repo labels from :func:`fetch_repo_labels`.
        llm_client: OpenAI-compatible LLM client.
        model: Model name.
        temperature: LLM temperature (0.0 for deterministic results).
        target_type: "issue" or "pr".
        changed_files_summary: For PRs, the diff summary.

    Returns:
        Dict with keys: category, confidence, summary, existing_labels, new_labels, reasoning.
        Falls back to ``{"category": "noise", "confidence": 0.0, "existing_labels": [], "new_labels": []}`` on error.
    """
    system_prompt = _build_labeler_system_prompt(existing_labels, target_type)

    if target_type == "pr":
        user_prompt = f"""\
## PR
**Title:** {title}

**Body:**
{body[:3000]}

## Changed files
{changed_files_summary[:3000]}

Pick labels from the existing list. Respond with JSON only.
"""
    else:
        user_prompt = f"""\
## Issue
**Title:** {title}

**Body:**
{body[:4000]}

Pick labels from the existing list. Respond with JSON only.
"""

    try:
        response = llm_client.chat(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            model=model,
            temperature=temperature,
            max_tokens=800,
        )

        result = parse_llm_json(response.content)
        return {
            "category": result.get("category", "noise"),
            "confidence": float(result.get("confidence", 0)),
            "summary": result.get("summary", ""),
            "existing_labels": _normalize_string_list(result.get("existing_labels", [])),
            "new_labels": _normalize_new_labels(result.get("new_labels", [])),
            "reasoning": result.get("reasoning", ""),
        }

    except Exception as e:
        logger.error(f"Classification failed for '{title[:80]}': {e}")
        return {
            "category": "noise",
            "confidence": 0.0,
            "existing_labels": [],
            "new_labels": [],
        }


def _normalize_string_list(value: Any) -> list[str]:
    """Ensure the value is a list of non-empty strings."""
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v and str(v).strip()]


def _normalize_new_labels(value: Any) -> list[dict[str, str]]:
    """Ensure new_labels is a list of dicts with name/description/color."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        result.append({
            "name": name,
            "description": str(item.get("description", "")).strip(),
            "color": str(item.get("color", "ededed")).strip().lstrip("#"),
        })
    return result


# ─── Label resolution (against repo labels) ──────────────────────────────────


def resolve_labels_against_repo(
    classification: dict[str, Any],
    existing_label_names: set[str],
    max_labels: int = 3,
) -> tuple[list[str], list[dict[str, str]]]:
    """Resolve classification output against actual repo labels.

    Filters ``existing_labels`` to only those that actually exist in the repo.
    Passes through ``new_labels`` for creation.

    Args:
        classification: Output from :func:`classify_with_context`.
        existing_label_names: Set of label names that exist in the repo.
        max_labels: Total max labels (existing + new).

    Returns:
        Tuple of ``(labels_to_apply, labels_to_create)``.
    """
    # Filter: only keep existing labels that actually exist in the repo
    ai_existing = classification.get("existing_labels", [])
    if not isinstance(ai_existing, list):
        ai_existing = []

    valid_existing: list[str] = []
    for label in ai_existing:
        if label and label in existing_label_names:
            valid_existing.append(label)
        else:
            logger.info(f"  AI suggested label '{label}' but it doesn't exist in repo")

    # Deduplicate
    seen: set[str] = set()
    deduped: list[str] = []
    for label in valid_existing:
        if label not in seen:
            deduped.append(label)
            seen.add(label)

    # Cap: existing labels take priority, then new labels fill remaining slots
    capped_existing = deduped[:max_labels]

    ai_new = classification.get("new_labels", [])
    if not isinstance(ai_new, list):
        ai_new = []

    # Deduplicate new labels against existing names
    new_labels: list[dict[str, str]] = []
    new_seen: set[str] = set(seen)
    for nl in ai_new:
        if not isinstance(nl, dict):
            continue
        name = nl.get("name", "")
        if name and name not in new_seen:
            new_labels.append(nl)
            new_seen.add(name)

    # Cap total
    remaining = max_labels - len(capped_existing)
    if remaining <= 0:
        new_labels = []
    elif remaining < len(new_labels):
        new_labels = new_labels[:remaining]

    return capped_existing, new_labels


# ─── Issue / PR fetching ─────────────────────────────────────────────────────


def fetch_issue(
    gh_client: Any,
    repo: str,
    issue_number: int,
) -> dict[str, Any] | None:
    """Fetch issue metadata from GitHub.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        issue_number: Issue number.

    Returns:
        Dict with keys: number, title, body, url, labels, or None on error.
    """
    try:
        gh_repo = gh_client.get_repo(repo)
        issue = gh_repo.get_issue(issue_number)

        if issue.pull_request is not None:
            logger.info(f"  #{issue_number} is a pull request, skipping (use --pr for PRs)")
            return None

        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body or "",
            "url": issue.html_url,
            "labels": [lb.name for lb in issue.labels],
        }

    except Exception as e:
        logger.error(f"Failed to fetch issue #{issue_number}: {e}")
        return None


def find_unlabeled_issues(
    gh_client: Any,
    repo: str,
    exclude_labels: list[str] | None = None,
    max_issues: int = 50,
) -> list[dict[str, Any]]:
    """Find open issues that have no labels (or only excluded labels).

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        exclude_labels: Labels that don't count (e.g. admin labels).
        max_issues: Maximum number of issues to return.

    Returns:
        List of issue dicts with keys: number, title, body, url, labels.
    """
    exclude = set(exclude_labels or [])
    issues: list[dict[str, Any]] = []

    try:
        gh_repo = gh_client.get_repo(repo)
        all_issues = gh_repo.get_issues(state="open", sort="created", direction="desc")

        for issue in all_issues:
            if len(issues) >= max_issues:
                break
            if issue.pull_request is not None:
                continue

            issue_labels = [lb.name for lb in issue.labels]
            meaningful = [lb for lb in issue_labels if lb not in exclude]

            if not meaningful:
                issues.append({
                    "number": issue.number,
                    "title": issue.title,
                    "body": issue.body or "",
                    "url": issue.html_url,
                    "labels": issue_labels,
                })

    except Exception as e:
        logger.error(f"Failed to find unlabeled issues for {repo}: {e}")

    return issues


# ─── Label application ───────────────────────────────────────────────────────


def apply_labels(
    gh_client: Any,
    repo: str,
    issue_number: int,
    labels: list[str],
) -> list[str]:
    """Apply labels to a GitHub issue.

    Ensures all labels exist in the repo (creates them if needed via
    :func:`ensure_github_labels` for RepoKeeper-owned labels only).

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        issue_number: Issue or PR number.
        labels: Labels to apply (must already exist in the repo).

    Returns:
        List of labels that were applied.
    """
    if not labels:
        return []

    try:
        gh_repo = gh_client.get_repo(repo)
        issue = gh_repo.get_issue(issue_number)

        issue.add_to_labels(*labels)

        logger.info(f"  Applied labels to #{issue_number}: {', '.join(labels)}")
        return list(labels)

    except Exception as e:
        logger.error(f"Failed to apply labels to #{issue_number}: {e}")
        return []


def create_new_labels(
    gh_client: Any,
    repo: str,
    new_labels: list[dict[str, str]],
) -> list[str]:
    """Create new GitHub labels with descriptions and colors.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        new_labels: List of dicts with name, description, color.

    Returns:
        List of label names that were successfully created.
    """
    if not new_labels:
        return []

    created: list[str] = []

    try:
        gh_repo = gh_client.get_repo(repo)

        for nl in new_labels:
            name = nl["name"].strip()
            if not name:
                continue

            # Check if label already exists
            try:
                gh_repo.get_label(name)
                logger.info(f"  Label '{name}' already exists, skipping creation")
                created.append(name)
                continue
            except Exception:
                pass

            description = nl.get("description", "").strip()
            color = nl.get("color", "ededed").strip()

            try:
                gh_repo.create_label(
                    name=name,
                    color=color,
                    description=description,
                )
                logger.info(
                    f"  Created label '{name}' "
                    f"(color: #{color}, description: '{description}')"
                )
                created.append(name)
            except Exception as e:
                logger.warning(f"  Failed to create label '{name}': {e}")

    except Exception as e:
        logger.error(f"Failed to access repo for label creation: {e}")

    return created


def suggest_labels_comment(
    gh_client: Any,
    repo: str,
    issue_number: int,
    classification: dict[str, Any],
    existing_labels: list[str],
    new_labels: list[dict[str, str]],
    target_type: str = "issue",
) -> bool:
    """Post a comment suggesting labels for manual review.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        issue_number: Issue/PR number.
        classification: AI classification result.
        existing_labels: Existing labels to suggest applying.
        new_labels: New labels to suggest creating.
        target_type: "issue" or "pr".

    Returns:
        True if the comment was posted.
    """
    all_labels = existing_labels + [nl["name"] for nl in new_labels]
    if not all_labels:
        return False

    category = classification.get("category", "unknown")
    confidence = classification.get("confidence", 0)
    summary = classification.get("summary", "")
    reasoning = classification.get("reasoning", "")

    lines = [
        f"🏷️ **RepoKeeper Auto-Labeler** suggests labels for this {target_type}:",
        "",
    ]

    if existing_labels:
        lines.append(f"**Apply existing labels:** {', '.join(f'`{lb}`' for lb in existing_labels)}")

    if new_labels:
        nl_str = ", ".join(
            f"`{nl['name']}` — _{nl.get('description', '')}_"
            for nl in new_labels
        )
        lines.append(f"**Create new labels:** {nl_str}")

    lines.append("")
    lines.append(f"**Category:** {category}")
    lines.append(f"**Confidence:** {confidence:.0%}")
    lines.append(f"**Summary:** {summary}")

    if reasoning:
        lines.append(f"**Reasoning:** {reasoning}")

    lines.append("")
    lines.append(
        "---\n"
        "<sub>🤖 This is an automated suggestion. "
        "A maintainer can apply these labels manually or adjust them. "
        "Powered by [RepoKeeper](https://github.com/shenxianpeng/repokeeper).</sub>"
    )

    try:
        gh_repo = gh_client.get_repo(repo)
        issue = gh_repo.get_issue(issue_number)
        issue.create_comment("\n".join(lines))
        logger.info(f"  Posted label suggestion comment on #{issue_number}")
        return True

    except Exception as e:
        logger.error(f"Failed to post suggestion comment on #{issue_number}: {e}")
        return False


# ─── Single-item labeling (issue + PR) ───────────────────────────────────────


def _build_target_data(
    gh_client: Any,
    repo: str,
    issue_number: int,
    pr_number: int | None,
) -> dict[str, Any] | None:
    """Fetch metadata for either an issue or a PR.

    Returns a dict with at least number, title, body, url, labels, target_type.
    For PRs, adds changed_files_summary, additions, deletions, changed_files_count.
    """
    if pr_number is not None:
        pr_data = fetch_pr_data(gh_client, repo, pr_number)
        if pr_data is None:
            return None
        pr_data["target_type"] = "pr"
        return pr_data

    issue_data = fetch_issue(gh_client, repo, issue_number)
    if issue_data is None:
        return None
    issue_data["target_type"] = "issue"
    issue_data["changed_files_summary"] = ""
    return issue_data


def label_single_issue(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    issue_number: int,
    profile: dict,
    repo_labels: list[dict[str, str]] | None = None,
) -> LabelerResult:
    """Label a single GitHub issue.

    Pipeline:
        1. Fetch repo labels (existing context).
        2. Fetch issue metadata.
        3. AI classify against existing labels.
        4. Resolve: pick existing labels + create new ones if needed.
        5. Apply or suggest labels based on mode.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        issue_number: Issue number to label.
        profile: Maintainer profile.
        repo_labels: Pre-fetched repo labels (avoids re-fetching in batch mode).

    Returns:
        LabelerResult describing what happened.
    """
    return _label_single(gh_client, llm_client, repo, issue_number, None, profile,
                         repo_labels=repo_labels)


def label_single_pr(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    pr_number: int,
    profile: dict,
    repo_labels: list[dict[str, str]] | None = None,
) -> LabelerResult:
    """Label a single GitHub pull request.

    Similar to :func:`label_single_issue` but includes the diff/changed
    files summary so the LLM can determine the PRIMARY purpose.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        pr_number: Pull request number to label.
        profile: Maintainer profile.
        repo_labels: Pre-fetched repo labels (avoids re-fetching in batch mode).

    Returns:
        LabelerResult.
    """
    return _label_single(gh_client, llm_client, repo, pr_number, pr_number, profile,
                         repo_labels=repo_labels)


def _label_single(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    issue_number: int,
    pr_number: int | None,
    profile: dict,
    repo_labels: list[dict[str, str]] | None = None,
) -> LabelerResult:
    """Internal: label a single issue or PR.

    Args:
        repo_labels: Pre-fetched repo labels. Fetched if None (single-issue mode).
    """

    labeler_config = profile.get("labeler", {})
    mode = labeler_config.get("mode", "add")
    confidence_threshold = labeler_config.get("confidence_threshold", 0.7)
    max_labels = labeler_config.get("max_labels", 3)
    model = get_module_model(profile, "labeler")
    allow_create = labeler_config.get("allow_create_labels", True)

    # Step 0: Fetch repo labels (once for batch, per-call for single)
    if repo_labels is None:
        repo_labels = fetch_repo_labels(gh_client, repo)
    existing_names = {lb["name"] for lb in repo_labels}

    # Step 1: Fetch target data
    target_data = _build_target_data(gh_client, repo, issue_number, pr_number)
    if target_data is None:
        skip_reason = "issue not found or is a PR (use --pr for PRs)"
        if pr_number is not None:
            skip_reason = "PR not found or inaccessible"
        return LabelerResult(
            issue_number=issue_number,
            issue_url="",
            title="",
            action="skipped",
            skipped_reason=skip_reason,
        )

    target_type = target_data.get("target_type", "issue")
    target_body = target_data.get("body", "")
    changed_files = target_data.get("changed_files_summary", "")

    # Step 2: Classify with repo context
    classification = classify_with_context(
        title=target_data["title"],
        body=target_body,
        existing_labels=repo_labels,
        llm_client=llm_client,
        model=model,
        target_type=target_type,
        changed_files_summary=changed_files,
    )

    category = classification.get("category", "noise")
    confidence = classification.get("confidence", 0)

    result = LabelerResult(
        issue_number=target_data["number"],
        issue_url=target_data["url"],
        title=target_data["title"],
        target_type=target_type,
        category=category,
        confidence=confidence,
    )

    # Step 3: Check confidence
    if confidence < confidence_threshold:
        result.action = "skipped"
        result.skipped_reason = (
            f"confidence {confidence:.0%} below threshold {confidence_threshold:.0%}"
        )
        logger.info(f"  Skipped #{target_data['number']}: {result.skipped_reason}")
        return result

    # Step 4: Resolve against actual repo labels
    existing_picks, new_to_create = resolve_labels_against_repo(
        classification, existing_names, max_labels,
    )
    result.suggested_labels = existing_picks + [nl["name"] for nl in new_to_create]

    if not existing_picks and (not new_to_create or not allow_create):
        result.action = "skipped"
        result.skipped_reason = "no matching labels found in repo"
        return result

    # Step 5: Apply or suggest
    if mode == "add":
        # Apply existing labels
        applied: list[str] = []
        if existing_picks:
            applied = apply_labels(gh_client, repo, target_data["number"], existing_picks)

        # Create + apply new labels
        created: list[str] = []
        if new_to_create and allow_create:
            created = create_new_labels(gh_client, repo, new_to_create)
            if created:
                apply_labels(gh_client, repo, target_data["number"], created)

        result.applied_labels = applied + created
        result.created_labels = created

        if result.applied_labels:
            result.action = "labeled"
            # Also add the LABELER_LABEL for tracking
            try:
                apply_labels(gh_client, repo, target_data["number"], [LABELER_LABEL])
            except Exception:
                pass
        else:
            result.action = "skipped"
            result.skipped_reason = "failed to apply labels"
    else:
        # mode == "suggest"
        if suggest_labels_comment(
            gh_client, repo, target_data["number"],
            classification, existing_picks, new_to_create, target_type,
        ):
            result.action = "commented"
        else:
            result.action = "skipped"
            result.skipped_reason = "failed to post suggestion comment"

    return result


# ─── Batch labeling ──────────────────────────────────────────────────────────


def label_unlabeled_issues(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict,
    max_issues: int = 50,
) -> LabelerReport:
    """Find all unlabeled open issues and label them.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        profile: Maintainer profile.
        max_issues: Max unlabeled issues to process.

    Returns:
        LabelerReport with results for every issue processed.
    """
    labeler_config = profile.get("labeler", {})
    exclude = labeler_config.get("exclude_labels", [LABELER_LABEL])

    logger.info(f"🏷️ Labeler: finding unlabeled issues in {repo}")

    # Fetch repo labels once for the entire batch
    repo_labels = fetch_repo_labels(gh_client, repo)

    unlabeled = find_unlabeled_issues(
        gh_client, repo, exclude_labels=exclude, max_issues=max_issues,
    )

    if not unlabeled:
        logger.info("  No unlabeled issues found")
        return LabelerReport(
            repo=repo, scanned_at=datetime.now(), total_issues=0,
        )

    logger.info(f"  Found {len(unlabeled)} unlabeled issues")

    report = LabelerReport(
        repo=repo, scanned_at=datetime.now(), total_issues=len(unlabeled),
    )

    for issue_data in unlabeled:
        try:
            result = label_single_issue(
                gh_client, llm_client, repo, issue_data["number"], profile,
                repo_labels=repo_labels,
            )
        except Exception as e:
            logger.error(f"  Error labeling #{issue_data['number']}: {e}")
            result = LabelerResult(
                issue_number=issue_data["number"],
                issue_url=issue_data.get("url", ""),
                title=issue_data.get("title", ""),
                action="skipped",
                error=str(e),
            )

        report.results.append(result)

        if result.action == "labeled":
            report.labeled.append(result)
        elif result.action == "commented":
            report.commented.append(result)
        elif result.error:
            report.errors.append(result)
        else:
            report.skipped.append(result)

    return report


# ─── Main entry point ────────────────────────────────────────────────────────


def run_labeler(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict | None = None,
    issue_number: int | None = None,
    pr_number: int | None = None,
) -> LabelerReport:
    """Run the auto-labeler.

    Three modes:
    - **Single-issue mode**: Pass ``issue_number``.
    - **Single-PR mode**: Pass ``pr_number``.
    - **Batch mode**: Omit both to label all unlabeled open issues.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        profile: Maintainer profile (loaded if None).
        issue_number: Optional specific issue to label.
        pr_number: Optional specific PR to label.

    Returns:
        LabelerReport with results.
    """
    if profile is None:
        profile = load_profile()

    labeler_config = profile.get("labeler", {})
    if not labeler_config.get("enabled", True):
        logger.info(f"Labeler disabled for {repo}")
        return LabelerReport(repo=repo, scanned_at=datetime.now())

    if issue_number is not None or pr_number is not None:
        number = pr_number if pr_number is not None else issue_number  # type: ignore[assignment]
        target = "PR" if pr_number else "issue"
        logger.info(f"🏷️ Labeler: processing {target} #{number} in {repo}")

        if pr_number is not None:
            result = label_single_pr(gh_client, llm_client, repo, pr_number, profile)
        else:
            result = label_single_issue(gh_client, llm_client, repo, issue_number or 0, profile)  # type: ignore[arg-type]

        report = LabelerReport(
            repo=repo, scanned_at=datetime.now(), total_issues=1,
            results=[result],
        )
        if result.action == "labeled":
            report.labeled = [result]
        elif result.action == "commented":
            report.commented = [result]
        elif result.error:
            report.errors = [result]
        else:
            report.skipped = [result]
        return report

    return label_unlabeled_issues(gh_client, llm_client, repo, profile)


# ─── Summary generation ──────────────────────────────────────────────────────


def generate_labeler_summary(report: LabelerReport) -> str:
    """Generate a markdown summary of the labeler run.

    Args:
        report: Filled LabelerReport.

    Returns:
        Markdown string.
    """
    lines = [
        f"# 🏷️ Auto-Labeler Report — [{report.repo}](https://github.com/{report.repo})",
        "",
        f"**Scanned:** {report.scanned_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Total issues:** {report.total_issues}",
        f"**Labeled:** {len(report.labeled)} | "
        f"**Suggested:** {len(report.commented)} | "
        f"**Skipped:** {len(report.skipped)} | "
        f"**Errors:** {len(report.errors)}",
        "",
    ]

    if report.labeled:
        lines.append("## ✅ Labels Applied")
        lines.append("")
        for r in report.labeled:
            extra = ""
            if r.created_labels:
                extra = f" (🆕 created: `{'`, `'.join(r.created_labels)}`)"
            lines.append(
                f"- [#{r.issue_number} {r.title}]({r.issue_url}) "
                f"[{r.target_type}] — "
                f"`{'`, `'.join(r.applied_labels)}` "
                f"({r.category}, {r.confidence:.0%}){extra}"
            )
        lines.append("")

    if report.commented:
        lines.append("## 💬 Suggestions Posted")
        lines.append("")
        for r in report.commented:
            lines.append(
                f"- [#{r.issue_number} {r.title}]({r.issue_url}) "
                f"[{r.target_type}] — "
                f"`{'`, `'.join(r.suggested_labels)}` "
                f"({r.category}, {r.confidence:.0%})"
            )
        lines.append("")

    if report.skipped:
        lines.append("## ⏭️ Skipped")
        lines.append("")
        for r in report.skipped:
            reason = r.skipped_reason or "unknown"
            lines.append(
                f"- [#{r.issue_number} {r.title}]({r.issue_url}) — {reason}"
            )
        lines.append("")

    if report.errors:
        lines.append("## ❌ Errors")
        lines.append("")
        for r in report.errors:
            lines.append(
                f"- [#{r.issue_number} {r.title}]({r.issue_url}) — {r.error}"
            )
        lines.append("")

    if not report.results:
        lines.append("✅ No issues to process.")
        lines.append("")

    lines.append("---")
    lines.append(
        "*Generated by RepoKeeper Auto-Labeler · "
        "[github.com/shenxianpeng/repokeeper]"
        "(https://github.com/shenxianpeng/repokeeper)*"
    )

    return "\n".join(lines)
