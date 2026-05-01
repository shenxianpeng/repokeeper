"""Command-line interface for RepoKeeper."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from github import Github
from openai import OpenAI

from .agent import run_agent
from .patrol import generate_health_summary, run_patrol
from .profile import generate_profile_template, load_profile, validate_profile
from .radar import generate_radar_summary, run_radar


def _make_github_client(token: str | None) -> Github:
    token = token or os.environ.get("REPOKEEPER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Missing GitHub token. Set REPOKEEPER_GITHUB_TOKEN or GITHUB_TOKEN.")
    return Github(token)


def _make_llm_client(api_key: str | None, base_url: str | None) -> OpenAI:
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Missing LLM API key. Set DEEPSEEK_API_KEY or OPENAI_API_KEY.")
    return OpenAI(api_key=api_key, base_url=base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"))


def _copy_workflows(target: Path) -> None:
    source = Path(__file__).resolve().parent / "templates" / "workflows"
    workflows_dir = target / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    if source.exists():
        for workflow in ("repokeeper.yml", "radar.yml", "patrol.yml"):
            src = source / workflow
            if src.exists():
                shutil.copyfile(src, workflows_dir / workflow)


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    profile_path = target / "repokeeper.yml"
    if profile_path.exists() and not args.force:
        print(f"{profile_path} already exists; use --force to overwrite.", file=sys.stderr)
        return 1
    generate_profile_template(profile_path)
    print(f"Wrote {profile_path}")
    if args.workflows:
        _copy_workflows(target)
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
    )
    if result.get("skip"):
        print(f"Skipped: {result.get('reason', '')}")
        return 0
    print(f"PR created: {result.get('pr_url')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repokeeper", description="AI-powered open source maintainer agent")
    parser.add_argument("--version", action="version", version="repokeeper 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a RepoKeeper profile")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--force", action="store_true", help="Overwrite an existing repokeeper.yml")
    init.add_argument("--workflows", action="store_true", help="Copy bundled GitHub Actions workflows")
    init.set_defaults(func=cmd_init)

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
    agent.set_defaults(func=cmd_agent)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
