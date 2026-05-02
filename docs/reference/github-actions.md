# GitHub Actions

RepoKeeper uses GitHub Actions workflows for automation. Each module has its
own workflow file.

## Workflow Files

| File | Module | Trigger |
|------|--------|---------|
| `.github/workflows/repokeeper.yml` | Implementation Agent | `agent-todo` label or `@repokeeper go` comment |
| `.github/workflows/radar.yml` | Community Radar | Every 3 hours (weekdays) + manual |
| `.github/workflows/patrol.yml` | Daily Patrol | Daily at 8am UTC (weekdays) + manual |

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

1. **Comment trigger:** A collaborator comments `@repokeeper go` on an issue
   (not a PR):
   ```yaml
   github.event_name == 'issue_comment' &&
   !github.event.issue.pull_request &&
   contains(github.event.comment.body, '@repokeeper go') &&
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

1. **Checkout** — clone the repository with full history
2. **Setup Python** — install Python 3.10+
3. **Install RepoKeeper** — `pip install repokeeper` or `pip install -e .`
4. **Run agent** — execute `repokeeper agent --repo "$GITHUB_REPOSITORY" --issue "$ISSUE_NUMBER"`

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

1. Checkout repository
2. Setup Python 3.10+
3. Install RepoKeeper
4. Run `repokeeper radar`

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

1. Checkout repository
2. Setup Python 3.10+
3. Install RepoKeeper
4. Run `repokeeper patrol --summary`
5. Upload patrol summary as artifact

### Artifacts

The patrol generates a `patrol-summary.md` file uploaded as a GitHub Actions
artifact for later review.

## Adding to Your Repository

### Option 1: Generate from the CLI

```bash
pip install repokeeper
repokeeper init --workflows
```

### Option 2: Copy from this repo

```bash
mkdir -p your-repo/.github/workflows
cp repokeeper/src/repokeeper/templates/workflows/*.yml your-repo/.github/workflows/
cp repokeeper/repokeeper.yml your-repo/repokeeper.yml
```

### Option 3: Use as a submodule

```bash
git submodule add https://github.com/shenxianpeng/repokeeper .github/repokeeper-src
```

Then point your workflow to the submodule path.

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
