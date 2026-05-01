# RepoKeeper 🤖

[![CI](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/shenxianpeng/repokeeper/branch/main/graph/badge.svg)](https://codecov.io/gh/shenxianpeng/repokeeper)
[![PyPI](https://img.shields.io/pypi/v/repokeeper.svg)](https://pypi.org/project/repokeeper/)
[![Python](https://img.shields.io/pypi/pyversions/repokeeper.svg)](https://pypi.org/project/repokeeper/)

**AI-powered open source maintainer agent.**

RepoKeeper automates the tedious parts of open source maintenance:
community monitoring, dependency updates, CI diagnosis, stale issue
detection, and AI-powered PR creation — all driven by a single YAML
profile that describes your maintainer preferences.

## Modules

| # | Module | What it does |
|---|--------|-------------|
| 🔭 | **Community Radar** | Monitors GitHub issues/discussions for keywords, AI-classifies posts, drafts issues, sends notifications |
| 🔍 | **Daily Patrol** | Scans dependencies, diagnoses CI failures, finds stale issues, generates health scores |
| 🤖 | **Implementation Agent** | Reads issues + codebase, implements code changes, opens a PR for your review |
| 👤 | **Maintainer Profile** | YAML config describing your style, tone, PR standards, and tech preferences |

## Quick Start

Install the CLI:

```bash
pip install repokeeper
```

Create a maintainer profile:

```bash
repokeeper init --workflows
```

```yaml
# repokeeper.yml — place this in any repo
maintainer: your-github-username

radar:
  keywords: [bug, crash, security, feature request]

patrol:
  schedule: "0 8 * * 1-5"

agent:
  model: deepseek-chat
```

Then label an issue `agent-todo` or comment `@repokeeper go`.

You can also run modules directly:

```bash
repokeeper profile validate
repokeeper radar --repo owner/repo
repokeeper patrol --repo owner/repo --summary
repokeeper agent --repo owner/repo --issue 42
```

## Documentation

Full docs: [https://shenxianpeng.github.io/repokeeper](https://shenxianpeng.github.io/repokeeper)

- [Getting Started](docs/setup.md)
- [Module 1: Community Radar](docs/module-1-radar.md)
- [Module 2: Daily Patrol](docs/module-2-patrol.md)
- [Module 3: Implementation Agent](docs/module-3-agent.md)
- [Module 4: Maintainer Profile](docs/module-4-profile.md)

## Requirements

- Python 3.10+
- GitHub Actions enabled
- DeepSeek API key (or any OpenAI-compatible API)

For the Implementation Agent, enable **Settings → Actions → General → Allow
GitHub Actions to create and approve pull requests**, or provide a
`REPOKEEPER_GITHUB_TOKEN` secret with write access to contents and pull requests.

## License

MIT
