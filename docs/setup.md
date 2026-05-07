# Getting Started

This guide walks through installing and configuring RepoKeeper.

## Prerequisites

- **Python 3.10+**
- **GitHub repository** with Actions enabled
- **DeepSeek API key** ([deepseek.com](https://platform.deepseek.com/api_keys)) — or any OpenAI-compatible API

## Installation

### Option 1: Generate the agent workflow (recommended for existing repos)

Install RepoKeeper and generate the starter files in your repository:

```bash
pip install repokeeper
repokeeper init . --minimal
```

That command creates this layout:

```
your-repo/
├── .github/
│   └── workflows/
│       ├── repokeeper.yml    # Implementation Agent (composite action)
└── repokeeper.yml            # Your maintainer profile
```

All workflows use the `shenxianpeng/repokeeper/<module>@v1` composite actions,
so they stay lean: checkout, Python, and pip install are bundled inside each action.

To also create Community Radar, Daily Patrol, Labeler, and Review workflows, run
`repokeeper init . --all-workflows --force`.

### Option 2: Use the CLI without workflows

```bash
pip install repokeeper
```

Then use the CLI:

```bash
# Run community radar scan
repokeeper radar --repo owner/repo

# Run daily patrol
repokeeper patrol --repo owner/repo

# Run implementation agent on an issue
repokeeper agent --repo owner/repo --issue 42
```

## Configuration

### Step 1: Create your global profile

```bash
mkdir -p ~/.repokeeper
```

Create `~/.repokeeper/global.yml`:

```yaml
maintainer: your-github-username

notifications:
  email: you@example.com

agent:
  model: deepseek-chat
```

### Step 2: Create per-repo profile

Place `repokeeper.yml` in each repo root. Only override what differs from your global profile:

```yaml
# repokeeper.yml — this repo uses different settings
radar:
  keywords:
    - bug
    - performance
    - ux

style:
  code_style: |
    Use functional components in React.
    Prefer TypeScript over JavaScript.
    Use Prettier for formatting.
```

### Step 3: Set up GitHub Secrets

Go to your repository → **Settings** → **Secrets and variables** → **Actions**:

| Secret Name | Value |
|-------------|-------|
| `DEEPSEEK_API_KEY` | `sk-...` (your DeepSeek API key) |
| `RKP_SMTP_USER` | (optional) SMTP username for email alerts |
| `RKP_SMTP_PASS` | (optional) SMTP password |
| `RKP_TELEGRAM_CHAT_ID` | (optional) Telegram chat ID |

### Step 4: Verify

Run the local setup check before pushing:

```bash
repokeeper doctor --repo owner/repo
```

Fix anything marked `missing`. The doctor checks:

- Git repository and profile file
- Implementation Agent workflow
- Required workflow triggers and permissions
- GitHub token environment
- LLM API key
- Repository slug

Then push the workflows and profile to your repo. Go to the **Actions** tab and
manually trigger any workflow to test.

## Profile Layering

RepoKeeper merges settings from three sources (later overrides earlier):

```
1. Built-in defaults         ← always present
2. ~/.repokeeper/global.yml  ← global preferences
3. repokeeper.yml (per-repo) ← per-repo overrides
4. RKP_* env vars            ← CI/secret overrides
```

Example: To override the agent model just for CI runs, set the secret:

```
RKP_AGENT_MODEL = deepseek-reasoner
```

This overrides whatever is in your YAML files.

## Next Steps

- [Module 1: Community Radar](module-1-radar.md) — monitor your community
- [Module 2: Daily Patrol](module-2-patrol.md) — automated health checks
- [Module 3: Implementation Agent](module-3-agent.md) — AI-powered PRs
- [Module 4: Maintainer Profile](module-4-profile.md) — full configuration reference
