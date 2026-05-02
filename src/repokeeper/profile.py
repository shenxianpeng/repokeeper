from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ─── Default profile ──────────────────────────────────────────────────────────

DEFAULT_PROFILE: dict[str, Any] = {
    "maintainer": os.environ.get("USER", "maintainer"),
    # ── Communication ──
    "tone": {
        "language": "en",             # en | zh | auto
        "style": "friendly",          # friendly | formal | minimal
        "emoji": True,                # allow emoji in replies
        "closing": "happy to help",   # custom closing phrase
    },
    # ── Code style ──
    "style": {
        "code_style": (
            "Follow existing code style exactly. "
            "Use type hints in Python. "
            "Keep functions small and focused."
        ),
        "testing": "pytest",          # pytest | unittest | jest | go test | none
        "linting": True,              # run linter after changes
        "formatting": "ruff",         # ruff | black | prettier | gofmt | none
    },
    # ── PR standards ──
    "pr": {
        "min_tests": True,            # require tests for new code
        "max_files_per_pr": 15,       # reject PRs touching too many files
        "require_changelog": False,   # enforce changelog entry
        "auto_merge": False,          # auto-merge after CI passes (dangerous!)
        "review_required": True,      # always require human review
    },
    # ── Tech stack preferences ──
    "tech": {
        "preferred": [],              # e.g. ["python", "fastapi", "postgresql"]
        "avoid": [],                  # e.g. ["jquery", "php"]
        "target_python": "3.10",      # minimum Python version
        "target_node": "20",          # minimum Node.js version
    },
    # ── Notifications ──
    "notifications": {
        "email": "",                  # email address for alerts
        "telegram": "",               # telegram chat ID or bot token
        "wechat": "",                 # wechat webhook URL
        "daily_summary": True,        # send daily health summary
        "urgent_only": False,         # only notify for urgent issues
    },
    # ── Agent behavior ──
    "agent": {
        "model": "deepseek-chat",     # deepseek-chat | deepseek-reasoner | gpt-4o
        "implement": True,            # allow automatic implementation
        "max_context_files": 40,      # max files to include in LLM context
        "temperature": 0.1,           # LLM temperature for code generation
        "skip_keywords": [],          # phrases in issues that trigger auto-skip
    },
    # ── Radar ──
    "radar": {
        "enabled": True,
        "keywords": [],               # watchlist keywords e.g. ["bug", "crash", "security"]
        "confidence_threshold": 0.7,  # minimum AI confidence to act
        "auto_create_issue": False,   # auto-create issues (else draft for approval)
    },
    # ── Patrol ──
    "patrol": {
        "enabled": True,
        "schedule": "0 8 * * 1-5",    # cron: 8am weekdays
        "auto_upgrade_deps": True,    # auto-PR for dependency upgrades
        "stale_days": 90,             # days before issue is considered stale
        "ci_auto_fix": True,          # attempt automatic CI fixes
    },
}


# ─── Loading ──────────────────────────────────────────────────────────────────

def _merge_dicts(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file safely, return empty dict on any error."""
    try:
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _apply_env_overrides(profile: dict) -> dict:
    """Apply RKP_* environment variable overrides using dot notation.

    Examples:
        RKP_TONE_LANGUAGE=zh
        RKP_AGENT_MODEL=deepseek-reasoner
        RKP_NOTIFICATIONS_EMAIL=me@example.com
    """
    for key, value in os.environ.items():
        if not key.startswith("RKP_"):
            continue
        # RKP_TONE_LANGUAGE=zh -> path=["tone", "language"]
        path = key[4:].lower().split("_")
        target = profile
        for part in path[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        # Type-coerce value
        raw = value
        if raw.lower() in ("true", "yes", "1"):
            coerced: Any = True
        elif raw.lower() in ("false", "no", "0"):
            coerced = False
        elif raw.isdigit():
            coerced = int(raw)
        elif raw.replace(".", "", 1).isdigit():
            coerced = float(raw)
        else:
            coerced = raw
        target[path[-1]] = coerced
    return profile


def load_profile(profile_path: str | Path | None = None) -> dict[str, Any]:
    """Load the full maintainer profile.

    Resolution order:
      1. Hardcoded defaults (DEFAULT_PROFILE)
      2. Global profile at ~/.repokeeper/global.yml
      3. Per-repo profile at repokeeper.yml (or profile_path)
      4. Environment variables (RKP_* prefix)

    Args:
        profile_path: Optional explicit path to a per-repo profile.
                      Defaults to repokeeper.yml in the current directory.

    Returns:
        Merged profile dictionary.
    """
    profile = dict(DEFAULT_PROFILE)

    # Layer 2: global
    global_path = Path.home() / ".repokeeper" / "global.yml"
    profile = _merge_dicts(profile, _load_yaml(global_path))

    # Layer 3: per-repo
    repo_path = Path(profile_path) if profile_path else Path("repokeeper.yml")
    profile = _merge_dicts(profile, _load_yaml(repo_path))

    # Layer 4: environment
    profile = _apply_env_overrides(profile)

    return profile


def is_organization(profile: dict) -> bool:
    """Check if the maintainer field refers to a GitHub organization.

    An organization is detected when the maintainer value contains a slash
    (e.g. "my-org") or when the profile has an 'organization' key set to true.
    """
    return profile.get("organization", False) or (
        isinstance(profile.get("maintainer"), str)
        and "/" in profile["maintainer"]
    )


def get_org_repos(gh_client: Any, org_name: str) -> list[str]:
    """Get all repository slugs for a GitHub organization.

    Args:
        gh_client: PyGithub Github instance.
        org_name: GitHub organization name.

    Returns:
        List of "owner/repo" strings.
    """
    try:
        org = gh_client.get_organization(org_name)
        return [repo.full_name for repo in org.get_repos()]
    except Exception:
        return []


# ─── Template generation ─────────────────────────────────────────────────────

def generate_profile_template(path: str | Path = "repokeeper.yml") -> None:
    """Write a commented template repokeeper.yml for a new repo."""
    template = """\
# RepoKeeper Maintainer Profile
# https://github.com/shenxianpeng/repokeeper
#
# This YAML describes your preferences as a maintainer.
# All RepoKeeper modules (Radar, Patrol, Agent) respect these settings.
# Uncomment and customize the sections you need.

# ── Maintainer info ──
# For a single user:
# maintainer: your-github-username
#
# For an organization (scans all repos in the org):
# maintainer: your-org-name
# organization: true

maintainer: your-github-username

# ── Communication tone ──
# tone:
#   language: en              # en | zh | auto
#   style: friendly           # friendly | formal | minimal
#   emoji: true
#   closing: "happy to help"

# ── Code style ──
# style:
#   code_style: |
#     Follow existing code style exactly.
#     Use type hints in Python.
#     Keep functions small and focused.
#   testing: pytest           # pytest | unittest | jest | go test | none
#   linting: true
#   formatting: ruff          # ruff | black | prettier | gofmt | none

# ── PR standards ──
# pr:
#   min_tests: true
#   max_files_per_pr: 15
#   require_changelog: false
#   auto_merge: false
#   review_required: true

# ── Tech stack preferences ──
# tech:
#   preferred:                # your preferred stack (AI considers these first)
#     - python
#     - fastapi
#   avoid:                    # tech you don't want to touch
#     - jquery
#   target_python: "3.10"
#   target_node: "20"

# ── Notifications ──
# notifications:
#   email: ""
#   telegram: ""
#   wechat: ""
#   daily_summary: true
#   urgent_only: false

# ── Agent behavior ──
# agent:
#   model: deepseek-chat      # deepseek-chat | deepseek-reasoner | gpt-4o
#   implement: true           # allow automatic PR generation
#   max_context_files: 40
#   temperature: 0.1
#   skip_keywords:            # phrases that trigger auto-skip
#     - "needs design"
#     - "breaking change"

# ── Community Radar ──
# radar:
#   enabled: true
#   keywords:                 # watchlist - Radar scans for these
#     - bug
#     - crash
#     - security
#     - feature request
#   confidence_threshold: 0.7
#   auto_create_issue: false  # false = draft for review, true = auto-create

# ── Daily Patrol ──
# patrol:
#   enabled: true
#   schedule: "0 8 * * 1-5"   # 8am Mon-Fri
#   auto_upgrade_deps: true
#   stale_days: 90
#   ci_auto_fix: true
#
# ─────────────────────────────────────
# Tip: Place this file in the root of each repo.
# Missing keys inherit from ~/.repokeeper/global.yml or built-in defaults.
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(template)


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_profile(profile: dict) -> list[str]:
    """Validate a profile and return a list of issues (empty = valid)."""
    issues: list[str] = []

    if not isinstance(profile.get("maintainer"), str) or not profile.get("maintainer"):
        issues.append("maintainer must be a non-empty string")

    # Validate agent model
    valid_models = {"deepseek-chat", "deepseek-reasoner", "gpt-4o", "gpt-4-turbo"}
    if profile.get("agent", {}).get("model") not in valid_models:
        issues.append(f"agent.model must be one of {valid_models}")

    # Validate tone style
    valid_tones = {"friendly", "formal", "minimal"}
    if profile.get("tone", {}).get("style") not in valid_tones:
        issues.append(f"tone.style must be one of {valid_tones}")

    # Validate radar threshold
    threshold = profile.get("radar", {}).get("confidence_threshold", 0.7)
    if not (0 <= threshold <= 1):
        issues.append("radar.confidence_threshold must be between 0 and 1")

    # Validate patrol schedule (cron)
    schedule = profile.get("patrol", {}).get("schedule", "")
    if schedule and len(schedule.split()) != 5:
        issues.append("patrol.schedule must be a valid 5-field cron expression")

    # Validate stale_days
    stale = profile.get("patrol", {}).get("stale_days", 90)
    if not isinstance(stale, int) or stale < 1:
        issues.append("patrol.stale_days must be a positive integer")

    return issues
