---
template: landing.html
title: RepoKeeper
hero:
  eyebrow: GitHub-native maintainer agent
  title: "Keep repositories moving.<br>*Turn issues into verified PRs.*"
  text: >-
    RepoKeeper runs maintenance where open source work already happens:
    GitHub issues, Actions, branches, checks, and pull requests. It watches
    the queue, reads the codebase, verifies changes, and leaves maintainers
    with reviewable work instead of another prompt.
  actions:
    - {text: Add to a repo, link: quick-start.md, icon: rocket, primary: true}
    - {text: See the agent flow, link: module-3-agent.md, icon: workflow}
  code:
    title: github-actions / repokeeper
    lang: text
    content: |
      # label: agent-todo
      [repokeeper] Issue #42: Add dark mode toggle
      [repokeeper] Running Pi coding agent (deepseek-chat)...
      [repokeeper] Plan: Add theme state and styles
      [repokeeper] Verifying: ruff check .
      [repokeeper] Verifying: pytest tests
      PR opened: github.com/owner/repo/pull/67
sections:
  - type: features
    columns: 4
    items:
      - {icon: tag, title: Trigger, text: Issue label or comment}
      - {icon: folder-git-2, title: Context, text: Repo files + maintainer profile}
      - {icon: shield-check, title: Verify, text: Project lint and tests}
      - {icon: git-pull-request, title: Deliver, text: "Branch, commit, pull request"}
  - type: split
    title: What ships now
    text: >-
      GitHub Copilot helps you code in the editor and review PRs.
      CodeRabbit and PR-Agent automate PR workflows with line-level suggestions.
      RepoKeeper does what they don't — implements issues, fixes PRs
      conversationally, scans 8 ecosystems for outdated dependencies, diagnoses
      CI failures, and monitors your community. Two backends: native (fast,
      cheap) and Pi (autonomous agent loop for complex tasks).
    bullets:
      - Issue-triggered implementation agent with native and Pi backends
      - Conversational PR fix mode — comment on a PR, agent fixes it
      - Inline code review with severity indicators on specific lines
      - Auto-labeler for issues and PRs (15 categories, diff-aware)
      - Draft release notes from merged PRs and direct commits
      - Profile-driven code style, skip keywords, and PR guardrails
      - Pre-PR verification through discovered or configured commands
      - Community radar (issues + discussions) and daily patrol reports
  - type: steps
    title: Fastest path
    items:
      - {title: Add the workflow, text: Copy one GitHub Actions workflow into the repository.}
      - {title: Add a secret, text: "Add a `DEEPSEEK_API_KEY` repository secret."}
      - {title: Hand over an issue, text: "Label an issue `agent-todo` or comment `/repokeeper go`."}
      - {title: Review, text: Review the generated pull request.}
      - {title: Iterate, text: "Comment `/repokeeper go` on the PR with feedback to get fixes."}
  - type: faq
    title: Current boundary
    items:
      - q: Can RepoKeeper merge its own pull requests?
        a: No. Human review remains required before merging generated PRs.
      - q: Does it edit workflow files?
        a: No. Workflow file edits are blocked for GitHub token safety.
      - q: Does it open dependency upgrade and CI repair PRs on its own?
        a: Not yet. They are reported as candidates first.
  - type: content
---

## RepoKeeper vs. AI coding tools

| | Copilot Code Review | CodeRabbit | PR-Agent | RepoKeeper |
| --- | --- | --- | --- | --- |
| Issue → PR | — | — | — | Native + Pi |
| PR fix (conversational) | — | — | — | Push to same branch |
| Code review | PR only | Line-level | /review | Inline + severity |
| PR description | — | Auto | /describe | /describe |
| Auto-labeling | — | Yes | Yes | 15 categories, diff-aware |
| Dependency scanning | — | — | — | 8 ecosystems |
| CI auto-fix | — | — | — | Repair PRs |
| Community radar | — | — | — | Issues + discussions |
| Scheduled / cron | — | — | — | Daily patrol |
| OSS cost | Subscription | Free | Free | **Free (your own LLM key)** |
