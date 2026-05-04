# Contributing to RepoKeeper

Thanks for helping improve RepoKeeper. This project is an automation tool for
open source maintainers, so contributions should keep safety, clarity, and
reviewability in mind.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,docs]"
```

## Local Checks

Run these before opening a pull request:

```bash
ruff check src/ tests/
pytest tests/ --cov=repokeeper --cov-report=term-missing
mkdocs build --strict
```

The package version is derived from git tags through `setuptools-scm`; do not
edit source files just to bump a version.

## Contribution Guidelines

- Keep changes small and focused.
- Add or update tests for behavior changes.
- Update documentation when user-facing behavior changes.
- Avoid broad refactors unless they are necessary for the issue.
- Treat GitHub token handling, workflow permissions, shell commands, and LLM
  output parsing as security-sensitive code.

## Good First Contributions

Good starter areas include:

- Documentation examples and screenshots
- Additional `repokeeper doctor` checks
- Better error messages for setup failures
- Tests for workflow templates and packaging behavior
- Ecosystem-specific dependency scanning improvements

## Pull Request Checklist

Before requesting review, confirm:

- `ruff check src/ tests/` passes
- `pytest tests/ --cov=repokeeper --cov-report=term-missing` passes
- `mkdocs build --strict` passes when docs changed
- The PR explains user-visible behavior changes
- The PR does not include generated caches, local virtual environments, or
  unrelated lockfile changes

