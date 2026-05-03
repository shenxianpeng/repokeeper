# Changelog

All notable changes to RepoKeeper will be documented in this file.

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
