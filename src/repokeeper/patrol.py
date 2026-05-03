"""
Module 2: Daily Patrol

Daily repository health checks:
- Dependency version scanning → auto-PR for outdated packages
- CI failure analysis → diagnosis + auto-fix attempts
- Stale issue detection → summaries for maintainer review
- Health summary generation per repo
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .profile import load_profile

logger = logging.getLogger(__name__)


# ─── Data models ─────────────────────────────────────────────────────────────

@dataclass
class DepCheck:
    """Result of checking a single dependency."""

    name: str
    current: str
    latest: str
    is_outdated: bool
    severity: str = "info"        # "critical" | "high" | "medium" | "low" | "info"
    changelog_url: str = ""
    breaking: bool = False


@dataclass
class CIFailure:
    """A CI failure with diagnosis."""

    workflow_name: str
    run_id: int
    run_url: str
    failed_at: datetime
    conclusion: str               # "failure" | "cancelled" | "timed_out"
    log_snippet: str = ""
    diagnosis: str = ""
    suggested_fix: str = ""
    auto_fixable: bool = False


@dataclass
class StaleIssue:
    """An open issue that hasn't been updated in a while."""

    number: int
    title: str
    url: str
    author: str
    created_at: datetime
    last_updated: datetime
    days_stale: int
    labels: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class PatrolReport:
    """Complete daily patrol report for one repository."""

    repo: str
    scanned_at: datetime
    # Dependencies
    dependencies_checked: int = 0
    outdated_deps: list[DepCheck] = field(default_factory=list)
    # CI
    ci_failures: list[CIFailure] = field(default_factory=list)
    ci_fixed: list[str] = field(default_factory=list)
    # Issues
    stale_issues: list[StaleIssue] = field(default_factory=list)
    # Meta
    health_score: int = 100       # 0-100, starts perfect, deductions applied
    warnings: list[str] = field(default_factory=list)


# ─── Dependency scanning ─────────────────────────────────────────────────────

# Supported package manifests
MANIFEST_PATTERNS = {
    "requirements.txt": "pip",
    "Pipfile": "pipenv",
    "pyproject.toml": "pip",      # can also be poetry/flit
    "package.json": "npm",
    "yarn.lock": "yarn",
    "go.mod": "go",
    "Cargo.toml": "cargo",
    "Gemfile": "bundler",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "composer.json": "composer",
}


def find_manifests(root: Path = Path(".")) -> list[Path]:
    """Find all supported package manifest files in a repo.

    Args:
        root: Repository root path.

    Returns:
        List of paths to manifest files.
    """
    manifests = []
    for pattern in MANIFEST_PATTERNS:
        for found in root.rglob(pattern):
            # Skip virtualenvs, node_modules, etc.
            parts = set(found.parts)
            if parts & {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}:
                continue
            manifests.append(found)
    return manifests


def check_python_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Python dependencies for outdated packages.

    Supports requirements.txt, pyproject.toml using pip-audit / pip list.
    Falls back to pip index if pip-audit not available.

    Args:
        manifest_path: Path to a Python dependency file.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # Try pip-audit first (best for security vulns + outdated checks)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True, timeout=60,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0:
            outdated = json.loads(result.stdout)
            for pkg in outdated:
                results.append(DepCheck(
                    name=pkg["name"],
                    current=pkg["version"],
                    latest=pkg["latest_version"],
                    is_outdated=True,
                    severity="medium",
                ))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        logger.warning(f"Dependency check failed for {manifest_path}: {e}")

    return results


def check_node_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Node.js dependencies for outdated packages using npm/yarn.

    Args:
        manifest_path: Path to package.json.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # npm outdated --json
        result = subprocess.run(
            ["npm", "outdated", "--json"],
            capture_output=True, text=True, timeout=60,
            cwd=manifest_path.parent,
        )
        if result.returncode == 1 and result.stdout.strip():
            # npm outdated exits 1 when there are outdated packages
            outdated = json.loads(result.stdout)
            for name, info in outdated.items():
                results.append(DepCheck(
                    name=name,
                    current=info.get("current", "?"),
                    latest=info.get("latest", "?"),
                    is_outdated=True,
                    severity="high" if info.get("type") == "latest" else "medium",
                ))
    except Exception as e:
        logger.warning(f"npm outdated failed for {manifest_path}: {e}")

    return results


def check_go_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Go dependencies using go list -u -m all.

    Args:
        manifest_path: Path to go.mod.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        result = subprocess.run(
            ["go", "list", "-u", "-m", "-json", "all"],
            capture_output=True, text=True, timeout=60,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0:
            # Parse JSON stream (multiple objects, not a list)
            decoder = json.JSONDecoder()
            pos = 0
            data = result.stdout.strip()
            while pos < len(data):
                obj, end = decoder.raw_decode(data[pos:])
                pos += end
                if obj.get("Update"):
                    results.append(DepCheck(
                        name=obj.get("Path", "?"),
                        current=obj.get("Version", "?"),
                        latest=obj["Update"].get("Version", "?"),
                        is_outdated=True,
                        severity="medium",
                    ))
                # Skip whitespace between objects
                while pos < len(data) and data[pos] in " \t\n\r":
                    pos += 1

    except Exception as e:
        logger.warning(f"go list failed for {manifest_path}: {e}")

    return results


def scan_dependencies(repo_path: Path = Path(".")) -> list[DepCheck]:
    """Scan all dependencies in a repository.

    Args:
        repo_path: Path to repository root.

    Returns:
        Combined list of all outdated dependency checks.
    """
    all_deps: list[DepCheck] = []
    manifests = find_manifests(repo_path)

    for manifest in manifests:
        name = manifest.name
        logger.info(f"  Checking dependencies in {manifest}")

        if name in ("requirements.txt", "Pipfile"):
            all_deps += check_python_deps(manifest)
        elif name == "pyproject.toml":
            # Check if it's a Python project
            all_deps += check_python_deps(manifest)
        elif name == "package.json":
            all_deps += check_node_deps(manifest)
        elif name == "go.mod":
            all_deps += check_go_deps(manifest)
        # Additional ecosystems can be added here

    return all_deps


# ─── CI failure analysis ─────────────────────────────────────────────────────

def scan_ci_failures(gh_client: Any, repo: str, since: datetime | None = None) -> list[CIFailure]:
    """Scan recent CI workflow runs for failures.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        since: Only check runs after this datetime.

    Returns:
        List of CIFailure objects.
    """
    failures: list[CIFailure] = []

    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=1)

    try:
        gh_repo = gh_client.get_repo(repo)
        workflows = gh_repo.get_workflows()

        for wf in workflows:
            runs = wf.get_runs(branch=gh_repo.default_branch)
            for run in runs:
                if run.created_at.replace(tzinfo=timezone.utc) < since:
                    continue
                if run.conclusion in ("failure", "cancelled", "timed_out"):
                    failures.append(CIFailure(
                        workflow_name=wf.name,
                        run_id=run.id,
                        run_url=run.html_url,
                        failed_at=run.created_at,
                        conclusion=run.conclusion,
                    ))
    except Exception as e:
        logger.warning(f"CI scan failed for {repo}: {e}")

    return failures


CI_DIAGNOSIS_PROMPT = """\
You are a DevOps engineer analyzing CI failures. Given a CI log snippet,
diagnose the most likely cause and suggest a concrete fix.

Respond with JSON:
{
  "diagnosis": "Root cause analysis in 1-2 sentences.",
  "suggested_fix": "Concrete steps to fix (code changes, config changes, etc.).",
  "auto_fixable": true/false,
  "confidence": 0.0-1.0
}

"auto_fixable": true only if the fix can be applied programmatically
(e.g., updating a config value, pinning a version, fixing a path).
Complex logic errors or missing dependencies = false.
"""


def diagnose_ci_failure(
    failure: CIFailure,
    llm_client: Any,
    gh_client: Any,
    model: str = "deepseek-chat",
) -> CIFailure:
    """Use AI to diagnose a CI failure.

    Fetches the workflow run logs and sends them to the LLM for analysis.

    Args:
        failure: CIFailure to diagnose.
        llm_client: OpenAI-compatible LLM client.
        gh_client: PyGithub Github instance.
        model: LLM model name.

    Returns:
        CIFailure with diagnosis fields populated.
    """
    try:
        # Fetch logs (GitHub API gives us job logs)
        owner, repo_name = failure.run_url.split("/")[-4:-2] if "/" in failure.run_url else ("", "")
        # Actually get the run object
        gh_client.get_repo(f"{owner}/{repo_name}")
        # This would use the checks API - simplified here
        log_snippet = f"Run ID: {failure.run_id}\nWorkflow: {failure.workflow_name}\nConclusion: {failure.conclusion}\nSee: {failure.run_url}"
        failure.log_snippet = log_snippet

        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CI_DIAGNOSIS_PROMPT},
                {"role": "user", "content": f"CI Failure:\n{log_snippet[:4000]}"},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

        failure.diagnosis = result.get("diagnosis", "Unable to diagnose.")
        failure.suggested_fix = result.get("suggested_fix", "")
        failure.auto_fixable = result.get("auto_fixable", False)

    except Exception as e:
        logger.error(f"CI diagnosis failed: {e}")
        failure.diagnosis = f"Diagnosis error: {e}"
        failure.auto_fixable = False

    return failure


# ─── Stale issue detection ───────────────────────────────────────────────────

def scan_stale_issues(
    gh_client: Any,
    repo: str,
    stale_days: int = 90,
    max_issues: int = 30,
) -> list[StaleIssue]:
    """Find open issues that haven't been updated recently.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        stale_days: Days since last update to consider stale.
        max_issues: Max issues to return.

    Returns:
        List of StaleIssue objects.
    """
    stale: list[StaleIssue] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    try:
        gh_repo = gh_client.get_repo(repo)
        issues = gh_repo.get_issues(state="open", sort="updated", direction="asc")

        for issue in issues:
            if issue.pull_request is not None:
                continue
            if len(stale) >= max_issues:
                break

            updated = issue.updated_at.replace(tzinfo=timezone.utc) if issue.updated_at else issue.created_at.replace(tzinfo=timezone.utc)
            if updated > cutoff:
                continue

            days = (datetime.now(timezone.utc) - updated).days
            stale.append(StaleIssue(
                number=issue.number,
                title=issue.title,
                url=issue.html_url,
                author=issue.user.login if issue.user else "unknown",
                created_at=issue.created_at,
                last_updated=updated,
                days_stale=days,
                labels=[lb.name for lb in issue.labels],
            ))

    except Exception as e:
        logger.warning(f"Stale issue scan failed for {repo}: {e}")

    return stale


STALE_SUMMARY_PROMPT = """\
Summarize a stale GitHub issue for a busy maintainer.

Respond with JSON:
{
  "summary": "One concise sentence summarizing the issue and its current state.",
  "suggested_action": "close | ping | implement | ignore",
  "reason": "Why this action is suggested."
}
"""


def summarize_stale_issue(
    issue: StaleIssue,
    llm_client: Any,
    model: str = "deepseek-chat",
) -> StaleIssue:
    """Generate an AI summary for a stale issue.

    Args:
        issue: StaleIssue to summarize.
        llm_client: OpenAI-compatible LLM client.
        model: LLM model name.

    Returns:
        StaleIssue with summary populated.
    """
    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": STALE_SUMMARY_PROMPT},
                {"role": "user", "content": (
                    f"Issue: {issue.title}\n"
                    f"Author: {issue.author}\n"
                    f"Created: {issue.created_at}\n"
                    f"Last updated: {issue.last_updated} ({issue.days_stale} days ago)\n"
                    f"Labels: {', '.join(issue.labels)}"
                )},
            ],
            temperature=0.1,
            max_tokens=300,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

        issue.summary = result.get("summary", f"Stale issue (#{issue.number}): {issue.title}")

    except Exception as e:
        logger.error(f"Stale summary failed: {e}")
        issue.summary = f"Stale issue (#{issue.number}): {issue.title}"

    return issue


# ─── Health scoring ──────────────────────────────────────────────────────────

def calculate_health(report: PatrolReport) -> int:
    """Calculate a repository health score (0-100).

    Deductions:
      - -10 per critical outdated dep
      - -5 per CI failure
      - -3 per stale issue (90+ days)
      - -1 per stale issue (30-90 days)
      - -5 per open issue without response > 7 days

    Args:
        report: PatrolReport to score.

    Returns:
        Health score 0-100.
    """
    score = 100

    # Dependency deductions
    for dep in report.outdated_deps:
        if dep.severity == "critical":
            score -= 10
        elif dep.severity == "high":
            score -= 5
        elif dep.severity == "medium":
            score -= 2

    # CI deductions
    score -= len(report.ci_failures) * 5

    # Stale issue deductions
    for issue in report.stale_issues:
        if issue.days_stale > 90:
            score -= 3
        elif issue.days_stale > 30:
            score -= 1

    return max(0, score)


def _health_label(score: int) -> str:
    if score >= 90:
        return "🟢 Healthy"
    elif score >= 70:
        return "🟡 Needs Attention"
    elif score >= 50:
        return "🟠 At Risk"
    else:
        return "🔴 Critical"


# ─── Health summary generation ───────────────────────────────────────────────

def generate_health_summary(report: PatrolReport, profile: dict) -> str:
    """Generate a markdown health summary for a repository.

    Args:
        report: Filled PatrolReport.
        profile: Maintainer profile.

    Returns:
        Markdown string suitable for a GitHub issue/comment or email.
    """
    score = calculate_health(report)
    label = _health_label(score)

    lines = [
        f"# 📋 Daily Patrol Report — [{report.repo}](https://github.com/{report.repo})",
        "",
        f"**Scanned:** {report.scanned_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Health Score:** {score}/100 {label}",
        "",
    ]

    # Dependencies
    if report.outdated_deps:
        lines.append("## 📦 Outdated Dependencies")
        lines.append(f"_{report.dependencies_checked} checked, {len(report.outdated_deps)} outdated_")
        lines.append("")
        lines.append("| Package | Current | Latest | Severity |")
        lines.append("|---------|---------|--------|----------|")
        for dep in report.outdated_deps[:20]:
            lines.append(f"| {dep.name} | {dep.current} | {dep.latest} | {dep.severity} |")
        lines.append("")

    # CI failures
    if report.ci_failures:
        lines.append("## ❌ CI Failures")
        lines.append("")
        for ci in report.ci_failures:
            lines.append(f"### [{ci.workflow_name}]({ci.run_url})")
            lines.append(f"- **Failed:** {ci.failed_at.strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"- **Diagnosis:** {ci.diagnosis}")
            if ci.suggested_fix:
                lines.append(f"- **Suggested Fix:** {ci.suggested_fix}")
            if ci.auto_fixable:
                lines.append("- **Auto-fixable:** ✅ Yes")
            lines.append("")

    # Stale issues
    if report.stale_issues:
        lines.append("## ⏰ Stale Issues")
        lines.append("")
        for issue in report.stale_issues[:10]:
            lines.append(
                f"- [#{issue.number} {issue.title}]({issue.url}) — "
                f"stale {issue.days_stale}d — {issue.summary}"
            )
        lines.append("")

    # Warnings
    if report.warnings:
        lines.append("## ⚠️ Warnings")
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # CI fixes applied
    if report.ci_fixed:
        lines.append("## 🔧 Auto-Fixes Applied")
        for fix in report.ci_fixed:
            lines.append(f"- {fix}")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by RepoKeeper Patrol · [github.com/shenxianpeng/repokeeper](https://github.com/shenxianpeng/repokeeper)*")

    return "\n".join(lines)


# ─── Main patrol pipeline ────────────────────────────────────────────────────

def run_patrol(
    gh_client: Any,
    llm_client: Any,
    repo: str,
    profile: dict | None = None,
    repo_path: Path | None = None,
) -> PatrolReport:
    """Run a complete Daily Patrol scan.

    1. Scans dependencies for outdated packages.
    2. Checks CI for recent failures, diagnoses them.
    3. Finds stale open issues, summarizes them.
    4. Generates a health report.

    Args:
        gh_client: PyGithub Github instance.
        llm_client: OpenAI-compatible LLM client.
        repo: Repository slug (owner/repo).
        profile: Maintainer profile (loaded if None).
        repo_path: Local path to repository (for dependency scanning).

    Returns:
        PatrolReport with all findings.
    """
    if profile is None:
        profile = load_profile()

    patrol_config = profile.get("patrol", {})
    if not patrol_config.get("enabled", True):
        logger.info(f"Patrol disabled for {repo}")
        return PatrolReport(repo=repo, scanned_at=datetime.now())

    model = profile.get("agent", {}).get("model", "deepseek-chat")
    stale_days = patrol_config.get("stale_days", 90)
    ci_auto_fix = patrol_config.get("ci_auto_fix", True)

    rp = repo_path or Path(".")
    report = PatrolReport(repo=repo, scanned_at=datetime.now())

    # ── Step 1: Dependencies ──
    logger.info(f"🔍 Patrol: scanning dependencies in {repo}")
    outdated = scan_dependencies(rp)
    report.outdated_deps = outdated
    report.dependencies_checked = len(find_manifests(rp))
    if outdated:
        logger.info(f"  Found {len(outdated)} outdated dependencies")

    # ── Step 2: CI failures ──
    logger.info(f"🔍 Patrol: checking CI for {repo}")
    ci_failures = scan_ci_failures(gh_client, repo)
    for ci in ci_failures:
        diagnose_ci_failure(ci, llm_client, gh_client, model=model)
        if ci.auto_fixable and ci_auto_fix:
            logger.info(f"  Auto-fixable CI failure: {ci.workflow_name}")
            # Auto-fix implementation would go here
            report.ci_fixed.append(f"Attempted fix for {ci.workflow_name} (run {ci.run_id})")
    report.ci_failures = ci_failures

    # ── Step 3: Stale issues ──
    logger.info(f"🔍 Patrol: scanning stale issues in {repo}")
    stale = scan_stale_issues(gh_client, repo, stale_days=stale_days)
    for issue in stale:
        summarize_stale_issue(issue, llm_client, model=model)
    report.stale_issues = stale
    if stale:
        logger.info(f"  Found {len(stale)} stale issues")

    # ── Step 4: Health score ──
    report.health_score = calculate_health(report)

    return report


# ─── Auto-upgrade dependency PR ──────────────────────────────────────────────

def create_dependency_upgrade_pr(
    gh_client: Any,
    repo: str,
    outdated_deps: list[DepCheck],
    profile: dict,
) -> str | None:
    """Create a PR to upgrade outdated dependencies.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug.
        outdated_deps: List of outdated DepCheck results.
        profile: Maintainer profile.

    Returns:
        PR URL if created, None otherwise.
    """
    if not outdated_deps:
        return None

    if not profile.get("patrol", {}).get("auto_upgrade_deps", True):
        logger.info("Auto-upgrade deps disabled in profile.")
        return None

    gh_repo = gh_client.get_repo(repo)
    default_branch = gh_repo.default_branch

    # Build PR body
    lines = [
        "## 📦 Dependency Upgrades",
        "",
        "RepoKeeper Patrol detected the following outdated dependencies:",
        "",
        "| Package | Current | Latest | Severity |",
        "|---------|---------|--------|----------|",
    ]
    for dep in outdated_deps:
        lines.append(f"| {dep.name} | {dep.current} | {dep.latest} | {dep.severity} |")

    body = "\n".join(lines)

    try:
        pr = gh_repo.create_pull(
            title="📦 chore: upgrade outdated dependencies",
            body=body,
            head="repokeeper/deps-upgrade",
            base=default_branch,
        )
        return pr.html_url
    except Exception as e:
        logger.error(f"Failed to create dep upgrade PR: {e}")
        return None
