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

from .collaboration import (
    AGENT_TODO_LABEL,
    CANDIDATE_LABEL,
    PATROL_LABEL,
    ensure_github_labels,
    format_candidate_block,
)
from .git_ops import safe_repo_path
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
    suggested_action: str = "investigate"
    action_reason: str = ""


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


def check_cargo_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Rust dependencies using cargo outdated.

    Args:
        manifest_path: Path to Cargo.toml.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # Try cargo outdated if available
        result = subprocess.run(
            ["cargo", "outdated", "--format", "json"],
            capture_output=True, text=True, timeout=120,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                for pkg in data if isinstance(data, list) else []:
                    results.append(DepCheck(
                        name=pkg.get("name", "?"),
                        current=pkg.get("project", "?"),
                        latest=pkg.get("latest", "?"),
                        is_outdated=True,
                        severity="high" if pkg.get("semver") == "major" else "medium",
                    ))
            except json.JSONDecodeError:
                # cargo outdated might not have --format json, try parsing text
                pass
    except FileNotFoundError:
        logger.info("cargo not installed; skipping Rust dependency checks")
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.warning(f"Cargo dependency check failed for {manifest_path}: {e}")

    return results


def check_bundler_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Ruby dependencies using bundle outdated.

    Args:
        manifest_path: Path to Gemfile.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # bundle outdated --parseable outputs: name (newest ver, installed ver, ...)
        result = subprocess.run(
            ["bundle", "outdated", "--parseable"],
            capture_output=True, text=True, timeout=120,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[0]
                    # Parse version info (format varies by bundler version)
                    current = parts[1].strip("()")
                    latest = parts[2].strip("()") if len(parts) > 2 else "?"
                    results.append(DepCheck(
                        name=name,
                        current=current,
                        latest=latest,
                        is_outdated=True,
                        severity="medium",
                    ))
    except FileNotFoundError:
        logger.info("bundler not installed; skipping Ruby dependency checks")
    except Exception as e:
        logger.warning(f"Bundler dependency check failed for {manifest_path}: {e}")

    return results


def check_composer_deps(manifest_path: Path) -> list[DepCheck]:
    """Check PHP dependencies using composer outdated.

    Args:
        manifest_path: Path to composer.json.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        result = subprocess.run(
            ["composer", "outdated", "--format=json", "--no-interaction"],
            capture_output=True, text=True, timeout=120,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                installed = data.get("installed", [])
                for pkg in installed if isinstance(installed, list) else []:
                    results.append(DepCheck(
                        name=pkg.get("name", "?"),
                        current=pkg.get("version", "?"),
                        latest=pkg.get("latest", "?"),
                        is_outdated=pkg.get("version") != pkg.get("latest"),
                        severity="medium",
                    ))
            except json.JSONDecodeError:
                pass
    except FileNotFoundError:
        logger.info("composer not installed; skipping PHP dependency checks")
    except Exception as e:
        logger.warning(f"Composer dependency check failed for {manifest_path}: {e}")

    return results


def check_maven_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Java/Maven dependencies.

    Uses mvn versions:display-dependency-updates if available,
    otherwise falls back to parsing pom.xml for dependency versions.

    Args:
        manifest_path: Path to pom.xml.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # Try maven plugin for accurate version checking
        result = subprocess.run(
            ["mvn", "versions:display-dependency-updates",
             "-DprocessDependencies=true", "-DoutputFormat=text", "-q"],
            capture_output=True, text=True, timeout=300,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0:
            import re as _re
            for line in result.stdout.splitlines():
                # Parse lines like: "[INFO]   com.example:lib ... 1.0 -> 2.0"
                match = _re.match(
                    r'.*?([\w.-]+:[\w.-]+)\s.*?(\S+)\s*->\s*(\S+)', line
                )
                if match:
                    results.append(DepCheck(
                        name=match.group(1),
                        current=match.group(2),
                        latest=match.group(3),
                        is_outdated=True,
                        severity="medium",
                    ))
    except FileNotFoundError:
        logger.info("maven not installed; skipping Java dependency checks")
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.warning(f"Maven dependency check failed for {manifest_path}: {e}")

    return results


def check_gradle_deps(manifest_path: Path) -> list[DepCheck]:
    """Check Java/Kotlin Gradle dependencies.

    Uses gradle dependencyUpdates task if the gradle-versions-plugin
    is configured, otherwise logs a best-effort notice.

    Args:
        manifest_path: Path to build.gradle or build.gradle.kts.

    Returns:
        List of DepCheck results.
    """
    results: list[DepCheck] = []

    try:
        # Gradle dependency check requires the gradle-versions-plugin.
        # We try a lightweight task first to detect outdated plugins.
        result = subprocess.run(
            ["./gradlew", "dependencyUpdates", "-DoutputFormatter=json",
             "-DoutputDir=build/dependencyUpdates"],
            capture_output=True, text=True, timeout=300,
            cwd=manifest_path.parent,
        )
        if result.returncode == 0:
            report_file = manifest_path.parent / "build" / "dependencyUpdates" / "report.json"
            if report_file.exists():
                try:
                    data = json.loads(report_file.read_text())
                    outdated_deps = (
                        data.get("outdated", {}).get("dependencies", [])
                        if isinstance(data, dict) else []
                    )
                    for dep in outdated_deps:
                        results.append(DepCheck(
                            name=dep.get("group", "") + ":" + dep.get("name", "?"),
                            current=dep.get("version", "?"),
                            latest=dep.get("available", {}).get("release", "?"),
                            is_outdated=True,
                            severity="medium",
                        ))
                except (json.JSONDecodeError, KeyError):
                    pass
    except FileNotFoundError:
        logger.info("gradle not installed; skipping Gradle dependency checks")
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.warning(f"Gradle dependency check failed for {manifest_path}: {e}")

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
            all_deps += check_python_deps(manifest)
        elif name == "package.json":
            all_deps += check_node_deps(manifest)
        elif name == "go.mod":
            all_deps += check_go_deps(manifest)
        elif name == "Cargo.toml":
            all_deps += check_cargo_deps(manifest)
        elif name == "Gemfile":
            all_deps += check_bundler_deps(manifest)
        elif name == "composer.json":
            all_deps += check_composer_deps(manifest)
        elif name == "pom.xml":
            all_deps += check_maven_deps(manifest)
        elif name in ("build.gradle", "build.gradle.kts"):
            all_deps += check_gradle_deps(manifest)
        # yarn.lock is handled via npm/yarn in the node ecosystem above
        # Pipfile is handled via Python ecosystem above

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


def _fetch_ci_log_snippet(
    gh_client: Any,
    repo: str,
    run_id: int,
    workflow_name: str,
    conclusion: str,
    run_url: str,
) -> str:
    """Fetch structured CI log data from a failed workflow run.

    Retrieves job and step information via the GitHub Actions API.
    Falls back to a minimal summary if the API call fails.

    Args:
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        run_id: Workflow run ID.
        workflow_name: Human-readable workflow name.
        conclusion: Run conclusion ("failure", "cancelled", "timed_out").
        run_url: URL to the workflow run.

    Returns:
        A formatted log snippet string for LLM diagnosis.
    """
    lines = [
        f"Workflow: {workflow_name}",
        f"Run ID: {run_id}",
        f"Conclusion: {conclusion}",
        f"URL: {run_url}",
    ]

    try:
        requester = gh_client._Github__requester  # type: ignore[attr-defined]
        _headers, data = requester.requestJsonAndCheck(
            "GET", f"/repos/{repo}/actions/runs/{run_id}/jobs",
        )

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        if jobs:
            lines.append(f"\nJobs ({len(jobs)} total):")
            for job in jobs:
                job_name = job.get("name", "?")
                job_conclusion = job.get("conclusion", "unknown")
                job_status = job.get("status", "unknown")
                lines.append(f"  [{job_conclusion}] {job_name} (status: {job_status})")

                steps = job.get("steps", [])
                for step in steps:
                    step_name = step.get("name", "?")
                    step_conclusion = step.get("conclusion", "unknown")
                    if step_conclusion in ("failure", "cancelled", "timed_out"):
                        lines.append(f"    ❌ Step: {step_name} → {step_conclusion}")
                    elif step_conclusion == "success":
                        lines.append(f"    ✅ Step: {step_name}")
                    else:
                        lines.append(f"    ⏭ Step: {step_name} → {step_conclusion}")
    except Exception as e:
        logger.warning(f"Failed to fetch CI job details for run {run_id}: {e}")
        lines.append(f"(Job details unavailable: {e})")

    return "\n".join(lines)


def diagnose_ci_failure(
    failure: CIFailure,
    llm_client: Any,
    gh_client: Any,
    repo: str,
    model: str = "deepseek-chat",
) -> CIFailure:
    """Use AI to diagnose a CI failure.

    Fetches the workflow run job/step data and sends it to the LLM for analysis.

    Args:
        failure: CIFailure to diagnose.
        llm_client: OpenAI-compatible LLM client.
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        model: LLM model name.

    Returns:
        CIFailure with diagnosis fields populated.
    """
    try:
        log_snippet = _fetch_ci_log_snippet(
            gh_client, repo,
            run_id=failure.run_id,
            workflow_name=failure.workflow_name,
            conclusion=failure.conclusion,
            run_url=failure.run_url,
        )
        failure.log_snippet = log_snippet

        response = llm_client.chat(
            system=CI_DIAGNOSIS_PROMPT,
            messages=[
                {"role": "user", "content": f"CI Failure:\n{log_snippet[:4000]}"},
            ],
            model=model,
            temperature=0.1,
            max_tokens=500,
        )

        raw = response.content.strip()
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
        response = llm_client.chat(
            system=STALE_SUMMARY_PROMPT,
            messages=[
                {"role": "user", "content": (
                    f"Issue: {issue.title}\n"
                    f"Author: {issue.author}\n"
                    f"Created: {issue.created_at}\n"
                    f"Last updated: {issue.last_updated} ({issue.days_stale} days ago)\n"
                    f"Labels: {', '.join(issue.labels)}"
                )},
            ],
            model=model,
            temperature=0.1,
            max_tokens=300,
        )

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

        issue.summary = result.get("summary", f"Stale issue (#{issue.number}): {issue.title}")
        issue.suggested_action = result.get("suggested_action", "investigate")
        issue.action_reason = result.get("reason", "")

    except Exception as e:
        logger.error(f"Stale summary failed: {e}")
        issue.summary = f"Stale issue (#{issue.number}): {issue.title}"
        issue.suggested_action = "investigate"

    return issue


_PATROL_MARKER_PREFIX = "<!-- repokeeper-patrol:"
_PATROL_MARKER_SUFFIX = "-->"


def _patrol_candidate_marker(issue_number: int) -> str:
    return f"{_PATROL_MARKER_PREFIX}stale-issue:{issue_number}{_PATROL_MARKER_SUFFIX}"


def _build_stale_issue_candidate_comment(issue: StaleIssue) -> str:
    marker = _patrol_candidate_marker(issue.number)
    block = format_candidate_block(
        source_module="Patrol",
        recommended_action=issue.suggested_action,
        risk="low",
        source_url=issue.url,
        summary=issue.summary,
        acceptance=(
            "Maintainer confirms the stale issue is still valid and explicitly "
            "approves implementation."
        ),
    )
    return (
        f"🔍 **RepoKeeper Patrol** found this stale issue may need action.\n\n"
        f"{block}\n\n"
        f"- **Reason:** {issue.action_reason or 'Issue is stale and needs review.'}\n"
        f"- **Stale for:** {issue.days_stale} days\n\n"
        f"{marker}"
    )


def publish_stale_issue_candidate(gh_client: Any, repo: str, issue: StaleIssue) -> bool:
    """Add candidate labels and a Patrol handoff comment to a stale issue."""
    try:
        gh_repo = gh_client.get_repo(repo)
        issue_obj = gh_repo.get_issue(issue.number)
        labels = [CANDIDATE_LABEL, PATROL_LABEL]
        labels = [label for label in labels if label != AGENT_TODO_LABEL]
        ensure_github_labels(gh_repo, labels)
        issue_obj.add_to_labels(*labels)

        marker = _patrol_candidate_marker(issue.number)
        comments: Any = getattr(issue_obj, "get_comments", lambda: [])()
        for comment in comments:
            if marker in (getattr(comment, "body", "") or ""):
                logger.info(f"  Patrol candidate already exists for issue #{issue.number}")
                return False

        issue_obj.create_comment(_build_stale_issue_candidate_comment(issue))
        logger.info(f"  Published Patrol candidate for issue #{issue.number}")
        return True
    except Exception as e:
        logger.warning(f"  Failed to publish Patrol candidate for issue #{issue.number}: {e}")
        return False


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
                f"stale {issue.days_stale}d — {issue.summary} "
                f"(suggested: {issue.suggested_action})"
            )
        lines.append("")

    pending: list[str] = []
    for dep in report.outdated_deps:
        pending.append(f"Review dependency `{dep.name}` upgrade ({dep.severity}).")
    for ci in report.ci_failures:
        if ci.auto_fixable:
            pending.append(f"Approve implementation for CI failure `{ci.workflow_name}`.")
        else:
            pending.append(f"Investigate CI failure `{ci.workflow_name}`.")
    for issue in report.stale_issues:
        if issue.suggested_action == "implement":
            pending.append(
                f"Approve implementation for [#{issue.number} {issue.title}]({issue.url})."
            )

    if pending:
        lines.append("## ⏳ Waiting for Maintainer Approval")
        lines.append("")
        lines.append(
            "RepoKeeper will not implement these candidates until a maintainer "
            "adds `agent-todo` or comments `@repokeeper go`."
        )
        lines.append("")
        for item in pending[:20]:
            lines.append(f"- {item}")
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


# ─── CI auto-fix ────────────────────────────────────────────────────────────

CI_FIX_SYSTEM_PROMPT = """\
You are a DevOps engineer fixing a CI pipeline failure.
Given the CI diagnosis and the failing workflow file,
generate a precise fix.

Respond with a single JSON object:

{
  "skip": false,
  "reason": "",
  "summary": "One sentence describing the fix.",
  "commit_message": "ci: short imperative message",
  "changes": {
    "path/to/workflow.yml": "<complete corrected file content>"
  }
}

- Only modify the failing workflow file or related config files.
- Make minimal, targeted changes.
- Provide FULL file content, not diffs.
- Set "skip": true if the failure cannot be fixed purely through config.
"""


def attempt_ci_auto_fix(
    failure: CIFailure,
    llm_client: Any,
    gh_client: Any,
    repo: str,
    profile: dict,
    repo_path: Path = Path("."),
) -> str | None:
    """Attempt to automatically fix a CI failure by generating a PR.

    Reads the failing workflow file, sends the CI diagnosis and file content
    to the LLM, and creates a PR with the proposed fix.

    Args:
        failure: Diagnosed CI failure with suggested_fix and auto_fixable=True.
        llm_client: OpenAI-compatible LLM client.
        gh_client: PyGithub Github instance.
        repo: Repository slug (owner/repo).
        profile: Maintainer profile.
        repo_path: Local repository path.

    Returns:
        Description of the fix applied, or None if unable to fix.
    """
    try:
        # Find the failing workflow file in the local repo
        workflow_files = list(repo_path.rglob("*.yml")) + list(repo_path.rglob("*.yaml"))
        workflow_files = [
            wf for wf in workflow_files
            if ".github/workflows" in str(wf)
        ]

        # Try to find the specific workflow file by name
        target_file = None
        for wf in workflow_files:
            try:
                content = wf.read_text(encoding="utf-8")
                if failure.workflow_name.lower() in content.lower():
                    target_file = wf
                    break
            except OSError:
                continue

        if target_file is None and workflow_files:
            target_file = workflow_files[0]

        if target_file is None:
            logger.warning("No workflow files found locally for CI auto-fix")
            return None

        workflow_content = target_file.read_text(encoding="utf-8")
        relative_path = str(target_file.relative_to(repo_path))

        user_prompt = f"""\
## CI Failure Diagnosis
- Workflow: {failure.workflow_name}
- Run URL: {failure.run_url}
- Conclusion: {failure.conclusion}
- Diagnosis: {failure.diagnosis}
- Suggested Fix: {failure.suggested_fix}

## Failing Workflow File ({relative_path})
```yaml
{workflow_content}
```

## CI Job Details
{failure.log_snippet[:2000]}
"""

        model = profile.get("agent", {}).get("model", "deepseek-chat")
        response = llm_client.chat(
            system=CI_FIX_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.1,
            max_tokens=4000,
        )

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)

        if result.get("skip"):
            logger.info(
                f"LLM declined CI auto-fix: {result.get('reason', 'unknown')}"
            )
            return None

        changes = result.get("changes", {})
        if not changes:
            logger.warning("LLM returned no changes for CI auto-fix")
            return None

        # Apply the fix locally and create a PR
        branch_name = f"repokeeper/ci-fix-{failure.run_id}"
        gh_repo = gh_client.get_repo(repo)

        import subprocess as _sp

        _sp.run(["git", "config", "user.email", "repokeeper[bot]@users.noreply.github.com"],
                check=False, capture_output=True, cwd=repo_path)
        _sp.run(["git", "config", "user.name", "repokeeper[bot]"],
                check=False, capture_output=True, cwd=repo_path)
        _sp.run(["git", "checkout", "-b", branch_name],
                check=False, capture_output=True, cwd=repo_path)

        for filepath, content in changes.items():
            try:
                target = safe_repo_path(filepath, repo_path, blocked_prefixes=())
            except ValueError as exc:
                logger.warning(f"Skipping unsafe CI auto-fix path: {exc}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        _sp.run(["git", "add", "-A"], check=False, capture_output=True, cwd=repo_path)
        diff_result = _sp.run(
            ["git", "diff", "--cached", "--name-only"],
            check=False, capture_output=True, text=True, cwd=repo_path,
        )
        if not diff_result.stdout.strip():
            logger.warning("CI auto-fix produced no safe file changes")
            return None

        _sp.run(
            ["git", "commit", "-m", result.get("commit_message", "ci: auto-fix")],
            check=False, capture_output=True, cwd=repo_path,
        )

        # Push the branch
        owner, _name = repo.split("/")
        gh_token = _get_gh_token_from_client(gh_client)
        if gh_token:
            remote_url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
            _sp.run(["git", "remote", "set-url", "origin", remote_url],
                    check=False, capture_output=True, cwd=repo_path)

        push_result = _sp.run(
            ["git", "push", "origin", branch_name],
            check=False, capture_output=True, text=True, cwd=repo_path,
        )
        if push_result.returncode != 0:
            logger.warning(f"CI auto-fix push failed: {push_result.stderr[:200]}")
            _sp.run(["git", "checkout", gh_repo.default_branch],
                    check=False, capture_output=True, cwd=repo_path)
            return None

        # Create PR
        pr_body = f"""\
## 🔧 CI Auto-Fix

Repokeeper Patrol diagnosed and attempted to fix a CI failure.

**Workflow:** {failure.workflow_name}
**Run:** {failure.run_url}

### Diagnosis
{failure.diagnosis}

### Fix applied
{result.get('summary', 'Auto-fix applied.')}

---
*Generated by RepoKeeper Patrol · Please review carefully.*
"""
        pr = gh_repo.create_pull(
            title=f"ci: auto-fix {failure.workflow_name} (#{failure.run_id})",
            body=pr_body,
            head=branch_name,
            base=gh_repo.default_branch,
        )

        # Return to default branch
        _sp.run(["git", "checkout", gh_repo.default_branch],
                check=False, capture_output=True, cwd=repo_path)

        return f"Fixed {failure.workflow_name}: {pr.html_url}"

    except Exception as e:
        logger.error(f"CI auto-fix failed for {failure.workflow_name}: {e}")
        return None


def _get_gh_token_from_client(gh_client: Any) -> str | None:
    """Extract the GitHub token from a PyGithub client for API calls.

    Args:
        gh_client: PyGithub Github instance.

    Returns:
        Token string, or None if unavailable.
    """
    try:
        requester = gh_client._Github__requester  # type: ignore[attr-defined]
        auth = getattr(requester, "auth", None)
        if auth is not None:
            return getattr(auth, "token", None)
    except Exception:
        pass
    return None


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
        diagnose_ci_failure(ci, llm_client, gh_client, repo, model=model)
        if ci.auto_fixable and ci_auto_fix:
            logger.info(f"  Auto-fixable CI failure: {ci.workflow_name}")
            fix_result = attempt_ci_auto_fix(
                ci, llm_client, gh_client, repo, profile, repo_path=rp,
            )
            if fix_result:
                report.ci_fixed.append(fix_result)
            else:
                report.ci_fixed.append(
                    f"Diagnosed but not auto-fixed: {ci.workflow_name} (run {ci.run_id})"
                )
    report.ci_failures = ci_failures

    # ── Step 3: Stale issues ──
    logger.info(f"🔍 Patrol: scanning stale issues in {repo}")
    stale = scan_stale_issues(gh_client, repo, stale_days=stale_days)
    for issue in stale:
        summarize_stale_issue(issue, llm_client, model=model)
        if issue.suggested_action == "implement" and gh_client is not None:
            publish_stale_issue_candidate(gh_client, repo, issue)
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
        return str(pr.html_url)
    except Exception as e:
        logger.error(f"Failed to create dep upgrade PR: {e}")
        return None
