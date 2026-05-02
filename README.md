# 🤖 RepoKeeper

<p align="center">
  <strong>AI-powered open source maintainer — runs <em>as your agent</em>, not just your assistant.</strong>
</p>

<p align="center">
  <a href="https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml"><img src="https://github.com/shenxianpeng/repokeeper/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/shenxianpeng/repokeeper"><img src="https://codecov.io/gh/shenxianpeng/repokeeper/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://pypi.org/project/repokeeper/"><img src="https://img.shields.io/pypi/v/repokeeper.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/repokeeper/"><img src="https://img.shields.io/pypi/pyversions/repokeeper.svg" alt="Python"></a>
  <a href="https://github.com/shenxianpeng/repokeeper/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <br>
  <a href="https://github.com/shenxianpeng/repokeeper"><img src="https://img.shields.io/badge/🤖%20RepoKeeper-AI%20Maintainer-6f42c1" alt="RepoKeeper"></a>
  <a href="https://github.com/shenxianpeng/repokeeper"><img src="https://img.shields.io/badge/Zero--Config-Ready-brightgreen" alt="Zero Config"></a>
</p>

---

**RepoKeeper** is an AI agent that *maintains* your open source project — it monitors your community, diagnoses CI failures, updates dependencies, finds stale issues, and **implements code from GitHub issues**, opening real PRs for your review.

Think of it as **an AI maintainer on your team**, not a code-completion tool.

---

## 🤔 Why RepoKeeper?

| | GitHub Copilot | RepoKeeper |
|---|---|---|
| **What it does** | Auto-completes code in your editor | Maintains your entire repo autonomously |
| **How it works** | Suggests next lines as you type | Reads issues + codebase → implements → opens PRs |
| **When it runs** | While you're coding | While you're sleeping ☕ |
| **Community** | No | Monitors issues, classifies, drafts responses |
| **Dependencies** | No | Scans for outdated deps, auto-upgrades |
| **CI** | No | Diagnoses failures, suggests fixes |
| **Config** | IDE settings | One YAML file (or zero — defaults work) |
| **Token cost** | $10–39/month subscription | ~$0.01 per PR with DeepSeek |

**Copilot helps you write code. RepoKeeper runs your repo.** They're complementary — use both.

---

## 🚀 What It Does

<table>
<tr>
  <td width="50%">

### 🔭 Community Radar
Monitors GitHub issues/discussions for keywords like `bug`, `crash`, `security`. AI classifies each hit, drafts issues or responses, and notifies you via email/Telegram/WeChat.

**Runs every 3 hours — catches things while you're away.**

  </td>
  <td width="50%">

### 🔍 Daily Patrol
Scans your dependency manifests, checks for outdated packages, diagnoses CI failures, identifies stale issues, and produces a **health score** with actionable recommendations.

**Runs every weekday morning — your daily repo health report.**

  </td>
</tr>
<tr>
  <td width="50%">

### 🤖 Implementation Agent
The flagship module. Label an issue `agent-todo` or comment `@repokeeper go`, and it reads your **entire codebase + the issue**, generates an implementation plan following your code style, pushes a branch, and opens a PR.

**No human needs to write a single line for routine issues.**

  </td>
  <td width="50%">

### 👤 Maintainer Profile
A single YAML file that describes *you* as a maintainer: code style, tone, PR standards, tech stack preferences. All modules respect it. **Or don't create one — sensible defaults work out of the box.**

  </td>
</tr>
</table>

---

## ⚡ Adopt in 60 Seconds

### No profile needed. Seriously.

```bash
# 1. Copy the workflow (that's it!)
mkdir -p .github/workflows
curl -o .github/workflows/repokeeper.yml \
  https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/.github/workflows/repokeeper.yml
```

### 2. Add your API key

Go to **Settings → Secrets and variables → Actions** → **New repository secret**:

| Name | Value |
|------|-------|
| `DEEPSEEK_API_KEY` | `sk-...` ([get one free](https://platform.deepseek.com/api_keys)) |

### 3. Use it

Label any issue `agent-todo` — RepoKeeper will analyze your codebase and open a PR.

```
💬 Or comment: @repokeeper go
```

**That's it.** No `repokeeper.yml` required. No Python install. No configuration. Sensible defaults handle everything.

> Want customization? Create a `repokeeper.yml` — [see the full profile reference](https://shenxianpeng.github.io/repokeeper/reference/profile-schema/).

---

## 📦 Install (optional CLI)

Prefer running locally?

```bash
pip install repokeeper
```

```bash
repokeeper radar --repo owner/repo        # Scan your community
repokeeper patrol --repo owner/repo       # Health check your repo
repokeeper agent --repo owner/repo --issue 42  # Implement an issue
```

---

## 🧠 How the Agent Works

```
Issue #42: "Add dark mode toggle"
          │
          ▼
   ┌──────────────────┐
   │  Reads codebase   │  ←  Collects 40 source files
   │  + issue + style  │  ←  Follows your code conventions
   └──────┬───────────┘
          │
          ▼
   ┌──────────────────┐
   │  AI generates     │  ←  Plans minimal, precise changes
   │  implementation   │  ←  Respects your tech stack
   └──────┬───────────┘
          │
          ▼
   ┌──────────────────┐
   │  Pushes branch    │  ←  repokeeper/issue-42-dark-mode
   │  Opens PR         │  ←  With change summary
   └──────────────────┘
```

**Safety built-in:** Auto-merge disabled by default, workflow files (*`.github/workflows/`*) are never modified, skip keywords can block unsafe issues, max file count enforced per PR.

---

## 📚 Documentation

Full docs at **[shenxianpeng.github.io/repokeeper](https://shenxianpeng.github.io/repokeeper)**

| Guide | Description |
|-------|-------------|
| [Quick Start](https://shenxianpeng.github.io/repokeeper/quick-start/) | 5-minute setup |
| [Module 1: Radar](https://shenxianpeng.github.io/repokeeper/module-1-radar/) | Community monitoring |
| [Module 2: Patrol](https://shenxianpeng.github.io/repokeeper/module-2-patrol/) | Daily health checks |
| [Module 3: Agent](https://shenxianpeng.github.io/repokeeper/module-3-agent/) | AI implementation agent |
| [Module 4: Profile](https://shenxianpeng.github.io/repokeeper/module-4-profile/) | Configuration reference |

---

## 🔧 Requirements

- GitHub Actions enabled on your repo
- A [DeepSeek API key](https://platform.deepseek.com/api_keys) (free tier available)
- That's it. No Python, no dependencies, no setup.

---

## 🤝 Contributing

Issues and PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 License

MIT © [Xianpeng Shen](https://github.com/shenxianpeng)
