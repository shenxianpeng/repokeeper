# AGENTS.md

Instructions for AI coding agents working on this repository.

## Preparing a Release

When asked to prepare a release (e.g., "准备发布 0.3.0"), do all of the following:

1. **Tag the release** — create a git tag:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   ```
   `setuptools-scm` derives the version from git tags, so there's no need to bump versions in source files.

2. **Update CHANGELOG** — the canonical file is `CHANGELOG.md` (root). `docs/changelog.md` is a symlink to it, so only edit `CHANGELOG.md`. Add a new `## [X.Y.Z] - YYYY-MM-DD` section summarizing changes since the last release, derived from `git log`.

3. **Update docs** — ensure `mkdocs build --strict` succeeds. The CHANGELOG is already wired into the nav via the symlink.

4. **Lint and test** — run `ruff check src/ tests/` and `pytest tests/ --cov=repokeeper --cov-report=term-missing`. All checks must pass with zero lint issues.

5. **Build** — run `python -m build` to verify the package builds cleanly.

## Before Committing

Always run the linter and fix any issues before committing:

```bash
ruff check src/ tests/
```

If there are any lint errors or warnings, fix them, then run the linter again. Repeat until there are **zero** lint issues.

## Test Coverage

Run tests with coverage before committing:

```bash
pytest tests/ --cov=repokeeper --cov-report=term-missing
```

Ensure all tests pass.
