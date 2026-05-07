# Changelog

All notable changes to RepoKeeper will be documented in this file.

## [1.0.0] - 2026-05-07

### Added
- **Composite GitHub Actions.** Each module now ships as a standalone composite
  action at `shenxianpeng/repokeeper/<module>@v1`. Composite actions bundle
  checkout, Python setup, `pip install`, and the module entrypoint into a
  single step. Workflow files are now ~15 lines instead of ~40.
  - Six actions: `agent`, `radar`, `patrol`, `labeler`, `review`, `doctor`.
  - Publishable to GitHub Marketplace.
- **Per-module AI model selection.** Each module can use a different LLM model
  via `labeler.model`, `radar.model`, `patrol.model`, `review.model` in the
  profile. Falls back to `agent.model` then `deepseek-chat`.
- **Auto-Labeler (Module 5).** New `labeler` module that automatically labels
  GitHub issues and pull requests using AI. Fetches repo labels first, then
  uses the LLM to pick from existing labels (respecting naming conventions
  like `area/module` or `type: bug`). Only creates new labels when no suitable
  existing label exists, matching the description pattern and color palette of
  existing labels. Supports two modes: `add` (apply directly) and `suggest`
  (post comment for manual review). Also supports batch mode (all unlabeled
  open issues).
  - **Issue labeling**: Classifies issues as bug, feature_request, question,
    documentation, performance, security, dependencies, and 9 more categories.
  - **PR labeling**: Considers changed files (diff summary) to determine the
    PRIMARY purpose. A feature PR that also touches docs gets `enhancement`,
    not `documentation`.
  - New `repokeeper labeler` CLI command with `--issue`, `--pr`, `--summary` flags.
  - New `labeler.yml` workflow template (triggers on `issues: [opened]` and
    `workflow_dispatch`).
  - Profile config: `labeler.enabled`, `.mode`, `.confidence_threshold`,
    `.max_labels`, `.allow_create_labels`, `.exclude_labels`.
  (46 tests, 82% coverage on labeler module.)
- **Tool comparison table** in README and docs now includes PR Agent (Qodo)
  alongside Copilot and Cursor, with updated descriptions reflecting modern
  AI agent coding capabilities.

### Changed
- **Workflow templates rewritten.** All five `.github/workflows/*.yml` templates
  now use the composite actions. Setup steps (checkout, Python, pip install)
  are no longer inlined.
- **Doctor check** now validates the composite action reference
  (`repokeeper/agent@`) instead of the raw `repokeeper agent` command.

### Fixed
- Mypy `no-any-return` error in `get_module_model` (profile.py:343).
- Labeler: fetch repo labels once in batch mode instead of per-issue.

## [0.10.0] - 2026-05-07

### Added
- **Code Review Agent (Module 5).** New `review` module that reads PR diffs and
  source files, checks them against the Maintainer Profile (code style, tech
  stack preferences, PR standards), and posts a structured review comment.
  Triggers via `agent-review` label or `@repokeeper review` comment.
  Includes `repokeeper review` CLI command and `review.yml` workflow template.
  Safety model: RepoKeeper never approves or merges — only provides suggestions.
  (32 tests, 98% coverage on review module.)
- New `REVIEW_LABEL = "agent-review"` constant in `collaboration.py`.
- **Two-step smart file selection.** The Implementation Agent now uses a
  two-step process for large repos: list all files → LLM selects the relevant
  subset → read only those files. Controlled by new profile options:
  `smart_file_selection`, `max_fix_attempts`, and `max_context_tokens`.
  Includes `list_repo_files()`, `collect_specific_files()`, and
  `estimate_tokens()` helpers in `repo_context.py`.
- **Verification fix loop.** When verification (lint/test) fails, the agent now
  automatically retries with contextual failure output, up to
  `max_fix_attempts` times, instead of giving up after one attempt.
- **Dogfooding review workflow.** This repo now runs the review module on its
  own PRs via `pull_request_target` for secure, self-reviewed development.

### Fixed
- Added `mypy` type ignore for `check_review_skip_keywords` to match the
  existing pattern in `agent.py:check_skip_keywords`.

## [0.9.0] - 2026-05-06

### Added
- **Agent candidate handoff workflow.** A new `collaboration.py` module enables
  seamless handoff between RepoKeeper modules. Radar and Patrol can now
  nominate candidates for the Implementation Agent to act on, closing the
  loop from discovery to fix.
- **Agent `--dry-run` mode.** When `--dry-run` is passed, the Implementation
  Agent stops after generating the plan, posts a summary comment on the issue,
  and returns the plan dict without applying changes or creating a PR.
- **Remote repository checks in `doctor`.** `repokeeper doctor` now verifies
  that the GitHub token can reach the target repository and checks whether
  Discussions are enabled (critical for Radar's discussion scanning).
- **Structured exception hierarchy.** New `repokeeper.exceptions` module with
  `RepoKeeperError` (base), `AuthError`, `ConfigError`, `LLMParseError`,
  `PermissionDeniedError`, `GitOperationError`, and `VerificationError`.
- **Root-level `SECURITY.md`.** Vulnerability reporting instructions, supported
  versions table, and safety model summary. Automatically surfaced by GitHub.

### Changed
- **Unified LLM JSON parsing.** `parse_llm_json` moved from agent.py to
  `llm_client.py` as a shared public function with truncated JSON repair.
  All ad-hoc parsing in radar.py and patrol.py now uses it consistently.
- **CI auto-fix uses `git_ops.git()`.** Replaced raw `subprocess.run` calls
  with the shared `git()` helper, gaining safe-repo-path validation.
- **Exception types refined.** Replaced bare `RuntimeError` / `ValueError` /
  `SystemExit` throughout the codebase with semantic exception types.
- `git_ops.git()` now accepts an explicit `cwd` parameter.

### Fixed
- Removed dead duplicate return statement in `scan_dependencies`.
- Logging now auto-initializes via `get_logger()` from all entry points
  (radar, patrol, CLI), ensuring consistent formatting everywhere.
- Stale issue candidate publish failures are now captured and reported in
  patrol warnings instead of being silently discarded.

### Test Coverage
- Coverage gate raised, with new tests for dry-run mode, remote checks,
  exception paths, `parse_llm_json` edge cases, and stale publish failures.

## [0.8.0] - 2026-05-06

### Added
- Add cross-repo global search capability to Community Radar.

### Documentation
- Updated example project name in Community Radar documentation.
- Add RepoKeeper badge with professional styling to the README for better project visibility.

### Test Coverage
- Added edge case tests for `collect_repo_files` and `apply_and_push` functions
  in the agent module (+146 lines).

## [0.7.0] - 2026-05-05

### Added
- **Radar auto-create issues.** When `auto_create_issue` is enabled, Community
  Radar now automatically creates GitHub issues from community hits. Each
  created issue links back to the original discussion, includes a hidden
  deduplication marker, and carries professional RepoKeeper branding.
- **Radar deduplication.** `_find_existing_radar_issue()` detects duplicates
  by hidden marker or title similarity. When a duplicate is found, the existing
  issue receives an activity comment instead of creating a redundant issue.
- **Radar branding.** All auto-created issues carry a `repokeeper-radar` label,
  a header citing the original author and source, and a branded footer linking
  to the project — turning every issue into organic promotion.
- Radar workflow template now includes `issues: write` permission for
  auto-creation support.
- **Cross-repo global search.** When `cross_repo_search: true`, the Radar
  searches *all of GitHub* (not just your own repo) for mentions of your
  project. Finds issues and discussions in other communities that reference
  your project name. Customizable via `cross_repo_query` with full GitHub
  search syntax. Uses both REST (issues) and GraphQL (discussions) APIs.

### Documentation
- Updated Radar docs with auto-creation, deduplication, and branding sections.
- Updated README Community Radar description to mention auto-create capability.

## [0.6.0] - 2026-05-05

### Added
- Make LLM cost estimates configurable.

### Changed
- Limit source distribution contents.
- Raise the coverage gate.
- Clarify workflow install paths.

### Fixed
- Constrain LLM file writes to repository-relative paths.
- Update commit preprocessors and parsers for better release note categorization.

## [0.5.0] - 2026-05-04

### Added
- Improve setup diagnostics.

### Documentation
- Sync README workflow setup with the quick-start guide.
- Document the security model.
- Add contribution templates.
- Add `doctor` to the quick start.

### Fixed
- Include workflow templates in the package.

### Other Changes
- Add `uv.lock`.

## [0.4.0] - 2026-05-03

### Added
- **GitHub Discussions scanning.** Community Radar now scans Discussions
  via PyGithub's GraphQL API (was a stub). Requires `discussions:read` scope.
- **CI log fetching.** `diagnose_ci_failure()` now retrieves actual job/step
  data from the GitHub Actions API instead of a placeholder string.
- **CI auto-fix.** `attempt_ci_auto_fix()` reads failing workflow files,
  generates corrections via LLM, and opens a fix PR.
- **Multi-ecosystem dependency scanning.** Five new checkers: Cargo (Rust),
  Bundler (Ruby), Composer (PHP), Maven (Java), Gradle (Java/Kotlin).
  All wired into `scan_dependencies()`.
- **Unified LLM client** (`repokeeper.llm_client`). Supports OpenAI-compatible
  APIs (DeepSeek, Ollama, LocalAI) and **Anthropic Claude** via optional
  `repokeeper[anthropic]` extra. Auto-detects provider from API key prefix.
- **Streaming output.** LLM responses stream to stdout with progress dots.
  Auto-disabled in CI environments and on retries.
- **Token usage tracking.** Every LLM call now reports prompt/completion
  tokens and USD cost estimation (pricing for DeepSeek, GPT-4o, Claude).
  Cost shown in PR comments.
- **Branch name collision handling.** `_resolve_branch_collision()` appends
  a timestamp suffix when the target branch already exists on the remote.

### Changed
- **agent.py refactored** from 837 lines into four focused modules:
  `repo_context.py`, `git_ops.py`, `verifier.py`, `agent.py` (470 lines).
  All public names re-exported for full backward compatibility.
- **Mypy static type checking** added to CI with zero errors across 11 files.
- **Centralized logging** (`repokeeper.logs`). Detects GitHub Actions vs
  local terminal, uses `[repokeeper]` prefix in CI.
- LLM call sites in radar and patrol migrated to `LLMClient.chat()`.
- `print()` calls in agent replaced with structured logging.

### Fixed
- P0 feature gaps resolved: Discussion scanning was a stub; CI log fetching /
  auto-fix were placeholders; 5 ecosystem checkers were missing.
- CI: missing type stubs (`types-PyYAML`, `types-requests`) added to dev deps.
- `bool()` wraps on `resp.status_code == 200` for mypy no-any-return.

### Test Coverage
- **patrol.py:** 25% → 87% (+62pp). 69 tests covering all dep checkers,
  CI scanning/diagnosis/auto-fix, stale issues, health scoring, PR creation.
- **radar.py:** 30% → 89% (+59pp). 35 tests covering issue/discussion
  scanning, classification, draft generation, notifications, summary.
- **Total:** 67% → 82% (+15pp). 217 tests, zero failures.

## [0.3.0] - 2026-05-03

### Added
- Agent changes are now verified before creating a PR.
- Radar: added `--summary` flag and upload `radar-summary` artifact.
- Improved CLI checks for onboarding flow.
- Comprehensive test suite: 95%+ coverage for agent, 100% for profile, 90% for CLI.

### Fixed
- Real-time logging and resilient LLM JSON parsing.
- Agent now auto-strips blocked paths instead of skipping the entire issue.
- Workflows now write radar/patrol summary to `GITHUB_STEP_SUMMARY`.
- Agent handles 404 on `get_repo` with `GITHUB_TOKEN` fallback.

### Changed
- Documentation and website redesigned with custom CSS, logo, hero section, and terminal mockup.
- README refreshed with AI-project feel, Copilot comparison, and zero-config adoption messaging.
- Setup guide aligned with current capabilities.
- MkDocs visual design refreshed.
- Template workflow action versions synced with own repo.

### Chores
- Added `AGENTS.md` for AI coding agent instructions.
- Dependabot now groups all dependency updates into single PRs.
- Bumped GitHub Actions dependencies.

## [0.2.0] - 2026-05-01

### Added
- All four RepoKeeper modules: Community Radar, Daily Patrol, Implementation Agent, Maintainer Profile.
- Full project documentation (MkDocs-based site).
- Packaging and CI/CD workflows.
- Python 3.10–3.14 support.
- CLI interface for all modules.

### Fixed
- Agent blocks `.github/workflows/` modifications to prevent push rejection.

## [0.1.0] - 2026-05-01

- Initial release.
