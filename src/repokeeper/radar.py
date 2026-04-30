"""
Module 1: Community Radar

Monitors GitHub issues and Discussions for keywords specified in the maintainer
profile. Uses AI to classify each hit as bug, feature request, or noise,
filters low-confidence results, generates structured issue drafts, and
pushes notifications for maintainer approval (email / Telegram / WeChat).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .profile import load_profile

logger = logging.getLogger(__name__)


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


def scan_discussions(
    gh_client: Any,
    repo: str,
    keywords: list[str],
    since: datetime | None = None,
    max_results: int = 30,
) -> list[RadarHit]:
    """Scan recent GitHub Discussions for keyword matches.

    Uses the GitHub GraphQL API (Discussions are not available via REST).
    Falls back gracefully if no token with discussion permissions.

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

    try:
        # GitHub Discussions require GraphQL API
        owner, name = repo.split("/")
        query = """
        query($owner: String!, $name: String!, $first: Int!) {
          repository(owner: $owner, name: $name) {
            discussions(first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
              nodes {
                number
                title
                body
                url
                createdAt
                updatedAt
                author { login }
              }
            }
          }
        }
        """
        # This requires a GraphQL client; for now we note the limitation
        logger.info(
            "Discussion scanning requires GraphQL API. "
            "Set GITHUB_TOKEN with discussion:read scope."
        )
        # Actual implementation would use requests to GitHub GraphQL API
        # Skipping for now as PyGithub doesn't natively support Discussions
    except Exception as e:
        logger.warning(f"Discussion scan failed for {repo}: {e}")

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
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

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
  "body": "Full issue body in markdown, with sections: Description, Steps to Reproduce (if bug), Expected Behavior, Additional Context",
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
"""

    try:
        response = llm_client.chat.completions.create(
            model=profile.get("agent", {}).get("model", "deepseek-chat"),
            messages=[
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)

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
        return resp.status_code == 200
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
        return resp.status_code == 200
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

    1. Scans issues and discussions for keywords from profile.
    2. Classifies each hit with AI.
    3. Filters low-confidence / noise results.
    4. Generates issue drafts for actionable hits.
    5. Sends notifications.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
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
    if not keywords:
        logger.warning(f"No keywords configured for {repo}. Add 'radar.keywords' to repokeeper.yml.")
        return RadarReport(repo=repo, scanned_at=datetime.now(), total_scanned=0)

    confidence_threshold = radar_config.get("confidence_threshold", 0.7)
    model = profile.get("agent", {}).get("model", "deepseek-chat")

    # Step 1: Scan
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

    # Step 4: Generate drafts
    for hit in actionable:
        draft = generate_issue_draft(hit, llm_client, profile)
        hit.suggested_title = draft.get("title", hit.suggested_title)
        hit.suggested_labels = draft.get("labels", hit.suggested_labels)

    # Step 5: Build report
    report = RadarReport(
        repo=repo,
        scanned_at=datetime.now(),
        total_scanned=len(hits),
        hits=actionable,
        bugs=[h for h in actionable if h.category == "bug"],
        feature_requests=[h for h in actionable if h.category == "feature_request"],
        noise=[h for h in hits if h.category == "noise"],
    )

    # Step 6: Notify
    if actionable:
        notify_maintainer(profile, report)

    return report
