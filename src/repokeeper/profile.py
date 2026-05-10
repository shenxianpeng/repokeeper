"""
Module 4: Maintainer Profile

Core configuration system. A YAML file describes your maintainer preferences:
code style, reply tone, PR acceptance standards, tech stacks to avoid.
Every repo can inherit the global profile and override locally.

Profile loading order (later overrides earlier):
  1. ~/.repokeeper/global.yml          (global defaults)
  2. <repo_root>/repokeeper.yml        (per-repo overrides)
  3. Environment variables              (RKP_* overrides, for CI/secrets)
"""

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
        "model": "deepseek-chat",     # deepseek-chat | deepseek-reasoner | gpt-4o | gpt-4o-mini
        "backend": "native",          # native | pi
        "implement": True,            # allow automatic implementation
        "max_context_files": 60,      # max files to include in LLM context
        "max_context_tokens": None,   # token budget override (None = auto)
        "temperature": 0.1,           # LLM temperature for code generation
        "skip_keywords": [],          # phrases in issues that trigger auto-skip
        "smart_file_selection": True, # two-step: LLM picks files, then reads them
        "context_expansion": True,    # include likely tests and local deps
        "change_mode": "edits",       # edits | patch | full_file
        "max_fix_attempts": 2,        # verification failure retry count (0 = off)
        "similar_issue_check": True,  # search for duplicate issues before implementing
    },
    # ── Radar ──
    "radar": {
        "enabled": True,
        "model": None,                # per-module model (None = use agent.model)
        "keywords": [],               # watchlist keywords e.g. ["bug", "crash", "security"]
        "confidence_threshold": 0.7,  # minimum AI confidence to act
        "auto_create_issue": False,   # auto-create issues (else draft for approval)
        "cross_repo_search": False,   # search *all* of GitHub for mentions (not just this repo)
        "cross_repo_query": "",       # custom search query; defaults to repo name
    },
    # ── Patrol ──
    "patrol": {
        "enabled": True,
        "model": None,                # per-module model (None = use agent.model)
        "schedule": "0 8 * * 1-5",    # cron: 8am weekdays
        "auto_upgrade_deps": True,    # auto-PR for dependency upgrades
        "stale_days": 90,             # days before issue is considered stale
        "ci_auto_fix": True,          # attempt automatic CI fixes
    },
    # ── Auto-Labeler ──
    "labeler": {
        "enabled": True,
        "model": None,                # per-module model (None = use agent.model)
        "mode": "add",               # "add" | "suggest" (comment suggestions)
        "confidence_threshold": 0.7,  # minimum AI confidence to apply labels
        "label_map": {},              # category → labels mapping (empty = defaults)
        "max_labels": 3,              # max labels to apply per issue
        "allow_create_labels": True,  # allow creating new labels when needed
        "exclude_labels": [],         # labels to ignore when finding unlabeled issues
    },
    # ── Review ──
    "review": {
        "model": None,                # per-module model (None = use agent.model)
        "describe_on_open": False,    # auto-generate PR description on pull_request.opened
        "incremental": True,          # re-review on new commits (pull_request.synchronize)
    },
    # ── Releaser (Draft Release Generator) ──
    "releaser": {
        "enabled": True,
        "model": None,                # per-module model (None = use agent.model)
        "max_commits": 200,           # max commits to scan for release notes
        "dry_run": False,             # if True, only generate notes without creating a release
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
#   model: deepseek-chat      # deepseek-chat | deepseek-reasoner | gpt-4o | gpt-4o-mini
#   backend: native           # native | pi  (pi = autonomous agent loop for complex tasks)
#   implement: true           # allow automatic PR generation
#   max_context_files: 60     # max files to send to LLM
#   max_context_tokens: null  # token budget (null = auto, or e.g. 25000)
#   temperature: 0.1
#   smart_file_selection: true  # LLM picks relevant files before implementing
#   context_expansion: true     # include related tests and local dependencies
#   change_mode: edits          # edits | patch | full_file
#   max_fix_attempts: 2       # retry count when verification fails (0 = off)
#   similar_issue_check: true # search for duplicate issues before implementing
#   skip_keywords:            # phrases that trigger auto-skip
#     - "needs design"
#     - "breaking change"
#   verify_commands:          # commands that must pass before PR creation
#     - ruff check .
#     - pytest tests

# ── Community Radar ──
# radar:
#   enabled: true
#   model: qwen2.5-coder      # per-module model (omitted = use agent.model)
#   keywords:                 # watchlist - Radar scans for these
#     - bug
#     - crash
#     - security
#     - feature request
#   confidence_threshold: 0.7
#   auto_create_issue: false  # false = draft for review, true = auto-create
#   cross_repo_search: false  # search *all* of GitHub for mentions of your project
#   cross_repo_query: ""      # custom search query (defaults to repo name)

# ── Daily Patrol ──
# patrol:
#   enabled: true
#   model: deepseek-chat      # per-module model (omitted = use agent.model)
#   schedule: "0 8 * * 1-5"   # 8am Mon-Fri
#   auto_upgrade_deps: true
#   stale_days: 90
#   ci_auto_fix: true
#
# ── Auto-Labeler ──
# labeler:
#   enabled: true
#   model: qwen2.5-coder      # per-module model (omitted = use agent.model)
#   mode: add                 # "add" = apply labels directly, "suggest" = post comment
#   confidence_threshold: 0.7
#   max_labels: 3
#   allow_create_labels: true # allow creating new labels (with descriptions)
#   label_map:                # optional: maps AI category to your GitHub labels
#     bug: ["bug"]
#     feature_request: ["enhancement"]
#     question: ["question"]
#     documentation: ["documentation"]
#   exclude_labels:           # labels to ignore when finding unlabeled issues
#     - "repokeeper-labeler"
#
# ── Code Review ──
# review:
#   model: deepseek-reasoner  # per-module model (omitted = use agent.model)
#   describe_on_open: false   # auto-generate PR description when a PR is opened
#   incremental: true         # re-review when new commits are pushed to the PR
#
# ─────────────────────────────────────
# Tip: Place this file in the root of each repo.
# Missing keys inherit from ~/.repokeeper/global.yml or built-in defaults.
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(template)


# ─── Validation ───────────────────────────────────────────────────────────────

def get_module_model(profile: dict, module: str) -> str:
    """Return the LLM model for a module.

    Resolution order:
      1. Module-specific model (e.g. ``labeler.model``)
      2. ``agent.model`` (global fallback)
      3. ``"deepseek-chat"`` (hardcoded default)

    Set a module's model to ``null`` / omit it to inherit ``agent.model``.
    """
    return str(profile.get(module, {}).get("model")
               or profile.get("agent", {}).get("model", "deepseek-chat"))


def validate_profile(profile: dict) -> list[str]:
    """Validate a profile and return a list of issues (empty = valid)."""
    issues: list[str] = []

    if not isinstance(profile.get("maintainer"), str) or not profile.get("maintainer"):
        issues.append("maintainer must be a non-empty string")

    # Validate per-module model overrides (fall back to agent.model)
    known_models = {"deepseek-chat", "deepseek-reasoner", "gpt-4o", "gpt-4o-mini"}
    for section in ("agent", "labeler", "radar", "patrol", "review", "releaser"):
        model = profile.get(section, {}).get("model")
        if model is not None and not isinstance(model, str):
            issues.append(f"{section}.model must be a string or null")
        elif model is not None and model not in known_models:
            # Allow unknown models (e.g. Ollama), pass silently
            pass

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

    change_mode = profile.get("agent", {}).get("change_mode", "edits")
    if change_mode not in {"edits", "patch", "full_file"}:
        issues.append("agent.change_mode must be one of {'edits', 'patch', 'full_file'}")

    backend = profile.get("agent", {}).get("backend", "native")
    if backend not in {"native", "pi"}:
        issues.append("agent.backend must be one of {'native', 'pi'}")

    return issues
