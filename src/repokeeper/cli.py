"""Command-line interface for RepoKeeper."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from github import Github

from repokeeper.exceptions import AuthError, ConfigError
from repokeeper.llm_client import LLMClient

from . import __version__
from .agent import run_agent
from .labeler import generate_labeler_summary, run_labeler
from .patrol import generate_health_summary, run_patrol
from .profile import generate_profile_template, load_profile, validate_profile
from .radar import generate_radar_summary, run_radar
from .review import run_review

AGENT_WORKFLOW = "repokeeper.yml"
OPTIONAL_WORKFLOWS = ("radar.yml", "patrol.yml", "review.yml", "labeler.yml")
ALL_WORKFLOWS = (AGENT_WORKFLOW, *OPTIONAL_WORKFLOWS)


def _make_github_client(token: str | None) -> Github:
    token = token or os.environ.get("REPOKEEPER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AuthError("Missing GitHub token. Set REPOKEEPER_GITHUB_TOKEN or GITHUB_TOKEN.")
    return Github(token)


def _make_llm_client(api_key: str | None, base_url: str | None) -> LLMClient:
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError("Missing LLM API key. Set DEEPSEEK_API_KEY or OPENAI_API_KEY.")
    return LLMClient(
        api_key=api_key,
        base_url=base_url or os.environ.get("LLM_BASE_URL"),
    )


def _copy_workflows(target: Path, workflows: tuple[str, ...] = ALL_WORKFLOWS) -> None:
    source = Path(__file__).resolve().parent / "templates" / "workflows"
    workflows_dir = target / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    if source.exists():
        for workflow in workflows:
            src = source / workflow
            if src.exists():
                shutil.copyfile(src, workflows_dir / workflow)


def _workflow_names(args: argparse.Namespace) -> tuple[str, ...]:
    if getattr(args, "all_workflows", False):
        return ALL_WORKFLOWS
    if getattr(args, "workflows", False):
        return ALL_WORKFLOWS
    if getattr(args, "minimal", False):
        return (AGENT_WORKFLOW,)
    return ()


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    profile_path = target / "repokeeper.yml"
    if profile_path.exists() and not args.force:
        print(f"{profile_path} already exists; use --force to overwrite.", file=sys.stderr)
        return 1
    generate_profile_template(profile_path)
    print(f"Wrote {profile_path}")
    workflows = _workflow_names(args)
    if workflows:
        _copy_workflows(target, workflows)
        print(f"Wrote workflows under {target / '.github' / 'workflows'}")
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    import yaml

    print(yaml.safe_dump(profile, sort_keys=False))
    return 0


def cmd_profile_validate(args: argparse.Namespace) -> int:
    issues = validate_profile(load_profile(args.profile))
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Profile is valid.")
    return 0


def cmd_radar(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    gh = _make_github_client(args.github_token)
    llm = _make_llm_client(args.llm_api_key, args.llm_base_url)
    report = run_radar(gh, llm, args.repo, profile)
    print(f"Radar: {len(report.hits)} actionable hits found")
    if args.summary:
        print(generate_radar_summary(report))
    return 0


def cmd_labeler(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    gh = _make_github_client(args.github_token)
    llm = _make_llm_client(args.llm_api_key, args.llm_base_url)
    report = run_labeler(gh, llm, args.repo, profile,
                         issue_number=args.issue, pr_number=args.pr)
    print(
        f"Labeler: {len(report.labeled)} labeled, "
        f"{len(report.commented)} suggested, "
        f"{len(report.skipped)} skipped"
    )
    if args.summary:
        print()
        print(generate_labeler_summary(report))
    return 0


def _run_patrol_report(args: argparse.Namespace) -> Any:
    profile = load_profile(args.profile)
    gh = _make_github_client(args.github_token)
    llm = _make_llm_client(args.llm_api_key, args.llm_base_url)
    return run_patrol(gh, llm, args.repo, profile, repo_path=Path(args.path))


def cmd_patrol(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    report = _run_patrol_report(args)
    print(
        f"Patrol: health={report.health_score}/100, "
        f"{len(report.outdated_deps)} outdated deps, "
        f"{len(report.ci_failures)} CI failures, "
        f"{len(report.stale_issues)} stale issues"
    )
    if args.summary:
        print()
        print(generate_health_summary(report, profile))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    report = _run_patrol_report(args)
    print(report.health_score)
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    result = run_agent(
        gh_token=args.github_token,
        repository=args.repo,
        issue_number=args.issue,
        llm_api_key=args.llm_api_key,
        llm_base_url=args.llm_base_url,
        profile_path=args.profile,
        dry_run=args.dry_run,
    )
    if result.get("skip"):
        reason = result.get("reason", "")
        if result.get("plan"):
            import json as _json

            print(_json.dumps(result["plan"], indent=2))
        else:
            print(f"Skipped: {reason}")
        return 0
    print(f"PR created: {result.get('pr_url')}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    result = run_review(
        gh_token=args.github_token,
        repository=args.repo,
        pr_number=args.pr,
        llm_api_key=args.llm_api_key,
        llm_base_url=args.llm_base_url,
        profile_path=args.profile,
    )
    if result.get("review_posted"):
        print(
            f"Review posted: {result.get('approval_recommendation', '?')}, "
            f"{result.get('issues_count', 0)} issue(s) found"
        )
    else:
        print(f"Review not posted: {result.get('reason', 'unknown')}")
    return 0


def _has_env(name: str) -> bool:
    return bool(os.environ.get(name))


def _run_remote_checks(token: str, repo_slug: str, failed: int, warnings: int) -> tuple[int, int]:
    """Verify the GitHub token can access the repository and inspect features.

    Args:
        token: GitHub personal access token.
        repo_slug: Repository as ``owner/name``.
        failed: Current failure counter (mutated via return).
        warnings: Current warning counter (mutated via return).

    Returns:
        Updated ``(failed, warnings)`` tuple.
    """
    import requests as _requests

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    # 1. Verify token can access the repository
    try:
        resp = _requests.get(
            f"https://api.github.com/repos/{repo_slug}",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            repo_data = resp.json()
            _print_check(True, f"Token can access {repo_slug}")

            # 2. Check if Discussions are enabled
            has_discussions = repo_data.get("has_discussions", False)
            if has_discussions:
                _print_check(True, "Discussions enabled")
            else:
                _print_check(
                    False,
                    "Discussions enabled",
                    "Radar discussion scanning will be unavailable",
                    status_if_false="warn",
                )
                warnings += 1

        elif resp.status_code == 404:
            _print_check(False, f"Token can access {repo_slug}", "404 — repo not found or token lacks access")
            failed += 1
            _print_fix("verify the repository slug is correct and the token has repo scope")
        elif resp.status_code == 401:
            _print_check(
                False,
                f"Token can access {repo_slug}",
                "401 — token is invalid or expired",
                status_if_false="warn",
            )
            warnings += 1
            _print_fix("regenerate the token in GitHub Settings → Developer settings → Personal access tokens")
        else:
            _print_check(
                False,
                f"Token can access {repo_slug}",
                f"HTTP {resp.status_code}",
                status_if_false="warn",
            )
            warnings += 1
    except Exception as exc:
        _print_check(
            False,
            f"Token can access {repo_slug}",
            f"request failed: {exc}",
            status_if_false="warn",
        )
        warnings += 1

    return failed, warnings


def _print_check(ok: bool, label: str, detail: str = "", status_if_false: str = "missing") -> None:
    status = "ok" if ok else status_if_false
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def _print_fix(command: str) -> None:
    print(f"  Fix: {command}")


def _workflow_has_required_settings(workflow_path: Path) -> list[str]:
    """Return setup issues found in the Implementation Agent workflow."""
    if not workflow_path.exists():
        return ["workflow file is missing"]

    text = workflow_path.read_text(encoding="utf-8")
    required_snippets = {
        "issue_comment trigger": "issue_comment:",
        "issues trigger": "issues:",
        "contents write permission": "contents: write",
        "issues write permission": "issues: write",
        "pull request write permission": "pull-requests: write",
        "agent command": "repokeeper agent",
    }
    return [label for label, snippet in required_snippets.items() if snippet not in text]


def cmd_doctor(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    profile_path = Path(args.profile).resolve() if args.profile else target / "repokeeper.yml"
    failed = 0
    warnings = 0

    print(f"RepoKeeper doctor for {target}")

    in_git_repo = (target / ".git").exists()
    _print_check(in_git_repo, "Git repository", "expected .git in the target path")
    if not in_git_repo:
        failed += 1
        _print_fix("run doctor from a repository root, or pass the repository path")

    profile_exists = profile_path.exists()
    _print_check(profile_exists, "Profile", str(profile_path))
    if profile_exists:
        issues = validate_profile(load_profile(profile_path))
        _print_check(not issues, "Profile validation")
        if issues:
            failed += 1
            for issue in issues:
                print(f"  - {issue}")
            _print_fix(f"edit {profile_path}")
    else:
        failed += 1
        _print_fix("repokeeper init . --minimal")

    workflows_dir = target / ".github" / "workflows"
    agent_workflow = workflows_dir / AGENT_WORKFLOW
    _print_check(agent_workflow.exists(), "Implementation Agent workflow", str(agent_workflow))
    workflow_issues = _workflow_has_required_settings(agent_workflow)
    if workflow_issues:
        failed += 1
        for issue in workflow_issues:
            print(f"  - {issue}")
        _print_fix("repokeeper init . --minimal --force")
    else:
        _print_check(True, "Workflow triggers and permissions")

    labeler_workflow = workflows_dir / "labeler.yml"
    if labeler_workflow.exists():
        _print_check(True, "Auto-Labeler workflow", str(labeler_workflow))
    else:
        _print_check(
            False, "Auto-Labeler workflow",
            "copy labeler.yml to enable automatic issue labeling",
            status_if_false="warn",
        )
        warnings += 1

    github_token = _has_env("REPOKEEPER_GITHUB_TOKEN") or _has_env("GITHUB_TOKEN")
    _print_check(
        github_token,
        "GitHub token",
        "set REPOKEEPER_GITHUB_TOKEN or GITHUB_TOKEN",
    )
    if not github_token:
        failed += 1
        _print_fix("add a GitHub Actions secret only if the default GITHUB_TOKEN cannot create PRs")

    llm_key = (
        _has_env("DEEPSEEK_API_KEY")
        or _has_env("OPENAI_API_KEY")
        or _has_env("ANTHROPIC_API_KEY")
    )
    _print_check(
        llm_key,
        "LLM API key",
        "set DEEPSEEK_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY",
    )
    if not llm_key:
        failed += 1
        _print_fix("add DEEPSEEK_API_KEY in Settings -> Secrets and variables -> Actions")

    if _has_env("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            warnings += 1
            _print_check(
                False,
                "Anthropic extra",
                "install with: pip install 'repokeeper[anthropic]'",
                status_if_false="warn",
            )

    repo_slug = args.repo or os.environ.get("GITHUB_REPOSITORY")
    _print_check(bool(repo_slug), "Repository slug", "pass --repo owner/name or set GITHUB_REPOSITORY")
    if not repo_slug:
        failed += 1
        _print_fix("repokeeper doctor --repo owner/name")

    # ── Remote checks (only when token + slug are present) ──
    if github_token and repo_slug:
        actual_token = os.environ.get("REPOKEEPER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        failed, warnings = _run_remote_checks(actual_token, repo_slug, failed, warnings)

    if failed:
        print(f"\nDoctor found {failed} issue(s) and {warnings} warning(s).")
        return 1
    if warnings:
        print(f"\nDoctor found no blocking issues ({warnings} warning(s)).")
        return 0
    print("\nDoctor found no local setup issues.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repokeeper", description="AI-powered open source maintainer agent")
    parser.add_argument("--version", action="version", version=f"repokeeper {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a RepoKeeper profile")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--force", action="store_true", help="Overwrite an existing repokeeper.yml")
    init.add_argument("--minimal", action="store_true", help="Copy only the Implementation Agent workflow")
    init.add_argument(
        "--workflows",
        action="store_true",
        help="Copy all bundled GitHub Actions workflows (backward-compatible alias)",
    )
    init.add_argument("--all-workflows", action="store_true", help="Copy all bundled GitHub Actions workflows")
    init.set_defaults(func=cmd_init)

    doctor = subparsers.add_parser("doctor", help="Check local RepoKeeper setup")
    doctor.add_argument("path", nargs="?", default=".")
    doctor.add_argument("--profile", default=None, help="Path to repokeeper.yml")
    doctor.add_argument("--repo", default=None, help="GitHub repository as owner/name")
    doctor.set_defaults(func=cmd_doctor)

    profile = subparsers.add_parser("profile", help="Inspect maintainer profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    show = profile_sub.add_parser("show", help="Show the merged profile")
    show.add_argument("--profile", default=None)
    show.set_defaults(func=cmd_profile_show)
    validate = profile_sub.add_parser("validate", help="Validate the merged profile")
    validate.add_argument("--profile", default=None)
    validate.set_defaults(func=cmd_profile_validate)

    def add_common_remote(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", required=True, help="GitHub repository as owner/name")
        p.add_argument("--profile", default=None, help="Path to repokeeper.yml")
        p.add_argument("--github-token", default=None, help="GitHub token override")
        p.add_argument("--llm-api-key", default=None, help="LLM API key override")
        p.add_argument("--llm-base-url", default=None, help="OpenAI-compatible API base URL")

    radar = subparsers.add_parser("radar", help="Run Community Radar")
    add_common_remote(radar)
    radar.add_argument("--summary", action="store_true", help="Print markdown summary")
    radar.set_defaults(func=cmd_radar)

    patrol = subparsers.add_parser("patrol", help="Run Daily Patrol")
    add_common_remote(patrol)
    patrol.add_argument("--path", default=".", help="Local repository path for dependency scanning")
    patrol.add_argument("--summary", action="store_true", help="Print markdown health summary")
    patrol.set_defaults(func=cmd_patrol)

    health = subparsers.add_parser("health", help="Print patrol health score")
    add_common_remote(health)
    health.add_argument("--path", default=".", help="Local repository path for dependency scanning")
    health.set_defaults(func=cmd_health)

    agent = subparsers.add_parser("agent", help="Run Implementation Agent for an issue")
    add_common_remote(agent)
    agent.add_argument("--issue", required=True, type=int, help="GitHub issue number")
    agent.add_argument(
        "--dry-run", action="store_true",
        help="Generate an implementation plan without applying changes or creating a PR",
    )
    agent.set_defaults(func=cmd_agent)

    review = subparsers.add_parser("review", help="Run Code Review Agent for a PR")
    add_common_remote(review)
    review.add_argument("--pr", required=True, type=int, help="GitHub pull request number")
    review.set_defaults(func=cmd_review)

    labeler = subparsers.add_parser("labeler", help="Auto-label issues and PRs with AI")
    add_common_remote(labeler)
    labeler.add_argument("--issue", type=int, default=None, help="Issue number to label (omit for batch mode)")
    labeler.add_argument("--pr", type=int, default=None, help="PR number to label")
    labeler.add_argument("--summary", action="store_true", help="Print markdown summary")
    labeler.set_defaults(func=cmd_labeler)

    return parser


def main(argv: list[str] | None = None) -> int:
    from repokeeper.logs import setup_logging

    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)  # type: ignore[no-any-return]


if __name__ == "__main__":
    raise SystemExit(main())
