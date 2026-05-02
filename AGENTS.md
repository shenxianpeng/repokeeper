# AGENTS.md

Instructions for AI coding agents working on this repository.

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
