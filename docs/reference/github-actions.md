# GitHub Actions

RepoKeeper ships as **composite actions** — one per module — hosted in this
repository at `shenxianpeng/repokeeper/<module>@v1`. Each composite action
bundles checkout, Python setup, `pip install repokeeper`, and the module
entrypoint into a single step.

## Composite Actions

| Action | Module |
|--------|--------|
| `shenxianpeng/repokeeper/agent@v1` | Implementation Agent |
| `shenxianpeng/repokeeper/radar@v1` | Community Radar |
| `shenxianpeng/repokeeper/patrol@v1` | Daily Patrol |
| `shenxianpeng/repokeeper/labeler@v1` | Auto-Labeler |
| `shenxianpeng/repokeeper/review@v1` | Code Review Agent |

Each action requires a workflow file in `.github/workflows/` that defines the
trigger, permissions, and job that calls the action.

## Workflow: Implementation Agent

**File:** `.github/workflows/repokeeper.yml`

### Triggers

```yaml
on:
  issue_comment:
    types: [created]
  issues:
    types: [labeled]
```

### Conditions

The workflow only runs when:

1. **Comment trigger:** A collaborator comments `/repokeeper go` on an issue
   (not a PR):
   ```yaml
   github.event_name == 'issue_comment' &&
   !github.event.issue.pull_request &&
   contains(github.event.comment.body, '/repokeeper go') &&
   github.event.comment.author_association in ('OWNER', 'MEMBER', 'COLLABORATOR')
   ```

2. **Label trigger:** An issue is labeled `agent-todo`:
   ```yaml
   github.event_name == 'issues' &&
   github.event.label.name == 'agent-todo'
   ```

### Permissions

```yaml
permissions:
  contents: write      # Push branches
  issues: write        # Comment on issues
  pull-requests: write # Create PRs
```

### Steps

A single step calls the composite action:

```yaml
steps:
  - uses: shenxianpeng/repokeeper/agent@v1
    with:
      repo: ${{ github.repository }}
      issue: ${{ github.event.issue.number }}
      llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
      llm_base_url: ${{ secrets.LLM_BASE_URL || 'https://api.deepseek.com' }}
      github_token: ${{ secrets.REPOKEEPER_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}
```

The composite action bundles checkout (fetch-depth: 0), Python 3.10+ setup,
`pip install repokeeper`, and `repokeeper agent` internally.

### Required Secrets

| Secret | Required | Purpose |
|--------|----------|---------|
| `DEEPSEEK_API_KEY` | Yes | AI model API key |
| `LLM_BASE_URL` | No | Custom LLM endpoint |
| `REPOKEEPER_GITHUB_TOKEN` | No | PAT fallback if `GITHUB_TOKEN` cannot create PRs |

!!! note "Pull request permissions"
    For the default `GITHUB_TOKEN`, enable **Settings → Actions → General →
    Allow GitHub Actions to create and approve pull requests**. If that is not
    allowed in your repository or organization, create a `REPOKEEPER_GITHUB_TOKEN`
    secret with contents and pull request write access.

## Workflow: Community Radar

**File:** `.github/workflows/radar.yml`

### Triggers

```yaml
on:
  schedule:
    - cron: '0 */3 * * 1-5'  # Every 3 hours, Mon-Fri
  workflow_dispatch:           # Manual trigger
```

### Permissions

```yaml
permissions:
  issues: read
  discussions: read
  contents: read
```

### Steps

```yaml
steps:
  - uses: shenxianpeng/repokeeper/radar@v1
    with:
      repo: ${{ github.repository }}
      llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
      github_token: ${{ secrets.GITHUB_TOKEN }}
```

The composite action handles checkout, Python setup, installation, and the
`repokeeper radar --summary` run.

### Required Secrets

| Secret | Required | Purpose |
|--------|----------|---------|
| `DEEPSEEK_API_KEY` | Yes | AI model API key |
| `RKP_SMTP_USER` | No | Email notifications |
| `RKP_SMTP_PASS` | No | Email password |
| `RKP_TELEGRAM_CHAT_ID` | No | Telegram notifications |

## Workflow: Daily Patrol

**File:** `.github/workflows/patrol.yml`

### Triggers

```yaml
on:
  schedule:
    - cron: '0 8 * * 1-5'  # 8am UTC, Mon-Fri
  workflow_dispatch:         # Manual trigger
```

### Permissions

```yaml
permissions:
  issues: read
  pull-requests: write
  contents: write
  actions: read
```

### Steps

```yaml
steps:
  - uses: shenxianpeng/repokeeper/patrol@v1
    with:
      repo: ${{ github.repository }}
      llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
      github_token: ${{ secrets.GITHUB_TOKEN }}
```

The composite action handles checkout, Python setup, installation, and the
`repokeeper patrol --summary` run.

### Artifacts

The patrol generates a `patrol-summary.md` file uploaded as a GitHub Actions
artifact for later review.

## Adding to Your Repository

### Option 1: Use the CLI

```bash
pip install repokeeper
repokeeper init --all-workflows
```

### Option 2: Create workflows manually

Create workflow files in `.github/workflows/` that reference the composite actions.
See the [templates](https://github.com/shenxianpeng/repokeeper/tree/main/src/repokeeper/templates/workflows)
for copyable examples.

## Customizing Schedules

You can change the cron schedule in your repo's workflow files:

```yaml
# Run every hour
- cron: '0 * * * *'

# Run at 9am daily
- cron: '0 9 * * *'

# Run on weekends too
- cron: '0 8 * * *'

# Run twice a day
- cron: '0 8,20 * * *'
```

!!! note "Cron times are UTC"
    GitHub Actions uses UTC timezone. Schedule accordingly.

## Rate Limits

### GitHub API

GitHub Actions has generous rate limits (5,000 requests/hour for the GITHUB_TOKEN).
RepoKeeper's API usage is minimal:

- **Radar:** ~50 API calls per scan (issue listing)
- **Patrol:** ~100 API calls per scan (workflows, issues, PR creation)
- **Agent:** ~10 API calls per run (issue fetch, comment, PR creation)

### LLM API

DeepSeek API limits vary by plan. Key usage points:

- **Radar:** 1 LLM call per detected keyword match (classification)
- **Patrol:** 1 LLM call per CI failure + 1 per stale issue
- **Agent:** 1 LLM call per triggered implementation

Set `radar.keywords` judiciously to control LLM usage.
