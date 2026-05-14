# Release Consistency

RepoKeeper is published in three places that must stay aligned:

| Surface | Current source of truth | Check |
|---|---|---|
| GitHub release | `https://github.com/shenxianpeng/repokeeper/releases/latest` | `gh release view --repo shenxianpeng/repokeeper` |
| Floating action tag | `shenxianpeng/repokeeper@v1` | `git ls-remote --tags origin v1` |
| PyPI package | `https://pypi.org/project/repokeeper/` | `python -m pip index versions repokeeper` |
| Documentation | `https://shenxianpeng.github.io/repokeeper/` | `uv run --extra docs mkdocs build --strict` |

As of 2026-05-14, the latest GitHub release and PyPI package are both
`v1.4.0` / `1.4.0`.

## Release Checklist

Before publishing a release:

1. Run `ruff check src/ tests/`.
2. Run `pytest tests/ --cov=repokeeper --cov-report=term-missing`.
3. Run `mypy src/repokeeper`.
4. Run `uv run --extra docs mkdocs build --strict`.
5. Confirm `CHANGELOG.md` includes the release notes.
6. Publish the GitHub release.
7. Confirm the release workflow uploaded the matching PyPI version.
8. Confirm the `release-tag.yml` workflow moved the floating major tag, for
   example `v1`, to the same commit.
9. Run the checks in the table above and update this page if anything changed.

Do not announce a feature in README or docs until the PyPI package and the
floating action tag both contain it.

## Action Entry Points

The Marketplace entry point is the root action. It can dispatch to every
RepoKeeper module:

```yaml
- uses: shenxianpeng/repokeeper@v1
  with:
    module: agent   # agent | review | describe | radar | monitor | patrol | labeler
```
