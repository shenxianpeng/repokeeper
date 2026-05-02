<p align="center">
  <img src="https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/docs/assets/logo.svg" width="80" alt="RepoKeeper logo">
</p>

# RepoKeeper

[![CI](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/shenxianpeng/repokeeper/branch/main/graph/badge.svg)](https://codecov.io/gh/shenxianpeng/repokeeper)
[![PyPI](https://img.shields.io/pypi/v/repokeeper.svg)](https://pypi.org/project/repokeeper/)
[![Python](https://img.shields.io/pypi/pyversions/repokeeper.svg)](https://pypi.org/project/repokeeper/)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://shenxianpeng.github.io/repokeeper/)

**AI-powered open source maintainer agent. Reads issues, writes code, opens PRs — 24/7.**

```bash
# Label an issue agent-todo — RepoKeeper handles the rest
@repokeeper go
```

Zero config. GitHub-native. ~$0.01 per PR with DeepSeek.

---

## Why RepoKeeper?

Open source maintenance is a second job you didn't sign up for. Existing tools help
*you* write code faster — Copilot, Cursor, Codeium. But what about **everything else**?
Triaging issues, bumping dependencies, diagnosing CI, responding to the community?

> **Copilot helps you write code. RepoKeeper runs your repo while you sleep.**

| | Copilot / Cursor | RepoKeeper |
|---|---|---|
| What it does | Suggests code as you type | Maintains your repo autonomously |
| How | Inline completion in editor | Reads issues + codebase → opens PRs |
| When | While you code | 24/7 on schedule |
| Community | No | Monitors, classifies, responds |
| Dependencies | No | Scans for outdated deps, auto-upgrades |
| CI | No | Diagnoses failures, suggests fixes |
| **Cost** | $10–39/month subscription | **~$0.01 per PR** with DeepSeek |
| Config | IDE settings | One YAML (or zero) |

## What It Does

- **🔭 Community Radar** — Monitors GitHub issues/discussions for keywords. AI classifies hits. Drafts responses. Notifies you.
- **🔍 Daily Patrol** — Scans dependencies, diagnoses CI failures, finds stale issues. Health score every weekday morning.
- **🤖 Implementation Agent** — Reads your codebase + issue → implements → pushes branch → opens PR. *You never write a line.*
- **👤 Maintainer Profile** — One YAML file describing your code style, tone, PR standards. *Or skip it — defaults work.*

## Adopt in 60 Seconds

### 1. Copy one workflow

```bash
mkdir -p .github/workflows
curl -o .github/workflows/repokeeper.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/.github/workflows/repokeeper.yml
```

### 2. Add your API key

**Settings → Secrets → Actions → New secret:** `DEEPSEEK_API_KEY` = `sk-...`

### 3. Trigger the agent

Label any issue `agent-todo` — or comment `@repokeeper go`.

**No Python. No install. No `repokeeper.yml`. Just works.**

> Want more? Copy `radar.yml` and `patrol.yml` too for community monitoring and daily health checks.

---

## Install (optional CLI)

```bash
pip install repokeeper
```

```bash
repokeeper init             # Create a profile
repokeeper radar --repo owner/repo
repokeeper patrol --repo owner/repo --summary
repokeeper agent --repo owner/repo --issue 42
```

---

## Documentation

Full docs at **[shenxianpeng.github.io/repokeeper](https://shenxianpeng.github.io/repokeeper)**

| Guide | |
|---|---|
| [Quick Start](https://shenxianpeng.github.io/repokeeper/quick-start/) | 5-minute setup |
| [Community Radar](https://shenxianpeng.github.io/repokeeper/module-1-radar/) | Monitor your community |
| [Daily Patrol](https://shenxianpeng.github.io/repokeeper/module-2-patrol/) | Automated health checks |
| [Implementation Agent](https://shenxianpeng.github.io/repokeeper/module-3-agent/) | AI-powered PRs |
| [Maintainer Profile](https://shenxianpeng.github.io/repokeeper/module-4-profile/) | Full config reference |

---

## License

MIT © [Xianpeng Shen](https://github.com/shenxianpeng)
