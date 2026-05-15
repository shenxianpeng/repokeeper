# Module 7: Release Notes

The Release Notes module drafts GitHub release notes from repository evidence.
It reads merged pull requests, direct commits, labels, changed files, and the
previous release range, then asks the configured LLM to classify and summarize
the changes.

RepoKeeper only creates or updates a draft release. It does not publish the
release, move tags, bump package versions, or edit `CHANGELOG.md`.

## How It Works

1. Finds the latest published release, unless `--base` is provided.
2. Compares `base...target` to collect changed commits and files.
3. Searches merged pull requests since the previous release date.
4. Keeps direct commits as first-class release evidence.
5. Generates grouped markdown release notes with source references.
6. Creates or updates a matching GitHub draft release.

## CLI

```bash
# Create or update a draft release using the latest published release as base
repokeeper release --repo owner/repo

# Preview without writing a GitHub draft release
repokeeper release --repo owner/repo --dry-run

# Override the range and target draft tag
repokeeper release --repo owner/repo --base v1.4.0 --target main --tag v1.4.1
```

## GitHub Actions

Use the bundled `release.yml` workflow or call the root action directly:

```yaml
- uses: shenxianpeng/repokeeper@v1
  with:
    module: release
    repo: ${{ github.repository }}
    llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
    github_token: ${{ secrets.REPOKEEPER_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}
```

The workflow needs:

```yaml
permissions:
  contents: write
  pull-requests: read
```

## Configuration

Add a `release` section to `repokeeper.yml`:

```yaml
release:
  enabled: true
  model: null
  temperature: 0.1
  audience: users and maintainers
  categories:
    - Breaking Changes
    - Features
    - Fixes
    - Documentation
    - Maintenance
  include_prereleases: false
  prerelease: false
```

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable release note drafting |
| `model` | `null` | Per-module model, or inherit `agent.model` |
| `temperature` | `0.1` | LLM temperature |
| `audience` | `users and maintainers` | Who the notes should be written for |
| `categories` | see example | Preferred section order |
| `include_prereleases` | `false` | Consider prereleases when finding the previous release |
| `prerelease` | `false` | Mark the draft release as a prerelease |

## Boundary

This module is intentionally narrower than a release manager:

- It drafts notes; it does not publish.
- It suggests a next patch tag by default; it does not move tags.
- It uses labels as hints; it does not require labels to be correct.
- It includes direct commits instead of assuming every change came from a PR.
