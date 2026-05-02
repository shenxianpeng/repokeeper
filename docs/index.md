# 🤖 RepoKeeper

<div align="center" markdown>

**Your AI maintainer. Autonomously monitors, maintains, and implements — so you can focus on building.**

[Quick Start :material-rocket-launch:](quick-start.md){ .md-button .md-button--primary }
[GitHub :material-github:](https://github.com/shenxianpeng/repokeeper){ .md-button }

</div>

---

## Why an AI Maintainer?

Open source maintenance isn't glamorous. Triaging issues, updating dependencies, diagnosing CI failures, responding to community questions — it piles up. *Hours* of work before you even write code.

RepoKeeper is an **AI agent that does this work for you**. It's not a code-completion tool you invoke — it's a team member that runs on schedule, watches your repo, and takes action.

| Traditional tool | RepoKeeper |
|:--|:--|
| Completes code in your editor | Opens PRs from issues |
| Reacts to your typing | Runs on schedule, 24/7 |
| Helps *you* write code | Writes code *for you* to review |
| No community awareness | Monitors issues, classifies, responds |
| IDE-bound | GitHub-native, runs in Actions |

---

## Four Modules, One Agent

<div class="grid cards" markdown>

- :material-radar: **Community Radar**

    ---

    Monitors GitHub for keywords like `bug`, `crash`, `security`. AI classifies each hit. Drafts issues. Sends notifications.

    [:octicons-arrow-right-24: Learn more](module-1-radar.md)

- :material-shield-search: **Daily Patrol**

    ---

    Scans dependencies. Diagnoses CI failures. Finds stale issues. Produces a **health score** with actionable fixes.

    [:octicons-arrow-right-24: Learn more](module-2-patrol.md)

- :material-robot: **Implementation Agent**

    ---

    The flagship. Reads your codebase + an issue → implements changes → pushes a branch → opens a PR. *Zero human code required.*

    [:octicons-arrow-right-24: Learn more](module-3-agent.md)

- :material-file-cog: **Maintainer Profile**

    ---

    One YAML file. Your code style, tone, PR standards, tech preferences. Every module respects it. *Or skip it — defaults work.*

    [:octicons-arrow-right-24: Learn more](module-4-profile.md)

</div>

---

## The 60-Second Setup

No Python. No dependencies. Just a workflow file and an API key.

```yaml title=".github/workflows/repokeeper.yml"
# Copy this one file into your repo — that's it.
```

<div class="grid cards" markdown>

- **:material-numeric-1-circle: Copy the workflow**

    ```bash
    mkdir -p .github/workflows
    curl -o .github/workflows/repokeeper.yml \
      https://raw.githubusercontent.com/shenxianpeng/repokeeper/main/.github/workflows/repokeeper.yml
    ```

- **:material-numeric-2-circle: Add your API key**

    **Settings → Secrets → Actions → New secret**
    
    | Name | Value |
    |------|-------|
    | `DEEPSEEK_API_KEY` | `sk-...` |

- **:material-numeric-3-circle: Trigger the agent**

    Label any issue `agent-todo`
    
    *Or comment `@repokeeper go`*

</div>

**No `repokeeper.yml` needed.** Built-in defaults handle everything: code style, PR standards, radar keywords, patrol schedule. You can customize later.

[:material-arrow-right: Full setup guide](setup.md)

---

## How the Agent Works

``` mermaid
graph TD
    ISSUE["Issue #42<br/>'Add dark mode'"] --> AGENT

    subgraph AGENT["🤖 RepoKeeper Agent"]
        READ["Reads 40 source files<br/>+ issue + comments"] --> PLAN
        PLAN["AI generates plan<br/>respects your code style"] --> CODE
        CODE["Produces minimal diff<br/>never touches workflows/"] --> PUSH
    end

    PUSH --> PR["Opens PR<br/>for your review"]
```

---

## Built for Safety

- :material-shield-check: **Workflow files never modified** — `.github/workflows/` is blocked
- :material-shield-check: **Auto-merge off by default** — every PR requires human review
- :material-shield-check: **Skip keywords** — `needs design`, `breaking change` can auto-skip
- :material-shield-check: **Max files per PR** — configurable, blocks overambitious changes
- :material-shield-check: **Low-confidence hits filtered** — Radar only acts above your threshold

---

## FAQ

**Does this replace me as a maintainer?**
No. It automates the *routine* work — triage, dependency bumps, simple fixes. You still review every PR, set direction, and make decisions.

**How much does it cost?**
With DeepSeek: ~$0.01 per PR. Radar + Patrol combined: ~$0.03/day. DeepSeek offers a free tier.

**Do I need a `repokeeper.yml`?**
No. Without one, all defaults apply. Create one when you want customization (style preferences, custom keywords, notifications).

**Can I run it locally?**
Yes: `pip install repokeeper`, then `repokeeper agent --repo owner/repo --issue 42`.

---

*RepoKeeper is MIT licensed. Built by [@shenxianpeng](https://github.com/shenxianpeng).*
