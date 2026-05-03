# Changelog

All notable changes to RepoKeeper will be documented in this file.

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
