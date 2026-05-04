## Summary

Describe the user-visible change.

## Verification

- [ ] `ruff check src/ tests/`
- [ ] `pytest tests/ --cov=repokeeper --cov-report=term-missing`
- [ ] `mkdocs build --strict` if documentation changed

## Safety

- [ ] This PR does not broaden GitHub token permissions unnecessarily.
- [ ] This PR does not allow agent edits to `.github/workflows/`.
- [ ] This PR keeps generated changes reviewable by a maintainer.

