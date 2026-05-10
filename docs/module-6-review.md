# Module 6: Code Review Agent 🔍

The Code Review Agent reads a pull request diff along with relevant codebase
context, then posts a structured code review with **inline line-level comments**
via GitHub's Pull Request Review API. It also auto-generates PR descriptions
and supports incremental re-review on new commits. It uses your Maintainer
Profile to check code style, tech stack preferences, PR standards, and overall
code quality.

> ⚠️ **Safety**: RepoKeeper **never** approves or merges PRs. It only provides
> review suggestions for the human maintainer.

## How It Works

1. **Triggered by label or comment** — add the `agent-review` label to a PR, or
   comment `/repokeeper review`.
2. **Reads PR diff** — fetches all changed files with patches (up to a size
   limit).
3. **Collects repo context** — scans relevant files for tech stack, code style,
   and patterns.
4. **Loads Maintainer Profile** — reads your code style, tone, PR standards
   from `repokeeper.yml`.
5. **Posts inline review** — creates a Pull Request Review with per-file,
   per-line annotations, suggestion blocks, and a summary. Falls back to a
   plain issue comment if the review API is unavailable.
6. **Incremental re-review** — when new commits are pushed to a PR, the
   previous RepoKeeper review is automatically dismissed and a fresh review
   is posted.

## Triggering

### Method 1: Label

Add the `agent-review` label to any pull request:

```
PR → Labels → agent-review
```

### Method 2: Comment

Comment `/repokeeper review` on a pull request (must be a repo collaborator,
member, or owner):

```
/repokeeper review
```

### Method 3: New commits (incremental re-review)

When `review.incremental` is `true` (default), pushing new commits to a PR
(`pull_request.synchronize`) triggers a re-review. The previous review is
dismissed and a fresh review with inline comments replaces it.

### CLI

```bash
# Run a review
repokeeper review --repo owner/repo --pr 42

# Generate a PR description from the diff
repokeeper describe --repo owner/repo --pr 42
```

## Installation

Copy the workflow template into your repo:

```bash
cp src/repokeeper/templates/workflows/review.yml .github/workflows/review.yml
```

Or run `repokeeper doctor --repo owner/repo` to diagnose missing workflows.

## What It Checks

The review agent evaluates:

| Area | What it checks |
|---|---|
| **Code Style** | Consistency with maintainer profile (indentation, naming, patterns) |
| **Tech Stack** | Does the implementation use your preferred stack/patterns? |
| **PR Standards** | File count, diff size, test coverage |
| **Code Quality** | Potential bugs, edge cases, performance issues |
| **Documentation** | Are new features documented? |
| **Test Coverage** | Are there tests for the changes? |

## Review Comment Format

The review is posted as a **Pull Request Review** with inline comments attached
to specific lines, plus a summary body:

- **🔴 Critical** — must-fix issues (blocks approval)
- **🟠 Major** — significant problems
- **🟡 Minor** — should-fix, but not blocking
- **⚪ Nit** — nice-to-have improvements

Each inline comment includes:
- The exact file and line number
- A clear problem description
- A suggested fix (GitHub ````suggestion` block for one-click apply)

Example inline comment:

```markdown
**Problem:** SQL injection risk — user input is concatenated into the query.

**Suggestion:**
```suggestion
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
```
```

The fallback (when no inline comments are possible) is a summary issue comment
with a table of findings.

## PR Description Generation

Repokeeper can auto-generate structured PR descriptions from the diff. This is
useful when contributors open PRs with minimal descriptions.

Trigger via CLI:

```bash
repokeeper describe --repo owner/repo --pr 42
```

Or enable automatic description generation in your profile:

```yaml
review:
  describe_on_open: true     # auto-generate on pull_request.opened
```

The LLM generates a description with these sections:
1. **Summary** — one paragraph explaining what the PR does
2. **Changes** — bullet list of key changes per file
3. **Testing** — suggested test plan
4. **Screenshots / Notes** — visual changes (or "None")

## Configuration

The review agent reads from your Maintainer Profile (`repokeeper.yml`):

```yaml
# Review-specific settings
review:
  model: deepseek-reasoner       # per-module model (default: agent.model)
  describe_on_open: false        # auto-generate PR description on pull_request.opened
  incremental: true              # re-review on new commits (pull_request.synchronize)

# Code standards the review agent checks against
style:
  code_style: "Follow existing patterns exactly. Use type hints."
  testing: pytest

# Tech stack preferences
tech:
  preferred: [python, fastapi]
  avoid: [jquery, php]

# PR standards
pr:
  min_tests: true
  max_files_per_pr: 15
  review_required: true
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `review.model` | string | `null` | LLM model override for reviews (null = inherit `agent.model`) |
| `review.describe_on_open` | bool | `false` | Auto-generate PR description when a PR is opened |
| `review.incremental` | bool | `true` | Re-review when new commits are pushed |

### Workflow Example

```yaml
# .github/workflows/review.yml
name: RepoKeeper Code Review

on:
  pull_request:
    types: [opened, synchronize]
  issue_comment:
    types: [created]

jobs:
  review:
    runs-on: ubuntu-latest
    if: |
      (
        github.event_name == 'pull_request' &&
        (github.event.action == 'opened' || github.event.action == 'synchronize')
      ) ||
      (
        github.event_name == 'issue_comment' &&
        github.event.issue.pull_request &&
        contains(github.event.comment.body, '/repokeeper review')
      )
    permissions:
      contents: read
      pull-requests: write
      issues: write
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v6
        with:
          python-version: '3.10'

      - name: Install RepoKeeper
        run: pip install repokeeper

      - name: Run Review Agent
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number || github.event.issue.number }}
        run: repokeeper review --repo ${{ github.repository }} --pr $PR_NUMBER
```

## See Also

- [Module 4: Maintainer Profile](module-4-profile.md) — profile configuration
- [Setup Guide](setup.md) — setting up RepoKeeper
