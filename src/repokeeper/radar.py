"""
Module 1: Community Radar

Monitors GitHub issues and Discussions for keywords specified in the maintainer
profile. Uses AI to classify each hit as bug, feature request, or noise,
filters low-confidence results, generates structured issue drafts, optionally
creates issues automatically (with deduplication), and pushes notifications
for maintainer approval (email / Telegram / WeChat).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .collaboration import (
    CANDIDATE_LABEL,
    RADAR_LABEL,
    candidate_labels,
    ensure_github_labels,
    format_candidate_block,
)
from .llm_client import parse_llm_json
from .logs import get_logger
from .profile import load_profile

logger = get_logger("radar")


# ─── Data models ─────────────────────────────────────────────────────────────

@dataclass
class RadarHit:
    """A single item detected by the Community Radar."""

    source: str               # "issue" | "discussion" | "pr"
    repo: str                 # "owner/repo"
    number: int
    title: str
    body: str
    url: str
    author: str
    created_at: datetime
    matched_keyword: str      # which keyword triggered the hit

    # AI analysis results (populated after classification)
    category: str = ""        # "bug" | "feature_request" | "question" | "noise"
    confidence: float = 0.0
    summary: str = ""
    suggested_title: str = ""
    suggested_labels: list[str] = field(default_factory=list)
    action_needed: bool = False


@dataclass
class RadarReport:
    """Result of a Community Radar scan."""

    repo: str
    scanned_at: datetime
    total_scanned: int
    hits: list[RadarHit] = field(default_factory=list)
    bugs: list[RadarHit] = field(default_factory=list)
    feature_requests: list[RadarHit] = field(default_factory=list)
    noise: list[RadarHit] = field(default_factory=list)
    # Track issues created or updated by this scan
    issues_created: list[dict[str, Any]] = field(default_factory=list)
    issues_updated: list[dict[str, Any]] = field(default_factory=list)


# ─── GitHub scanning ─────────────────────────────────────────────────────────

def _build_search_query(keywords: list[str], repo: str) -> str:
    """Build a GitHub search query from keywords.

    Example: 'repo:owner/name (bug OR crash) type:issue'
    """
    terms = " OR ".join(f'"{kw}"' for kw in keywords)
    return f"repo:{repo} ({terms})"


def scan_issues(
    gh_client: Any,
    repo: str,
    keywords: list[str],
    since: datetime | None = None,
    max_results: int = 50,
) -> list[RadarHit]:
    """Scan recent GitHub issues in a repo for keyword matches.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        keywords: List of keywords to search for.
        since: Only scan issues updated after this datetime.
        max_results: Max issues to scan.

    Returns:
        List of RadarHit objects.
    """
    gh_repo = gh_client.get_repo(repo)
    hits: list[RadarHit] = []

    # Get recent issues
    issues = gh_repo.get_issues(state="all", sort="updated", direction="desc")
    count = 0

    for issue in issues:
        if count >= max_results:
            break
        # Skip pull requests (they also appear in get_issues)
        if issue.pull_request is not None:
            continue
        if since and issue.updated_at < since:
            continue

        combined = f"{issue.title} {issue.body or ''}".lower()
        for kw in keywords:
            if kw.lower() in combined:
                hits.append(RadarHit(
                    source="issue",
                    repo=repo,
                    number=issue.number,
                    title=issue.title,
                    body=issue.body or "",
                    url=issue.html_url,
                    author=issue.user.login if issue.user else "unknown",
                    created_at=issue.created_at,
                    matched_keyword=kw,
                ))
                break  # one keyword match per issue

        count += 1

    return hits


DISCUSSION_GRAPHQL_SCHEMA = """
number
title
body
url
createdAt
updatedAt
author { login }
"""


def scan_discussions(
    gh_client: Any,
    repo: str,
    keywords: list[str],
    since: datetime | None = None,
    max_results: int = 30,
) -> list[RadarHit]:
    """Scan recent GitHub Discussions for keyword matches.

    Uses PyGithub's GraphQL-based get_discussions() API.
    Requires a token with ``discussions:read`` scope.
    Falls back gracefully if discussions are unavailable.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        keywords: List of keywords to search for.
        since: Only scan discussions updated after this datetime.
        max_results: Max discussions to scan.

    Returns:
        List of RadarHit objects.
    """
    hits: list[RadarHit] = []

    if not keywords:
        return hits

    try:
        gh_repo = gh_client.get_repo(repo)

        # PyGithub >=2.1 provides native discussion support via GraphQL.
        # Falls back gracefully on older versions or repos without discussions.
        if not hasattr(gh_repo, "get_discussions"):
            logger.warning(
                "Discussion scanning requires PyGithub >=2.1. "
                "Upgrade with: pip install --upgrade PyGithub"
            )
            return hits

        discussions = gh_repo.get_discussions(DISCUSSION_GRAPHQL_SCHEMA)

        count = 0
        for discussion in discussions:
            if count >= max_results:
                break

            updated = getattr(discussion, "updated_at", discussion.created_at)
            if since and updated.replace(tzinfo=timezone.utc) < since:
                continue

            body = getattr(discussion, "body_text", "") or getattr(discussion, "body", "") or ""
            combined = f"{discussion.title} {body}".lower()

            for kw in keywords:
                if kw.lower() in combined:
                    author = "unknown"
                    if discussion.author:
                        author = getattr(discussion.author, "login", "unknown")
                    hits.append(RadarHit(
                        source="discussion",
                        repo=repo,
                        number=discussion.number,
                        title=discussion.title,
                        body=body,
                        url=discussion.url,
                        author=author,
                        created_at=discussion.created_at,
                        matched_keyword=kw,
                    ))
                    break

            count += 1

        if count > 0:
            logger.info(f"  Scanned {count} discussions, found {len(hits)} keyword matches")

    except Exception as e:
        logger.warning(f"Discussion scan failed for {repo}: {e}")

    return hits


# ─── Cross-repo (global) scanning ────────────────────────────────────────────

def scan_global_issues(
    gh_client: Any,
    search_query: str,
    keywords: list[str],
    since: datetime | None = None,
    max_results: int = 50,
) -> list[RadarHit]:
    """Scan GitHub globally for issues mentioning the project.

    Uses the `GitHub Search API <https://docs.github.com/en/rest/search>`_
    to find issues across **all public repositories** that match the query.

    Args:
        gh_client: PyGithub Github instance.
        search_query: GitHub search query (e.g. ``'"myproject" is:issue is:open'``).
        keywords: Optional keywords to match within result bodies.
                  If empty, every search result is included.
        since: Only include issues updated after this datetime.
        max_results: Max issues to return.

    Returns:
        List of RadarHit objects (``repo`` field reflects the source repo).
    """
    hits: list[RadarHit] = []

    try:
        results = gh_client.search_issues(
            query=search_query,
            sort="updated",
            order="desc",
        )

        count = 0
        for issue in results:
            if count >= max_results:
                break
            if since and issue.updated_at < since:
                continue

            combined = f"{issue.title} {issue.body or ''}".lower()

            matched_kw = ""
            if keywords:
                for kw in keywords:
                    if kw.lower() in combined:
                        matched_kw = kw
                        break
            else:
                # No keywords → include every result
                matched_kw = search_query

            if matched_kw:
                issue_repo = getattr(issue, "repository", None)
                repo_full = issue_repo.full_name if issue_repo else "unknown"

                hits.append(RadarHit(
                    source="issue",
                    repo=repo_full,
                    number=issue.number,
                    title=issue.title,
                    body=issue.body or "",
                    url=issue.html_url,
                    author=issue.user.login if issue.user else "unknown",
                    created_at=issue.created_at,
                    matched_keyword=matched_kw,
                ))

            count += 1

    except Exception as e:
        logger.warning(f"Global issue search failed: {e}")

    return hits


_GLOBAL_DISCUSSION_GRAPHQL = """
query($query: String!, $first: Int!) {
  search(query: $query, type: DISCUSSION, first: $first) {
    nodes {
      ... on Discussion {
        number
        title
        body
        url
        createdAt
        updatedAt
        author { login }
        repository { nameWithOwner }
      }
    }
  }
}
"""


def scan_global_discussions(
    gh_client: Any,
    search_query: str,
    keywords: list[str],
    since: datetime | None = None,
    max_results: int = 30,
) -> list[RadarHit]:
    """Scan GitHub globally for discussions mentioning the project.

    Uses the `GitHub GraphQL API
    <https://docs.github.com/en/graphql/reference/queries#search>`_
    to search discussions across **all public repositories**.

    Args:
        gh_client: PyGithub Github instance.
        search_query: GitHub search query (e.g. ``'"myproject" type:discussion'``).
        keywords: Optional keywords to match within result bodies.
                  If empty, every search result is included.
        since: Only include discussions updated after this datetime.
        max_results: Max discussions to return.

    Returns:
        List of RadarHit objects (``repo`` field reflects the source repo).
    """
    hits: list[RadarHit] = []

    try:
        # Access the internal GraphQL requester.
        # PyGithub does not expose a public `graphql_query` method on the
        # Github object, but the underlying Requester supports it.
        requester = gh_client._Github__requester  # type: ignore[attr-defined]

        result = requester.graphql_query(
            query=_GLOBAL_DISCUSSION_GRAPHQL,
            variables={"query": search_query, "first": max_results},
        )

        nodes = result.get("data", {}).get("search", {}).get("nodes", [])

        for node in nodes:
            if not node:
                continue

            body = node.get("body", "") or ""
            combined = f"{node.get('title', '')} {body}".lower()

            matched_kw = ""
            if keywords:
                for kw in keywords:
                    if kw.lower() in combined:
                        matched_kw = kw
                        break
            else:
                matched_kw = search_query

            if not matched_kw:
                continue

            created_at_str = node.get("createdAt", "")
            created_at = (
                datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at_str else datetime.now()
            )

            updated_at_str = node.get("updatedAt", created_at_str)
            if updated_at_str and since:
                updated_at = datetime.fromisoformat(
                    updated_at_str.replace("Z", "+00:00")
                )
                if updated_at < since:
                    continue

            author = "unknown"
            if node.get("author"):
                author = node["author"].get("login", "unknown")

            repo_name = (
                node.get("repository", {}).get("nameWithOwner", "unknown")
                if node.get("repository") else "unknown"
            )

            hits.append(RadarHit(
                source="discussion",
                repo=repo_name,
                number=node["number"],
                title=node.get("title", ""),
                body=body,
                url=node["url"],
                author=author,
                created_at=created_at,
                matched_keyword=matched_kw,
            ))

    except Exception as e:
        logger.warning(f"Global discussion search failed: {e}")

    return hits


# ─── AI Classification ───────────────────────────────────────────────────────

CLASSIFIER_SYSTEM_PROMPT = """\
You are an AI triage assistant for open source maintainers.
Your job is to classify community posts (issues/discussions) into categories.

Analyze the post and respond with a single JSON object:

{
  "category": "bug | feature_request | question | noise",
  "confidence": 0.0 to 1.0,
  "summary": "One-sentence summary of what the user is asking.",
  "suggested_title": "A clear, concise issue title (if actionable).",
  "suggested_labels": ["label1", "label2"],
  "action_needed": true/false,
  "reasoning": "Brief explanation of classification."
}

Classification rules:
- "bug": User reports broken/unexpected behavior, error messages, crashes.
- "feature_request": User asks for new functionality, enhancements, improvements.
- "question": User asks how to use something, seeks clarification.
- "noise": Spam, off-topic, vague rants, "+1" comments, or unactionable chatter.

Be conservative with "action_needed": only mark true for clearly actionable items.
"""


def classify_hit(
    hit: RadarHit,
    llm_client: Any,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
) -> RadarHit:
    """Use AI to classify a RadarHit.

    Args:
        hit: The RadarHit to classify.
        llm_client: OpenAI-compatible client.
        model: Model name to use.
        temperature: LLM temperature.

    Returns:
        The same RadarHit with AI classification fields populated.
    """
    user_prompt = f"""\
## Post
**Title:** {hit.title}
**Source:** {hit.source} #{hit.number}
**Author:** {hit.author}
**Matched keyword:** {hit.matched_keyword}

**Body:**
{hit.body[:4000]}

Classify this post. Respond with JSON only.
"""

    try:
        response = llm_client.chat(
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=temperature,
            max_tokens=500,
        )

        result = parse_llm_json(response.content)

        hit.category = result.get("category", "noise")
        hit.confidence = float(result.get("confidence", 0))
        hit.summary = result.get("summary", "")
        hit.suggested_title = result.get("suggested_title", "")
        hit.suggested_labels = result.get("suggested_labels", [])
        hit.action_needed = result.get("action_needed", False)

    except Exception as e:
        logger.error(f"Classification failed for {hit.repo}#{hit.number}: {e}")
        hit.category = "noise"
        hit.confidence = 0.0

    return hit


# ─── Filtering ───────────────────────────────────────────────────────────────

def filter_hits(
    hits: list[RadarHit],
    confidence_threshold: float = 0.7,
) -> list[RadarHit]:
    """Filter hits below confidence threshold.

    Args:
        hits: Classified RadarHit objects.
        confidence_threshold: Minimum AI confidence to keep.

    Returns:
        Filtered list of actionable hits (excludes "noise" and low confidence).
    """
    return [
        h for h in hits
        if h.category != "noise" and h.confidence >= confidence_threshold
    ]


# ─── Draft generation ────────────────────────────────────────────────────────

DRAFT_SYSTEM_PROMPT = """\
You are an assistant that drafts well-structured GitHub issues from community
feedback. Write clear, actionable issue descriptions.

Respond with a JSON object:
{
  "title": "Clear, descriptive title",
  "body": "Full issue body in markdown, with sections: Description, Steps to Reproduce (if bug), Expected Behavior, Additional Context. The first line MUST be '> **Reported by @author** in [original discussion](URL)' linking to the source.",
  "labels": ["label1"]
}
"""


def generate_issue_draft(
    hit: RadarHit,
    llm_client: Any,
    profile: dict,
) -> dict[str, Any]:
    """Generate a structured issue draft from a RadarHit.

    Args:
        hit: A classified RadarHit (already filtered, high confidence).
        llm_client: OpenAI-compatible LLM client.
        profile: Maintainer profile dict.

    Returns:
        Dict with keys: title, body, labels.
    """
    tone = profile.get("tone", {})
    language = tone.get("language", "en")

    user_prompt = f"""\
## Original Post
**Title:** {hit.title}
**Author:** {hit.author}
**Source:** {hit.url}

**Body:**
{hit.body[:4000]}

## AI Analysis
- Category: {hit.category}
- Confidence: {hit.confidence:.0%}
- Summary: {hit.summary}

## Maintainer Preferences
- Language: {language}
- Tone: {tone.get('style', 'friendly')}

Create a well-structured GitHub issue draft. Use {language}.

Important: The body MUST start with this exact line as the first line:
> **Reported by @{hit.author}** in [original discussion]({hit.url})

Follow that with a blank line, then the structured issue description.
"""

    try:
        response = llm_client.chat(
            system=DRAFT_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            model=profile.get("agent", {}).get("model", "deepseek-chat"),
            temperature=0.2,
            max_tokens=1500,
        )

        return parse_llm_json(response.content)  # type: ignore[no-any-return]

    except Exception as e:
        logger.error(f"Draft generation failed: {e}")
        return {
            "title": hit.suggested_title or hit.title,
            "body": f"> Auto-generated from community post by {hit.author}\n\n{hit.body[:2000]}",
            "labels": hit.suggested_labels,
        }


# ─── Notification ────────────────────────────────────────────────────────────

def notify_maintainer(profile: dict, report: RadarReport) -> dict[str, bool]:
    """Send notifications about radar findings.

    Supports: email (SMTP), Telegram bot, WeChat webhook.

    Args:
        profile: Maintainer profile with notification config.
        report: RadarReport with findings.

    Returns:
        Dict of {channel: success}.
    """
    notifications = profile.get("notifications", {})
    results: dict[str, bool] = {}

    # Build message
    actionable = [h for h in report.hits if h.action_needed]
    if not actionable:
        return results

    subject = f"🔭 RepoKeeper Radar: {len(actionable)} items in {report.repo}"
    body_parts = [f"## Community Radar Report — {report.repo}", ""]
    for hit in actionable:
        body_parts.append(
            f"- **[{hit.category}]** [{hit.suggested_title or hit.title}]({hit.url}) "
            f"({hit.confidence:.0%} confidence)"
        )
    body = "\n".join(body_parts)

    # Email
    if notifications.get("email"):
        results["email"] = _send_email(
            to=notifications["email"],
            subject=subject,
            body=body,
        )

    # Telegram
    if notifications.get("telegram"):
        results["telegram"] = _send_telegram(
            token=notifications["telegram"],
            message=f"{subject}\n{body}",
        )

    # WeChat
    if notifications.get("wechat"):
        results["wechat"] = _send_wechat(
            webhook_url=notifications["wechat"],
            title=subject,
            content=body,
        )

    return results


def _send_email(to: str, subject: str, body: str) -> bool:
    """Send email via SMTP (placeholder — configure SMTP in env)."""
    import os
    import smtplib
    from email.mime.text import MIMEText

    smtp_host = os.environ.get("RKP_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("RKP_SMTP_PORT", "587"))
    smtp_user = os.environ.get("RKP_SMTP_USER", "")
    smtp_pass = os.environ.get("RKP_SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        logger.warning("SMTP not configured. Set RKP_SMTP_USER and RKP_SMTP_PASS.")
        return False

    try:
        msg = MIMEText(body, "markdown")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Email failed: {e}")
        return False


def _send_telegram(token: str, message: str) -> bool:
    """Send a Telegram message via bot API."""
    import requests

    try:
        # token can be "bot_token" or "chat_id:bot_token"
        if ":" in token:
            chat_id, bot_token = token.split(":", 1)
        else:
            bot_token = token
            chat_id = os.environ.get("RKP_TELEGRAM_CHAT_ID", "")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        return bool(resp.status_code == 200)
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False


def _send_wechat(webhook_url: str, title: str, content: str) -> bool:
    """Send a WeChat Work (企业微信) webhook message."""
    import requests

    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n{content}",
            },
        }
        resp = requests.post(webhook_url, json=payload, timeout=10)
        return bool(resp.status_code == 200)
    except Exception as e:
        logger.error(f"WeChat failed: {e}")
        return False


# ─── Main scan pipeline ──────────────────────────────────────────────────────

def run_radar(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict | None = None,
    since: datetime | None = None,
) -> RadarReport:
    """Run a complete Community Radar scan.

    Two modes are supported:

    * **Local mode** (default) — scans *this repo's* issues and discussions
      for keywords configured in ``radar.keywords``.
    * **Cross-repo mode** — when ``radar.cross_repo_search`` is ``true``,
      searches *all of GitHub* for mentions of the project (or a custom
      ``radar.cross_repo_query``) via the GitHub Search and GraphQL APIs.
      Results are deduplicated and issues are created in *this repo*.

    Pipeline:
        1. Scan issues and discussions (local or global).
        2. Classify each hit with AI.
        3. Filter low-confidence / noise results.
        4. Generate issue drafts or auto-create issues.
        5. Send notifications.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo) — issues are created here.
        profile: Maintainer profile (loaded if None).
        since: Only scan items after this datetime.

    Returns:
        RadarReport with full results.
    """
    if profile is None:
        profile = load_profile()

    radar_config = profile.get("radar", {})
    if not radar_config.get("enabled", True):
        logger.info(f"Radar disabled for {repo}")
        return RadarReport(repo=repo, scanned_at=datetime.now(), total_scanned=0)

    keywords = radar_config.get("keywords", [])
    cross_repo = radar_config.get("cross_repo_search", False)

    confidence_threshold = radar_config.get("confidence_threshold", 0.7)
    model = profile.get("agent", {}).get("model", "deepseek-chat")

    # Step 1: Scan
    if cross_repo:
        # ── Cross-repo (global) mode ──
        # Build the search query from cross_repo_query or derive from repo name.
        query = radar_config.get("cross_repo_query", "")
        if not query:
            project_name = repo.split("/")[-1] if "/" in repo else repo
            query = f'"{project_name}"'

        # Exclude the project's own repo to focus on *other* communities.
        exclude_repo = f" -repo:{repo}"

        issue_query = f"{query} is:issue is:open{exclude_repo}"
        discussion_query = f"{query} type:discussion{exclude_repo}"

        logger.info(
            f"🔭 Radar scanning globally for: {query} "
            f"(issues + discussions, excluding {repo})"
        )
        hits = scan_global_issues(gh_client, issue_query, keywords, since=since)
        hits += scan_global_discussions(gh_client, discussion_query, keywords, since=since)

    else:
        # ── Single-repo (local) mode ──
        if not keywords:
            logger.warning(
                f"No keywords configured for {repo}. "
                f"Add 'radar.keywords' to repokeeper.yml."
            )
            return RadarReport(repo=repo, scanned_at=datetime.now(), total_scanned=0)

        logger.info(f"🔭 Radar scanning {repo} for keywords: {keywords}")
        hits = scan_issues(gh_client, repo, keywords, since=since)
        hits += scan_discussions(gh_client, repo, keywords, since=since)

    logger.info(f"  Found {len(hits)} raw hits")

    # Step 2: Classify
    for hit in hits:
        classify_hit(hit, llm_client, model=model)

    # Step 3: Filter
    actionable = filter_hits(hits, confidence_threshold)
    logger.info(f"  {len(actionable)} actionable after filtering (threshold={confidence_threshold})")

    # Step 4: Auto-create issues (or generate drafts for notification)
    auto_create = radar_config.get("auto_create_issue", False)
    gh_repo = gh_client.get_repo(repo) if gh_client else None

    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    if auto_create and gh_repo is not None:
        logger.info(f"  Auto-creating issues for {len(actionable)} actionable hits...")
        for hit in actionable:
            try:
                result = _process_radar_hit(hit, gh_repo, llm_client, profile)
                if result is not None:
                    if "action" in result:
                        updated.append(result)
                    else:
                        created.append(result)
            except Exception as e:
                logger.error(f"  Failed to process hit {hit.url}: {e}")
                # Fall back to generating just the draft for notification
                draft = generate_issue_draft(hit, llm_client, profile)
                hit.suggested_title = draft.get("title", hit.suggested_title)
                hit.suggested_labels = draft.get("labels", hit.suggested_labels)
    else:
        # Draft-only mode (auto_create_issue is False)
        for hit in actionable:
            draft = generate_issue_draft(hit, llm_client, profile)
            hit.suggested_title = draft.get("title", hit.suggested_title)
            hit.suggested_labels = draft.get("labels", hit.suggested_labels)

    if created:
        logger.info(f"  Created {len(created)} new issues")
    if updated:
        logger.info(f"  Updated {len(updated)} existing issues")

    # Step 5: Build report
    report = RadarReport(
        repo=repo,
        scanned_at=datetime.now(),
        total_scanned=len(hits),
        hits=actionable,
        bugs=[h for h in actionable if h.category == "bug"],
        feature_requests=[h for h in actionable if h.category == "feature_request"],
        noise=[h for h in hits if h.category == "noise"],
        issues_created=created,
        issues_updated=updated,
    )

    # Step 6: Notify
    if actionable:
        notify_maintainer(profile, report)

    return report


# ─── Branding ─────────────────────────────────────────────────────────────────

# Hidden marker injected into issue bodies for reliable deduplication.
# Format: <!-- repokeeper-radar:SOURCE_URL -->
_RADAR_MARKER_PREFIX = "<!-- repokeeper-radar:"
_RADAR_MARKER_SUFFIX = "-->"

_REPOKEEPER_FOOTER = (
    "---\n"
    "<sub>🤖 Created by [RepoKeeper](https://github.com/shenxianpeng/repokeeper) "
    "— AI-powered open source maintenance. "
    "[Learn more](https://github.com/shenxianpeng/repokeeper#readme)</sub>"
)


def _radar_marker(source_url: str) -> str:
    """Build the hidden deduplication marker for a source URL."""
    return f"{_RADAR_MARKER_PREFIX}{source_url}{_RADAR_MARKER_SUFFIX}"


def _extract_source_url_from_marker(body: str) -> str | None:
    """Extract the source URL from a radar marker in an issue body.

    Args:
        body: Issue body text.

    Returns:
        Source URL if found, ``None`` otherwise.
    """
    import re as _re

    m = _re.search(
        rf"{_re.escape(_RADAR_MARKER_PREFIX)}(.+?){_re.escape(_RADAR_MARKER_SUFFIX)}",
        body,
    )
    return m.group(1) if m else None


def _build_radar_issue_body(draft_body: str, hit: RadarHit) -> str:
    """Wrap the AI-generated draft body with RepoKeeper header, marker, and footer.

    The final body structure::

        > **Reported by @author** in [original discussion](url)
        <blank line>
        <draft body>
        <!-- repokeeper-radar:SOURCE_URL -->
        ---
        <Repokeeper footer>

    Args:
        draft_body: AI-generated body from :func:`generate_issue_draft`.
        hit: The RadarHit with source metadata.

    Returns:
        Full issue body string.
    """
    # Strip any existing header markers from the draft to avoid duplication
    body = draft_body.strip()
    for prefix in ("> **Reported by", "> **Originally reported by"):
        if body.startswith(prefix):
            # Remove the first line if it's a header
            lines = body.split("\n", 1)
            body = lines[1].strip() if len(lines) > 1 else ""
            break

    header = (
        f"> **Reported by @{hit.author}** "
        f"in [original {hit.source}]({hit.url})\n"
    )
    candidate = format_candidate_block(
        source_module="Radar",
        recommended_action="implement",
        confidence=hit.confidence,
        source_url=hit.url,
        summary=hit.summary,
        acceptance=(
            "The issue is confirmed as actionable and the maintainer explicitly "
            "approves implementation."
        ),
    )
    marker = _radar_marker(hit.url)

    return f"{header}\n{candidate}\n\n{body}\n\n{marker}\n{_REPOKEEPER_FOOTER}"


# ─── Deduplication & issue creation ──────────────────────────────────────────


def _find_existing_radar_issue(
    gh_repo: Any,
    source_url: str,
    title: str,
) -> Any | None:
    """Find an existing issue created by RepoKeeper Radar for the same source.

    Checks in this order:
    1. Issues with the ``repokeeper-radar`` label that contain the hidden
       marker for this exact source URL.
    2. Open issues with the ``repokeeper-radar`` label that have a similar
       title (case-insensitive, leading/trailing whitespace removed).

    Args:
        gh_repo: PyGithub Repository object.
        source_url: URL of the original community post.
        title: Suggested title for the new issue.

    Returns:
        The existing PyGithub Issue object if found, ``None`` otherwise.
    """
    marker = _radar_marker(source_url)
    normalized_title = title.strip().lower()

    try:
        # Get issues with the radar label (open + closed, limit to recent)
        issues = gh_repo.get_issues(labels=[RADAR_LABEL], state="all", sort="updated", direction="desc")
        for issue in issues:
            body = issue.body or ""

            # Exact match by hidden marker (most reliable)
            if marker in body:
                logger.info(
                    f"  Found existing issue #{issue.number} with matching radar marker"
                )
                return issue

            # Fallback: title similarity (for issues created before marker support)
            if issue.title.strip().lower() == normalized_title:
                logger.info(
                    f"  Found existing issue #{issue.number} with matching title"
                )
                return issue

    except Exception as e:
        logger.warning(f"  Deduplication search failed: {e}")

    return None


def _create_radar_issue(
    gh_repo: Any,
    hit: RadarHit,
    draft_body: str,
    labels: list[str],
) -> dict[str, Any]:
    """Create a GitHub issue from a Radar hit.

    Applies the ``repokeeper-candidate`` and ``repokeeper-radar`` labels
    alongside any category-specific labels.  The body includes a hidden
    deduplication marker and professional RepoKeeper branding.

    Args:
        gh_repo: PyGithub Repository object.
        hit: The classified RadarHit.
        draft_body: AI-generated body text.
        labels: Labels to apply (from draft generation).

    Returns:
        Dict with ``issue_number``, ``issue_url``, ``source_url``.

    Raises:
        RuntimeError: If GitHub refuses to create the issue.
    """
    full_body = _build_radar_issue_body(draft_body, hit)
    all_labels = candidate_labels(RADAR_LABEL, labels)
    ensure_github_labels(gh_repo, all_labels)

    try:
        created = gh_repo.create_issue(
            title=hit.suggested_title or hit.title,
            body=full_body,
            labels=all_labels,
        )
    except Exception as e:
        raise RuntimeError(
            f"Failed to create issue for {hit.url}: {e}"
        ) from e

    logger.info(
        f"  Created issue #{created.number}: {created.title} "
        f"(labels: {', '.join(all_labels)})"
    )

    return {
        "issue_number": created.number,
        "issue_url": created.html_url,
        "source_url": hit.url,
    }


def _update_existing_radar_issue(
    issue_obj: Any,
    hit: RadarHit,
) -> dict[str, Any]:
    """Update an existing radar issue by adding a comment about new activity.

    Does not modify the original issue body — only adds a timestamped comment
    noting that the original discussion was seen again.  This keeps the issue
    history clean while letting the maintainer know the topic is still active.

    Args:
        issue_obj: Existing PyGithub Issue object.
        hit: The RadarHit that was matched.

    Returns:
        Dict with ``issue_number``, ``issue_url``, ``source_url``, ``action``.
    """
    candidate = format_candidate_block(
        source_module="Radar",
        recommended_action="implement",
        confidence=hit.confidence,
        source_url=hit.url,
        summary=hit.summary,
        acceptance="Maintainer confirms the topic is still actionable.",
    )
    comment = (
        f"🔭 **RepoKeeper Radar** detected renewed activity on the "
        f"[original {hit.source}]({hit.url}) "
        f"(matched keyword: `{hit.matched_keyword}`).\n\n"
        f"{candidate}\n\n"
        f"This issue may still be relevant. "
        f"Consider reviewing or updating its status."
    )

    try:
        issue_obj.add_to_labels(CANDIDATE_LABEL, RADAR_LABEL)
        issue_obj.create_comment(comment)
    except Exception as e:
        logger.warning(f"  Failed to add update comment to #{issue_obj.number}: {e}")

    logger.info(
        f"  Updated existing issue #{issue_obj.number} with activity comment"
    )

    return {
        "issue_number": issue_obj.number,
        "issue_url": issue_obj.html_url,
        "source_url": hit.url,
        "action": "commented",
    }


def _process_radar_hit(
    hit: RadarHit,
    gh_repo: Any,
    llm_client: Any,
    profile: dict,
) -> dict[str, Any] | None:
    """Process a single actionable radar hit: draft → deduplicate → create/update.

    Args:
        hit: Classified and filtered RadarHit with a draft already generated.
        gh_repo: PyGithub Repository object.
        llm_client: OpenAI-compatible LLM client.
        profile: Maintainer profile dict.

    Returns:
        Result dict (``issues_created`` or ``issues_updated`` shape),
        or ``None`` if skipped.
    """
    # Generate the AI draft for the issue body (without branding wrapper)
    draft = generate_issue_draft(hit, llm_client, profile)
    hit.suggested_title = draft.get("title", hit.suggested_title)
    hit.suggested_labels = draft.get("labels", hit.suggested_labels)
    draft_body = draft.get("body", hit.body[:2000])

    # Check for existing issue
    existing = _find_existing_radar_issue(
        gh_repo,
        source_url=hit.url,
        title=hit.suggested_title,
    )

    if existing is not None:
        # Update existing issue with a comment instead of creating duplicate
        return _update_existing_radar_issue(existing, hit)

    # Create new issue
    return _create_radar_issue(gh_repo, hit, draft_body, hit.suggested_labels)


def generate_radar_summary(report: RadarReport) -> str:
    """Generate a markdown summary of the radar scan.

    Args:
        report: Filled RadarReport.

    Returns:
        Markdown string.
    """
    lines = [
        f"# 📡 Community Radar Report — [{report.repo}](https://github.com/{report.repo})",
        "",
        f"**Scanned:** {report.scanned_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Total scanned:** {report.total_scanned} | **Actionable:** {len(report.hits)}",
        "",
    ]

    if report.bugs:
        lines.append("## 🐛 Bugs")
        lines.append("")
        for hit in report.bugs:
            lines.append(f"- [{hit.title}]({hit.url}) — {hit.summary}")
        lines.append("")

    if report.feature_requests:
        lines.append("## 💡 Feature Requests")
        lines.append("")
        for hit in report.feature_requests:
            lines.append(f"- [{hit.title}]({hit.url}) — {hit.summary}")
        lines.append("")

    if not report.hits:
        lines.append("✅ No actionable hits found.")
        lines.append("")

    if report.issues_created:
        lines.append("## 📝 Issues Created")
        lines.append("")
        for entry in report.issues_created:
            lines.append(
                f"- [#{entry['issue_number']}]({entry['issue_url']}) "
                f"← [source]({entry['source_url']})"
            )
        lines.append("")

    if report.issues_updated:
        lines.append("## 🔄 Issues Updated (duplicates)")
        lines.append("")
        for entry in report.issues_updated:
            lines.append(
                f"- [#{entry['issue_number']}]({entry['issue_url']}) "
                f"← [source]({entry['source_url']})"
            )
        lines.append("")

    pending = report.hits or report.issues_created or report.issues_updated
    if pending:
        lines.append("## ⏳ Waiting for Maintainer Approval")
        lines.append("")
        lines.append(
            "RepoKeeper will not implement these candidates until a maintainer "
            "adds `agent-todo` or comments `@repokeeper go`."
        )
        lines.append("")
        if report.issues_created:
            for entry in report.issues_created:
                lines.append(f"- [#{entry['issue_number']}]({entry['issue_url']}) — candidate issue")
        if report.issues_updated:
            for entry in report.issues_updated:
                lines.append(f"- [#{entry['issue_number']}]({entry['issue_url']}) — updated candidate")
        if not report.issues_created and not report.issues_updated and report.hits:
            for hit in report.hits[:10]:
                lines.append(f"- [{hit.title}]({hit.url}) — {hit.summary}")
        lines.append("")

    return "\n".join(lines)
