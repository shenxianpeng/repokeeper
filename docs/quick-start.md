# Quick Start

Get RepoKeeper running in 5 minutes.

## 1. Copy the agent workflow

Create `.github/workflows/repokeeper.yml`:

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

The composite action bundles checkout, Python setup, installation, and the agent
run into a single step.

You can also run `pip install repokeeper && repokeeper init . --minimal` if
you prefer the CLI to write the profile and workflow.

## 2. Add your API key

Go to **Settings → Secrets and variables → Actions** → **New repository secret**:

- **Name:** `DEEPSEEK_API_KEY`
- **Value:** `sk-...`

## 3. Push and test

Run a setup check before pushing:

```bash
repokeeper doctor --repo owner/repo
```

Fix anything marked `missing`. The doctor checks the profile, workflow file,
required workflow triggers and permissions, token environment, LLM key, and
repository slug.

```bash
git add .github/workflows/repokeeper.yml
git commit -m "Add RepoKeeper agent"
git push
```

Create a new issue, label it `agent-todo`, and RepoKeeper will analyze your
codebase and open a PR with the implementation.

Or comment `@repokeeper go` on an existing issue (must be a repo collaborator).

## 4. Optional: create a profile

RepoKeeper runs with defaults, but `repokeeper.yml` lets you customize style,
verification commands, and skip rules:

```bash
cat > repokeeper.yml <<'EOF'
maintainer: your-github-username

style:
  testing: pytest
  linting: true

agent:
  model: deepseek-chat
  verify_commands:
    - ruff check .
    - pytest tests
EOF
```

Run the local setup check any time:

```bash
repokeeper doctor --repo owner/repo
```

Want community monitoring and daily health reports too? Run
`repokeeper init . --all-workflows --force` to generate the full set of
workflows (Agent, Radar, Patrol, Labeler, Review).

Other useful commands:

```bash
repokeeper review --repo owner/repo --pr 42   # code review with inline comments
repokeeper describe --repo owner/repo --pr 42  # auto-generate PR description
```

---

That's it. RepoKeeper can now handle issue-triggered implementation PRs.
