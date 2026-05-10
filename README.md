<p align="center">
  <img src="https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/docs/assets/logo.svg" width="80" alt="RepoKeeper logo">
</p>

# RepoKeeper

[![CI](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/shenxianpeng/repokeeper/branch/main/graph/badge.svg)](https://codecov.io/gh/shenxianpeng/repokeeper)
[![PyPI](https://img.shields.io/pypi/v/repokeeper.svg)](https://pypi.org/project/repokeeper/)
[![Python](https://img.shields.io/pypi/pyversions/repokeeper.svg)](https://pypi.org/project/repokeeper/)
[![RepoKeeper](https://img.shields.io/badge/%F0%9F%A4%96-RepoKeeper-6e40c9)](https://github.com/shenxianpeng/repokeeper)
[![Docs](https://img.shields.io/badge/docs-mkdocs--ng-4051b5)](https://shenxianpeng.github.io/repokeeper/)
[![Discussions](https://img.shields.io/badge/Discussions-Q%26A-6e40c9?logo=github)](https://github.com/shenxianpeng/repokeeper/discussions)

**AI-powered open source maintainer agent. Reads issues, writes code, opens PRs — 24/7.**

```bash
# Label an issue agent-todo — RepoKeeper handles the rest
@repokeeper go
```

Zero config. GitHub-native. ~$0.01 per PR with DeepSeek.

---

## Why RepoKeeper?

Open source maintenance is a second job you didn't sign up for. AI coding
agents like Copilot and Cursor help you write code in the editor. PR Agent
(Qodo) automates PR workflows. But what about **everything else**? Triaging
issues, bumping dependencies, diagnosing CI, responding to the community?

> **Copilot codes with you. PR Agent polishes PRs. RepoKeeper runs your repo while you sleep.**

| | Copilot / Cursor | PR Agent (Qodo) | RepoKeeper |
|---|---|---|---|
| What it does | AI agent coding in your editor | Automates PR descriptions, reviews, suggestions | Maintains your repo autonomously |
| How | Reads codebase → implements → verifies | PR lifecycle automation | Reads issues + codebase → opens verified PRs |
| When | While you code | On PR events | 24/7 on schedule (labels, comments, cron) |
| Community | No | No | Monitors, classifies, responds |
| Dependencies | No | No | Scans 8 ecosystems for outdated deps |
| CI | No | No | Diagnoses failures, suggests fixes |
| **Cost** | $10–39/month subscription | Free OSS / paid plans | ~$0.01 per PR (your own LLM key) |
| Config | IDE settings | CLI / PR comments | One YAML (or zero) |

## What It Does

- **🔭 Community Radar** — Monitors GitHub issues **and discussions** for keywords. AI classifies hits as bugs, feature requests, or noise. **Auto-creates issues** with deduplication and RepoKeeper branding, linking back to original discussions. Notifies you via email, Telegram, or WeChat.
- **🔍 Daily Patrol** — Scans **8 ecosystems** (pip, npm, Go, Cargo, Bundler, Composer, Maven, Gradle) for outdated deps. Diagnoses CI failures with real job/step data. **Auto-fixes CI** by opening repair PRs. Finds stale issues. Health score every weekday morning.
- **🤖 Implementation Agent** — Reads your codebase + issue → implements → verifies (lint + tests) → pushes branch → opens PR. **Streams LLM output** in real-time. **Estimates token cost**. Supports **DeepSeek, OpenAI, and Anthropic Claude** models.
- **🏷️ Auto-Labeler** — AI classifies new issues and PRs, picks labels from your repo's existing set (matching naming conventions), and creates new labels only when needed — with consistent style and descriptions. Supports issue and PR labeling with diff-aware classification.
- **👤 Maintainer Profile** — One YAML file describing your code style, tone, PR standards. *Or skip it — defaults work.*

## Adopt in 60 Seconds

Three ways to onboard — pick one:

### 📋 Copy a workflow

Create `.github/workflows/repokeeper.yml` in your repo:

```yaml
name: RepoKeeper Implementation Agent

on:
  issue_comment:
    types: [created]
  issues:
    types: [labeled]

jobs:
  agent:
    runs-on: ubuntu-latest
    if: |
      (
        github.event_name == 'issue_comment' &&
        !github.event.issue.pull_request &&
        contains(github.event.comment.body, '@repokeeper go') &&
        (
          github.event.comment.author_association == 'OWNER' ||
          github.event.comment.author_association == 'MEMBER' ||
          github.event.comment.author_association == 'COLLABORATOR'
        )
      ) ||
      (
        github.event_name == 'issues' &&
        github.event.label.name == 'agent-todo'
      )
    permissions:
      contents: write
      issues: write
      pull-requests: write
    steps:
      - uses: shenxianpeng/repokeeper/agent@v1
        with:
          repo: ${{ github.repository }}
          issue: ${{ github.event.issue.number }}
          llm_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
          llm_base_url: ${{ secrets.LLM_BASE_URL || 'https://api.deepseek.com' }}
          github_token: ${{ secrets.REPOKEEPER_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}
```

Then add your API key: **Settings → Secrets → Actions → New secret:** `DEEPSEEK_API_KEY` = `sk-...`

Before pushing, run the setup check:

```bash
repokeeper doctor --repo owner/repo
```

`doctor` verifies the profile, workflow triggers, workflow permissions, token
environment, LLM key, and repository slug. Fix anything marked `missing`, then
push the workflow.

> Want Radar, Patrol, Labeler, and Review too? Add [`radar`](radar/action.yml), [`patrol`](patrol/action.yml), [`labeler`](labeler/action.yml), and [`review`](review/action.yml) composite actions to separate workflow files in `.github/workflows/`.

### 🖥️ CLI

```bash
pip install repokeeper
repokeeper init --all-workflows   # profile + all 5 workflows
repokeeper init --minimal         # profile + agent workflow only
repokeeper doctor --repo owner/repo
```

### 🤖 Ask AI

Paste this into any AI coding agent (Copilot Chat, Claude Code, Cursor, Windsurf, pi, etc.):

> Add RepoKeeper to this repository. Create `.github/workflows/repokeeper.yml` that uses the `shenxianpeng/repokeeper/agent@v1` composite action — trigger on issue comments (`@repokeeper go`) and labels (`agent-todo`). Pass `DEEPSEEK_API_KEY` as the `llm_api_key` input. Then tell me to add a `DEEPSEEK_API_KEY` secret in GitHub Actions settings.

### Trigger the agent

Label any issue `agent-todo` — or comment `@repokeeper go`.

---

## Install (optional CLI)

```bash
pip install repokeeper
```

```bash
repokeeper init             # Create a profile
repokeeper init --minimal   # Create a profile + agent workflow
repokeeper doctor --repo owner/repo
repokeeper radar --repo owner/repo
repokeeper patrol --repo owner/repo --summary
repokeeper agent --repo owner/repo --issue 42
repokeeper labeler --repo owner/repo --issue 42
repokeeper labeler --repo owner/repo --pr 42
repokeeper review --repo owner/repo --pr 42
repokeeper describe --repo owner/repo --pr 42
```

---

## Documentation

Full docs at **[shenxianpeng.github.io/repokeeper](https://shenxianpeng.github.io/repokeeper)**

| Guide | |
|---|---|
| [Quick Start](https://shenxianpeng.github.io/repokeeper/quick-start/) | 5-minute setup |
| [Security](https://shenxianpeng.github.io/repokeeper/security/) | Permissions, tokens, and automation boundaries |
| [Dogfood Cases](https://shenxianpeng.github.io/repokeeper/dogfood/) | Public proof template for real RepoKeeper-generated PRs |
| [Community Radar](https://shenxianpeng.github.io/repokeeper/module-1-radar/) | Monitor your community |
| [Daily Patrol](https://shenxianpeng.github.io/repokeeper/module-2-patrol/) | Automated health checks |
| [Implementation Agent](https://shenxianpeng.github.io/repokeeper/module-3-agent/) | AI-powered PRs |
| [Auto-Labeler](https://shenxianpeng.github.io/repokeeper/module-5-labeler/) | AI-powered issue & PR labeling |
| [Maintainer Profile](https://shenxianpeng.github.io/repokeeper/module-4-profile/) | Full config reference |
| [Code Review Agent](https://shenxianpeng.github.io/repokeeper/module-6-review/) | Inline review, PR descriptions, incremental re-review |
| [Benchmarks](https://shenxianpeng.github.io/repokeeper/benchmarks/) | Cost and performance estimates by scenario |

## Community

Ask questions, share your RepoKeeper setup, or discuss maintainer automation
strategies in **[GitHub Discussions](https://github.com/shenxianpeng/repokeeper/discussions)**.

## Contributing

Contributions are welcome, especially documentation examples, setup diagnostics,
tests, and safety improvements. See [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

## Safety Model

RepoKeeper creates reviewable pull requests; it does not merge them for you.
The default workflow limits write access to branches, issue comments, and pull
requests, and the agent blocks edits under `.github/workflows/`. See the
[Security guide](https://shenxianpeng.github.io/repokeeper/security/) before
enabling it on sensitive repositories.

---

## License

MIT © [Xianpeng Shen](https://github.com/shenxianpeng)
