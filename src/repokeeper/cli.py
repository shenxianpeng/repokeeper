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
from .patrol import generate_health_summary, run_patrol
from .profile import generate_profile_template, load_profile, validate_profile
from .radar import generate_radar_summary, run_radar

AGENT_WORKFLOW = "repokeeper.yml"
OPTIONAL_WORKFLOWS = ("radar.yml", "patrol.yml")
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
        remote_repo=args.remote_repo,
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


def _has_env(name: str) -> bool:
    return bool(os.environ.get(name))


def _run_remote_checks(token: str, repo_slug: str, failed: int, warnings: int) -> tuple[int, int]:
    """Verify the GitHub token can access the repository and inspect features.

